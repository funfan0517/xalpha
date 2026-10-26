#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""每日操作汇总看板 · 跨策略归档 + 单文件 HTML

把 ``pipeline/strategies.json`` 里注册的每个策略的「每日产物」归一化成同一结构
（代码 / 名称 / 动作 / 说明），按运行日归档到 ``data/_daily_digest.json``，
并渲染 ``data/_daily_digest.html``（单文件、无外链，双击可看）。

产物格式优先读结构化文件（json / jsonl）；``ema_cross`` 与 ``tech_indicators``
目前只落 markdown，则解析其自产表格（格式由本仓库脚本生成，稳定且简单）。
任一策略解析失败只影响它自己——卡片标「解析失败」，不中断其它策略。

用法::

    python pipeline/daily_digest.py              # 归档今天 + 重绘看板
    python pipeline/daily_digest.py --html-only  # 只按已有历史重绘
    python pipeline/daily_digest.py --date 2026-10-20   # 指定归档日（补录用）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime

_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_DIR)
CFG = os.path.join(_DIR, "strategies.json")
DB = os.path.join(_ROOT, "data", "_daily_digest.json")
HTML = os.path.join(_ROOT, "data", "_daily_digest.html")

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


def _entry(key, name, as_of, badge, tone, summary, rows, stats=None,
           note="", src="", error="", cols=None):
    return {
        "key": key,
        "name": name,
        "as_of": as_of,
        "badge": badge,
        "tone": tone if tone in ACT else "none",
        "summary": summary,
        "stats": stats or [],
        "rows": rows,
        "cols": cols or ["代码", "名称", "动作", "说明"],
        "note": note,
        "src": src,
        "error": error,
    }


def _row(code, name, act, label, detail=""):
    return {"c": code or "—", "n": name or "—", "act": act if act in ACT else "none",
            "a": label or "", "d": detail or ""}


# ---------------------------------------------------------------- 各策略解析
def parse_lights(_cfg):
    path = os.path.join(_ROOT, "strategies", "lights", "daily", "_lights_scan_out.jsonl")
    raw = [r for r in _rows_of_jsonl(path) if r.get("ok")] if os.path.exists(path) else []
    if not raw:
        raise ValueError("产物缺失或全池失败")
    as_of = _first_date(*[r.get("date") for r in raw])
    gate_ok = [r for r in raw if r.get("gate_pass")]
    targets = [r for r in raw if float(r.get("target") or 0) > 0]

    rows = []
    for r in raw:
        tgt = float(r.get("target") or 0)
        if tgt > 0:
            row = _row(r.get("inner"), r.get("theme"), "buy", "建仓/持有",
                       f"得分 {r.get('score')} · {r.get('why', '')}")
        elif r.get("gate_pass"):
            row = _row(r.get("inner"), r.get("theme"), "watch", "门槛过但不建仓",
                       f"得分 {r.get('score')} · {r.get('why', '')}")
        else:
            row = _row(r.get("inner"), r.get("theme"), "none", "目标空仓",
                       f"未过：{'、'.join(r.get('failed') or []) or '灯未达标'}")
        rows.append(row)

    tgt_txt = "、".join(f"`{r['inner']}` {r.get('theme', '')}" for r in targets) or "（空仓）"
    return _entry(
        "lights", "亮灯策略", as_of,
        f"建仓 {len(targets)}" if targets else "空仓",
        "buy" if targets else "none",
        f"门槛全过 {len(gate_ok)}/{len(raw)} · 目标建仓 {len(targets)} 只：{tgt_txt}",
        rows,
        [["全池", str(len(raw))], ["门槛全过", str(len(gate_ok))],
         ["目标建仓", str(len(targets))]],
        note="盘中运行时用未收盘 bar，量能类灯会随尾盘变化；分级来自 _lights_active.json 快照。",
        src=_rel(os.path.join(_ROOT, "strategies", "lights", "daily", "_signal_report.md")),
    )


def parse_momentum(_cfg):
    path = os.path.join(_ROOT, "strategies", "momentum_rotation", "daily", "_mom_signal.json")
    d = json.loads(_read(path))
    as_of = d.get("as_of")
    tgt = [str(x) for x in (d.get("target_if_rebalance_today") or [])]
    hold = [str(x) for x in (d.get("holdings") or [])]
    rebal = bool(d.get("is_rebalance_day"))

    rows = []
    for s in d.get("snapshot") or []:
        code = str(s.get("proxy"))
        in_t, in_h = code in tgt, code in hold
        if in_t and in_h:
            act, label = "hold", "持有 · 在目标内"
        elif in_t:
            act, label = "buy", "新入选"
        elif in_h:
            act, label = "hold", "持有 · 预演剔除"
        else:
            act, label = "none", "未入选"
        rows.append(_row(
            code, s.get("name"), act, label,
            f"动量 {float(s.get('mom120') or 0) * 100:+.2f}% · "
            f"MA20 {round(float(s.get('ma20') or 0), 3)} · "
            f"收盘 {s.get('close')} · 场外 {s.get('off')}",
        ))

    if rebal:
        parts = []
        for a in d.get("actions") or []:
            parts.append(f"{a.get('action', '')} {a.get('code', '')}")
        summary = "调仓日：" + ("；".join(parts) if parts else "维持现有持仓")
        badge, tone = "调仓", "buy"
    else:
        summary = (f"非调仓日，无需操作 · 持仓 {('、'.join('`' + h + '`' for h in hold)) or '空仓'} · "
                   f"距下次调仓 {d.get('next_rebalance_in_trading_days')} 个交易日")
        if tgt and set(tgt) != set(hold):
            summary += f" · 预演目标 {('、'.join('`' + t + '`' for t in tgt))}"
        badge, tone = "不动", "hold"

    return _entry(
        "momentum_rotation", "全球动量轮动", as_of, badge, tone, summary, rows,
        [["当前持仓", "、".join(hold) or "空仓"],
         ["预演目标", "、".join(tgt) or "空仓"],
         ["距调仓", f"{d.get('next_rebalance_in_trading_days')} 日"]],
        note="信号用场内代理日线，执行走场外主仓；10 交易日再平衡。",
        src=_rel(os.path.join(_ROOT, "strategies", "momentum_rotation", "daily", "_signal_report.md")),
    )


def parse_ema_cross(_cfg):
    path = os.path.join(_ROOT, "strategies", "ema_cross", "daily", "_signal_report.md")
    table = _table(_read(path).splitlines())
    if not table:
        raise ValueError("markdown 表格解析为空")

    rows, as_of = [], None
    for cells in table:
        if len(cells) < 7:
            continue
        head, close = cells[0], cells[1]
        m = CODE_RE.search(head)
        code = m.group(1) if m else head
        name = head.split("`")[0].strip()
        as_of = as_of or _first_date(close)
        sig, action, state = cells[5], cells[6], cells[4]
        if "金叉" in sig:
            act, label = "buy", "买入"
        elif "死叉" in sig:
            act, label = "sell", "卖出"
        elif state.startswith("持仓"):
            act, label = "hold", "持有"
        else:
            act, label = "none", "空仓"
        rows.append(_row(code, name, act, label,
                         f"{sig if sig != '—' else '无信号'} · {cells[3]} · {state}"))

    buys = [r for r in rows if r["act"] == "buy"]
    sells = [r for r in rows if r["act"] == "sell"]
    if buys or sells:
        badge, tone = f"买 {len(buys)} / 卖 {len(sells)}", "buy" if buys else "sell"
        summary = "新信号：" + "；".join(f"{r['a']} {r['c']}" for r in buys + sells)
    else:
        badge, tone = "无信号", "hold"
        summary = f"{len(rows)} 只 A 类全部无新信号（{sum(1 for r in rows if r['act'] == 'hold')} 只持仓、其余空仓）"
    return _entry(
        "ema_cross", "双均线趋势", as_of, badge, tone, summary, rows,
        [["扫描", str(len(rows))], ["持仓", str(sum(1 for r in rows if r["act"] == "hold"))],
         ["空仓", str(sum(1 for r in rows if r["act"] == "none"))]],
        note="解析自 _signal_report.md（该策略只落 markdown）。",
        src=_rel(path),
    )


def parse_right_side(_cfg):
    path = os.path.join(_ROOT, "strategies", "right_side", "daily", "_right_side_scan_out.jsonl")
    raw = [r for r in _rows_of_jsonl(path) if r.get("ok")] if os.path.exists(path) else []
    if not raw:
        raise ValueError("产物缺失或全池失败")
    as_of = _first_date(*[r.get("date") for r in raw])

    tone_map = {"READY": "buy", "WATCH": "watch", "VETO": "avoid", "NO": "none"}
    label_map = {"READY": "买点", "WATCH": "差一票", "VETO": "被否决", "NO": "未达标"}
    rows = []
    for r in raw:
        st = str(r.get("status") or "NO")
        miss = "、".join(r.get("missing") or [])
        detail = r.get("action") or ""
        if miss and st != "NO":
            detail = f"{detail} · 缺：{miss}"
        rows.append(_row(r.get("inner"), r.get("theme"), tone_map.get(st, "none"),
                         label_map.get(st, st), detail))

    ready = [r for r in rows if r["act"] == "buy"]
    watch = [r for r in rows if r["act"] == "watch"]
    if ready:
        summary = "右侧买点：" + "、".join(f"`{r['c']}` {r['n']}" for r in ready)
        badge, tone = f"买点 {len(ready)}", "buy"
    else:
        summary = "无标的五票全过 —— 空仓等待" + (
            "；观察：" + "、".join(f"`{r['c']}` {r['n']}" for r in watch) if watch else "")
        badge, tone = "空仓", "none"
    return _entry(
        "right_side", "右侧买入", as_of, badge, tone, summary, rows,
        [["全池", str(len(rows))], ["买点", str(len(ready))], ["差一票", str(len(watch))]],
        note="收盘前运行会自动退到上一交易日（不拿盘中未完成 bar 判信号）。",
        src=_rel(os.path.join(_ROOT, "strategies", "right_side", "daily", "_right_side_report.md")),
    )


def parse_core_rotation(_cfg):
    json_path = os.path.join(_ROOT, "strategies", "core_rotation", "daily", "_core_daily.json")
    md_path = os.path.join(_ROOT, "strategies", "core_rotation", "daily", "_daily_report.md")
    d = json.loads(_read(json_path))
    lines = _read(md_path).splitlines()
    as_of = _first_date(*(a.get("bar_date") for a in d.get("assets") or []), d.get("generated_at"))

    # md 的「六类目标配置」表：资产 / 场外主仓 / 区间 / 中枢 / 操作要点
    tips = {}
    for cells in _table(_section(lines, "情景判定")):
        if len(cells) >= 5:
            code = CODE_RE.search(cells[1])
            tips[cells[0]] = {"code": code.group(1) if code else "", "tips": cells[4]}

    weights = d.get("target_weights") or {}
    rows = []
    for a in d.get("assets") or []:
        name = a.get("name", "")
        t = tips.get(name, {})
        tip = t.get("tips") or ""
        if "完全规避" in tip:
            act, label = "avoid", "规避"
        elif "停加" in tip or "减" in tip:
            act, label = "watch", "停加/减"
        elif "偏多" in tip:
            act, label = "buy", "偏多配"
        else:
            act, label = "hold", "持有"
        key = a.get("key", "")
        rows.append(_row(t.get("code") or a.get("off"), name, act, label,
                         f"中枢 {weights.get(key, '—')}% · 收盘 {a.get('bar_close')} · "
                         f"20日 {float(a.get('d20') or 0):+.1f}% · {tip}"))

    summary = f"情景 {d.get('sc')} · " + " / ".join(f"{k} {v}%" for k, v in weights.items())
    return _entry(
        "core_rotation", "核心轮动", as_of, d.get("sc") or "—", "watch", summary, rows,
        [[k, f"{v}%"] for k, v in weights.items()],
        note=d.get("sc_note", ""),
        src=_rel(md_path),
    )


def parse_dividend(_cfg):
    # 2026-10-26 起每日操作是 V4 RSI14 择时（V0 打分退出流程，产物仍在 v0_rank/daily/）
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
    pool = sorted(d.get("pool") or [], key=lambda p: rsi.get(p.get("code")) or 999)

    rows = []
    for p in pool:
        code = p.get("code")
        w = pos.get(code, 0.0)
        act, label = ("hold", f"持有 {w * 100:.0f}%") if w > 0 else ("none", "空仓")
        r = rsi.get(code)
        rows.append(_row(code, p.get("name"), act, label,
                         f"RSI14 {r:.1f} · 板块均值 {d.get('board_rsi', '—')} · "
                         f"权重 {w * 100:.0f}%" if r is not None else
                         f"权重 {w * 100:.0f}%（今日无 RSI 快照）"))

    holds = "、".join(f"`{p.get('code')}` {float(p.get('weight') or 0) * 100:.0f}%"
                     for p in positions) or "空仓"
    preview = ""
    if prev.get("changed"):
        tgt = "、".join(f"`{t.get('code')}` {float(t.get('weight') or 0) * 100:.0f}%"
                       for t in prev.get("target") or []) or "空仓"
        preview = f" · 预览会变 → {tgt}"
    return _entry(
        "dividend", "红利 V4 · RSI 择时", as_of,
        tier.replace("（", " ").replace("）", ""), tone,
        f"板块 RSI {d.get('board_rsi', '—')} · 持 {holds}{preview}", rows,
        [["板块 RSI", str(d.get("board_rsi", "—"))], ["档位", tier],
         ["持仓", f"{len(positions)} 只 · {sum(pos.values()) * 100:.0f}%"],
         ["下次决策", str(d.get("next_decision") or "—")]],
        note="V4 · RSI14 择时：板块(池内 5 只)RSI 均值 ≥70 清仓 / ≥50 减半，横截面取 RSI 最低 2 只等权；"
             "月末收盘确认、次日开盘执行 —— 月中看到的是预览，不要提前动手。"
             "2026-10-26 起替代 V0 打分（V0 产物仍在 v0_rank/daily/，可手动重跑）。",
        src=_rel(os.path.join(_ROOT, daily["signal_md"])),
    )


def parse_growth_div(_cfg):
    path = os.path.join(_ROOT, "strategies", "growth_div_rotation", "daily", "_gd_signal.json")
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
        src=_rel(os.path.join(_ROOT, "strategies", "growth_div_rotation", "daily", "_signal_report.md")),
    )


def parse_tech_indicators(_cfg):
    path = os.path.join(_ROOT, "strategies", "tech_indicators", "daily", "_daily_recommend.md")
    lines = _read(path).splitlines()
    as_of = _first_date(next((l for l in lines if "全池最新交易日" in l), ""))
    detail = _table(_section(lines, "全标的明细"))
    if not detail:
        raise ValueError("markdown 明细表解析为空")

    # 按「策略」聚合：每个指标策略一行，行内列出它自己的买入/卖出标的
    names = ["MACD", "V3", "V6", "V7"]
    group = {n: {"buy": [], "sell": [], "hold": [], "none": []} for n in names}
    pool = 0
    for cells in detail:
        if len(cells) < 10:
            continue
        pool += 1
        code, name = cells[0], cells[1]
        for strat, a in zip(names, cells[6:10]):
            key = {"买入": "buy", "卖出": "sell", "持有": "hold"}.get(a, "none")
            group[strat][key].append(f"{code} {name}")

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
        if g["hold"] and len(g["hold"]) <= 10:
            parts.append("持有：" + "、".join(g["hold"]))
        if not parts:
            parts.append(f"无买卖信号（持有 {len(g['hold'])} / 空仓 {len(g['none'])}）")
        rows.append(_row(strat, f"买 {len(g['buy'])} · 卖 {len(g['sell'])} · "
                                f"持 {len(g['hold'])} · 空 {len(g['none'])}", act, label,
                         "；".join(parts)))

    n_buy = sum(len(group[n]["buy"]) for n in names)
    n_sell = sum(len(group[n]["sell"]) for n in names)
    active = [r for r in rows if r["act"] in ("buy", "sell")]
    if active:
        badge = f"买 {n_buy} / 卖 {n_sell}"
        tone = "buy" if n_buy else "sell"
        summary = "；".join(f"{r['c']}：{r['a']}" for r in active)
    else:
        badge, tone = "无动作", "none"
        summary = f"四策略对全池 {pool} 只均无买入/卖出信号"
    return _entry(
        "tech_indicators", "技术指标（四策略）", as_of, badge, tone, summary, rows,
        [["全池", str(pool)], ["买入", str(n_buy)], ["卖出", str(n_sell)]],
        note="解析自 _daily_recommend.md（该策略只落 markdown）；每行是一个指标策略对全池的操作汇总。",
        src=_rel(path),
        cols=["策略", "动作统计", "动作", "该策略的操作明细"],
    )


PARSERS = {
    "lights": parse_lights,
    "momentum_rotation": parse_momentum,
    "ema_cross": parse_ema_cross,
    "right_side": parse_right_side,
    "core_rotation": parse_core_rotation,
    "dividend": parse_dividend,
    "growth_div_rotation": parse_growth_div,
    "tech_indicators": parse_tech_indicators,
}


# ---------------------------------------------------------------- 归档
def collect():
    cfg = json.loads(_read(CFG))["strategies"]
    out = {}
    for key, meta in cfg.items():
        name = meta.get("name", key).split("（")[0]
        fn = PARSERS.get(key)
        if fn is None:
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


def render(db):
    dates = sorted(db.get("runs") or {})
    payload = {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "dates": dates,
               "runs": {d: db["runs"][d] for d in dates}}
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
.tbwrap{max-height:420px;overflow:auto}
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
      if(onlyAct.checked && r.act === 'none') return false;
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
    var open = (q || (s.rows || []).some(function(r){ return r.act !== 'none'; })) ? ' open' : '';
    var cls = s.error ? ' card err' : ' card';
    return '<div class="' + cls.trim() + '"><details' + open + '>' +
      '<summary><div class="ttl"><h2>' + esc(s.name) + '</h2>' +
      '<span class="badge ' + esc(s.tone) + '">' + esc(s.badge) + '</span></div>' +
      '<div class="sum">' + md(s.summary) + '</div>' +
      '<div class="meta">' + (s.as_of ? ('数据日 ' + esc(s.as_of)) : '无数据') +
      (s.rows ? (' · ' + s.rows.length + ' 行') : '') + '</div></summary>' +
      (chips ? '<div class="chips">' + chips + '</div>' : '') +
      body + (s.note ? '<div class="note">' + esc(s.note) + '</div>' : '') +
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
  function render(){
    var run = D.runs[cur]; if(!run) return;
    var q = qBox.value.trim().toLowerCase();
    var keys = Object.keys(run.strategies);
    sub.textContent = '数据日 ' + cur + ' · 生成 ' + (run.run_at || '') + ' · 共 ' + keys.length +
      ' 个策略 · 已归档 ' + D.dates.length + ' 天';
    kpis.innerHTML =
      '<div class="kpi"><b>' + keys.length + '</b><span>策略</span></div>' +
      '<div class="kpi"><b style="color:var(--buy)">' + cnt(run, 'buy') + '</b><span>买入 / 建仓</span></div>' +
      '<div class="kpi"><b style="color:var(--sell)">' + cnt(run, 'sell') + '</b><span>卖出 / 清仓</span></div>' +
      '<div class="kpi"><b style="color:var(--watch)">' + cnt(run, 'watch') + '</b><span>观察 / 差一步</span></div>' +
      '<div class="kpi"><b style="color:var(--avoid)">' + cnt(run, 'avoid') + '</b><span>规避</span></div>';
    app.innerHTML = keys.map(function(k){ return card(k, run.strategies[k], q); }).join('');
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
