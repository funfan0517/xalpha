#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""每日操作汇总看板 · 跨策略归档 + 单文件 HTML

把 ``pipeline/strategies.json`` 里注册的每个策略的「每日产物」归一化成同一结构
（代码 / 名称 / 动作 / 说明），按运行日归档到 ``data/_daily_digest.json``，
并渲染 ``data/_daily_digest.html``（单文件、无外链，双击可看）。

卡片可选带图：策略在 ``daily.chart`` 里声明产物图（仓库相对路径），卡片就在表格下方
内嵌显示。一张卡要多图时用 ``_entry(imgs=[{"path": .., "cap": ..}])``（板块2 的八张
变种单腿图就走这条路），渲染成表下方的图墙，每张图下面一行小字标注是哪个标的。
历史库（``_daily_digest.json``）**只记路径**，图随策略脚本每日重画；
HTML 渲染时按路径读一次、base64 内嵌（按路径去重），这样 ``data/_daily_digest.html``
是真正自包含的单文件 —— IDE 预览 / 内嵌浏览器的 base 路径不是仓库根时，相对路径
会 404 显示破图，内嵌可避免。

看板按 ``strategies.json`` 顶层 ``blocks`` 分节（每日操作板块 1→5：总策略 → 波动类 →
风格轮动 → 稳健红利 → 技术指标），策略通过自己的 ``daily.block`` 认领板块；
未认领的归入末尾「其他」。板块定义里不存策略清单，加策略只改那一处 ``daily.block``。

``daily.enabled=false`` → 整卡撤掉（含历史归档）；``daily.card=false`` → 数据仍取、但不单独出卡，
由所属卡片内嵌展示（板块5 的 V17 趋势闸门就缩成技术指标卡片里 V10 下面的一行）。

产物格式优先读结构化文件（json / jsonl）；``tech_indicators`` 目前只落 markdown，
则解析其自产表格（格式由本仓库脚本生成，稳定且简单）。
任一策略解析失败只影响它自己——卡片标「解析失败」，不中断其它策略。

用法::

    python pipeline/daily_digest.py              # 归档今天 + 重绘看板
    python pipeline/daily_digest.py --html-only  # 只按已有历史重绘
    python pipeline/daily_digest.py --date 2026-10-20   # 指定归档日（补录用）
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import sys
from datetime import datetime

_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_DIR)
CFG = os.path.join(_DIR, "strategies.json")
DB = os.path.join(_ROOT, "data", "_daily_digest.json")
HTML = os.path.join(_ROOT, "data", "_daily_digest.html")
# strategies.json 原文（collect() 时载入）：个别卡片要跨策略读产物路径，
# 例如板块5 的技术指标卡片把 V17 闸门缩成一行，需按 v17_trend_gate.daily.signal_json 取数。
_DOC = {}
# 看板卡片图的同目录副本（部分预览器只 serve 与 HTML 同目录的文件）
_IMG_DIR = os.path.join(_ROOT, "data", "_daily_digest_assets")

DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
CODE_RE = re.compile(r"`([0-9A-Za-z]{5,6})`")

ACT = ("buy", "sell", "hold", "watch", "avoid", "none")


# ---------------------------------------------------------------- 通用工具
def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _rows_of_jsonl(path):
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _rel(path):
    """产物路径统一写成相对仓库根（产物换机器也能打开）。"""
    return os.path.relpath(path, _ROOT).replace("\\", "/")


def _section(lines, keyword):
    """取 markdown 中以 ``## ...keyword...`` 开头到下一个二级标题之间的行。"""
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith("## ") and keyword in ln:
            start = i + 1
            break
    if start is None:
        return []
    out = []
    for ln in lines[start:]:
        if ln.startswith("## "):
            break
        out.append(ln)
    return out


def _table(lines):
    """解析 markdown 表格 -> list[list[str]]，自动跳过表头与分隔行。"""
    rows, header = [], True
    for ln in lines:
        s = ln.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(set(c) <= set("-: ") for c in cells):
            continue
        if header:
            header = False
            if cells and cells[0] in ("代码", "标的", "策略", "资产", "标签"):
                continue
        rows.append(cells)
    return rows


def _first_date(*vals):
    for v in vals:
        if not v:
            continue
        m = DATE_RE.search(str(v))
        if m:
            return m.group(1)
    return None


def _num(s):
    """从「**31.6%**」「+1.6pp」「×1.07」「−0.0pp」这类单元格里取第一个数字。"""
    m = re.search(r"[-+−]?\d+(?:\.\d+)?", str(s).replace(",", ""))
    return float(m.group(0).replace("−", "-")) if m else None


def _card_on(meta):
    """该策略是否单独出卡。

    - ``daily.enabled=false`` → 整卡撤掉（策略已移出每日操作，历史归档也不再渲染）；
    - ``daily.card=false``   → 数据仍取、但不单独出卡（如 V17 缩成板块5 技术指标卡片的一行）。
    """
    daily = (meta or {}).get("daily") or {}
    return daily.get("enabled") is not False and daily.get("card") is not False


def _blocks(doc):
    """每日操作的板块定义（顺序即看板分节顺序）。

    ``pipeline/strategies.json`` 顶层的 ``blocks`` 只声明板块本身（序号 / 标题 / 说明），
    每个策略在 ``daily.block`` 里认领板块 key —— 归属关系由策略侧声明，避免板块里
    再维护一份策略清单导致两边不同步。未认领板块的策略统一归入「其他」。
    """
    cfg = doc.get("strategies") or {}
    out = []
    for i, b in enumerate(doc.get("blocks") or [], 1):
        key = b.get("key")
        out.append({
            "no": b.get("no", i),
            "key": key,
            "title": b.get("title") or key,
            "desc": b.get("desc") or "",
            "note": b.get("note") or "",
            # daily.enabled=false 的策略不进本板块（否则看板会渲染早已撤掉的卡）；
            # daily.card=false 表示「数据仍要、但不单独出卡」（如 V17 缩成板块5 的一行）
            "keys": [k for k, m in cfg.items()
                     if ((m.get("daily") or {}).get("block") or "") == key
                     and _card_on(m)],
        })
    return out


def _entry(key, name, as_of, badge, tone, summary, rows, stats=None,
           note="", src="", error="", cols=None, img="", img_cap="", all_rows=False,
           imgs=None, data=""):
    """``all_rows=True``: 该卡不受看板「只看有动作」筛选影响 —— 用于「池子本身就小、
    空仓标的的读数（RSI/温度）也是决策依据」的卡片，例如红利 V4 的 5 只择时池。

    ``as_of``: 该策略的**信号日**（决策依据的那根收盘）。
    ``data``: 该策略**依赖的各数据源最新时点**（如「净值 11-04~11-05（QDII 11-04）」）——
    同一策略内不同源可能差好几天（QDII 净值滞后 1~2 日、估值月度序列、ETF 行情当日），
    只写一个 as_of 会把这种差异藏掉（2026-11-06 用户要求每张卡都标出来）。

    ``imgs``: 多图卡片（板块2 的八张变种单腿图）—— `[{"path": 仓库相对路径, "cap": 标题}]`，
    渲染成表下方的图墙，每张图下面一行小字标注是哪个标的。单图卡片继续用 ``img``/``img_cap``。
    """
    return {
        "key": key,
        "name": name,
        "as_of": as_of,
        "data": data,
        "badge": badge,
        "tone": tone if tone in ACT else "none",
        "summary": summary,
        "stats": stats or [],
        "rows": rows,
        "cols": cols or ["代码", "名称", "动作", "说明"],
        "note": note,
        "src": src,
        "error": error,
        "img": img,
        "img_cap": img_cap,
        "imgs": imgs or [],
        "all_rows": bool(all_rows),
    }


def _row(code, name, act, label, detail=""):
    return {"c": code or "—", "n": name or "—", "act": act if act in ACT else "none",
            "a": label or "", "d": detail or ""}


# ---------------------------------------------------------------- 各策略解析
def parse_dividend(_cfg):
    # 2026-10-26 每日操作 = V4 RSI14 择时；2026-10-30 起 V9 五因子温度一并纳入（同卡片展示）
    daily = _cfg["daily"]
    path = os.path.join(_ROOT, daily["signal_json"])
    d = json.loads(_read(path))
    as_of = d.get("as_of")
    tier = d.get("tier") or "—"
    mult = float(d.get("mult") or 0)
    tone = "buy" if mult >= 0.99 else ("watch" if mult > 0.01 else "sell")

    positions = d.get("positions") or []
    pos = {p.get("code"): float(p.get("weight") or 0) for p in positions}
    prev = d.get("preview") or {}
    rsi = {p.get("code"): p.get("rsi") for p in prev.get("picks") or []}
    pool = sorted(d.get("pool") or [],
                  key=lambda p: (p.get("rsi") if p.get("rsi") is not None
                                 else rsi.get(p.get("code"))) or 999)

    nd = d.get("next_decision") or "—"
    rows = []
    for p in pool:
        code = p.get("code")
        w = pos.get(code, 0.0)
        act, label = ("hold", f"持有 {w * 100:.0f}%") if w > 0 else ("none", "空仓")
        r = p.get("rsi")
        if r is None:
            r = rsi.get(code)
        pv = p.get("preview_weight")
        if pv is None:
            dec = "待定"
        elif float(pv) > 1e-9:
            dec = f"预计入选 {float(pv) * 100:.0f}%"
        else:
            dec = "预计落选"
        # 池子只有 5 只，逐只 RSI 本身就是「为什么选这 2 只」的依据 —— 空仓行的读数也要给全。
        detail = (f"RSI14 {r:.1f}" if r is not None else "RSI14 —（今日无快照）")
        detail += f" · 权重 {w * 100:.0f}% · 下次 {nd} {dec}"
        rows.append(_row(code, p.get("name"), act, label, detail))

    holds = "、".join(f"`{p.get('code')}` {float(p.get('weight') or 0) * 100:.0f}%"
                     for p in positions) or "空仓"
    # 「下次预计」是策略自带的预演（用未走完的本月数据，非前瞻）—— 直接摆到卡片摘要与 chip 上，
    # 否则它只藏在明细表第四列，容易被读成「今天没有建议」。
    prev_tier = prev.get("tier") or "—"
    preview = f" · 下次 {nd} 预计 {prev_tier}"
    if prev.get("changed"):
        tgt = "、".join(f"`{t.get('code')}` {float(t.get('weight') or 0) * 100:.0f}%"
                       for t in prev.get("target") or []) or "空仓"
        preview += f"，会换仓 → {tgt}"
    else:
        preview += "，持仓不变"

    # 2026-10-30 起 V9 估值温度独立成卡（见 parse_dividend_v9）：V4 只管「仓位怎么摆」，
    # V9 只管「贵不贵、额度给谁」，混在一张表里会被读成同一层决策。
    return _entry(
        "dividend", "红利 V4 · RSI14 择时", as_of,
        tier.replace("（", " ").replace("）", ""), tone,
        f"板块 RSI {d.get('board_rsi', '—')} · 持 {holds}{preview}", rows,
        [["板块 RSI", str(d.get("board_rsi", "—"))], ["档位", tier],
         ["持仓", f"{len(positions)} 只 · {sum(pos.values()) * 100:.0f}%"],
         ["下次决策", f"{nd} · 预计 {prev_tier}"]],
        note="V4 · RSI14 择时（月末收盘确认、次日开盘执行；月中为预览）：板块(池内 5 只)RSI 均值 ≥70 清仓 / ≥50 减半，"
             "横截面取 RSI 最低 2 只等权。2026-10-26 起替代 V0 打分（V0 产物仍在 v0_rank/daily/，可手动重跑）。"
             "V9 五因子估值温度已拆成板块4 的第二张卡。",
        src=_rel(os.path.join(_ROOT, daily["signal_md"])),
        data=f"基金累计净值 {as_of} · 生效信号 {d.get('signal_date')} 收盘（月末确认，月中为预览）",
        cols=["代码", "名称", "本月动作", "RSI / 权重 / 下次预计"],
        # 池内只有 5 只、且「空仓那 3 只的 RSI」正是横截面选 2 只的依据 —— 这一卡不吃
        # 「只看有动作」筛选，否则每天只剩 2 行，看不出档位是怎么来的。
        all_rows=True,
    )


def parse_dividend_v9(_cfg):
    """V9 五因子估值温度：6 只有估值标的的逐只档位，不调仓位（体检表）。"""
    daily = _cfg["daily"]
    d = json.loads(_read(os.path.join(_ROOT, daily["signal_json"])))
    funds = sorted(d.get("funds") or [], key=lambda f: -(float(f.get("temp") or 0)))
    rows, hot, cold = [], [], []
    for f in funds:
        temp = float(f.get("temp") or 0)
        band = f.get("band") or "—"
        if band in ("危险", "过热", "偏热"):
            act, hot = "sell", hot + [f.get("code")]
        elif band in ("冰点", "偏冷"):
            act, cold = "buy", cold + [f.get("code")]
        else:
            act = "hold"
        rows.append(_row(f.get("code"), f.get("name"), act, band,
                         f"温度 {temp:.1f} · {f.get('action', '')} · "
                         f"{f.get('index_name') or f.get('index', '')}"))

    bt, bp = d.get("board_temp"), d.get("board_pct")
    tone = "sell" if len(hot) > len(cold) else ("buy" if len(cold) > len(hot) else "hold")
    badge = f"板块温度 {float(bt):.1f}" if bt is not None else "—"
    return _entry(
        "dividend_v9", "红利 V9 · 五因子估值温度", d.get("as_of"), badge, tone,
        f"{len(funds)} 只有估值标的 · 板块温度 {badge}"
        + (f"（自身分位 {float(bp) * 100:.0f}%）" if bp is not None else "")
        + f" · 偏热 {len(hot)} / 偏冷 {len(cold)} · 只给额度参考，不调仓位",
        rows,
        [["板块温度", "—" if bt is None else f"{float(bt):.1f}"],
         ["板块自身分位", "—" if bp is None else f"{float(bp) * 100:.0f}%"],
         ["偏热只数", str(len(hot))], ["偏冷只数", str(len(cold))]],
        note="V9 · 五因子温度（利差 35 / PE 20 / PB 16 / 乖离 15 / 位置 14，各取近 2 年分位）："
             "档位 危险≥85 / 过热≥75 / 偏热≥65 / 中性偏热≥55 / 中性≥40 / 偏冷≥20 / 冰点<20。"
             "回测显示横截面选品与分位减仓均无 alpha，故**只当估值体温计用，不输出仓位倍率**；"
             "仓位节奏看板块4 的 V4 卡。完整温度表见 strategies/dividend/v9_temp5/daily/_v9_temp.html。",
        src=_rel(os.path.join(_ROOT, daily["signal_md"])),
        data=f"指数估值 + 基金净值 {d.get('as_of')}（分位窗口近 2 年）",
    )


def parse_growth_div(_cfg):
    path = os.path.join(_ROOT, "strategies", "growth_div_rotation", "v0", "daily", "_gd_signal.json")
    d = json.loads(_read(path))
    snap = d.get("snapshot") or {}
    act_txt = d.get("action_tomorrow") or "不动"
    tone = {"切成长": "buy", "切红利": "sell"}.get(act_txt, "hold")
    rows = [_row(
        d.get("current_position_code"), d.get("current_position"), tone, act_txt,
        f"{d.get('reason', '')} · R {snap.get('R'):.4f} vs MA20 {snap.get('ma20'):.4f} · "
        f"Q {float(snap.get('q') or 0) * 100:.1f}%" if snap.get("R") else d.get("reason", ""),
    )]
    return _entry(
        "growth_div_rotation", "成长/红利轮动", d.get("as_of"),
        act_txt, tone,
        f"持仓 {d.get('current_position')} · 明日 {act_txt} · {d.get('reason', '')}",
        rows,
        [["R", f"{snap.get('R', 0):.2f}"], ["MA20", f"{snap.get('ma20', 0):.2f}"],
         ["Q", f"{float(snap.get('q') or 0) * 100:.1f}%"],
         ["本月切换", f"{d.get('month_switches')}/{d.get('month_cap')}"]],
        note="二元轮动（成长 / 红利·现金），信号用指数收盘，次日开盘执行。",
        src=_rel(os.path.join(_ROOT, "strategies", "growth_div_rotation", "v0", "daily", "_signal_report.md")),
        data=f"指数日线（科创50 000688 ÷ 中证红利 000922）{snap.get('date') or d.get('as_of')}",
    )


def parse_gd_v1(_cfg):
    """成长/红利轮动 v1：R 的绝对水位（0.20/0.31）+ 5 期分批，与 v0 的 MA20 缓冲带并列。"""
    daily = _cfg["daily"]
    d = json.loads(_read(os.path.join(_ROOT, daily["signal_json"])))
    act = d.get("action") or "不动"
    tone = {"切成长": "buy", "切红利": "sell", "分批中": "watch"}.get(act, "hold")
    t = d.get("trigger") or {}
    side = d.get("target") or "dividend"
    ex = (d.get("exec") or {}).get(side) or {}
    rows = [_row(ex.get("code"), f"{d.get('target_label', side)}侧", tone, act,
                 f"{d.get('reason', '')} · R {d.get('R')} 昨 {d.get('R_prev')} · "
                 f"{t.get('dist_text', '')}")]
    return _entry(
        "gd_v1", "成长/红利轮动 v1 · 绝对阈值", d.get("as_of"), act, tone,
        f"持 {d.get('target_label', side)} · 今日 {act} · {t.get('dist_text', '')}",
        rows,
        [["R", f"{float(d.get('R') or 0):.4f}"],
         ["阈值 LOW/HIGH", f"{d.get('low')} / {d.get('high')}"],
         ["当前标的", str(d.get("target_label", side))],
         ["分批剩余", f"{d.get('pending_left')} 份"]],
        note="v1 = R 的**绝对水位**（科创50 ÷ 中证红利）：R≤0.20 清红利分 5 期买成长 / R≥0.31 清成长分 5 期买红利 / "
             "中间地带不动、只按定投日买当前标的。与 v0 的差别：v0 看 R 相对 MA20±1% 缓冲带 + 250 日分位（无量纲、换标的不失效），"
             "v1 看绝对水位（量纲相关，换指数/基期必须重标定）。回测 2020-01~2026-10：v1 年化 +11.31%/回撤 -45.55%/Calmar 0.25、"
             "全期仅 2 次换仓；v0 +9.43%/-35.78%/Calmar 0.26、64 次切换 —— 风险调整后打平，v1 阈值近乎惰性。",
        src=_rel(os.path.join(_ROOT, daily["signal_report"])),
    )


def parse_gd_v2(_cfg):
    """成长/红利轮动 v2：R 绝对水位 0.35/0.65，一次性整仓切换（**信号层裸基准**）。"""
    daily = _cfg["daily"]
    d = json.loads(_read(os.path.join(_ROOT, daily["signal_json"])))
    act = d.get("action") or "不动"
    tone = {"加仓成长": "buy", "减仓成长": "sell"}.get(act, "hold")
    t = d.get("trigger") or {}
    cur_w = float(d.get("current_w") or 0)
    side = "growth" if cur_w >= 0.5 else "dividend"
    ex = (d.get("exec") or {}).get(side) or {}
    rows = [_row(ex.get("code"), f"{d.get('current_label')}侧 {cur_w:.0%}", tone, act,
                 f"{d.get('reason', '')} · {t.get('dist_text', '')}")]
    return _entry(
        "gd_v2", "成长/红利轮动 v2 · 绝对水位 0.35/0.65", d.get("as_of"), act, tone,
        f"持 {d.get('current_label')}（成长 {cur_w:.0%}）· 明日 {act} · R {d.get('R')}",
        rows,
        [["R", f"{float(d.get('R') or 0):.4f}"],
         ["阈值 LOW/HIGH", f"{d.get('low')} / {d.get('high')}"],
         ["成长权重", f"{cur_w:.0%} → {float(d.get('target_w') or 0):.0%}"],
         ["下次触发", t.get("dist_text", "—")]],
        note="v2 = R 的**绝对水位**（创业板指 ÷ 中证红利）：R≤0.35 满仓成长 / R≥0.65 满仓红利 / "
             "中间带保持原仓，无冷却、无分批。全期 +17.27%/-51.85%/Calmar 0.33 —— 年化家族最高，"
             "但回撤比它换着持有的中证红利（-46.51%）还深，**定位是「信号层裸基准」**、"
             "不是可直接实盘的仓位方案；要看风控层的加成请对照同一板块的 v5 卡。"
             "阈值是全样本内挑出的绝对值（量纲相关），换指数/基期须重标定。",
        src=_rel(os.path.join(_ROOT, daily["signal_report"])),
    )


def _gd_side_act(cur, nxt):
    """权重变化 -> (动作类型, 说明)。"""
    if nxt - cur > 1e-6:
        return "buy", f"{cur:.0%} → {nxt:.0%}"
    if cur - nxt > 1e-6:
        return "sell", f"{cur:.0%} → {nxt:.0%}"
    return "hold", f"维持 {cur:.0%}"


def parse_gd_v5(_cfg):
    """成长/红利轮动 v5：R 水位 + 双侧 MA200 闸门 + 18% 波动率目标（**家族风险调整最优**）。"""
    daily = _cfg["daily"]
    d = json.loads(_read(os.path.join(_ROOT, daily["signal_json"])))
    act = d.get("action") or "不动"
    tone = ("buy" if "加仓" in act else
            "sell" if ("减仓" in act or "降仓" in act) else
            "watch" if "限速" in act else "hold")
    w = d.get("weights") or {}
    nx = d.get("next") or {}
    nw = nx.get("weights") or {}
    ex = d.get("exec") or {}
    tr = d.get("trend") or {}
    vol = d.get("vol")

    rows = []
    for key, label in (("growth", "成长 · 创业板"), ("div", "红利 · 中证红利"), ("cash", "现金 · 货基/短债")):
        cur, nxt = float(w.get(key) or 0), float(nw.get(key) or 0)
        a, txt = _gd_side_act(cur, nxt)
        code = (ex.get(key) or {}).get("code") or "—"
        rows.append(_row(code, label, a, txt,
                         f"{label}目标 {nxt:.0%}" if key != "cash" else f"风险预算腾出 {nxt:.0%}"))

    broken = [n for n, k in (("创业板指", "growth_broken"), ("中证红利", "div_broken")) if tr.get(k)]
    badge = act if act != "不动" else f"在场 {float(w.get('growth') or 0) + float(w.get('div') or 0):.0%}"
    return _entry(
        "gd_v5", "成长/红利轮动 v5 · 水位+趋势+波动目标", d.get("as_of"), badge, tone,
        f"成长 {float(w.get('growth') or 0):.0%} / 红利 {float(w.get('div') or 0):.0%} / "
        f"现金 {float(w.get('cash') or 0):.0%} · 明日 {act}"
        + (f" · {'/'.join(broken)}破 MA200" if broken else ""),
        rows,
        [["R", f"{float(d.get('R') or 0):.4f}"],
         ["缩放系数", f"{float(d.get('scale') or 0):.0%}（目标波动 {float(d.get('target_vol') or 0):.0%}）"],
         ["已实现波动", "—" if vol is None else f"{float(vol):.1%}"],
         ["距上次调仓", f"{nx.get('gap', '—')} 日" + ("（限速中）" if nx.get("throttled") else "")]],
        note="v5 = v3 的 R 水位 + **双侧 MA200 闸门**（破线该侧减半、削掉的进现金）+ "
             "**18% 波动率目标**（120 日已实现波动缩放总仓位，死区 0.15）+ 全局限速 10 日。"
             "全期 +14.23%/-26.74%/夏普 0.88/Calmar 0.53，**家族风险调整最优**；代价是年化比 v3 少 1.4pp、"
             "平均只在场 63%（缩放均值 0.80，仅 3% 交易日满仓）。注意趋势层的贡献会随市场阶段反转："
             "2010~2021 有效（v3 反超 v2 +0.97pp），2021~2026 拖累（v3 落后 v2 6.07pp）。",
        src=_rel(os.path.join(_ROOT, daily["signal_report"])),
        data=f"指数日线（创业板指 399006 ÷ 中证红利 000922）{d.get('as_of')}",
        cols=["执行代码", "资产", "动作", "目标仓位"],
        # 仓位图由 scan.py 每日按当日权重重画，卡片里直接内嵌（缺图时留空，不影响卡片）
        img=daily.get("chart") or "",
        img_cap="三资产仓位（堆叠）+ 创业板指 / 中证红利点位（右轴 1 / 右轴 2）+ 风格比值 R"
                "（下栏，0.35 / 0.65 为切换阈值）· 每日随 scan.py 重画",
    )


def _v17_row():
    """V17 趋势闸门的一行摘要（**不单独出卡**，挂在板块5「技术指标」的 V10 行下面）。

    只保留两件读得懂的事：今天的加减仓标的、以及处于上涨趋势（= 满仓维持）的标的。
    全池 54 只逐只象限在 ``v17_trend_gate/daily/_v17_trend_gate_daily.md/.html``，
    详情不搬进汇总看板 —— 54 行会把 6 策略表压没。
    产物缺失时返回 ``None``（卡片照常出，不因 V17 缺数而失败）。
    """
    meta = ((_DOC.get("strategies") or {}).get("v17_trend_gate") or {})
    rel = ((meta.get("daily") or {}).get("signal_json")
           or "strategies/tech_indicators/v17_trend_gate/daily/_v17_trend_gate_daily.json")
    path = os.path.join(_ROOT, rel.replace("/", os.sep))
    if not os.path.exists(path):
        return None
    try:
        d = json.loads(_read(path))
    except json.JSONDecodeError:
        return None
    etfs = d.get("etfs") or []
    if not etfs:
        return None
    buys = [x for x in etfs if x.get("decide") == "buy"]
    sells = [x for x in etfs if x.get("decide") == "sell"]
    ups = [x for x in etfs if x.get("quad") == "上涨趋势"]
    quads = (d.get("summary") or {}).get("quads") or {}

    def _tag(x):
        return f"{x.get('code')} {x.get('name')}"

    if buys:
        act, label = "buy", f"加 {len(buys)}"
    elif sells:
        act, label = "sell", f"减 {len(sells)}"
    else:
        act, label = "hold", "无动作"
    parts = []
    if buys:
        parts.append("加仓：" + "、".join(_tag(x) for x in buys))
    if sells:
        parts.append("减仓：" + "、".join(_tag(x) for x in sells))
    if not (buys or sells):
        parts.append("今日无调仓")
    tail = f"**处于上涨趋势（满仓维持 · {len(ups)} 只）**：" + ("、".join(_tag(x) for x in ups) or "（无）")
    if quads:
        tail += (f" · 全池 {len(etfs)} 只 = 上涨 {quads.get('上涨趋势', 0)} / 反弹 {quads.get('反弹', 0)} / "
                 f"高位回调 {quads.get('高位回调', 0)} / 下跌 {quads.get('下跌趋势', 0)}")
    parts.append(tail)
    return (_row("V17 闸门", f"加 {len(buys)} · 减 {len(sells)} · 上涨 {len(ups)}", act, label,
                 "；".join(parts)), len(ups))


def parse_tech_indicators(_cfg):
    path = os.path.join(_ROOT, "strategies", "tech_indicators", "daily", "_daily_recommend.md")
    lines = _read(path).splitlines()
    as_of = _first_date(next((l for l in lines if "全池最新交易日" in l), ""))
    detail = _table(_section(lines, "全标的明细"))
    if not detail:
        raise ValueError("markdown 明细表解析为空")

    # 按「策略」聚合：每个指标策略一行，行内列出它自己的买入/卖出标的
    # ★ 策略清单与列宽都从 strategies.json 的 daily.strategies 取（增删策略只改那里，
    #   这里不再硬编码列数 —— 早先写死 cells[6:10] 会静默丢掉第 5 个及以后的策略列）。
    names = [str(x) for x in ((_cfg.get("daily") or {}).get("strategies") or [])]
    if not names:
        raise ValueError("strategies.json 的 daily.strategies 为空")
    ncol = len(names)
    group = {n: {"buy": [], "sell": [], "hold": [], "none": [], "watch": [], "avoid": []}
             for n in names}
    amap = {"买入": "buy", "卖出": "sell", "持有": "hold",
            "观察": "watch", "否决": "avoid", "空仓": "none"}
    pool = 0
    for cells in detail:
        if len(cells) < 6 + ncol:
            continue
        pool += 1
        code, name = cells[0], cells[1]
        for strat, a in zip(names, cells[6:6 + ncol]):
            group[strat][amap.get(a, "none")].append(f"{code} {name}")

    # 行标签：MACD/V3/V6/V7 保持原样，两个状态机变种补上人话后缀（解析仍用原名匹配表头）
    # ⚠ V6/V7 的样本外检验不通过（见各变种报告），标签带 ⚠ 防止与 MACD 等权误读
    disp = {"V6": "V6 打分制 ⚠", "V7": "V7 风格路由 ⚠",
            "V9": "V9 右侧", "V10": "V10 亮灯"}
    # 样本外证据：只写「过不过」，具体数字留在策略报告里，避免看板变成论文
    risk = {"V6": "样本外夏普 0.14，低于 MACD 0.54",
            "V7": "真实样本外 0.41，低于 MACD 0.53"}
    rows = []
    for strat in names:
        g = group[strat]
        if g["buy"]:
            act, label = "buy", f"买入 {len(g['buy'])}"
        elif g["sell"]:
            act, label = "sell", f"卖出 {len(g['sell'])}"
        else:
            act, label = "hold", "无动作"
        parts = []
        if g["buy"]:
            parts.append("买入：" + "、".join(g["buy"]))
        if g["sell"]:
            parts.append("卖出：" + "、".join(g["sell"]))
        if g["watch"]:
            parts.append("观察（差一票）：" + "、".join(g["watch"]))
        if g["avoid"]:
            parts.append("否决：" + "、".join(g["avoid"]))
        if g["hold"] and len(g["hold"]) <= 10:
            parts.append("持有：" + "、".join(g["hold"]))
        if not parts:
            parts.append(f"无买卖信号（持有 {len(g['hold'])} / 空仓 {len(g['none'])}）")
        detail = "；".join(parts)
        if strat in risk:
            detail += f"｜⚠ {risk[strat]}"
        rows.append(_row(disp.get(strat, strat), f"买 {len(g['buy'])} · 卖 {len(g['sell'])} · "
                                f"持 {len(g['hold'])} · 空 {len(g['none'])}", act, label,
                         detail))

    # V17 不单独出卡：只在 V10（最后一行）下面加一行 —— 今日加减仓 + 处于上涨趋势的标的。
    v17 = _v17_row()
    n_up = 0
    if v17:
        rows.append(v17[0])
        n_up = v17[1]

    n_buy = sum(len(group[n]["buy"]) for n in names)
    n_sell = sum(len(group[n]["sell"]) for n in names)
    # 徽标只统计上面 6 个指标策略（V17 是趋势闸门、口径不同，混进来会让「买 N/卖 M」失真）
    active = [r for r in rows if r["act"] in ("buy", "sell") and r["c"] != "V17 闸门"]
    if active:
        badge = f"买 {n_buy} / 卖 {n_sell}"
        tone = "buy" if n_buy else "sell"
        summary = "；".join(f"{r['c']}：{r['a']}" for r in active)
    else:
        badge, tone = "无动作", "none"
        summary = f"{ncol} 策略对全池 {pool} 只均无买入/卖出信号"
    if v17:
        summary += f" · V17 趋势闸门 {v17[0]['n']}"
    # 六列的数据日并不相同：MACD/V3/V6/V7 用当日行情，V9/V10 是状态机型、各自 scan 的落盘日
    head_txt = "\n".join(lines[:8])
    m9 = re.search(r"v9\s*数据日\D*(\d{4}-\d{2}-\d{2})", head_txt, re.I)
    m10 = re.search(r"v10\s*数据日\D*(\d{4}-\d{2}-\d{2})", head_txt, re.I)
    v9d, v10d = (m9.group(1) if m9 else None), (m10.group(1) if m10 else None)
    data = f"ETF 行情 {as_of}"
    if v9d or v10d:
        data += f" · V9 {v9d or '—'} / V10 {v10d or as_of}"
    return _entry(
        "tech_indicators", f"技术指标（{ncol} 策略）", as_of, badge, tone, summary, rows,
        [["全池", str(pool)], ["买入", str(n_buy)], ["卖出", str(n_sell)],
         ["策略", str(ncol)], ["V17 上涨趋势", f"{n_up} / 54"]],
        note="解析自 _daily_recommend.md（该策略只落 markdown）；每行是一个指标策略对全池的操作汇总。"
             "⚠ V6/V7 的样本外检验不通过（WF 夏普 0.14 / 真实 OOS 0.41，均低于 MACD 0.54），"
             "保留在表里只为观察，**不要与 MACD 等权使用**；「N 策略共振」把这两列剔除后才有意义。"
             "最后一行「V17 闸门」是趋势四象限（年线位置 × 年线斜率）的缩影：只列今日加减仓标的"
             "与处于上涨趋势（满仓维持）的标的，54 只逐只象限见 "
             "strategies/tech_indicators/v17_trend_gate/daily/_v17_trend_gate_daily.md —— "
             "它与上面 6 个指标策略口径不同（单标的分档，非 0/1 择时），读数不可直接互比。",
        src=_rel(path),
        data=data,
        cols=["策略", "动作统计", "动作", "该策略的操作明细"],
    )


def parse_v17(_cfg):
    """V17 趋势四象限闸门：54 只场内 ETF 逐只档位（满仓/半仓/观察仓）。

    读 v17 每日扫描的 JSON（结构见 strategies/tech_indicators/v17_trend_gate/daily/_daily_scan.py
    的 build_html/json 产物）：每个 ETF 有 quad / state / level_txt / dev / slope_up /
    below_days / lack_short / decide / action。
    卡片列出全池（all_rows=True，让「上涨趋势 = 满仓维持」的标的也出现，不被「只看有动作」收掉），
    动作列 = 今日调仓（加仓/减仓）或当前目标仓位（满仓/半仓/观察仓）。
    """
    daily = _cfg["daily"]
    json_path = os.path.join(_ROOT, daily["signal_json"])
    if not os.path.exists(json_path):
        raise ValueError("产物缺失：请先跑 strategies/tech_indicators/v17_trend_gate/daily/_daily_scan.py")
    d = json.loads(_read(json_path))
    as_of = d.get("as_of")
    etfs = d.get("etfs") or []
    summ = d.get("summary") or {}
    quads = summ.get("quads") or {}
    n_buy = summ.get("buy", 0)
    n_sell = summ.get("sell", 0)

    quad_order = ["上涨趋势", "反弹", "高位回调", "下跌趋势"]
    rows = []
    for x in sorted(etfs, key=lambda z: (quad_order.index(z["quad"])
                                        if z["quad"] in quad_order else 9, z["name"])):
        decide = x.get("decide")
        if decide == "buy":
            act, label = "buy", "加仓"
        elif decide == "sell":
            act, label = "sell", "减仓"
        else:
            act, label = "none", x.get("level") or "—"
        detail = (f"{x['quad']} · 档位 {x.get('level_txt', '—')} · 距年线 {x['dev']:+.1%} · "
                  f"年线斜率 {'↑' if x.get('slope_up') else '↓'} · "
                  f"连续在年线下 {x.get('below_days', 0)} 日 · 还缺 {x.get('lack_short', '—')}")
        rows.append(_row(x.get("code"), x.get("name"), act, label, detail))

    qtxt = (f"上涨趋势 {quads.get('上涨趋势', 0)} · 反弹 {quads.get('反弹', 0)} · "
            f"高位回调 {quads.get('高位回调', 0)} · 下跌趋势 {quads.get('下跌趋势', 0)}")
    if n_buy or n_sell:
        tone, badge = ("buy" if n_buy else "sell"), f"加 {n_buy} / 减 {n_sell}"
        summary = qtxt + f" · 今日加仓 {n_buy} / 减仓 {n_sell}"
    else:
        tone, badge = "none", "无动作"
        summary = qtxt + " · 今日无调仓"
    return _entry(
        "v17_trend_gate", "技术指标 V17 · 趋势四象限闸门", as_of, badge, tone, summary, rows,
        [["全池", str(len(etfs))],
         ["上涨趋势", str(quads.get("上涨趋势", 0))],
         ["反弹", str(quads.get("反弹", 0))],
         ["高位回调", str(quads.get("高位回调", 0))],
         ["下跌趋势", str(quads.get("下跌趋势", 0))],
         ["今日加仓", str(n_buy)], ["今日减仓", str(n_sell)]],
        note="V17 · 趋势四象限闸门（年线位置 × 年线斜率 → 满仓/半仓/观察仓）：close 在 MA250 上/下 × "
             "MA250 近 20 日朝上/朝下，两个都朝上才是上涨趋势（满仓），其余三格都不给加仓许可。"
             "规则从 vol_legs/v5_trend_gate 照搬、数据源换成家族 54 只场内 ETF 收盘价。"
             "它是单标的分档（非 0/1 择时），压低各标的仓位、换回撤，不保证增收益；"
             "滞后是设计特性（年线确认进入下跌趋势时价格通常已跌掉两位数）。"
             "明细见 strategies/tech_indicators/v17_trend_gate/daily/_v17_trend_gate_daily.html。",
        src=_rel(os.path.join(_ROOT, daily["signal_report"])),
        cols=["代码", "名称", "动作 / 仓位", "象限 / 档位 / 距年线 / 斜率 / 还缺什么"],
        all_rows=True,
    )


def parse_v1_1_ma20(_cfg):
    base_dir = os.path.join(_ROOT, "strategies", "vol_legs", "v1_price_volume", "v1_1_ma20", "daily")
    json_path = os.path.join(base_dir, "_v1_1_ma20_daily.json")
    md_path = os.path.join(base_dir, "_v1_1_ma20_daily.md")
    if not os.path.exists(json_path):
        raise ValueError("产物缺失：请先跑 run_daily_vol_legs.ps1（第 2 步）")
    data = json.loads(_read(json_path))
    legs = data.get("legs") or []
    if not legs:
        raise ValueError("产物无腿记录")
    summ = data.get("summary") or {}
    rule = data.get("rule") or {}
    tone_map = {"买入": "buy", "卖出": "sell", "持有": "hold", "空仓": "none"}
    buys = [x for x in legs if x.get("action") == "买入"]
    sells = [x for x in legs if x.get("action") == "卖出"]
    stale = [x for x in legs if str(x.get("fresh") or "").startswith(("⚠️", "❌"))]

    rows = []
    for x in legs:
        act = x.get("action") or "空仓"
        pos = "持仓" if x.get("pos") else "空仓"
        detail = "距MA20 {:+.2%} · 复权净值 {:.4f} · MA20 {:.4f} · 当前{} · 带 {:g}pp · {}".format(
            float(x.get("dist") or 0), float(x.get("nav") or 0), float(x.get("ma20") or 0),
            pos, float(x.get("band") or 0), x.get("fresh") or "")
        rows.append(_row(x.get("exec"), x.get("name"), tone_map.get(act, "none"), act, detail))

    def _txt(items):
        return "、".join(f"{x.get('name', '')}(`{x.get('exec', '')}`)" for x in items)

    if buys or sells:
        parts = []
        if buys:
            parts.append(f"买入 {_txt(buys)}")
        if sells:
            parts.append(f"卖出 {_txt(sells)}")
        summary = " · ".join(parts)
        badge = f"买 {len(buys)} 卖 {len(sells)}"
        tone = "buy" if buys else "sell"
    else:
        summary = "七腿无穿越信号 —— 维持原仓位（" + _txt(legs) + "）"
        badge, tone = "无动作", "none"

    return _entry(
        "v1_1_ma20", "波动资产 V1 量价家族 · 子策略 V1-1（七腿 MA20 穿越）", data.get("as_of"), badge, tone,
        summary, rows,
        [["七腿", str(summ.get("n", len(legs)))], ["买入", str(len(buys))],
         ["卖出", str(len(sells))], ["持有", str(summ.get("hold", 0))],
         ["空仓", str(summ.get("empty", 0))], ["陈旧/失败", str(len(stale))]],
        note=f"池 = 主策略七腿（`rule.LEGS`，增删腿只改那一处）；"
             f"场外复权净值 {rule.get('buy', '上穿MA20')} 买 / {rule.get('sell', '下穿MA20')} 卖，"
             f"{rule.get('trade_at', 'T+1 净值')} 成交。**成本是决定性变量**：逐腿 13~16 笔/年，"
             "按 A 类真实费率扣费后组合年化 8.10% → 1.42%（C 类销售服务费未计）。"
             "本变种只管逐腿进出时点，组合层仓位带仍由主策略管。",
        src=_rel(md_path),
        cols=["场外代码", "腿", "动作", "关键值"],
    )


def parse_v3_macd_boll(_cfg):
    """V3 变种（MACD + 布林自适应窗口）：逐腿读数 = DIF/DEA + ER 窗口 + 到触发位的距离。"""
    base_dir = os.path.join(_ROOT, "strategies", "vol_legs", "v3_macd_boll", "daily")
    json_path = os.path.join(base_dir, "_v3_macd_boll_daily.json")
    md_path = os.path.join(base_dir, "_v3_macd_boll_daily.md")
    if not os.path.exists(json_path):
        raise ValueError("产物缺失：请先跑 run_daily_vol_legs.ps1（第 2 步）")
    data = json.loads(_read(json_path))
    legs = data.get("legs") or []
    if not legs:
        raise ValueError("产物无腿记录")
    summ = data.get("summary") or {}
    rule = data.get("rule") or {}
    tone_map = {"买入": "buy", "卖出": "sell", "持有": "hold", "空仓": "none"}
    buys = [x for x in legs if x.get("action") == "买入"]
    sells = [x for x in legs if x.get("action") == "卖出"]
    stale = [x for x in legs if str(x.get("fresh") or "").startswith(("⚠️", "❌"))]

    rows = []
    for x in legs:
        act = x.get("action") or "空仓"
        pos = "持仓" if x.get("pos") else "空仓"
        detail = ("DIF {:+.4f} / DEA {:+.4f} · ER {:.2f}（{}）· {} {:+.2%} · "
                  "当前{} · 带 {:g}pp · {}").format(
            float(x.get("dif") or 0), float(x.get("dea") or 0), float(x.get("er") or 0),
            x.get("window") or "—", x.get("dist_kind") or "距上轨", float(x.get("dist") or 0),
            pos, float(x.get("band") or 0), x.get("fresh") or "")
        rows.append(_row(x.get("exec"), x.get("name"), tone_map.get(act, "none"), act, detail))

    def _txt(items):
        return "、".join(f"{x.get('name', '')}(`{x.get('exec', '')}`)" for x in items)

    if buys or sells:
        parts = []
        if buys:
            parts.append(f"买入 {_txt(buys)}")
        if sells:
            parts.append(f"卖出 {_txt(sells)}")
        summary = " · ".join(parts)
        badge = f"买 {len(buys)} 卖 {len(sells)}"
        tone = "buy" if buys else "sell"
    else:
        summary = "七腿均无突破/离场信号 —— 维持原仓位（" + _txt(legs) + "）"
        badge, tone = "无动作", "none"

    return _entry(
        "v3_macd_boll", "波动资产 V3 变种 · MACD+BOLL 自适应窗口", data.get("as_of"), badge, tone,
        summary, rows,
        [["七腿", str(summ.get("n", len(legs)))], ["买入", str(len(buys))],
         ["卖出", str(len(sells))], ["持有", str(summ.get("hold", 0))],
         ["空仓", str(summ.get("empty", 0))], ["陈旧/失败", str(len(stale))]],
        note=f"池 = 主策略七腿（`rule.LEGS`）。入场 {rule.get('buy', 'DIF>0 且 DIF>DEA + 开口 + 上穿上轨')}，"
             f"离场 {rule.get('sell', '死叉 或 跌破中轨')}，布林窗口按 ER(20) 滞回 "
             f"{rule.get('window_fast')}↔{rule.get('window_slow')}，{rule.get('trade_at', 'T+1 净值')} 成交。"
             "**突破追涨型**，与主策略「跌了才加仓」的阶梯方向相反，不可叠加在同一份额度上；"
             "年笔数 2~4/年、曝光 12%~19%，扣费后组合年化 5.55%→3.74%。",
        src=_rel(md_path),
        cols=["场外代码", "腿", "动作", "关键值"],
    )


def parse_permanent(_cfg):
    """永久投资组合：8 项目标权重（基准 × ±50% 倾斜）。

    json 只给权重/得分（键是 bond/divlv/... 无中文名），名称、场外代码、允许区间
    只在自产 markdown 的「当前最佳百分比」表里 —— 所以两者合起来读。
    """
    json_path = os.path.join(_ROOT, "strategies", "permanent", "daily", "_permanent_signal.json")
    md_path = os.path.join(_ROOT, "strategies", "permanent", "daily", "_permanent_report.md")
    d = json.loads(_read(json_path))
    table = _table(_section(_read(md_path).splitlines(), "当前最佳百分比"))
    if not table:
        raise ValueError("markdown 最佳百分比表解析为空")
    as_of = d.get("as_of") or _first_date(d.get("generated"))

    rows, n_buy, n_sell, worst = [], 0, 0, None
    for cells in table:
        if len(cells) < 9:
            continue
        name = cells[1]
        code = cells[2].replace("`", "").replace(" ", "") or "—"
        b, w, diff = _num(cells[3]), _num(cells[4]), _num(cells[6])
        k, sc = _num(cells[7]), _num(cells[8])
        if b is None or w is None:
            continue
        if diff is None:
            act, label = "hold", f"持有 {w:.1f}%"
        elif diff >= 0.5:
            act, label = "buy", f"加至 {w:.1f}%"
            n_buy += 1
        elif diff <= -0.5:
            act, label = "sell", f"减至 {w:.1f}%"
            n_sell += 1
            if worst is None or diff < worst[1]:
                worst = (name, diff)
        else:
            act, label = "hold", f"维持 {w:.1f}%"
        detail = f"基准 {b:.1f}%"
        if diff is not None:
            detail += f" · 偏离 {diff:+.1f}pp"
        if k is not None:
            detail += f" · ×{k:.2f}"
        if sc is not None:
            detail += f" · 得分 {sc:+.2f}"
        rows.append(_row(name, code, act, label, detail))

    short = {"bond": "债", "divlv": "红利", "ndx": "纳指", "spx": "标普",
             "gold": "黄金", "cash": "现金", "a500": "A500", "kc": "科创"}
    wtxt = " / ".join(f"{short.get(k, k)} {float(v):.1f}%"
                      for k, v in (d.get("weights") or {}).items())
    # 各腿净值截止日差很多（QDII 滞后、债券中长端走场外披露更慢）—— 卡片上逐个最旧源点名
    da = {str(k): str(v) for k, v in (d.get("data_asof") or {}).items() if v}
    data = ""
    if da:
        lo, hi = min(da.values()), max(da.values())
        nm = {"bond_s": "短债", "bond_m": "中债", "bond_l": "长债", "divlv": "红利低波",
              "ndx": "纳指", "spx": "标普", "gold": "黄金", "a500": "A500", "kc": "科创"}
        old = "/".join(nm.get(k, k) for k, v in da.items() if v == lo)
        data = (f"净值 {hi}" if lo == hi
                else f"净值 {lo} ~ {hi}（最旧 {old} {lo}）")
    return _entry(
        "permanent", "永久组合", as_of, f"加 {n_buy} / 减 {n_sell}", "watch",
        f"8 项基准 ±50% 倾斜：加 {n_buy} 项、减 {n_sell} 项 · 目标 {wtxt}",
        rows,
        [["加仓项", str(n_buy)], ["减仓项", str(n_sell)],
         ["最大减仓", f"{worst[0]} {worst[1]:+.1f}pp" if worst else "—"],
         ["净值截止", as_of or "—"]],
        note="这是**目标权重**不是当日买卖：单项偏离基准 >5pp 才动，半年检查一次。"
             "信号用累计净值(totvalue, 分红再投复权)，QDII(270042/017641) 净值滞后 1-2 日。",
        src=_rel(md_path),
        data=data,
        cols=["资产", "场外代码", "建议", "基准/偏离/倍数/得分"],
    )


def parse_vol_legs(_cfg):
    """波动资产操作规则：7 腿(仓位带) + 阶梯状态。

    产物 `_vol_legs_signal.json` 已按腿组织(legs/actions/ladder/rebalance)，直接映射成表格行；
    动作 = 次日收盘待执行的挂单(§0 收盘后计算、次日执行)，空动作表示「不动」。
    """
    base = os.path.join(_ROOT, "strategies", "vol_legs", "daily")
    json_path = os.path.join(base, "_vol_legs_signal.json")
    md_path = os.path.join(base, "_vol_legs_report.md")
    if not os.path.exists(json_path):
        raise ValueError("产物缺失：请先跑 run_daily_vol_legs.ps1")
    d = json.loads(_read(json_path))
    legs = d.get("legs") or {}
    if not legs:
        raise ValueError("产物无腿记录")

    act_map = {"buy": "buy", "sell": "sell", "hold": "hold", "none": "none"}
    rows = []
    for key, x in legs.items():
        dd = x.get("dd")
        detail = "MA250 偏离 {:+.1%} · 回撤 {} · RSI14 {:.1f} · {} · 仓位 {:.1f}/{:.0f}pp".format(
            float(x.get("dev") or 0),
            "—" if dd is None else f"{-dd:.1%}",
            float(x.get("rsi") or 0), x.get("zone") or "—",
            float(x.get("pos_pp") or 0), float(x.get("band") or 0))
        if x.get("action") in ("buy", "sell"):
            detail = f"{x.get('rule', '')} · {x.get('detail', '')} | " + detail
        rows.append(_row(x.get("exec"), x.get("name"),
                         act_map.get(x.get("action") or "none", "none"),
                         x.get("action_label") or "", detail))

    # 逐腿净值日（QDII 纳指/标普滞后 1~2 日, 与境内腿不是同一天）—— 卡片上点名最旧的腿
    das = {k: str(x.get("as_of")) for k, x in legs.items() if x.get("as_of")}
    data = ""
    if das:
        lo, hi = min(das.values()), max(das.values())
        old = "/".join(str(legs[k].get("name") or k) for k, v in das.items() if v == lo)
        data = (f"净值 {hi}" if lo == hi else f"净值 {lo} ~ {hi}（最旧 {old} {lo}）")

    acts = d.get("actions") or []
    buys = [a for a in acts if a.get("kind") == "buy"]
    sells = [a for a in acts if a.get("kind") == "sell"]
    if acts:
        badge = f"买 {len(buys)} / 卖 {len(sells)}"
        tone = "buy" if buys else "sell"
        summary = "；".join(f"{a.get('label')} {a.get('name')}" for a in acts)
        summary += f" · 投入 {d.get('invested_pp')}/{d.get('sleeve_pp')}pp"
    else:
        badge, tone = "无动作", "none"
        summary = (f"七腿均未触发阈值 · 投入 {d.get('invested_pp'):g}/{d.get('sleeve_pp'):g}pp · "
                   f"现金 {d.get('cash_pp'):g}pp")

    lad = d.get("ladder") or {}
    a5 = lad.get("a500") or {}
    h3 = lad.get("hs300") or {}
    gd = lad.get("gold") or {}
    reb = d.get("rebalance") or {}
    val = d.get("valuation") or {}
    chips = [["投入", f"{d.get('invested_pp'):g}/{d.get('sleeve_pp'):g}pp"],
             ["A500 阶梯", f"{float(a5.get('units') or 0):.2f} 份"],
             ["沪深300 阶梯", f"{float(h3.get('units') or 0):.2f} 份"],
             ["黄金阶梯", f"{float(gd.get('units') or 0):.2f} 份"],
             ["再平衡核对", str(reb.get("next_check") or "—")],
             ["ERP 闸门", "null" if not val.get("a500_erp_gate") else str(val.get("a500_erp_gate"))],
             ["沪深300 ERP", str(val.get("hs300_erp_gate") or "null")],
             ["美股闸门", str(val.get("ndx_erp_gate") or "null")]]
    qdii = val.get("qdii_limit_closed") or []
    return _entry(
        "vol_legs", "波动资产(A500/沪深300/纳指/标普/科创/恒科/黄金)", d.get("as_of"),
        badge, tone, summary, rows, chips,
        note="§0 口径：收盘后计算、次日下单（场外 15:00 前提交才拿当日净值）—— 这里显示的是"
             "**下一交易日**该成交的那一笔。"
             f"七腿仓位带共 {d.get('sleeve_pp'):g}pp（中证A500 2.5 / 沪深300 2.5 / 纳指100 10 / "
             "标普500 10 / 科创50 2.5 / 恒生科技 2.5 / 黄金 10），1 份 = 带的 20%"
             "（A500·沪深300 0.5pp、美股/黄金 2pp）。"
             "**信号与记账口径均为场外基金复权净值**（含汇率与分红再投，不含场内溢价）；"
             "估值闸门为 null（或美股人工分位）时按 §1「规则不执行」处理"
             + (f"；当前 QDII 额度关闭：{'/'.join(qdii)}（档位顺延）" if qdii else "")
             + "。详见报告第五、六节。",
        src=_rel(md_path),
        data=data,
        cols=["场外代码", "资产", "动作", "关键值"],
    )


# ---------------------------------------------------------------- 波动类变种汇总
# 参考板块5「技术指标」的列法：一行一个策略，列为 [策略 / 动作统计 / 动作 / 明细]。
# 板块2 的 v2/v3/v4/v5 各自有逐腿产物，这里合成一张「变种操作一览」卡，
# 并把每个变种的**重点标注标的**用 ★ + 加粗突出（即使当天空仓也列出，便于一眼看到重点腿状态）；
# 非重点的其余腿也一并列出各自的仓位状态，标注「仅供参考」。
_VOL_VARIANTS = [
    ("v2", "V2 BOLL突破",
     "strategies/vol_legs/v2_boll_breakout/daily/_v2_boll_daily.json", {"a500", "hs300"}),
    ("v3", "V3 MACD+BOLL",
     "strategies/vol_legs/v3_macd_boll/daily/_v3_macd_boll_daily.json", {"kc50", "hkt"}),
    ("v4", "V4 ERP股债通道",
     "strategies/vol_legs/v4_erp_band/daily/_v4_erp_band_daily.json", {"hs300"}),
    ("v5", "V5 趋势闸门",
     "strategies/vol_legs/v5_trend_gate/daily/_v5_trend_gate_daily.json", {"gold"}),
    ("v9", "V9 波动率目标仓位",
     "strategies/vol_legs/v9_vol_target/daily/_v9_vol_target_daily.json", {"spx", "ndx"}),
]

# 板块2 的图墙：`run_daily_vol_legs.ps1` 第 3 步每天重画的八张单腿/操作点图。
# 每张图下面标注**是哪个标的的图**（代码 + 场外复权口径），改图名时这里跟着改。
_VOL_FIGS = [
    ("strategies/vol_legs/v2_boll_breakout/research/_v2_a500.png",
     "**中证A500**（`023299` 场外复权）· V2 BOLL(35,2) 上轨突破择时 vs 买入持有"
     "（只画 2025-03-27 成立后的真实净值）"),
    ("strategies/vol_legs/v2_boll_breakout/research/_v2_hs300.png",
     "**沪深300**（`000051` 场外复权）· V2 BOLL(35,2) 上轨突破择时 vs 买入持有"),
    ("strategies/vol_legs/v3_macd_boll/research/_v3_hkt.png",
     "**恒生科技**（`012805` 场外复权）· V3 MACD+BOLL 择时 vs 买入持有"),
    ("strategies/vol_legs/v3_macd_boll/research/_v3_kc50.png",
     "**科创50**（`011609` 场外复权）· V3 MACD+BOLL 择时 vs 买入持有"),
    ("strategies/vol_legs/v4_erp_band/backtest/_v4_erp_band_signals.png",
     "**沪深300**（`000051`）× 10Y 国债 · V4 ERP 股债利差两带滞回的操作点"
     "（▲加仓 / ▼减仓，T 日信号 → T+1 收盘成交）"),
    ("strategies/vol_legs/v5_trend_gate/research/plot_trend/_v5_trend_gold.png",
     "**黄金**（`000216` 场外复权）· V5 趋势四象限闸门全历史 2014-10 起（真实操作点 + 档位阶梯）"),
    ("strategies/vol_legs/v9_vol_target/research/_v9_vt_spx.png",
     "**标普500**（`017641` 场外复权，信号 `.INX` 美元指数）· V9 波动率目标仓位 TV15%"
     "（底色=vol20 高于/低于目标，▲▼=成交点）"),
    ("strategies/vol_legs/v9_vol_target/research/_v9_vt_ndx.png",
     "**纳指100**（`270042` 场外复权，信号 `.NDX` 美元指数）· V9 波动率目标仓位 TV15%"
     "（底色=vol20 高于/低于目标，▲▼=成交点）"),
]


def _vol_classify(x):
    """逐腿动作归一：买入 / 卖出 / 持有 / 空仓（兼容 v5 的 '维持' + state 档位）。"""
    t = str(x.get("action") or "")
    if "买" in t or "加仓" in t:
        return "买入"
    if "卖" in t or "减仓" in t or "清" in t:
        return "卖出"
    # v9 的动作列写的是「→ 满仓（快减）」，不含买卖字样 —— 待成交决策靠 pending vs state 判向
    if x.get("pending") is not None:
        try:
            return "买入" if float(x["pending"]) > float(x.get("state") or 0) else "卖出"
        except (TypeError, ValueError):
            pass
    pos = x.get("pos")
    if pos is None:
        pos = x.get("state")
    try:
        pos = float(pos or 0)
    except (TypeError, ValueError):
        pos = 0.0
    return "持有" if pos > 1e-9 else "空仓"


def _vol_level(x):
    lvl = x.get("level_txt") or x.get("level")
    if not lvl:
        return ""
    # v9 有「待成交」决策时（月末定仓 / 月中快减快补），把目标仓位一起写出来：
    # 「（66% → 48%）」比只写当前仓位「（66%）」更接近「今天要动多少」。
    p = x.get("pending")
    if p is not None:
        try:
            return f"（{lvl} → {float(p):.0%}）"
        except (TypeError, ValueError):
            pass
    return f"（{lvl}）"


def _vol_variant_row(label, d, emph):
    """七腿变种（v2/v3/v5）-> 一行。"""
    legs = d.get("legs") or []
    by = {"买入": [], "卖出": [], "持有": [], "空仓": []}
    for x in legs:
        by[_vol_classify(x)].append(x)

    emph_parts = [f"**{x.get('name')}** {_vol_classify(x)}{_vol_level(x)}"
                  for x in legs if x.get("leg") in emph]
    detail = "★重点：" + ("、".join(emph_parts) or "—")

    # 其余腿也带上各自的仓位状态（v2/v3 空仓·持有、v5 观察仓等档位），仅供参考 ——
    # 用户要看的是「今天这些腿各是什么位置」，只列名字看不出仓位（2026-11-05 提出）。
    # 写成「名称 状态（档位）」，与 ★ 重点腿同一副样子，读起来对得上。
    rest = [f"{x.get('name')} {_vol_classify(x)}{_vol_level(x)}"
            for cls in ("买入", "卖出", "持有", "空仓") for x in by[cls]
            if x.get("leg") not in emph]
    if rest:
        detail += " ｜ 其余（仅供参考）：" + "、".join(rest)

    nb, ns = len(by["买入"]), len(by["卖出"])
    if nb:
        act, alabel = "buy", f"买入 {nb}"
    elif ns:
        act, alabel = "sell", f"卖出 {ns}"
    else:
        act, alabel = "hold", "无动作"
    stats = f"买{nb}·卖{ns}·持{len(by['持有'])}·空{len(by['空仓'])}"
    return _row(label, stats, act, alabel, detail), act


def _vol_v4_row(label, d):
    """V4 是沪深300 单标的股债通道，没有 A500 腿 —— 只摆当前档位 + 下一跨档。

    **读 ``target_equity`` 而不是 ``plan_weight``**：规则是「带内不动、跨带才执行该带的计划仓位」
    （``ladder_mode=hold_left``）。z=1.07 落在满仓带里，但实际持仓可以是 0% —— 只有从清仓带
    升上来的那一刻才补到 100%。早先只读 plan_weight，卡片写成「满仓（目标 100%）」，
    与报告里「实际目标仓位 0%」自相矛盾（2026-11-04 用户指出）。
    """
    rd = d.get("readings") or {}
    act_d = d.get("action") or {}
    band_name = rd.get("band_name") or "—"
    plan = float(rd.get("plan_weight") or 0)
    tgt = rd.get("target_equity")
    tgt = plan if tgt is None else float(tgt)
    cur = act_d.get("current_equity")
    cur = tgt if cur is None else float(cur)
    delta = float(act_d.get("delta_amount") or 0)
    erp, z = rd.get("erp"), rd.get("z")
    # 当日是否真的要下单：只有跨带日 delta 才非零，其余日子「带内不动」
    if delta > 0:
        act, act_txt = "buy", f"加至 {tgt:.0%}"
    elif delta < 0:
        act, act_txt = "sell", f"减至 {tgt:.0%}"
    else:
        act, act_txt = "hold", f"持有 {tgt:.0%}"
    detail = (f"★**沪深300** 实际目标 **{tgt:.0%}**（当前 {cur:.0%}）· 所在带「{band_name}」的"
              f"计划仓位 {plan:.0%}，**离开本带才执行**（带内不动）")
    if erp is not None and z is not None:
        detail += f" · ERP {float(erp):.2f} / z {float(z):.2f}"
    # 带内趋势提前（v5 MA250+20）：B0 读到上涨就买满、B2 读到下跌就清仓
    tq = rd.get("trend_q")
    if tq and tq != "—":
        detail += f" · 趋势 {tq}"
        if rd.get("intra_events"):
            detail += f"（带内提前 {int(rd['intra_events'])} 次）"
    # 锚健康度（只观测）：z 是「相对 250 日均值」的偏离，锚会漂；H 低 = z 退化成短期动量
    hlt = rd.get("health")
    if hlt is not None:
        detail += f" / 锚健康度 H {float(hlt):.2f}"
        if rd.get("dislocated"):
            detail += "（**脱钩**，贴线误判风险高）"
    nxt = act_d.get("next_cross") or []
    if nxt:
        detail += " ｜ 下一跨档：" + str(nxt[0])
    detail += "（v4 仅沪深300 单标的方法论，无 A500 腿）"
    stats = f"沪深300 单标的 · 实际 {tgt:.0%}"
    return _row(label, stats, act, act_txt, detail), act


def parse_vol_variants(_cfg):
    """板块2 的变种操作一览：v2 / v3 / v4 / v5 / v9（一行一个变种，★=重点标的）。"""
    rows, srcs, as_ofs = [], [], []
    n_buy = n_sell = 0
    dt, v9dt = {}, ""          # 各变种产物日；v9 另记「信号日 / QDII 净值日」两个
    for vx, label, rel, emph in _VOL_VARIANTS:
        full = os.path.join(_ROOT, rel)
        srcs.append(_rel(full))
        if not os.path.exists(full):
            rows.append(_row(label, "产物缺失", "none", "—",
                             f"未找到 `{rel}`，请先跑该变种 daily 扫描"))
            continue
        d = json.loads(_read(full))
        if d.get("as_of"):
            as_ofs.append(str(d["as_of"]))
            dt[vx] = str(d["as_of"])
        if vx == "v9":
            sg = [str(x["sig_date"]) for x in d.get("legs") or [] if x.get("sig_date")]
            nv = [str(x["nav_date"]) for x in d.get("legs") or [] if x.get("nav_date")]
            if sg or nv:
                v9dt = (f"信号 {max(sg)} / 净值 {max(nv)}" if sg and nv
                        else f"信号 {(sg or nv)[-1]}")
        try:
            if vx == "v4":
                row, act = _vol_v4_row(label, d)
            else:
                row, act = _vol_variant_row(label, d, emph)
        except Exception as exc:  # noqa: BLE001
            row = _row(label, "解析失败", "none", "—", f"{type(exc).__name__}: {exc}")
            act = "none"
        rows.append(row)
        if act == "buy":
            n_buy += 1
        elif act == "sell":
            n_sell += 1

    as_of = max(as_ofs) if as_ofs else None
    # 数据时点：v2/v3/v4/v5 各取自己产物的数据日；v9 另标「信号日 / QDII 净值日」——
    # 它的信号走美元指数（当日收盘就有），执行净值滞后一天，只写一个日期会看不懂。
    others = {k: v for k, v in dt.items() if k != "v9"}
    vals = sorted({v for v in others.values() if v})
    data = ""
    if vals:
        data = (f"v2/v3/v4/v5 数据 {vals[0]}" if len(vals) == 1
                else " · ".join(f"{k} {v}" for k, v in others.items() if v))
    if v9dt:
        data = (data + " · " if data else "") + f"v9 {v9dt}"
    tone = "buy" if n_buy else ("sell" if n_sell else "none")
    summary = "v2/v3/v4/v5/v9 今日操作（★=重点标的）：" + "；".join(
        f"{r['c']} {r['a']}" for r in rows)
    # 图墙：只收「文件确实存在」的图，缺图不至于在看板上留破图框。
    figs = [{"path": rel, "cap": cap} for rel, cap in _VOL_FIGS
            if os.path.exists(os.path.join(_ROOT, rel.replace("/", os.sep)))]
    return _entry(
        "vol_variants", "波动类变种操作一览 · v2 / v3 / v4 / v5 / v9", as_of,
        f"买 {n_buy} / 卖 {n_sell}", tone, summary, rows,
        [["变种", str(len(rows))],
         ["重点腿", "A500/沪深300 · 科创50/恒生科技 · 沪深300 · 黄金 · 标普500/纳指100"],
         ["买入", str(n_buy)], ["卖出", str(n_sell)]],
        note="参考板块5「技术指标」的列法（一行一个策略，列=策略/动作统计/动作/明细）。"
             "★ = 该变种的**重点标注标的**：v2→中证A500/沪深300；v3→科创50/恒生科技；"
             "v4→沪深300；v5→黄金；v9→标普500/纳指100。v2/v3/v5 为七腿逐腿择时（突破追涨型），"
             "v4 为沪深300×国债 ERP 通道（单标的，无 A500 腿）；"
             "v9 为**美股两腿（纳指100/标普500）波动率目标仓位**（月末定仓 + 月中快减/快补），"
             "括号里是该腿的带内仓位（如 66% / 满仓）；有**待成交决策**时写成「当前 → 目标」"
             "（如 66% → 48%），不是 0/1。"
             "注意 v4 的动作列写的是**实际目标仓位**（带内不动、跨带才执行该带的计划仓位），"
             "所以「所在带=满仓」时持仓仍可能是 0%。"
             "v4 明细里的「锚健康度 H」= 1 − |z − ERP 1250 日分位|，只作解释、不改变仓位。"
             "数据取自各变种 daily/ 产物。"
             "「其余（仅供参考）」= 非重点标注的腿，也标出各自的仓位状态"
             "（买入/卖出/持有/空仓，v5/v9 另带档位/带内仓位），仅作参考，不作为当日操作。"
             f"表下 {len(figs)} 张图 = 各变种重点标的的单腿图/操作点图，由"
             " `run_daily_vol_legs.ps1` 第 3 步每日重画（图注写的是标的，不是变种）。",
        src=" · ".join(srcs),
        data=data,
        cols=["变种", "动作统计", "动作", "操作明细（★=重点标的，其余仅供参考）"],
        all_rows=True,
        imgs=figs,
    )


PARSERS = {
    "dividend": parse_dividend,
    "vol_variants": parse_vol_variants,
    "dividend_v9": parse_dividend_v9,
    "growth_div_rotation": parse_growth_div,
    "gd_v1": parse_gd_v1,
    "gd_v2": parse_gd_v2,
    "gd_v5": parse_gd_v5,
    "tech_indicators": parse_tech_indicators,
    "v17_trend_gate": parse_v17,
    "v1_1_ma20": parse_v1_1_ma20,
    "v3_macd_boll": parse_v3_macd_boll,
    "permanent": parse_permanent,
    "vol_legs": parse_vol_legs,
}


# ---------------------------------------------------------------- 归档
def collect():
    global _DOC
    doc = json.loads(_read(CFG))
    _DOC = doc
    cfg = doc["strategies"]
    # 按板块顺序解析（板块内保持 json 注册顺序），看板与归档的卡片顺序因此稳定
    blocks = _blocks(doc)
    order = [k for b in blocks for k in b["keys"]]
    order += [k for k in cfg if k not in order]
    out = {}
    for key in order:
        meta = cfg[key]
        name = meta.get("name", key).split("（")[0]
        if (meta.get("daily") or {}).get("enabled") is False:
            print(f"[digest] {key:22s} 已停用(daily.enabled=false)，跳过")
            continue
        if (meta.get("daily") or {}).get("card") is False:
            print(f"[digest] {key:22s} 不单独出卡(daily.card=false)，数据由所属卡片内嵌展示")
            continue
        fn = PARSERS.get(key)
        if fn is None:
            print(f"[digest] {key:22s} 无解析器（未注册 PARSERS），卡片将显示「未支持」")
            out[key] = _entry(key, name, None, "未支持", "none",
                              "该策略尚无解析器（新增策略时补 PARSERS）", [],
                              error="no parser")
            continue
        try:
            ent = fn(meta)
            ent["name"] = ent.get("name") or name
            out[key] = ent
            print(f"[digest] {key:22s} {ent['as_of']}  {ent['badge']:12s} rows={len(ent['rows'])}")
        except Exception as exc:  # noqa: BLE001 - 单个策略失败不阻断汇总
            print(f"[digest] {key:22s} 解析失败: {type(exc).__name__}: {exc}")
            out[key] = _entry(key, name, None, "解析失败", "none",
                              f"{type(exc).__name__}: {exc}", [], error=str(exc))
    return out


def archive(entries, day):
    db = {"schema": 1, "runs": {}}
    if os.path.exists(DB):
        try:
            db = json.loads(_read(DB))
        except json.JSONDecodeError:
            print("[digest] 历史库损坏，重建")
    db.setdefault("schema", 1)
    db.setdefault("runs", {})
    db["runs"][day] = {"run_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "strategies": entries}
    db["runs"] = dict(sorted(db["runs"].items()))
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    with open(DB, "w", encoding="utf-8") as fh:
        json.dump(db, fh, ensure_ascii=False, indent=1)
    print(f"[digest] 归档 {day} -> {_rel(DB)}（历史 {len(db['runs'])} 天）")
    return db


def _img_assets(runs):
    """把卡片声明的产物图内嵌成 data URI（只进 HTML，历史库仍只记路径）。

    IDE 预览 / 内嵌浏览器打开 ``data/_daily_digest.html`` 时，页面的 base 路径常常不是
    仓库根，``../strategies/...`` 会 404，页面显示破图；内嵌后单文件自包含。
    按路径去重，同一张图只嵌一次，历史多天不会把 HTML 撑成多份大小。
    """
    assets = {}
    os.makedirs(_IMG_DIR, exist_ok=True)
    for run in runs.values():
        for key, ent in (run.get("strategies") or {}).items():
            ent = ent or {}
            rels = [ent.get("img") or ""] + [x.get("path") or "" for x in (ent.get("imgs") or [])]
            for rel in rels:
                if not rel or rel in assets:
                    continue
                full = os.path.join(_ROOT, rel.replace("/", os.sep))
                if not os.path.exists(full):
                    print(f"[digest] 卡片图缺失，退回路径引用: {rel}")
                    continue
                with open(full, "rb") as fh:
                    blob = fh.read()
                mime = mimetypes.guess_type(full)[0] or "image/png"
                assets[rel] = "data:%s;base64,%s" % (mime, base64.b64encode(blob).decode("ascii"))
                # 同目录副本：有些预览器只 serve 看板所在目录（不许 ../ 越界），或对超长
                # data URI 直接拒绝；卡片 onerror 会依次退到这份副本、再退到原产物路径。
                dst = os.path.join(_IMG_DIR, "%s__%s" % (key, os.path.basename(full)))
                if not os.path.exists(dst) or os.path.getsize(dst) != len(blob):
                    with open(dst, "wb") as fh:
                        fh.write(blob)
                print(f"[digest] 内嵌卡片图 {rel}（{len(assets[rel]) / 1024:.0f} KB base64）"
                      f" + 副本 {_rel(dst)}")
    return assets


def render(db):
    dates = sorted(db.get("runs") or {})
    # 板块定义每次渲染都从 strategies.json 读：老归档（entry 里没有板块字段）也能按
    # 当前归属重新分节，不必重建历史库。
    doc = json.loads(_read(CFG))
    blocks = _blocks(doc)
    # 停用策略连历史归档也不渲染：看板是「当前每日操作」的视图，翻历史日期时看到
    # 早就撤掉的卡（如板块3 的 v2）只会让人以为它还在流程里。原始记录仍留在 json 里。
    disabled = {k for k, m in doc["strategies"].items() if not _card_on(m)}
    runs = {}
    for d in dates:
        run = db["runs"][d]
        runs[d] = {"run_at": run.get("run_at"),
                   "strategies": {k: v for k, v in (run.get("strategies") or {}).items()
                                  if k not in disabled}}
    payload = {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "dates": dates,
               "blocks": blocks,
               # HTML 在 data/ 下，卡片里的图优先用内嵌 data URI，其次退回「仓库相对路径」
               "root": os.path.relpath(_ROOT, os.path.dirname(HTML)).replace("\\", "/"),
               "assets": _img_assets(runs),
               "runs": runs}
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html = _HTML_TMPL.replace("__DATA__", data)
    with open(HTML, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"[digest] 看板 -> {_rel(HTML)}（{len(dates)} 天）")


# ---------------------------------------------------------------- HTML 模板
_HTML_TMPL = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>每日操作汇总 · XAlpha</title>
<style>
:root{
  --bg:#f4f6fb; --card:#fff; --ink:#1b2333; --muted:#77829a; --line:#e8edf6;
  --accent:#3a63f3; --buy:#e01e37; --sell:#0f9d58; --hold:#3a63f3;
  --watch:#c47f00; --avoid:#8a93a6; --none:#aab2c2;
  --buy-bg:#fdecef; --sell-bg:#e9f7f0; --hold-bg:#eceffe;
  --watch-bg:#fff6e5; --avoid-bg:#f1f3f7; --none-bg:#f4f6fa;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}
header.top{position:sticky;top:0;z-index:20;background:rgba(255,255,255,.92);
  backdrop-filter:blur(8px);border-bottom:1px solid var(--line)}
.top-in{max-width:1440px;margin:0 auto;padding:14px 22px;display:flex;
  align-items:center;gap:18px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:12px;margin-right:auto}
.logo{width:36px;height:36px;border-radius:11px;background:linear-gradient(135deg,#3a63f3,#7b3ff2);
  color:#fff;display:grid;place-items:center;font-weight:700;font-size:17px}
h1{font-size:17px;margin:0;letter-spacing:.2px}
.sub{font-size:12px;color:var(--muted);margin:0}
.ctrl{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
select,input[type=text]{height:36px;border:1px solid var(--line);background:#fff;border-radius:10px;
  padding:0 12px;font-size:13px;color:var(--ink);outline:none}
input[type=text]{width:230px}
select:focus,input[type=text]:focus{border-color:#b9c7f7;box-shadow:0 0 0 3px #eef2ff}
.sw{display:flex;align-items:center;gap:7px;font-size:13px;color:var(--muted);cursor:pointer;
  user-select:none;padding:0 4px}
.sw input{accent-color:var(--accent);width:15px;height:15px}
main{max-width:1440px;margin:0 auto;padding:20px 22px 60px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:18px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px}
.kpi b{display:block;font-size:22px;line-height:1.25;font-variant-numeric:tabular-nums}
.kpi span{font-size:12px;color:var(--muted)}
.grid{display:grid;gap:14px}
.block{margin-bottom:26px}
.bhead{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:0 2px 8px}
.bhead h3{font-size:16px;margin:0;font-weight:700;letter-spacing:.2px}
.bno{font-size:12px;font-weight:700;color:#fff;border-radius:8px;padding:2px 9px;
  background:linear-gradient(135deg,#3a63f3,#7b3ff2);white-space:nowrap}
.bstat{font-size:12px;color:var(--muted);margin-left:auto;white-space:nowrap}
.bstat b{color:var(--ink);font-variant-numeric:tabular-nums}
.bdesc{margin:0 2px 12px;font-size:12.5px;color:#5f6b80;max-width:1180px}
.bnote{margin:10px 2px 0;font-size:12px;color:#a3abbd;max-width:1180px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;overflow:hidden;
  box-shadow:0 1px 2px rgba(20,35,70,.04)}
.card>details>summary{list-style:none;cursor:pointer;padding:15px 18px;display:flex;gap:12px;
  align-items:flex-start;flex-wrap:wrap}
.card>details>summary::-webkit-details-marker{display:none}
.card>details[open]>summary{border-bottom:1px solid var(--line)}
.ttl{display:flex;align-items:center;gap:10px;flex-wrap:wrap;min-width:220px}
.ttl h2{font-size:15px;margin:0;font-weight:650}
.badge{font-size:12px;font-weight:650;padding:2.5px 10px;border-radius:999px;white-space:nowrap}
.badge.buy{color:var(--buy);background:var(--buy-bg)}
.badge.sell{color:var(--sell);background:var(--sell-bg)}
.badge.hold{color:var(--hold);background:var(--hold-bg)}
.badge.watch{color:var(--watch);background:var(--watch-bg)}
.badge.avoid{color:var(--avoid);background:var(--avoid-bg)}
.badge.none{color:var(--none);background:var(--none-bg)}
.sum{color:#4a5568;font-size:13px;flex:1;min-width:260px}
.meta{font-size:12px;color:var(--muted);white-space:nowrap}
.chips{display:flex;gap:8px;flex-wrap:wrap;padding:0 18px 6px}
.chip{font-size:12px;color:#5b6478;background:#f7f9fd;border:1px solid var(--line);
  border-radius:8px;padding:2px 9px}
.chip b{color:var(--ink);font-variant-numeric:tabular-nums}
/* 表格不再限高: 原 max-height:420px 会在卡片内产生竖向滚动条, 截图截不全。
   现在整表铺开、跟页面一起滚; 表头仍 sticky, 长表滚动时贴住视口顶部。 */
.tbwrap{overflow:visible}
table{width:100%;border-collapse:collapse;font-size:13px}
thead th{position:sticky;top:0;background:#fafbfe;z-index:1;color:var(--muted);font-weight:600;
  text-align:left;padding:9px 14px;border-bottom:1px solid var(--line);white-space:nowrap;font-size:12px}
tbody td{padding:8px 14px;border-bottom:1px solid #f2f5fa;vertical-align:top}
tbody tr:last-child td{border-bottom:none}
tbody tr:hover{background:#fafcff}
td.c{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12.5px;white-space:nowrap}
td.n{white-space:nowrap}
td.a{white-space:nowrap;font-weight:600;font-size:12.5px}
td.a.buy{color:var(--buy)} td.a.sell{color:var(--sell)} td.a.hold{color:var(--hold)}
td.a.watch{color:var(--watch)} td.a.avoid{color:var(--avoid)} td.a.none{color:var(--none)}
td.d{color:#5f6b80;font-size:12.5px}
code{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;background:#f2f5fa;
  border-radius:4px;padding:0 4px;font-size:12px}
.empty{padding:26px;text-align:center;color:var(--muted);font-size:13px}
.fig{padding:4px 18px 14px;margin:0}
.fig img{display:block;width:100%;height:auto;border:1px solid var(--line);
  border-radius:12px;background:#fff}
.figcap{font-size:11.5px;color:var(--muted);padding-top:6px}
/* 多图卡片（板块2 的八张变种单腿图）：宽屏两列图墙，窄屏自动落成一列；
   图注写「是哪个标的的图」，不写变种名（变种已在表格行里）。
   图墙里是缩略图，点任意一张弹全屏（字太小）/ Esc 或再点关闭。 */
.figs{display:grid;grid-template-columns:repeat(auto-fit,minmax(560px,1fr));
  gap:10px 16px;padding:4px 18px 14px}
.figs .fig{padding:0;min-width:0}
.figs .figcap{text-align:center;padding-top:5px}
.figs .figcap b{color:var(--ink);font-weight:600}
.fig img{cursor:zoom-in}
.zoomwrap{position:fixed;inset:0;z-index:80;display:none;place-items:center;padding:18px;
  background:rgba(10,16,30,.88);cursor:zoom-out}
.zoomwrap.on{display:grid}
.zoomwrap img{max-width:100%;max-height:100%;border-radius:10px;background:#fff;
  box-shadow:0 18px 60px rgba(0,0,0,.55)}
.note{padding:9px 18px 14px;font-size:12px;color:var(--muted)}
.src{padding:0 18px 14px;font-size:11.5px;color:#a3abbd;font-family:ui-monospace,Consolas,monospace}
footer{max-width:1440px;margin:0 auto;padding:0 22px 40px;color:var(--muted);font-size:12px}
.err{border-color:#f5c2c7;background:#fff8f8}
@media(max-width:720px){
  input[type=text]{width:100%}
  .ctrl{width:100%}
  .sum{min-width:100%}
  td.d{display:none}
  thead th.h-d{display:none}
}
</style>
</head>
<body>
<header class="top"><div class="top-in">
  <div class="brand"><div class="logo">X</div>
    <div><h1>每日操作汇总</h1><p class="sub" id="sub"></p></div></div>
  <div class="ctrl">
    <select id="dateSel" aria-label="日期"></select>
    <input type="text" id="q" placeholder="搜索代码 / 名称 / 说明">
    <label class="sw"><input type="checkbox" id="onlyAct" checked>只看有动作</label>
  </div>
</div></header>
<main>
  <div class="kpis" id="kpis"></div>
  <div class="grid" id="app"></div>
</main>
<footer>机械规则输出，非投资建议 · 数据源：各策略 <code>daily/</code> 产物 · 本页由 <code>pipeline/daily_digest.py</code> 生成</footer>
<div class="zoomwrap" id="zoomWrap"><img id="zoomImg" alt="放大查看"></div>
<script>window.__DIGEST__ = __DATA__;</script>
<script>
(function(){
  var D = window.__DIGEST__ || {dates:[],runs:{}};
  var dateSel = document.getElementById('dateSel');
  var qBox = document.getElementById('q');
  var onlyAct = document.getElementById('onlyAct');
  var app = document.getElementById('app');
  var kpis = document.getElementById('kpis');
  var sub = document.getElementById('sub');
  if(!D.dates.length){
    app.innerHTML = '<div class="card"><div class="empty">还没有归档数据，先跑 <code>python pipeline/daily_digest.py</code></div></div>';
    return;
  }
  var cur = D.dates[D.dates.length - 1];
  D.dates.slice().reverse().forEach(function(d){
    var o = document.createElement('option'); o.value = d; o.textContent = d; dateSel.appendChild(o);
  });
  dateSel.value = cur;

  // 图墙里是缩略图（单腿图的图注/刻度字很小）——点图弹全屏，Esc 或再点一次关闭。
  var zoomWrap = document.getElementById('zoomWrap');
  var zoomImg = document.getElementById('zoomImg');
  window.mfZoom = function(src){
    if (!src) return;
    zoomImg.src = src;
    zoomWrap.classList.add('on');
  };
  function zoomOff(){
    zoomWrap.classList.remove('on');
    zoomImg.removeAttribute('src');
  }
  zoomWrap.addEventListener('click', zoomOff);
  document.addEventListener('keydown', function(e){ if (e.key === 'Escape') zoomOff(); });

  function esc(s){
    return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];
    });
  }
  function md(s){
    return esc(s).replace(/`([^`]+)`/g, '<code>$1</code>')
                .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  }
  function visibleRows(s, q){
    var rows = (s.rows || []).filter(function(r){
      var hit = !q || (r.c + ' ' + r.n + ' ' + r.a + ' ' + r.d).toLowerCase().indexOf(q) >= 0;
      if(!hit) return false;
      if(q) return true;                      // 搜索时忽略「只看有动作」
      // 小表不筛选：池子固定的策略（7 腿 / 5 只红利 / 8 项基准）空仓行的读数本身就是
      // 决策依据，且一共才几行；老归档没有 all_rows 字段也按小表处理。
      // 大表（如 54 只全池）才需要「只看有动作」把噪声收起来。
      if(onlyAct.checked && r.act === 'none' && !s.all_rows && (s.rows || []).length > 10) return false;
      return true;
    });
    return rows;
  }
  function card(key, s, q){
    var rows = visibleRows(s, q);
    var chips = (s.stats || []).map(function(kv){
      return '<span class="chip">' + esc(kv[0]) + ' <b>' + esc(kv[1]) + '</b></span>';
    }).join('');
    var cols = s.cols || ['代码', '名称', '动作', '说明'];
    var ths = cols.map(function(c, i){
      return '<th' + (i === cols.length - 1 ? ' class="h-d"' : '') + '>' + esc(c) + '</th>';
    }).join('');
    var body = rows.length ? '<div class="tbwrap"><table><thead><tr>' + ths +
        '</tr></thead><tbody>' +
        rows.map(function(r){
          return '<tr><td class="c">' + esc(r.c) + '</td><td class="n">' + esc(r.n) +
                 '</td><td class="a ' + esc(r.act) + '">' + esc(r.a) +
                 '</td><td class="d">' + md(r.d) + '</td></tr>';
        }).join('') + '</tbody></table></div>'
      : '<div class="empty">' + (s.rows && s.rows.length ? '当前筛选下无明细' : '无明细产物') + '</div>';
    // 带图的卡片默认展开（图是该策略的正文，不该藏在折叠里）
    // file:// 下不能带查询串（Chrome 会拒绝加载 file 路径 + ?d=），所以只在 http 下做防缓存
    var bust = location.protocol === 'file:' ? '' : ('?d=' + encodeURIComponent(cur));
    // 图三级来源，任一可用即显示：
    //  ① data URI（内嵌，单文件自包含）
    //  ② data/_daily_digest_assets/<key>__<文件名>（与看板同目录，预览器把 data/ 当根时也取得到）
    //  ③ 原产物路径 ../strategies/...
    // 不用 loading="lazy"：滚动容器 / 内嵌预览器里 IntersectionObserver 常不触发，
    // 图会停在占位破图。图数量少，直接加载。
    function imgTag(key, path, alt){
      var bn = String(path || '').split('/').pop();
      var local = '_daily_digest_assets/' + key + '__' + bn;
      var isrc = (D.assets && D.assets[path]) || local;
      return '<img alt="' + esc(alt) + '" title="点击放大" src="' + esc(isrc) +
        '" data-fb1="' + esc(local) + '" data-fb2="' + esc((D.root || '.') + '/' + path) +
        '" onclick="mfZoom(this.src)"' +
        ' onerror="var f=[this.dataset.fb1,this.dataset.fb2];var n=+(this.dataset.n||0);' +
        'if(n<2){this.dataset.n=n+1;this.src=f[n];}else{this.onerror=null;}">';
    }
    // 单图（img）与多图（imgs：板块2 的八张变种单腿图）统一处理；多图渲染成图墙，
    // 每张图下面一行小字标注是哪个标的。
    var figs = [];
    if (s.img) figs.push({path: s.img, cap: s.img_cap, alt: s.name});
    (s.imgs || []).forEach(function(x){
      if (x && x.path) figs.push({path: x.path, cap: x.cap, alt: x.cap || s.name});
    });
    var fig = figs.length ? figs.map(function(f){
      return '<figure class="fig">' + imgTag(s.key, f.path, f.alt) +
        (f.cap ? '<figcaption class="figcap">' + md(f.cap) + '</figcaption>' : '') +
        '</figure>';
    }).join('') : '';
    if (figs.length > 1) fig = '<div class="figs">' + fig + '</div>';
    var open = (q || figs.length || (s.rows || []).some(function(r){ return r.act !== 'none'; }))
      ? ' open' : '';
    var cls = s.error ? ' card err' : ' card';
    return '<div class="' + cls.trim() + '"><details' + open + '>' +
      '<summary><div class="ttl"><h2>' + esc(s.name) + '</h2>' +
      '<span class="badge ' + esc(s.tone) + '">' + esc(s.badge) + '</span></div>' +
      '<div class="sum">' + md(s.summary) + '</div>' +
      '<div class="meta">' + (s.as_of ? ('数据日 ' + esc(s.as_of)) : '无数据') +
      (s.data ? (' · 源数据 ' + esc(s.data)) : '') +
      (s.rows ? (' · ' + s.rows.length + ' 行') : '') + '</div></summary>' +
      (chips ? '<div class="chips">' + chips + '</div>' : '') +
      body + fig + (s.note ? '<div class="note">' + esc(s.note) + '</div>' : '') +
      (s.src ? '<div class="src">' + esc(s.src) + '</div>' : '') +
      '</details></div>';
  }
  function cnt(run, act){
    var n = 0;
    Object.keys(run.strategies).forEach(function(k){
      (run.strategies[k].rows || []).forEach(function(r){ if(r.act === act) n++; });
    });
    return n;
  }
  function blockStats(run, ks){
    var o = {buy:0, sell:0, watch:0};
    ks.forEach(function(k){
      (run.strategies[k].rows || []).forEach(function(r){
        if(o.hasOwnProperty(r.act)) o[r.act]++;
      });
    });
    return o;
  }
  function blockHTML(run, b, ks, q){
    var cards = ks.map(function(k){ return card(k, run.strategies[k], q); }).join('');
    var s = blockStats(run, ks);
    var head = '<div class="bhead"><span class="bno">板块 ' + esc(b.no) + '</span>' +
      '<h3>' + esc(b.title) + '</h3>' +
      '<span class="bstat">买 <b>' + s.buy + '</b> · 卖 <b>' + s.sell +
      '</b> · 观察 <b>' + s.watch + '</b> · ' + ks.length + ' 个策略</span></div>';
    return '<section class="block">' + head +
      (b.desc ? '<p class="bdesc">' + md(b.desc) + '</p>' : '') +
      '<div class="grid">' + cards + '</div>' +
      (b.note ? '<p class="bnote">' + md(b.note) + '</p>' : '') +
      '</section>';
  }
  function render(){
    var run = D.runs[cur]; if(!run) return;
    var q = qBox.value.trim().toLowerCase();
    var keys = Object.keys(run.strategies);
    // 板块顺序以 strategies.json 的 blocks 为准；未认领板块的策略归入「其他」，
    // 这样新增策略忘了填 daily.block 时不会静默消失。
    var blocks = (D.blocks && D.blocks.length)
      ? D.blocks : [{no:1, title:'全部策略', desc:'', note:'', keys:keys}];
    var used = {};
    var html = '';
    var nBlock = 0;
    blocks.forEach(function(b){
      var ks = (b.keys || []).filter(function(k){ return run.strategies[k]; });
      ks.forEach(function(k){ used[k] = 1; });
      if(!ks.length) return;
      nBlock++;
      html += blockHTML(run, b, ks, q);
    });
    var rest = keys.filter(function(k){ return !used[k]; });
    if(rest.length){
      html += blockHTML(run, {no:'+', title:'其他（未认领板块）', desc:'', note:''}, rest, q);
    }
    sub.textContent = '数据日 ' + cur + ' · 生成 ' + (run.run_at || '') + ' · ' + nBlock +
      ' 个板块 / ' + keys.length + ' 个策略 · 已归档 ' + D.dates.length + ' 天';
    kpis.innerHTML =
      '<div class="kpi"><b>' + nBlock + '</b><span>板块</span></div>' +
      '<div class="kpi"><b>' + keys.length + '</b><span>策略</span></div>' +
      '<div class="kpi"><b style="color:var(--buy)">' + cnt(run, 'buy') + '</b><span>买入 / 建仓</span></div>' +
      '<div class="kpi"><b style="color:var(--sell)">' + cnt(run, 'sell') + '</b><span>卖出 / 清仓</span></div>' +
      '<div class="kpi"><b style="color:var(--watch)">' + cnt(run, 'watch') + '</b><span>观察 / 差一步</span></div>' +
      '<div class="kpi"><b style="color:var(--avoid)">' + cnt(run, 'avoid') + '</b><span>规避</span></div>';
    app.innerHTML = html;
  }
  dateSel.addEventListener('change', function(){ cur = dateSel.value; render(); });
  qBox.addEventListener('input', render);
  onlyAct.addEventListener('change', render);
  render();
})();
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description="每日操作汇总：归档各策略产物并渲染 HTML 看板")
    ap.add_argument("--html-only", action="store_true", help="只按已有历史重绘，不读产物不归档")
    ap.add_argument("--date", help="归档日期（默认今天），补录历史用")
    args = ap.parse_args()

    if args.html_only:
        if not os.path.exists(DB):
            sys.exit(f"历史库不存在：{DB}")
        render(json.loads(_read(DB)))
        return

    entries = collect()
    day = args.date or datetime.now().strftime("%Y-%m-%d")
    db = archive(entries, day)
    render(db)


if __name__ == "__main__":
    main()
