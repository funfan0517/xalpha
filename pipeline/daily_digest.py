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
由所属卡片内嵌展示（只在所属卡片里占一行，不单独出卡）。

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
# 例如板块2 的「变种操作一览」按 vol_variants 的路径取各变种 JSON。
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


def _proxy_mismatch_note(codes):
    """场内信号代理与场外执行标的**跟踪指数不同源**的腿（基差风险）—— 只列本卡池内命中的。

    清单由 ``pipeline/universe.py::proxy_mismatch`` 从 `data/_universe.md` 的「指数代码」列派生
    （场内外不同源时该列写成 ``A(场外) / B(场内)``），新增标的一入库就自动带进脚注，
    不用再靠人工比对 74 行。``codes`` = 本卡出现的场内代码；无命中时返回空串。
    """
    if not codes:
        return ""
    if _DIR not in sys.path:
        sys.path.insert(0, _DIR)
    import universe  # 同目录 pipeline/universe.py（唯一池解析，勿改名为 pipeline.universe）
    hit = [m for m in universe.proxy_mismatch() if m["inner_code"] in set(codes)]
    if not hit:
        return ""
    legs = "、".join(
        f"{m['inner_code']} {m['inner_name']}（{m['inner_index']}）≠ 场外 {m['off_code']}（{m['off_index']}）"
        for m in hit)
    return (f"｜⚠ 信号代理与执行标的不同源（基差风险）：{legs}。"
            "这些腿的买卖点按场内代理指数解读，不代表场外基金同步涨跌。")


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

    - ``daily.enabled`` **必须显式为 true** 才出卡（缺键 = 不上板，fail-safe）——
      新策略默认不得进每日操作报告，未经用户同意不许出现在看板上（AGENTS.md §9）；
    - ``daily.enabled=false`` → 整卡撤掉（策略已移出每日操作，历史归档也不再渲染）；
    - ``daily.card=false``   → 数据仍取、但不单独出卡（只在所属卡片里占一行）。
    """
    daily = (meta or {}).get("daily") or {}
    return daily.get("enabled") is True and daily.get("card") is not False


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
            # daily.card=false 表示「数据仍要、但不单独出卡」（只在所属卡片里占一行）
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
        # 池 10 只只是候选广度，逐只 RSI 才是「为什么选这 2 只」的依据 —— 空仓行的读数也要给全。
        detail = (f"RSI14 {r:.1f}" if r is not None else "RSI14 —（今日无快照）")
        detail += f" · 权重 {w * 100:.0f}%"
        # best_txt 由 daily.py 按 fit.py 口径生成（含「持平」处理），看板不重复实现
        if p.get("best_txt"):
            detail += f" · 最优 {p['best_txt']}"
        detail += f" · 下次 {nd} {dec}"
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
        note=f"V4 · RSI14 择时（月末收盘确认、次日开盘执行；月中为预览）：板块(池内 {len(pool)} 只)"
             "RSI 均值 ≥70 清仓 / ≥50 减半，横截面取 RSI 最低 2 只等权。"
             f"**池 {len(pool)} 只只是候选广度 —— 每期只持 RSI 最低 2 只，其余 {len(pool) - 2} 只权重 0**，"
             "不要按池内只数买入。「最优」= 该标的单独跑同一套 RSI 规则 vs 买入持有（`fit.py`），"
             "标「买入持有」的标的被 RSI 减仓是负和。2026-11-20 起池由 5 只筛池改为全池。"
             "V9 五因子估值温度已拆成板块4 的第二张卡。",
        src=_rel(os.path.join(_ROOT, daily["signal_md"])),
        data=f"基金累计净值 {as_of} · 生效信号 {d.get('signal_date')} 收盘（月末确认，月中为预览）",
        cols=["代码", "名称", "本月动作", "RSI / 权重 / 最优策略 / 下次预计"],
        # 池 10 只、「空仓那 8 只的 RSI」正是横截面选 2 只的依据 —— 这一卡不吃
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
    """V17 八态趋势的一行摘要（**不单独出卡**，挂在板块5 技术指标卡的 6 策略表下面）。

    只保留四件读得懂的事：今天加减了谁、**允许持有的标的**（急涨/慢涨/弱回调）、
    全池八态分布、平均仓位。
    54 只逐只状态在 ``v17_octo_trend/daily/_v17_octo_daily.md``，不搬进汇总看板。
    产物缺失时返回 ``None``（卡片照常出，不因 V17 缺数而失败）。
    """
    meta = ((_DOC.get("strategies") or {}).get("v17_octo_trend") or {})
    rel = ((meta.get("daily") or {}).get("signal_json")
           or "strategies/tech_indicators/v17_octo_trend/daily/_v17_octo_daily.json")
    path = os.path.join(_ROOT, rel.replace("/", os.sep))
    if not os.path.exists(path):
        return None
    try:
        d = json.loads(_read(path))
    except json.JSONDecodeError:
        return None
    legs = d.get("legs") or []
    if not legs:
        return None
    buys = [x for x in legs if x.get("action") == "加仓" and float(x.get("act") or 0) > 0]
    sells = [x for x in legs if x.get("action") == "减仓" and float(x.get("act") or 0) < 0]
    ups = [x for x in legs if x.get("state") == "急涨"]
    # 允许持有的三个态（急涨 100% / 慢涨 50% / 弱回调 100%）—— 新产物直接给 hold_ok，
    # 老产物没有这个键就从 legs 反推，避免产物版本不一致时整行消失。
    hold = d.get("hold_ok")
    if hold is None:
        hold = [dict(code=x.get("code"), name=x.get("name"), state=x.get("state"),
                     cap=float(x.get("cap") or 0), pos=float(x.get("pos") or 0))
                for x in legs if x.get("state") in ("急涨", "慢涨", "弱回调")]
    hold = sorted(hold, key=lambda y: (-float(y.get("pos") or 0), str(y.get("name"))))
    avg = sum(float(x.get("pos") or 0) for x in legs) / len(legs)
    cnt = {}
    for x in legs:
        cnt[str(x.get("state"))] = cnt.get(str(x.get("state")), 0) + 1
    dist = " / ".join(f"{k} {v}" for k, v in
                      sorted(((k, v) for k, v in cnt.items() if k != "暖机"),
                             key=lambda kv: -kv[1]))

    def _tag(x):
        return (f"{x.get('name')}(`{x.get('code')}`) "
                f"{float(x.get('pos_prev') or 0):.0%}→{float(x.get('pos') or 0):.0%}")

    parts = []
    if buys:
        parts.append("加仓：" + "、".join(_tag(x) for x in buys))
    if sells:
        parts.append("减仓：" + "、".join(_tag(x) for x in sells))
    if not (buys or sells):
        parts.append("今日无调仓")
    if hold:
        items = "、".join(f"{h.get('name')}(`{h.get('code')}`) {h.get('state')}" for h in hold[:12])
        parts.append("**允许持有（急涨/慢涨/弱回调）：" + items
                     + (f" 等 {len(hold)} 只" if len(hold) > 12 else "") + "**")
    else:
        parts.append("**允许持有（急涨/慢涨/弱回调）：无**")
    if dist:
        parts.append("八态：" + dist)
    parts.append(f"平均仓位 **{avg:.0%}**")
    if buys:
        act, label = "buy", f"加 {len(buys)}"
    elif sells:
        act, label = "sell", f"减 {len(sells)}"
    else:
        act, label = "hold", "无动作"
    return (_row("V17 八态", f"加 {len(buys)} · 减 {len(sells)} · 满仓 {len(ups)}", act, label,
                 "；".join(parts)),
            len(buys), len(sells), avg, d.get("as_of"), len(hold))


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
    # 池内代码先取出来：下面的循环会用 `detail` 承接每行的明细文本（覆盖上表的 list），
    # 之后再从 `detail` 里反推池代码会拿到单个字符。
    pool_codes = {cells[0] for cells in detail if len(cells) >= 6 + ncol}
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

    n_buy = sum(len(group[n]["buy"]) for n in names)
    n_sell = sum(len(group[n]["sell"]) for n in names)
    # V17 八态趋势不单独出卡（daily.card=false），只在 6 策略表下面加一行。
    # 徽标仍只统计上面 6 个指标策略 —— V17 是仓位分档、口径不同，混进来会让「买 N / 卖 M」失真。
    v17 = _v17_row()
    active = [r for r in rows if r["act"] in ("buy", "sell")]
    if v17:
        rows.append(v17[0])
    if active:
        badge = f"买 {n_buy} / 卖 {n_sell}"
        tone = "buy" if n_buy else "sell"
        summary = "；".join(f"{r['c']}：{r['a']}" for r in active)
    else:
        badge, tone = "无动作", "none"
        summary = f"{ncol} 策略对全池 {pool} 只均无买入/卖出信号"
    if v17:
        summary += f" · V17 八态 加 {v17[1]} · 减 {v17[2]}"
    # 六列的数据日并不相同：MACD/V3/V6/V7 用当日行情，V9/V10 是状态机型、各自 scan 的落盘日
    head_txt = "\n".join(lines[:8])
    m9 = re.search(r"v9\s*数据日\D*(\d{4}-\d{2}-\d{2})", head_txt, re.I)
    m10 = re.search(r"v10\s*数据日\D*(\d{4}-\d{2}-\d{2})", head_txt, re.I)
    v9d, v10d = (m9.group(1) if m9 else None), (m10.group(1) if m10 else None)
    data = f"ETF 行情 {as_of}"
    if v9d or v10d:
        data += f" · V9 {v9d or '—'} / V10 {v10d or as_of}"
    if v17 and v17[4]:
        data += f" · V17 {v17[4]}"
    mis_note = _proxy_mismatch_note(pool_codes)
    stats = [["全池", str(pool)], ["买入", str(n_buy)], ["卖出", str(n_sell)],
             ["策略", str(ncol)]]
    v17_note = ""
    if v17:
        stats += [["V17 八态 加/减", f"{v17[1]} / {v17[2]}"],
                  ["V17 持有许可", f"{v17[5]} 只"],
                  ["V17 平均仓位", f"{v17[3]:.0%}"]]
        v17_note = ("最后一行「V17 八态」是 vol_legs v12 八态趋势的扩池版（54 只场内 ETF，"
                    "**ETF 自身量能**口径）：八态 → 仓位比例（非 0/1 择时）。"
                    "「允许持有」= 急涨/慢涨/弱回调（许可 100%/50%/100%），只表示**可以拿**，"
                    "不是买入指令，实际仓位还要过波动层与升格确认。"
                    "与上面 6 个指标策略口径不同，读数不可直接互比；逐只状态见 "
                    "strategies/tech_indicators/v17_octo_trend/daily/_v17_octo_daily.md。")
    return _entry(
        "tech_indicators", f"技术指标（{ncol} 策略）", as_of, badge, tone, summary, rows,
        stats,
        note="解析自 _daily_recommend.md（该策略只落 markdown）；每行是一个指标策略对全池的操作汇总。"
             "⚠ V6/V7 的样本外检验不通过（WF 夏普 0.14 / 真实 OOS 0.41，均低于 MACD 0.54），"
             "保留在表里只为观察，**不要与 MACD 等权使用**；「N 策略共振」把这两列剔除后才有意义。"
             + v17_note
             + mis_note,
        src=_rel(path),
        data=data,
        cols=["策略", "动作统计", "动作", "该策略的操作明细"],
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
             f"{_VOL_PRICE} {rule.get('buy', '上穿MA20')} 买 / {rule.get('sell', '下穿MA20')} 卖，"
             f"T+1 {_VOL_FILL}成交（口径：真实指数，黄金用场内 `518880`）。"
             "**成本是决定性变量**：逐腿 13~16 笔/年，"
             "按 A 类真实费率扣费后组合年化 8.10% → 1.42%（C 类销售服务费未计）。"
             "本变种只管逐腿进出时点，组合层仓位带仍由主策略管。",
        src=_rel(md_path),
        cols=["执行代码", "腿", "动作", "关键值"],
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
        cols=["执行代码", "腿", "动作", "关键值"],
    )


# 板块1：每项标的的「基准 × 最优策略」映射 —— README §9.1 在看板侧的唯一实现处。
# 换某条腿的执行策略时，这里、README §9.1、strategies.json 的 note 三处一起改。
# (key, 标的, 场外代码, 最优策略短名, 该策略的 daily 产物仓库相对路径)
_PERM_BEST = {
    "bond":  ("短中长期国债", "012265/001512/003377", "v7_risk_budget",
              "strategies/bond_cash/v7_risk_budget/daily/_v7_daily.json"),
    "divlv": ("红利低波", "", "v4_rsi",
              "strategies/dividend/v4_rsi/daily/_v4_signal.json"),
    "ndx":   ("纳指100", "270042", "v16_ndx_sw",
              "strategies/vol_legs/v16_ndx_sw/daily/_v16_daily.json"),
    "spx":   ("标普500", "017641", "v17_spx_v12",
              "strategies/vol_legs/v17_spx_v12/daily/_v17_daily.json"),
    "gold":  ("黄金", "000216", "v20_gold_v5",
              "strategies/vol_legs/v20_gold_v5/daily/_v20_daily.json"),
    "cash":  ("货币基金", "自选", "现金腿", ""),
    "a500":  ("中证A500", "023299", "v14_a500_e2",
              "strategies/vol_legs/v14_a500_e2/daily/_v14_daily.json"),
    "kc":    ("科创50", "011609", "v18_kc50_e2tv",
              "strategies/vol_legs/v18_kc50_e2tv/daily/_v18_daily.json"),
    "hs300": ("沪深300", "006131", "v15_hs300_min2",
              "strategies/vol_legs/v15_hs300_min2/daily/_v15_daily.json"),
}


def _perm_leg_pos(key, rel):
    """-> (当前倍率, 目标倍率, 信号日, 细节)。倍率 0~1，乘基准 pp 即组合层仓位。

    只读各策略自己的 `daily/_*_daily.json`，看板不重算策略。
    """
    if key == "cash":
        return 1.0, 1.0, None, "无价格信号，恒 100%"
    d = json.loads(_read(os.path.join(_ROOT, rel)))
    as_of = d.get("as_of")
    if key == "bond":       # v7 只做债腿内部风险预算，整体恒满仓
        return 1.0, 1.0, as_of, "债腿内部 ERC，整体满仓"
    if key == "divlv":      # v4 是月度信号：mult 即当前生效档位，当前=目标
        m = float(d.get("mult") or 0)
        codes = "/".join(str(p.get("code", "")) for p in (d.get("positions") or []))
        return m, m, as_of, f"{d.get('tier', '—')} · {codes}"
    cur = float(d.get("current_position") or 0)
    tgt = d.get("next_target")
    tgt = cur if tgt is None else float(tgt)
    return cur, tgt, as_of, str(d.get("action") or "")


def parse_permanent(_cfg):
    """板块1：9 项标的的「基准 pp × 最优策略仓位」= 目标仓位。

    **2026-11-23 改版（用户要求）**：本卡不再显示 §2 的 ±50% 倾斜权重，改为
    「基准 / 最优策略 / 策略仓位（当前→目标）/ 组合 pp（当前→目标）」。
    §2 倾斜权重仍由 `_signal.py` 算并写在 `_permanent_report.md`，只是不在此卡显示。

    基准取 `_permanent_signal.json::base`（`rule.RAW` 的快照），策略仓位取各策略
    自己的 `daily/_*_daily.json`；两者相乘即该项占组合的目标 pp。
    """
    sig = json.loads(_read(os.path.join(
        _ROOT, "strategies", "permanent", "daily", "_permanent_signal.json")))
    base = sig.get("base") or {}
    rows, cur_tot, tgt_tot, n_empty, dates = [], 0.0, 0.0, 0, []
    for key, (name, code, strat, rel) in _PERM_BEST.items():
        b = float(base.get(key) or 0)
        if b <= 0:
            continue
        cur, tgt, as_of, detail = _perm_leg_pos(key, rel)
        if as_of:
            dates.append(str(as_of))
        cpp, tpp = b * cur, b * tgt
        cur_tot += cpp
        tgt_tot += tpp
        if key != "cash" and tgt <= 0:
            n_empty += 1
        act = "buy" if tpp - cpp >= 0.05 else ("sell" if cpp - tpp >= 0.05 else "hold")
        rows.append(_row(
            f"{name}（{code}）" if code else name,
            f"{b:g}pp · {strat}",
            act,
            f"{cur:.0%} → {tgt:.0%}",
            f"{cpp:.2f} → {tpp:.2f} pp" + (f" · {detail}" if detail else ""),
        ))
    cash_pp = float(base.get("cash") or 0)
    data = ""
    if dates:
        lo, hi = min(dates), max(dates)
        data = f"各腿信号日 {hi}" if lo == hi else f"各腿信号日 {lo} ~ {hi}（最旧 {lo}）"
    return _entry(
        "permanent", "永久组合 · 基准 / 最优策略 / 仓位", max(dates) if dates else None,
        f"投入 {cur_tot:.0f}pp", "watch",
        f"{len(rows)} 项：基准 pp × 最优策略仓位 = 目标仓位 · 当前投入 {cur_tot:.1f}pp / "
        f"目标 {tgt_tot:.1f}pp · {n_empty} 条策略腿空仓",
        rows,
        [["风险敞口", f"{cur_tot - cash_pp:.1f}pp"],
         ["当前投入", f"{cur_tot:.1f}pp"],
         ["目标投入", f"{tgt_tot:.1f}pp"],
         ["未投资现金", f"{100 - cur_tot:.1f}pp"]],
        note="**目标仓位 = 基准 pp × 策略仓位**，不是当日买卖：任一项偏离目标 >5pp 才调，半年检查一次。"
             "策略映射与实测见 `strategies/permanent/README.md` §9.1；"
             "§2 的 ±50% 倾斜权重不在此卡显示（见 `strategies/permanent/daily/_permanent_report.md`）。"
             "各腿信号日不同（QDII 净值滞后 1-2 日、红利与 V7 是月度信号），以卡片首列为准。",
        src=_rel(os.path.join(_ROOT, "strategies", "permanent", "README.md")),
        data=data,
        cols=["标的", "基准 · 最优策略", "策略仓位（当前→目标）", "组合 pp（当前→目标）"],
        all_rows=True,
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
             f"**信号与记账口径 = {_VOL_PRICE}**（黄金用场内 `518880`，"
             "不含分红再投、场内溢价与汇率）；"
             "估值闸门为 null（或美股人工分位）时按 §1「规则不执行」处理"
             + (f"；当前 QDII 额度关闭：{'/'.join(qdii)}（档位顺延）" if qdii else "")
             + "。详见报告第五、六节。",
        src=_rel(md_path),
        data=data,
        cols=["执行代码", "资产", "动作", "关键值"],
    )


# ---------------------------------------------------------------- 波动类变种汇总
# 参考板块5「技术指标」的列法：一行一个策略，列为 [策略 / 动作统计 / 动作 / 明细]。
# 2026-11-18 起本卡只列 **v14~v20 七个单标的专用策略**（一策略一腿）。
# v2 / v3 / v4 / v6 / v9 / v12 已从看板移除：其 daily 产物与 PNG 仍由
# `run_daily_vol_legs.ps1` 照常生成，只是不再进这张卡。
# v21（利弗莫尔关键点）**不进看板**：规则未稳定、7 腿样本还在验证，见
# `strategies/vol_legs/v21_livermore_keypoint/backtest/_v21_7legs.md`。
_VOL_VARIANTS = [
    # v14~v20：单标的专用策略（一策略一腿）。
    ("v14", "V14 中证A500 单腿",
     "strategies/vol_legs/v14_a500_e2/daily/_v14_daily.json", {"a500"}),
    ("v15", "V15 沪深300 单腿",
     "strategies/vol_legs/v15_hs300_min2/daily/_v15_daily.json", {"hs300"}),
    ("v16", "V16 纳指100 单腿",
     "strategies/vol_legs/v16_ndx_sw/daily/_v16_daily.json", {"ndx"}),
    ("v17", "V17 标普500 单腿",
     "strategies/vol_legs/v17_spx_v12/daily/_v17_daily.json", {"spx"}),
    ("v18", "V18 科创50 单腿",
     "strategies/vol_legs/v18_kc50_e2tv/daily/_v18_daily.json", {"kc50"}),
    ("v19", "V19 恒生科技 单腿",
     "strategies/vol_legs/v19_hkt_v3/daily/_v19_daily.json", {"hkt"}),
    ("v20", "V20 黄金 单腿",
     "strategies/vol_legs/v20_gold_v5/daily/_v20_daily.json", {"gold"}),
]

# 板块2 口径（2026-11-13 起唯一）：信号 / 回测 / 记账都用该市场真实指数，黄金用场内
# `518880`；不再有 SIGNAL_MODE 开关，也就不必去 rule.py 读一个可能漂移的字符串。
_VOL_PRICE = "真实指数"
_VOL_FILL = "收盘"


# 板块2 的图墙：`run_daily_vol_legs.ps1` 第 3 步每天重画的七张单腿长期图（v14~v20）。
# 每张图下面标注**是哪个标的的图**（代码 + 当前口径），改图名时这里跟着改。
# v2/v3/v4/v6/v9/v12 的十二张旧图已从看板移除（策略产物仍照常生成，只是不再内嵌）。
_VOL_FIGS = [
    # v14~v20：七条腿各一张长期图（近三年图不进看板，见各策略 research/）。
    ("strategies/vol_legs/v14_a500_e2/research/_v14_a500.png",
     f"**中证A500**（执行 `023299` · {_VOL_PRICE}）· V14 E2 前二等权融合（V3+V12）★ 研究超越项"
     " · 长期图（近三年版在策略目录 research/ 下）"),
    ("strategies/vol_legs/v15_hs300_min2/research/_v15_hs300.png",
     f"**沪深300**（执行 `000051` · {_VOL_PRICE}）· V15 MIN2 双确认：min(V2 BOLL 突破, V12 八态趋势许可)"
     " · 长期图（近三年版在策略目录 research/ 下）"),
    ("strategies/vol_legs/v16_ndx_sw/research/_v16_ndx.png",
     f"**纳指100**（执行 `270042` · {_VOL_PRICE}）· V16 MA200 切换：上=V10 波动层 / 下=V12 八态趋势"
     "（★ 研究超越项；MA 100~250 邻域仍过线）· 长期图（近三年版在策略目录 research/ 下）"),
    ("strategies/vol_legs/v17_spx_v12/research/_v17_spx.png",
     f"**标普500**（执行 `017641` · {_VOL_PRICE}）· V17 V12 八态趋势（当前 ★1；研究确认无提升）"
     " · 长期图（近三年版在策略目录 research/ 下）"),
    ("strategies/vol_legs/v18_kc50_e2tv/research/_v18_kc50.png",
     f"**科创50**（执行 `011609` · {_VOL_PRICE}）· V18 (V2 + V12 等权融合) × 波动上限 TV20%"
     " · 长期图（近三年版在策略目录 research/ 下）"),
    ("strategies/vol_legs/v19_hkt_v3/research/_v19_hkt.png",
     f"**恒生科技**（执行 `012805` · {_VOL_PRICE}）· V19 V3 MACD+BOLL（原生引擎），当前 ★1；研究确认无提升"
     " · 长期图（近三年版在策略目录 research/ 下）"),
    ("strategies/vol_legs/v20_gold_v5/research/_v20_gold.png",
     f"**黄金**（执行 `518880` · {_VOL_PRICE}）· V20 V5.1 黄金仓位（风险预算 × 实际利率闸门），当前 ★1"
     " · 长期图（近三年版在策略目录 research/ 下）"),
]


def _vol_classify(x):
    """逐腿动作归一：买入 / 卖出 / 持有 / 空仓（兼容带档位口径的 '维持' + state）。"""
    t = str(x.get("action") or "")
    if "并持有" in t:          # v14~v20 的「买入并持有」= 已在场内、今日无待成交，统计成「持有」
        return "持有"
    if "买" in t or "加仓" in t:
        return "买入"
    if "卖" in t or "减仓" in t or "清" in t:
        return "卖出"
    # v9 的动作列写的是「→ 满仓（快减）」，不含买卖字样 —— 待成交决策靠 pending vs state 判向。
    # 只在 pending **确实不同**时才判买/卖：v14~v20 的 `action` 写「空仓 / 买入并持有」，
    # 但 `pending == state`（无待成交），旧写法把它们误标成「卖出 1」「买入 1」，
    # 于是「今日无动作」的腿在看板上看起来像刚成交（2026-11-19 发现并修正）。
    if x.get("pending") is not None:
        try:
            _p = float(x["pending"])
            _s = x.get("state")
            _s = float(_s) if _s is not None else float(x.get("pos") or 0)
            if _p > _s + 1e-9:
                return "买入"
            if _p < _s - 1e-9:
                return "卖出"
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
            # v14~v20 的 level_txt 本身就写成「当前 X% → 目标 Y%」，再拼一次会变成三段箭头
            if "→" in str(lvl):
                return f"（{lvl}）"
            return f"（{lvl} → {float(p):.0%}）"
        except (TypeError, ValueError):
            pass
    return f"（{lvl}）"


def _vol_variant_row(label, d, emph):
    """七腿变种（v2/v3）-> 一行。"""
    legs = d.get("legs") or []
    by = {"买入": [], "卖出": [], "持有": [], "空仓": []}
    for x in legs:
        by[_vol_classify(x)].append(x)

    emph_parts = [f"**{x.get('name')}** {_vol_classify(x)}{_vol_level(x)}"
                  for x in legs if x.get("leg") in emph]
    # 单标的策略（v14~v20，一策略一腿）写成「★目标腿」；多腿变种才用「★重点 / ★全七腿」
    # —— 否则单腿卡会显示成「★全七腿（均执行）」，既误导又是旧口径的残留。
    if len(legs) == 1:
        head = "★目标腿："
    elif len(emph_parts) < len(legs):
        head = "★重点："
    else:
        head = "★全七腿（均执行）："
    detail = head + ("、".join(emph_parts) or "—")

    # 其余腿也带上各自的仓位状态（v2/v3 空仓·持有、v6/v9 带档位/带内仓位），仅供参考 ——
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
    # 带内趋势提前（共享状态机 MA250+20）：B0 读到上涨就买满、B2 读到下跌就清仓
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


def _vol_v6_row(label, d):
    """V6 是**黄金单标的**的三口径（A 长期配置 / B 控回撤 / C 低仓位占坑）。

    与 v2/v3 等「七腿逐腿」变种不同，它的产物里没有 ``legs``：三条口径并列在 ``A``/``B``/``C``
    三个字典里，各自给出「当前仓位 + 今日动作 + 触发依据」。它回答的是「投给黄金的这笔钱
    该怎么拿」，三条**互斥**（不是三个标的），末尾固定点明「选一条执行」。

    ★ 只标 **B 控回撤**（2026-11-09 用户指定「B 策略标注为重点，A 和 C 不标注」）：
    A/C 照样写全仓位与依据，但不加 ★ 也不加粗，避免三条并列时看起来同等重要。

    动作 / 方向 / 依据全部读 ``_daily_scan.py`` 写出的 json（``action`` / ``dir`` / ``cond``），
    看板不复刻阈值判断，避免参数改了以后两边算法漂移。
    """
    def _f(x, default=0.0):
        try:
            return float(x)
        except (TypeError, ValueError):
            return default

    a = d.get("A") or {}
    b = d.get("B") or {}
    c = d.get("C") or {}
    p = d.get("params") or {}
    macd = p.get("macd") or [50, 200]
    floor = _f(p.get("floor"), 0.5)
    vol_win, vol_tgt = int(_f(p.get("vol_window"), 120)), _f(p.get("vol_target"), 0.10)

    def _act(x):
        t = str(x.get("action") or "无").strip()
        return "无动作" if t in ("", "无", "None") else t

    a_st, a_pp = _f(a.get("state")), _f(a.get("pp"))
    a_pos = ("年线上方" if a.get("above")
             else ("保险丝已生效（上限 %.0f%% 带）" % (floor * 100) if a.get("degraded")
                   else "年线下方第 %s 日" % a.get("below_days")))
    # 优先用产物里的 cond（它已经写全「年线位置 + 保险丝进度」），别再和 a_pos 拼一遍
    a_note = str(a.get("cond") or "").strip() or a_pos
    p1 = f"A 长期配置 {a_st:.0%} 带（{a_pp:.1f}pp）· 今日{_act(a)}（{a_note}）"

    b_st, b_pp = _f(b.get("state")), _f(b.get("pp"))
    p2 = (f"★**B 控回撤** {b_st:.0%} 带（{b_pp:.1f}pp）· 今日**{_act(b)}**"
          f"（{vol_win} 日已实现波动 {_f(b.get('vol')) * 100:.1f}% vs 目标 {vol_tgt:.0%}"
          f" → 系数 {_f(b.get('scale')):.2f}）")

    c_st = _f(c.get("state"))
    p3 = (f"C 低仓位占坑 {'持有' if c_st > 1e-9 else '空仓'} · 今日{_act(c)}"
          f"（MACD {int(macd[0])}/{int(macd[1])} "
          f"{'金叉' if c.get('bull') else '死叉'}；标的场内 `518880`）")

    detail = (p1 + " ｜ " + p2 + " ｜ " + p3
              + " ｜ ⚠ 三口径**互斥**（同一笔钱的三种拿法：A 长期不管 / B 压波动 / C 明确进出），"
                "**选一条执行，不要相加**")

    dirs = [str(x.get("dir") or "") for x in (a, b, c)]
    nb, ns = dirs.count("buy"), dirs.count("sell")
    if nb:
        act, alabel = "buy", f"买入 {nb}"
    elif ns:
        act, alabel = "sell", f"卖出 {ns}"
    else:
        act, alabel = "hold", "无动作"
    stats = f"黄金单标的 · A {a_st:.0%} / B {b_st:.0%} / C {c_st:.0%}"
    return _row(label, stats, act, alabel, detail), act


def parse_vol_variants(_cfg):
    """板块2 的变种操作一览：v14~v20（七个单标的专用策略，一行一条腿）。

    2026-11-18 起 v2 / v3 / v4 / v6 / v9 / v12 从看板移除；
    其 daily 产物与 PNG 仍由 run_daily_vol_legs.ps1 照常生成，只是不再进这张卡。
    v21（利弗莫尔关键点）**不进本卡**：规则与 7 腿样本仍在验证，产物只留在策略目录。
    """
    rows, srcs, as_ofs = [], [], []
    n_buy = n_sell = 0
    dt = {}                    # 各策略产物日
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
        try:
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
    # 数据时点：各单腿策略产物日（v14~v20）。
    vals = sorted({v for v in dt.values() if v})
    data = ""
    if vals:
        data = ("/".join(dt) + f" 数据 {vals[0]}" if len(vals) == 1
                else " · ".join(f"{k} {v}" for k, v in dt.items() if v))
    tone = "buy" if n_buy else ("sell" if n_sell else "none")
    summary = "v14–v20 今日操作（★=该腿）：" + "；".join(
        f"{r['c']} {r['a']}" for r in rows)
    # 图墙：只收「文件确实存在」的图，缺图不至于在看板上留破图框。
    figs = [{"path": rel, "cap": cap} for rel, cap in _VOL_FIGS
            if os.path.exists(os.path.join(_ROOT, rel.replace("/", os.sep)))]
    return _entry(
        "vol_variants", "波动类变种操作一览 · v14–v20 单标的策略", as_of,
        f"买 {n_buy} / 卖 {n_sell}", tone, summary, rows,
        [["策略", str(len(rows))],
         ["目标腿", "v14 A500 · v15 沪深300 · v16 纳指100 · v17 标普500 · "
                    "v18 科创50 · v19 恒生科技 · v20 黄金（一策略一腿）"],
         ["买入", str(n_buy)], ["卖出", str(n_sell)]],
        note="参考板块5「技术指标」的列法（一行一个策略，列=策略/动作统计/动作/明细）。"
             "2026-11-18 起本卡只列 **v14~v20 七个单标的专用策略**（一策略一腿）："
             "v14 中证A500=E3(V3+V2+V12)、v15 沪深300=MIN2(V12∩V2)、"
             "v16 纳指100=MA200 切换（上=V10 波动层 / 下=V12 八态）、"
             "v17 标普500=V12、v18 科创50=E2×TV20、v19 恒生科技=V3、"
             "v20 黄金=V5.1（风险预算 × 实际利率闸门）。"
             "**2026-11-19 研究重跑**：v12 把「慢跌」改到 0% 后，沪深300/标普500/科创50 的 ★1 都换成 V12；"
             "黄金腿纳入重构后的 v5_gold_score 后 ★1 换成 V5.1。"
             "**2026-11-20**：v12 的美股两腿改 C5（无死区）后，纳指 ★2 由 V9 换成 V12，"
             "`SW MA200→V12` 成为纳指的超越项，v16 据此由「纯 V10」改为「MA200 切换」；"
             "v14(E3)/v15(MIN2)/v16(SW-v12)/v18(E2×TV20) 是 `research/beat_best/_beat_best_top3.md` 的研究超越项；"
             "v17/v19 两条是「研究确认无提升、保持当前 ★1」的单标的封装；"
             "v20 就是 best_by_leg §0 的黄金 ★1 本身（重跑后无候选通过双闸门）。"
             "v21 利弗莫尔关键点（用户视频口径）**未稳定前不进本卡**，"
             "其产物与 7 腿验证只在策略目录。"
             "★ = 该策略唯一目标腿。"
             "v2/v3/v4/v6/v9/v12 已从本卡移除（其 daily 产物与 PNG 仍由 "
             "`run_daily_vol_legs.ps1` 照常生成，只是不再进看板）。"
             "括号里是仓位状态（买入/卖出/持有/空仓）；有**待成交决策**时写成「当前 → 目标」，不是 0/1。"
             f"表下 {len(figs)} 张图 = 各策略目标腿的长期净值/仓位图，由"
             " `run_daily_vol_legs.ps1` 第 3 步每日重画（近三年版在各自策略 research/ 下）。",
        src=" · ".join(srcs),
        data=data,
        cols=["变种", "动作统计", "动作", "操作明细（★=重点标的，其余仅供参考）"],
        all_rows=True,
        imgs=figs,
    )


def parse_bond_cash(_cfg):
    """板块6 稳健资产：债腿 30pp（**35/65 固定中性**）+ 现金腿 10pp（5+5）。

    全部读数取自 ``strategies/bond_cash/daily/_bond_cash_daily.json``（阈值与档位由
    ``_daily_scan.py`` 算，看板**不复刻**任何判断，避免两边漂移）。

    动作口径：默认 ``gate_on=False`` —— 债腿固定配比**每天都是「持有」**，不做择时。
    动量开关只作「影子档位」列出来（若开议会处于哪一档），不产生买卖动作。

    配比文字全部取自产物的 ``config.core_w`` / ``config.base_w``，不在这里写死比例
    （2026-11-20 由 70/30 改为 35/65 时，就是靠这一点避免看板与规则源漂移）。
    """
    daily = _cfg["daily"]
    path = os.path.join(_ROOT, daily["signal_json"])
    d = json.loads(_read(path))
    as_of = d.get("as_of")
    g = d.get("gate") or {}
    bond = d.get("bond") or {}
    cash = d.get("cash") or {}
    cf = d.get("config") or {}
    cw = float(cf.get("core_w") or 0.35)
    bw = float(cf.get("base_w") or (1.0 - cw))
    tilt = ("固定中性" if abs(cw - 0.35) < 1e-9
            else "固定哑铃" if abs(cw - 0.70) < 1e-9 else "固定配比")

    def _pp(m):
        return " + ".join(f"{float(v):.0f}" for v in (m.get("items") or {}).values())

    rows = []
    for r in d.get("rows") or []:
        role = r.get("role")
        pp = float(r.get("pp") or 0)
        act = r.get("act") if pp > 0 else "none"
        label = r.get("label") or ("—" if pp <= 0 else f"{pp:.1f}pp")
        bits = [r.get("role_label") or "", r.get("theme") or ""]
        r20, r250 = r.get("r20"), r.get("r250")
        if r20 is not None:
            bits.append(f"20日 {float(r20):+.2f}%")
        if r250 is not None:
            bits.append(f"250日 {float(r250):+.2f}%")
        if role in ("spare", "satellite", "excluded"):
            bits.append("不动手")
        rows.append(_row(r.get("code"), r.get("short") or r.get("name"), act, label,
                         " · ".join(b for b in bits if b)))

    mom = g.get("mom")
    shadow = "进攻档" if g.get("shadow") else "防守档"
    on = bool(g.get("on"))
    gate_txt = (f"动量 {float(mom):+.3f}%（{g.get('win')} 日）· 影子**{shadow}**"
                if mom is not None else "动量 —")
    gate_txt += f" · 开关**未启用**（{tilt}）" if not on else " · 开关**已启用**"

    summary = (f"债腿 {float(bond.get('pp') or 0):.0f}pp = {_pp(bond)}"
               f"（{cw:.0%} 003376 / {bw:.0%} 011983）· 现金腿 {float(cash.get('pp') or 0):.0f}pp = {_pp(cash)}"
               f"（余额宝 / 存单）· {gate_txt}")

    return _entry(
        "bond_cash", "稳健资产 · 债腿 30pp + 现金腿 10pp", as_of,
        tilt if not on else f"开关·{shadow}", "none", summary, rows,
        [["债腿", f"{float(bond.get('pp') or 0):.0f}pp（{_pp(bond)}）"],
         ["现金腿", f"{float(cash.get('pp') or 0):.0f}pp（{_pp(cash)}）"],
         ["动量(参考)", f"{float(mom):+.2f}% · {shadow}" if mom is not None else "—"],
         ["连跌纪律", f"{(d.get('discipline') or {}).get('down_days', 0)} 日"]],
        note="债腿与现金腿**分格不混用**：债腿只用 6xx（久期来源）、现金腿只用 7xx（存单/短融不算久期）。"
             f"债腿 = {cw:.0%} 7-10 年国开 A（`003376`）+ {bw:.0%} 3-5 年政金 A（`011983`），"
             f"**{tilt}、每天不动手**。"
             "**2026-11-20 由 70/30 改为 35/65**（用户指令）：35% 是家族里 v1/v3 的「中性」长端档，"
             "改后 v0 是纯中性基准，回测（2021-12~2026-11）由 +4.54%/−2.38%/夏普 1.80 "
             "变为 +4.20%/−1.98%/**1.87** —— 少赚 0.34pp，回撤浅 0.40pp，夏普反而更高。"
             "动量开关（中债 7-10 近 63 日累计收益符号）默认**关闭**：本仓库用真实壳复现不出文档"
             "的正增量（零死区 10 年翻 50+ 次 vs 文档 20 次，成本吃穿毛 alpha；且文档的价值集中在"
             "2016-11 债灾，003376 成立 2016-09-26 + 63 日预热后首个信号已在债灾中段），"
             "详见 `strategies/bond_cash/backtest/_bond_cash_bt.md` §五。"
             "`006331` 国企债含权**不计入债腿**（与沪深300 +0.53、与转债 +0.74 → 计权益侧）。"
             "卫星 `511090`（≤20% 债腿，需 30Y−10Y 利差>0.40pp，本仓库无该数据 → 人工复核）与 "
             "`100050`（≤1/5 债腿，需美债 10Y 分位>90 且动量转正）默认 0pp。",
        src=_rel(path),
        data=f"净值 {as_of}（15 只场外累计净值）",
        cols=["代码", "标的", "动作", "配置/读数"],
        all_rows=True,
    )


def parse_bond_cash_v1(_cfg):
    """板块6 V1 卡片：债券晴雨表（温度）→ 四因子 → 情景 → 长端占比。

    读 ``strategies/bond_cash/v1_four_factor/daily/_v1_daily.json``；所有判断已在
    v1 的 engine 里做完，看板只做展示，不复刻规则。

    V1 不改 v0 的 40pp 额度：只在债腿 30pp 内部把「7-10 年 vs 3-5 年」的比例
    交给四因子打分决定（基准 35%、上限 70%、滞后带 5.25pp）。
    """
    path = os.path.join(_ROOT, _cfg["daily"]["signal_json"])
    d = json.loads(_read(path))
    as_of = d.get("as_of")
    t, s, pos, sc = (d.get(k) or {} for k in ("temp", "signal", "position", "scenario"))

    rows = []
    for r in d.get("rows") or []:
        rows.append(_row(r["code"], r.get("short") or r.get("name"), r.get("act", "none"),
                         r.get("label") or "—", r.get("note") or ""))

    summary = (f"合成温度 {t.get('composite')}（{t.get('band')}）· score {s.get('score', 0):+d} "
               f"**{s.get('side')}**（置信{'低' if s.get('conf') == 'low' else '中' if s.get('conf') == 'medium' else '高'}）"
               f"· 长端 {pos.get('cur')}% → 目标 {pos.get('target')}%"
               f"（{'触发调仓' if pos.get('moved') else '滞后带内不动'}）"
               f"· 未来20日债牛 {sc.get('bull')}%")

    return _entry(
        "bond_cash_v1", "V1 · 四因子久期档（债券晴雨表）", as_of,
        s.get("side") or "—", pos.get("act", "hold"), summary, rows,
        [["合成温度", f"{t.get('composite')} · {t.get('band')}"],
         ["覆盖", f"{t.get('coverage')}/4 项"],
         ["长端占比", f"{pos.get('cur')}% → {pos.get('target')}%"],
         ["20日债牛", f"{sc.get('bull')}%（n={sc.get('n')}）"]],
        note="规则来源 `G:/tradingagents/bond_strategy/bond_rules.md` v1.0。"
             "**v1 只改债腿内部的久期分布，不动 v0 的 40pp 额度**：长端 `003376` 占比 = "
             "35% + score/4 × (70%−35%) × 置信折扣，其余放 `011983`；现金腿 5pp 货基 + 5pp 存单不变。"
             "温度 40% 估值 / 20% 资金 / 25% 动能 / 15% 信用，**只定赔率不定方向**（R-03）；"
             "四因子互斥强制震荡观望（R-08），R-11 背离降级；情景只出概率、不出点位（R-12/R-15）。"
             "R-18 的 1.5pp 滞后带按带宽比例 35/10 放缩为 **5.25pp**（不放缩会天天翻仓，实测 72 次/年 → 14 次/年）。"
             "**回测诚实结论**：长窗 2018-08~ 固定哑铃 +3.83%/−4.69%，V1 +2.94%/−4.47%（138 次换手）；"
             "现行窗 2023-07~ 固定哑铃 +4.45%/−2.38%，V1 +3.82%/−2.14%（74 次）。"
             "V1 以收益换回撤，在债牛里跑不赢固定哑铃 —— 四因子天然互斥（估值偏贵时趋势偏多）"
             "使多数日子回到 35% 中性。信用因子依赖 akshare 曲线，2023 年前不可用（覆盖 3/4）。",
        src=_rel(path),
        data=" · ".join(f"{k} {v}" for k, v in (d.get("sources") or {}).items()),
        cols=["项", "读数", "动作", "说明"],
        all_rows=True,
    )


def parse_bond_cash_v2(_cfg):
    """板块6 V2 对照卡：横截面双动量轮动（默认 daily.enabled=false，不进看板）。"""
    path = os.path.join(_ROOT, _cfg["daily"]["signal_json"])
    d = json.loads(_read(path))
    m = d.get("momentum") or {}
    rows = [_row(r["code"], r.get("short") or r.get("name"), r.get("act", "none"),
                 r.get("label") or "—", r.get("note") or "") for r in d.get("rows") or []]
    held = d.get("held") or {}
    return _entry(
        "bond_cash_v2", "V2 · 横截面双动量轮动（对照，未启用）", d.get("as_of"),
        "对照策略", "none",
        f"近 {m.get('win')} 日动量排名取前 {m.get('top_n')} 名等权 · 持仓 " +
        ("、".join(f"{k} {v:.0%}" for k, v in held.items()) if held else "—") +
        f" · 上次调仓 {(d.get('last_rebalance') or {}).get('date')}",
        rows,
        [["动量窗", f"{m.get('win')} 日"], ["持仓数", str(m.get("top_n"))],
         ["防御", str(m.get("defensive"))],
         ["下次调仓", d.get("next_rebalance") or "—"]],
        note="规则来源 Bond ETF Dual Momentum + MDPI FinTech 2026 Median（对照）。"
             "**回测结论为负**：2022-03~2026-11 双动量 top2 −1.74% / 回撤 −8.04%，"
             "v0 固定哑铃 +4.56% / −2.38%；零成本下也只有 +0.56%（满仓 003376 +4.86%）—— "
             "问题在信号本身而非成本。MDPI 的中位组在本市场也是 −1.87%，"
             "63/126/252 日 × top1/top2/median/bottom2 共 12 组全部 +0.24%~+0.91%，"
             "没有一组接近长端买入持有。`daily.enabled=false`：产物保留供研究，不进默认调度。",
        src=_rel(path),
        data="净值 " + ", ".join(f"{k} {v}" for k, v in (d.get("sources") or {}).items()),
        cols=["代码", "标的", "动作", "配置/动量"],
        all_rows=True,
    )


def parse_bond_cash_v3(_cfg):
    """板块6 V3 卡片：Carry + 趋势投票的久期择时。"""
    path = os.path.join(_ROOT, _cfg["daily"]["signal_json"])
    d = json.loads(_read(path))
    c, t, s = (d.get(k) or {} for k in ("carry", "trend", "signal"))
    rows = [_row(r["code"], r.get("short") or r.get("name"), r.get("act", "none"),
                 r.get("label") or "—", r.get("note") or "") for r in d.get("rows") or []]
    return _entry(
        "bond_cash_v3", "V3 · Carry + 趋势投票（久期择时）", d.get("as_of"),
        f"score {s.get('score', 0):+d}", s.get("act", "hold"),
        f"Carry {c.get('vote', 0):+d}（10Y−1Y {c.get('spread')}% / 3年分位 {c.get('pct')}%）"
        f" + Trend {t.get('vote', 0):+d} → score **{s.get('score', 0):+d}** · "
        f"长端 {s.get('cur')}% → 目标 {s.get('long_w')}%"
        f"（{'触发' if s.get('moved') else '滞后带内不动'}）",
        rows,
        [["Carry 票", f"{c.get('vote', 0):+d}（分位 {c.get('pct')}%）"],
         ["Trend 票", f"{t.get('vote', 0):+d}"],
         ["长端占比", f"{s.get('eff')}%"],
         ["下次调仓", s.get("next_rebalance") or "—"]],
        note="规则来源 Newfound Research (2018) carry-trend 双确认 + 兴业证券 (2026-05) "
             "利率 MA 投票久期轮动的可复现交集。**信号源是中债收益率曲线而非基金净值**"
             "（避开 v1 已知的净值噪声放大缺陷），月末采样、低换手。"
             "Carry = 10Y−1Y 利差近 3 年分位（≥66.7%→+1 / ≤33.3%→−1）；"
             "Trend = 10Y MA5<MA20 与 MA20<MA60 双确认；两票相加映射 0/17.5/35/52.5/70% 长端。"
             "回测 2023-05~2026-11：V3 +3.76% / −1.88% / 夏普 1.58，优于纯 Carry(+3.14%) 与纯 "
             "Trend(+3.18%)，但低于 v0 固定哑铃 +4.39%/−2.38%，夏普低于固定中性 35/65 的 1.70 —— "
             "样本仅 3.5 年且几乎全债牛，不能外推到完整周期。做空/ECFC/LPPLS 未实现。",
        src=_rel(path),
        data=" · ".join(f"{k} {v}" for k, v in (d.get("sources") or {}).items()),
        cols=["腿/信号", "标的", "动作", "配置/依据"],
        all_rows=True,
    )


def parse_bond_cash_v4(_cfg):
    """板块6 V4 卡片：Carry + Trend + Hedge 三票的久期择时（股票动量反向映射债券）。"""
    path = os.path.join(_ROOT, _cfg["daily"]["signal_json"])
    d = json.loads(_read(path))
    c, t, h, s = (d.get(k) or {} for k in ("carry", "trend", "hedge", "signal"))
    rows = [_row(r["code"], r.get("short") or r.get("name"), r.get("act", "none"),
                 r.get("label") or "—", r.get("note") or "") for r in d.get("rows") or []]
    return _entry(
        "bond_cash_v4", "V4 · 股债对冲三票（Carry+Trend+Hedge）", d.get("as_of"),
        f"score {s.get('score', 0):+d}", s.get("act", "hold"),
        f"Carry {c.get('vote', 0):+d}（10Y−1Y {c.get('spread')}% / 3年分位 {c.get('pct')}%）"
        f" + Trend {t.get('vote', 0):+d}"
        f" + Hedge {h.get('vote', 0):+d}（沪深300 252日 {(h.get('stock_ret') or 0):.2%} / "
        f"5年分位 {h.get('stock_pct')}%）"
        f" → score **{s.get('score', 0):+d}** · 长端 {s.get('cur')}% → 目标 {s.get('long_w')}%"
        f"（{'触发' if s.get('moved') else '滞后带内不动'}）",
        rows,
        [["Carry 票", f"{c.get('vote', 0):+d}（分位 {c.get('pct')}%）"],
         ["Trend 票", f"{t.get('vote', 0):+d}"],
         ["Hedge 票", f"{h.get('vote', 0):+d}（分位 {h.get('stock_pct')}%）"],
         ["长端占比", f"{s.get('eff')}%"],
         ["下次调仓", s.get("next_rebalance") or "—"]],
        note="规则来源 Newfound Research (2018) carry-trend-hedge 三票框架 + 国泰海通 (2026-06) "
             "股债增强配置。carry/trend 两票与 V3 逐行同源，只多一条 Hedge（沪深300 近 252 日收益的 "
             "5 年分位 ≥90%→−1 减久期 / ≤10%→+1 避险加久期）。"
             "**回测 2023-05~2026-11：V4 三票 +3.73%/−1.79%/夏普 1.61 vs V3 口径双票 "
             "+3.81%/−1.91%/1.63 —— Hedge 票独立贡献 −0.08pp，基本为零**（3.5 年里股票分位顶 291 天、"
             "分位底仅 3 天，Hedge 退化成「股票强势时减久期」的单边开关）。阈值 80/20 与 95/5 结论不变，"
             "只作研究对照。",
        src=_rel(path),
        data=" · ".join(f"{k} {v}" for k, v in (d.get("sources") or {}).items()),
        cols=["腿/信号", "标的", "动作", "配置/依据"],
        all_rows=True,
    )


def parse_bond_cash_v5(_cfg):
    """板块6 V5 卡片：波动率目标的久期敞口（不预测方向）。"""
    path = os.path.join(_ROOT, _cfg["daily"]["signal_json"])
    d = json.loads(_read(path))
    v, s = (d.get(k) or {} for k in ("vol", "signal"))
    rows = [_row(r["code"], r.get("short") or r.get("name"), r.get("act", "none"),
                 r.get("label") or "—", r.get("note") or "") for r in d.get("rows") or []]
    sig = "—" if v.get("sigma") is None else f"{v['sigma']:.2%}"
    return _entry(
        "bond_cash_v5", "V5 · 波动率目标（按 σ 缩放久期敞口）", d.get("as_of"),
        f"σ {sig}", s.get("act", "hold"),
        f"σ = max(σ21, σ63) = **{sig}**（σ21 {(v.get('sigma21') or 0):.2%} / "
        f"σ63 {(v.get('sigma63') or 0):.2%}）· "
        f"σ/目标 {(v.get('ratio') or 0):.2f}×"
        + (" · **熔断：长端清零**" if v.get("guard") else "")
        + f" → 长端 {s.get('cur')}% → 目标 {s.get('w_raw')}%"
          f"（{'触发' if s.get('moved') else '滞后带内不动'}）",
        rows,
        [["σ21 / σ63", f"{(v.get('sigma21') or 0):.2%} / {(v.get('sigma63') or 0):.2%}"],
         ["σ / 目标", f"{(v.get('ratio') or 0):.2f}×（目标 {v.get('target')}）"],
         ["熔断", "是" if v.get("guard") else "否"],
         ["长端占比", f"{s.get('eff')}%"],
         ["下次调仓", s.get("next_rebalance") or "—"]],
        note="规则来源 Moreira & Muir (2017) volatility-managed portfolios + 目标波动率资产组合管理"
             "（中国股/债/商品 7.5 年实证）+ 兴证 (2020-05) VaR 久期择时。"
             "**不预测方向，只按风险预算缩放**：w = 70% × min(1, 1.50%/σ)，σ>3.00% 时熔断清零。"
             "回测 2021-12~2026-11（5 年）：V5 +4.15%/−1.66%/夏普 1.80 vs v0 固定哑铃 "
             "+4.49%/−2.38%/1.79 —— **0.34pp 年化换 0.72pp 回撤，是风险预算不是 alpha**；"
             "反向对照（高波动加久期）+4.25%/−2.38%/1.66 证明回撤改善来自机制本身。"
             "目标波动调到 2.5% 会退化成「基本不动手」（平均长端 66.5%）。",
        src=_rel(path),
        data="净值 " + ", ".join(f"{k} {v2}" for k, v2 in (d.get("sources") or {}).items()),
        cols=["腿/信号", "标的", "动作", "配置/依据"],
        all_rows=True,
    )


def parse_bond_cash_v6(_cfg):
    """板块6 V6 卡片：双利差锚（期限利差定久期 × 信用利差定品种）。"""
    path = os.path.join(_ROOT, _cfg["daily"]["signal_json"])
    d = json.loads(_read(path))
    c, sl, cr, s = (d.get(k) or {} for k in ("carry", "slope", "credit", "signal"))
    rows = [_row(r["code"], r.get("short") or r.get("name"), r.get("act", "none"),
                 r.get("label") or "—", r.get("note") or "") for r in d.get("rows") or []]
    return _entry(
        "bond_cash_v6", "V6 · 双利差锚（期限利差 × 信用利差）", d.get("as_of"),
        f"score {s.get('score', 0):+d}", s.get("act", "hold"),
        f"久期：Carry {c.get('vote', 0):+d}（分位 {c.get('pct')}%）"
        f" + Slope {sl.get('vote', 0):+d}（Δ{sl.get('ds_bp')}bp）→ score **{s.get('score', 0):+d}**"
        f" · 长端 {s.get('cur')}% → 目标 {s.get('long_w')}%"
        f"（{'触发' if s.get('moved') else '滞后带内不动'}）"
        f" · 品种：Δ信用利差 {cr.get('dcs_bp')}bp → 底仓 **{cr.get('leg_name')}**",
        rows,
        [["Carry 票", f"{c.get('vote', 0):+d}（分位 {c.get('pct')}%）"],
         ["Slope 票", f"{sl.get('vote', 0):+d}（Δ{sl.get('ds_bp')}bp）"],
         ["Credit 票", f"{cr.get('vote', 0):+d}（Δ{cr.get('dcs_bp')}bp）"],
         ["长端占比", f"{s.get('eff')}%"],
         ["底仓", f"{cr.get('leg_name')}（{cr.get('leg')}）"]],
        note="规则来源 国信证券《基于双利差锚的周期划分和债券投资策略》(2021) + "
             "国泰海通《信用—利率与久期的双层策略》(2026)。两轴正交：久期轴 = carry（10Y−1Y 近 3 年分位）"
             "+ slope（10Y−1Y 的 60 日变化，±10bp 死区）；品种轴 = AAA3Y−国债3Y 的 60 日变化"
             "（≤−5bp 信用收窄→000032 / ≥+5bp 走阔→011983 / 中间维持）。"
             "**回测 2023-05~2026-11：V6 双轴 +3.14%/−1.58%/1.52 同时输给只久期轴 +3.79%/1.69、"
             "只品种轴 +4.13%/1.63 与 v0 +4.39%/1.67 —— 双轴互相抵消**（久期轴把长端压到平均 25.8%、"
             "品种轴 71% 时间在信用债），40bp 成本下崩到 +1.79%。唯一亮点：2025 债熊年 +0.65% vs v0 −0.13%。"
             "双利差锚适合做周期判断，不适合月度券种轮动。",
        src=_rel(path),
        data=" · ".join(f"{k} {v}" for k, v in (d.get("sources") or {}).items()),
        cols=["腿/信号", "标的", "动作", "配置/依据"],
        all_rows=True,
    )


def parse_bond_cash_v8(_cfg):
    """板块6 V8 卡片：久期阶梯结构预算 + 同档择一 + 进攻档煞车 + 长端趋势闸门。

    读 ``strategies/bond_cash/v8_duration_ladder/daily/_v8_daily.json``；权重、择壳、
    闸门状态都在 v8 的 engine 里算完，看板只展示。

    **口径提示**：v8 的 30pp 债腿内部是**六腿权重**（0~100%，和 = 100%），与 v0/v1/v3 的
    「长端占比」不是同一个量；闸门离场时长端那部分退到货基，会在卡里单独标出来。
    """
    path = os.path.join(_ROOT, _cfg["daily"]["signal_json"])
    d = json.loads(_read(path))
    tg = d.get("trend_gate") or {}
    sig = d.get("signal") or {}
    wh = d.get("weights_held") or {}
    pick = d.get("pick") or "—"

    rows = [_row(r["code"], r.get("short") or r.get("name"), r.get("act", "none"),
                 r.get("label") or "—", r.get("note") or "") for r in d.get("rows") or []]

    mom = tg.get("mom")
    mom_txt = f"{mom:+.2f}%" if mom is not None else "—"
    gate_txt = ("未启用" if not tg else
                (f"长端在仓（{tg.get('mom_win')} 日动量 {mom_txt}）"
                 if tg.get("state") else
                 f"长端离场 → 退 {tg.get('to')}（动量 {mom_txt}）"))
    long_w = float(wh.get("003376") or 0.0)
    summary = (f"六腿权重：长端 `003376` **{long_w:.0%}** · 中端持 `{pick}` · "
               f"债腿波动 {d.get('vol')}% · 长端闸门 {gate_txt} · "
               f"{'今日调仓' if sig.get('moved') else '不动手'}"
               f"（{sig.get('next_rebalance') or '—'}）")

    return _entry(
        "bond_cash_v8", "V8 · 久期阶梯风险预算（六腿 0~100%）", d.get("as_of"),
        f"长端 {long_w:.0%}", "hold",
        summary, rows,
        [["债腿额度", f"{d.get('bond_pp')}pp（现金 {d.get('cash_pp')}pp 固定）"],
         ["中端择壳", f"{pick}"],
         ["长端闸门", gate_txt],
         ["债腿波动", f"{d.get('vol')}%（60 日年化）"]],
        note="**V8 与 v0/v1/v3 的区别：它解的是「六条腿各配多少」（0~100%，和 100%），"
             "不是「长端占债腿几成」。** 结构预算长端 20% / 中端 45%（同档择一）/ 短端 5% / "
             "信用 30%（2/3 + 1/3），现金 10pp 仍固定在外。动态只两处：中端按 504 日夏普换壳"
             "（死区 0.15）、长端按 σ252/σ60 只煞车不追高。"
             "**回测（2021-12~2026-11，债腿内部）夏普 1.83 vs v0 固定中性 35/65 的 1.87** —— "
             "⚠ 2026-11-20 v0 由 70/30 改成 35/65 后基准变强，v8 已**不跑赢基准**（对旧 70/30 是 1.80，"
             "当初只赢 0.03）。真正起作用的是「长端降到 20%」的结构，不是择时（关掉两项动态是 1.85）；"
             "而 35/65 用一个数字就做到了同一件事。长窗（2019-06 起，中端只能用 `001512`）1.58 vs 1.41，"
             "长窗反而跑赢 —— 对底仓壳质量敏感，两个窗口结论相反，别只引一个。"
             "长端趋势闸门（2026-11-20 装）在**费前**把夏普抬到 1.98，但按 A 类真实赎回费阶梯"
             "（<7 日 1.50% / 7~30 日 0.50% / 30~365 日 0.10%）逐笔计后只有 1.65，"
             "**低于不开闸门**；它买的是「回撤更浅」，不是更高夏普。",
        src=_rel(path),
        data=" · ".join(f"{k} {v}" for k, v in (d.get("sources") or {}).items()),
        cols=["腿", "标的/信号", "动作", "占总资产"],
        all_rows=True,
    )


def parse_bond_cash_v7(_cfg):
    """板块6 **默认策略**卡片：四门派 ERC 风险预算（利率 / 信用 / 转债）+ 风险贡献分解。

    读 ``strategies/bond_cash/v7_risk_budget/daily/_v7_daily.json``；配比、ERC 不动点解、
    风险贡献全在 v7 的 engine 里算完，看板只展示（不复刻规则，避免两边漂移）。

    2026-11-21 起 V7 是板块6 的默认策略、v0（35/65 固定中性）转为基准 —— 卡面同时给
    「债腿怎么分」与「谁在偷风险」，与基准的相对表现（短窗落后、长窗领先）写在 note 里。
    """
    path = os.path.join(_ROOT, _cfg["daily"]["signal_json"])
    d = json.loads(_read(path))
    sig = d.get("signal") or {}
    wh = d.get("weights_held") or {}
    held = d.get("held") or {}
    tgt = d.get("target") or {}
    rc = d.get("rc_held") or {}
    diff = sig.get("diff_pp") or {}
    nm = {"rate": "利率", "credit": "信用", "conv": "转债"}

    rows = [_row(r["code"], r.get("short") or r.get("name"), r.get("act", "none"),
                 r.get("label") or "—", r.get("note") or "") for r in d.get("rows") or []]

    wtxt = " / ".join(f"{nm.get(k, k)} {float(v):.0%}" for k, v in wh.items())
    rctxt = " / ".join(f"{nm.get(k, k)} {float(v):.0f}%" for k, v in rc.items())
    top = max(rc, key=rc.get) if rc else None
    summary = (f"债腿权重 {wtxt} · 风险贡献 {rctxt}"
               f"（最吃风险：**{nm.get(top, top)}**）· 债腿波动 {d.get('vol')}% · "
               f"{'今日调仓' if sig.get('moved') else '不动手'}"
               f"（{sig.get('next_rebalance') or '—'}）")

    return _entry(
        "bond_cash_v7", "V7 · 门派风险预算（ERC，板块默认）", d.get("as_of"),
        f"利率 {float(wh.get('rate') or 0):.0%}", "hold",
        summary, rows,
        [["债腿额度", f"{float(d.get('bond_pp') or 0):.0f}pp"
                     f"（现金 {float(d.get('cash_pp') or 0):.0f}pp 固定、不进 ERC）"],
         ["当前→目标",
          " / ".join(f"{nm.get(k, k)} {float(held.get(k) or 0):.2f}→"
                     f"{float(tgt.get(k) or 0):.2f}" for k in ("rate", "credit", "conv"))],
         ["偏差", " / ".join(f"{nm.get(k, k)} {float(v):+.2f}pp"
                            for k, v in diff.items()) or "—"],
         ["债腿波动", f"{d.get('vol')}%（60 日年化）"]],
        note="**2026-11-21 起这是板块6 的默认策略**：债腿 30pp 按四门派等风险贡献（ERC）分配，"
             "现金 10pp 固定在外，月末调仓、平时不动手；v0（35% `003376` + 65% `011983` 固定中性）"
             "转为**基准**，不再单独出卡。"
             "它的产出有三件：① 目标配比；② **风险贡献分解**（谁在偷风险 —— 利率债受下限 50% 绑定，"
             "风险贡献长期高于其权重；转债权重只有 5% 却能贡献 17~26%）；③ 与基准的相对位置。"
             "**相对基准 v0 35/65**：短窗（近 5 年）夏普 **1.77 < 1.87**，"
             "长窗（9.9 年）**1.92 vs 1.31、回撤 −2.03% vs −4.26%、最差年 +0.33% vs −1.78%** —— "
             "两类窗口结论相反，选它靠的是长窗与「最差年不亏」，不是短期更强。"
             "⚠ 转债腿只有 `470058` 一只主动型壳（中证转债指数接口未落地），信用腿 `000032` 是主动信用纯债；"
             "协方差窗 60 日、收缩 δ=0.30，参数为样本内标定。",
        src=_rel(path),
        data=" · ".join(f"{k} {v}" for k, v in (d.get("sources") or {}).items()),
        cols=["门派/腿", "标的", "动作", "占总资产"],
        all_rows=True,
    )


def parse_bond_cash_v5(_cfg):
    """板块6 V5 卡片：按已实现波动缩放长端敞口（目标 1.5%，上限 70%，σ>3% 清零）。

    读 ``strategies/bond_cash/v5_vol_target/daily/_v5_daily.json``。与 v0/v1/v3 同样的
    「长端占比」口径（0~100%），只是这个占比由波动反推，不由方向票决定。

    **口径提示**：2026-11-20 v0 基准改为 35/65 后，V5 的夏普（1.80）低于基准（1.87）——
    它用动态缩放复制了基准的敞口位置，多付换手却没抬夏普。卡里如实标注。
    """
    path = os.path.join(_ROOT, _cfg["daily"]["signal_json"])
    d = json.loads(_read(path))
    v = d.get("vol") or {}
    sig = d.get("signal") or {}
    eff = float(sig.get("eff") or 0)
    pending = sig.get("pending")
    ratio = float(v.get("ratio") or 0)
    guard = bool(v.get("guard"))

    rows = [_row(r["code"], r.get("short") or r.get("name"), r.get("act", "none"),
                 r.get("label") or "—", r.get("note") or "") for r in d.get("rows") or []]

    summary = (f"已实现波动 **{float(v.get('sigma') or 0) * 100:.2f}%**（目标 "
               f"{float(v.get('target') or 0) * 100:.2f}% 的 **{ratio:.2f}×**）→ 长端 "
               f"**{eff:.1f}%**"
               + (" · ⛔ 熔断清零" if guard else "")
               + f" · {'今日调仓' if sig.get('moved') else '不动手'}"
               f"（{sig.get('next_rebalance') or '—'}）")

    return _entry(
        "bond_cash_v5", "V5 · 波动率目标（长端占比由 σ 反推）", d.get("as_of"),
        f"长端 {eff:.0f}%", sig.get("act", "none"), summary, rows,
        [["σ21 / σ63", f"{float(v.get('sigma21') or 0) * 100:.2f}% / "
                       f"{float(v.get('sigma63') or 0) * 100:.2f}%"],
         ["当前 / 目标", f"{eff:.1f}% → {float(pending or 0):.1f}%"],
         ["熔断", "⛔ 已触发（σ>3% 清零）" if guard else "未触发（σ≤3%）"],
         ["调仓", "月末最后一个交易日" if not sig.get("is_rebalance") else "今天"]],
        note="**口径与 v8 不同：V5 解的是「长端占债腿几成」（0~70%），v8 解的是六条腿各配多少。** "
             "规则：σ = max(σ21, σ63) 年化；`w = 70% × min(1, 1.50%/σ)`，clip 到 [0,70%]，"
             "σ>3% 直接清零；月末调仓 + 滞后带（避免噪声来回切）。"
             "**回测（2021-12~2026-11）**：+4.15% / −1.66% / 夏普 **1.80**，"
             "vs 基准 v0 35/65 **+4.15% / −1.98% / 1.87** —— ⚠ 年化相同、**夏普低 0.07，跑不赢基准**。"
             "机制对照（反过来「高波动加久期」）+4.25% / −2.38% / 1.66：收益几乎相同、回撤差 0.72pp，"
             "说明「回撤更浅」确实来自按波动缩放，不是运气；但也说明**它只是在复制敞口位置**，"
             "而 35/65 用固定配比免费拿到了同样的位置。定位是**风险预算**，不是 alpha。",
        src=_rel(path),
        data=" · ".join(f"{k} {n}" for k, n in (d.get("sources") or {}).items()),
        cols=["腿", "标的/信号", "动作", "占总资产"],
        all_rows=True,
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
    "v1_1_ma20": parse_v1_1_ma20,
    "v3_macd_boll": parse_v3_macd_boll,
    "permanent": parse_permanent,
    "vol_legs": parse_vol_legs,
    "bond_cash": parse_bond_cash,
    "bond_cash_v1": parse_bond_cash_v1,
    "bond_cash_v2": parse_bond_cash_v2,
    "bond_cash_v3": parse_bond_cash_v3,
    "bond_cash_v4": parse_bond_cash_v4,
    "bond_cash_v5": parse_bond_cash_v5,
    "bond_cash_v6": parse_bond_cash_v6,
    "bond_cash_v7": parse_bond_cash_v7,
    "bond_cash_v8": parse_bond_cash_v8,
}


# ---------------------------------------------------------------- 归档
def _normalize_vol_history(db, entries):
    """2026-11-18 板块2 移除 v2/v3/v4/v6/v9/v12 后，把历史归档里的旧卡片一起收敛。

    不这么做的话：翻历史日期还能看到那六行，且 `_img_assets()` 会把旧 12 张图
    一并 base64 内嵌进 HTML（多 5MB）。行/图按当前 `_VOL_VARIANTS` / `_VOL_FIGS`
    过滤，描述性字段用当前卡片覆盖，历史数据字段（as_of/badge/tone）保留。
    """
    cur = (entries or {}).get("vol_variants") or {}
    allow_rows = {lab for _vx, lab, _rel, _em in _VOL_VARIANTS}
    allow_figs = {rel for rel, _cap in _VOL_FIGS}
    for run in (db.get("runs") or {}).values():
        card = (run.get("strategies") or {}).get("vol_variants")
        if not card:
            continue
        if allow_rows:
            card["rows"] = [r for r in (card.get("rows") or [])
                            if (r.get("c") or "") in allow_rows]
        if allow_figs:
            card["imgs"] = [x for x in (card.get("imgs") or [])
                            if (x.get("path") or "") in allow_figs]
        for fld in ("name", "summary", "note", "data", "src", "cols", "stats"):
            if fld in cur:
                card[fld] = cur[fld]


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
    _normalize_vol_history(db, entries)
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
    _normalize_vol_history(
        db, ((db.get("runs") or {}).get(max(db.get("runs") or {})) or {}).get("strategies") or {})
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
      // 小表不筛选：池子固定的策略（7 腿 / 5 只红利 / 9 项基准）空仓行的读数本身就是
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
