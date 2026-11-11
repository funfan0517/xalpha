# -*- coding: utf-8 -*-
"""波动资产 · 各标的「当前最优策略」清单（只读产物，不重跑回测）。

回答一个问题：**七条腿，每一只标的上现在该用哪支策略，年化/回撤/夏普是多少。**

## 它怎么算「最优」

1. 把家族里**每个变种自己的回测产物**（`backtest/_*_bt.jsonl`）读进来，取各变种的**默认口径那一行**
   （变种名里带「默认」/`gate=是`/`mode=otc` 等，见下方 `ADAPTERS` 的筛选注释）；
2. 每个标的内部，只保留**样本年数 ≥ 该标的最长样本 75%** 的行参与选优
   —— 短样本（如 v13 的 A500 只有 0.9 年）照样列出来，
   但标 `·短样本` 且**不参与选优**，否则「1 年样本 + 夏普 1.5」会挤掉 15 年样本 + 夏普 0.6；
3. 选优规则：**夏普最高**（同分看年化）。买入持有也是候选 —— 长牛里所有时机策略都跑输时，
   「最优策略」就该诚实地是「一直拿着」；
4. 另列「最高年化」标记（`↑`），因为夏普最优与收益最高常常不是同一支（v3 vs v1/v2）。

## 为什么不自己重算

各自的 `backtest/*.md` 才是权威产物（参数、费率、窗口都在里面），本脚本只做**横向汇编**，
所以产物一更新，重跑本脚本清单就跟着更新。代价是：**各行的样本窗口与费率口径不统一**，
因此每行都单独标了「样本」与「费率口径」，跨行比较前先看这两列。

产出（本目录 = `strategies/vol_legs/research/best_by_leg/`）：
- `_best_by_leg.md`    人读清单（一页结论 + 分标的候选明细 + 未纳入原因）
- `_best_by_leg.jsonl` 机读结果（每行 = 一个 标的×策略）

跑一次：`python strategies/vol_legs/research/best_by_leg/_best_by_leg.py`

> 模拟结果，非投资建议。
"""
import io
import json
import math
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))     # .../research/best_by_leg
_RES = os.path.dirname(_HERE)                          # .../research
_VL = os.path.dirname(_RES)                            # strategies/vol_legs
_ROOT = os.path.dirname(os.path.dirname(_VL))          # 仓库根（推导，不写死）
if _VL not in sys.path:
    sys.path.insert(0, _VL)

import rule  # noqa: E402  （参数权威源：腿名 / 执行代码 / 目标带）

OUT_MD = os.path.join(_HERE, "_best_by_leg.md")
OUT_JSONL = os.path.join(_HERE, "_best_by_leg.jsonl")

LEGS = rule.LEG_ORDER
MAX_SHORT_RATIO = 0.75          # 兜底（缺 start 时用）：样本 < 该腿最长样本的 75% → 短样本
MIN_YEARS = 3.0                 # 绝对下限：不到 3 年的样本一律不选优（连一轮牛熊都盖不住）
MAX_SHORT_GAP = 4.0             # 起点比该腿最早起点晚 4 年以上 → 短样本（缺掉的那段足够改变结论）


# ================================================================ 读产物
def _num(x):
    """-> float 或 None（把 NaN / 非数统一成 None）。"""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _load_jsonl(rel):
    path = os.path.join(_VL, rel.replace("/", os.sep))
    if not os.path.exists(path):
        print(f"  [WARN] 缺产物: {rel}")
        return []
    rows = []
    with io.open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _load_json(rel):
    path = os.path.join(_VL, rel.replace("/", os.sep))
    if not os.path.exists(path):
        print(f"  [WARN] 缺产物: {rel}")
        return {}
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def _years(start, end, fallback=None):
    y = _num(fallback)
    if y is not None and y > 0:
        return y
    try:
        from datetime import date
        a = date.fromisoformat(str(start)[:10])
        b = date.fromisoformat(str(end)[:10])
        return (b - a).days / 365.25
    except Exception:  # noqa: BLE001
        return None


def mk(leg, vx, label, r, src, *, ann=None, mdd=None, sharpe=None, calmar=None,
       vol=None, exposure=None, trades=None, start=None, end=None, years=None,
       fee="", layer="leg", bench=False, note=""):
    """统一成一行。字段缺失就留 None，表格里显示「—」。

    ⚠ **含费优先**：V1 / V2 / V3 / V5 的产物把零费当主列、含费放 `*_fee`。
    只要调用方没显式指定 ann/sharpe/mdd，这里自动取含费字段，
    保证清单里所有行是同一把尺子（否则高换手的 MA20 会被系统性高估）。
    """
    use_fee = ann is None and r.get("ann_fee") is not None
    a = _num(ann if ann is not None else (r.get("ann_fee") if use_fee else r.get("ann")))
    m = _num(mdd if mdd is not None else
             (r.get("mdd_fee") if (use_fee and r.get("mdd_fee") is not None) else r.get("mdd")))
    s = _num(sharpe if sharpe is not None else
             (r.get("sharpe_fee") if (use_fee and r.get("sharpe_fee") is not None) else r.get("sharpe")))
    c = _num(calmar if calmar is not None else r.get("calmar"))
    if c is None and a is not None and m is not None and m < -1e-9:
        c = a / abs(m)
    st = start or r.get("start")
    en = end or r.get("end")
    return dict(
        leg=leg, leg_name=rule.LEGS[leg]["name"], vx=vx, label=label,
        ann=a, mdd=m, sharpe=s, calmar=c,
        vol=_num(vol if vol is not None else r.get("vol")),
        exposure=_num(exposure if exposure is not None else
                      (r.get("exposure") if r.get("exposure") is not None else r.get("expo"))),
        trades=(trades if trades is not None else
                (r.get("trades") if r.get("trades") is not None else
                 (r.get("acts") if r.get("acts") is not None else r.get("actions")))),
        start=str(st)[:10] if st else None,
        end=str(en)[:10] if en else None,
        years=_years(st, en, years if years is not None else r.get("years")),
        fee=fee, layer=layer, bench=bench, note=note, src=src,
    )


# ---------------------------------------------------------------- 各变种适配器
def rows_ladder():
    """主策略单腿（回撤阶梯 vs 一次性满仓）；只有 a500/hs300/ndx/spx 有独立单腿产物。"""
    d = _load_json("backtest/_vol_legs_bt.json")
    out = []
    for leg, blk in (d.get("singles") or {}).items():
        if leg not in rule.LEGS:
            continue
        band = rule.LEGS[leg]["band"]
        w = blk.get("window") or [None, None]
        L = blk.get("ladder") or {}
        expo = None
        if _num(blk.get("avg_invested")) is not None and band:
            expo = _num(blk["avg_invested"]) / band
        out.append(mk(leg, "ladder", "主策略 · 回撤阶梯（单腿）", L, "backtest/_vol_legs_bt.json",
                      start=w[0], end=w[1], exposure=expo, trades=blk.get("trades"),
                      fee="含费（rule 费率）",
                      note="带内份额按阶梯补，创新高归零；未投按货基计息"))
    return out


def rows_simple(rel, vx, label, fee, note="", min_trades=1, fee_adj=True):
    """v1_1 / v2 / v3 这类「7 腿 × 1 行」的产物。

    ⚠ 这三个产物把**零费**当主列（`ann` / `sharpe`），含费另列 `ann_fee` / `sharpe_fee`。
    本清单统一用**含费**（A 类腿 0.10% 买 + 0.50% 卖，换手高的策略会被吃掉几个 pp），
    否则清单会系统性高估 MA20 这类高换手策略（沪深300 腿上零费 +4.23% → 含费 -4.28%）。
    零费口径仍可在各自产物的 `ann` / `sharpe` 字段里看到。
    """
    out = []
    for r in _load_jsonl(rel):
        leg = r.get("leg")
        if leg not in rule.LEGS:
            continue
        if min_trades and (r.get("trades") or 0) < min_trades:
            continue
        kw = {}
        if fee_adj and r.get("ann_fee") is not None:
            kw = dict(ann=r.get("ann_fee"), sharpe=r.get("sharpe_fee"),
                      mdd=(r.get("mdd_fee") if r.get("mdd_fee") is not None else r.get("mdd")))
        out.append(mk(leg, vx, label, r, rel, fee=fee, note=note, **kw))
    return out


def rows_v4():
    """沪深300 × 国债的 ERP 通道（层次不同：不是这条腿的择时，是股债摆仓）。"""
    rows = _load_jsonl("v4_erp_band/backtest/_v4_erp_band_bt.jsonl")
    # 该产物把 start/end/years 只写在第一行（元信息行），默认行只有 ann/mdd/sharpe
    meta = next((r for r in rows if r.get("years") and r.get("start")), {})
    start, end, years = meta.get("start"), meta.get("end"), meta.get("years")
    out = []
    for r in rows:
        name = str(r.get("name") or "")
        if "默认" not in name or _num(r.get("ann")) is None:
            continue
        out.append(mk("hs300", "v4", "V4 ERP 股债通道（默认）", r,
                      "v4_erp_band/backtest/_v4_erp_band_bt.jsonl",
                      start=start, end=end, years=years,
                      fee="含费（月末回锚）", layer="股债",
                      note="沪深300 vs 国债的 0/100 摆仓，不是这条腿的择时；不参与选优"))
    return out


def rows_v5():
    out = []
    for r in _load_jsonl("v5_trend_gate/backtest/_v5_trend_gate_bt.jsonl"):
        leg = r.get("leg")
        if leg not in rule.LEGS or r.get("gate") != "是":
            continue
        out.append(mk(leg, "v5", "V5 趋势闸门（含波动层）", r,
                      "v5_trend_gate/backtest/_v5_trend_gate_bt.jsonl",
                      fee="含费", note="年线四象限给档位，波动层只压满带"))
    return out


def rows_v6():
    """黄金三口径（A 长期配置 / B 控回撤 / C 低仓位占坑）+ 恒持满带。"""
    out = []
    for r in _load_jsonl("v6_gold/backtest/_v6_gold_bt.jsonl"):
        if r.get("group") or r.get("mode") != "otc":
            continue
        key = r.get("key")
        if key not in ("A", "B", "C", "BH"):
            continue
        bench = key == "BH"
        label = f"V6-{key} {r.get('label')}" if not bench else "买入持有（与 V6 同窗口）"
        out.append(mk("gold", f"v6{key}", label, r,
                      "v6_gold/backtest/_v6_gold_bt.jsonl",
                      fee="含费（A 类 0.5% 赎回）",
                      bench=bench, layer=("leg" if not bench else "基准对照"),
                      note="黄金专用：没有现金流锚，PE/ERP/股息率都不适用"))
    return out


def rows_us(rel, vx, label, note=""):
    """v7 / v8 / v9 / v10：只覆盖 ndx / spx，取变种名里「默认」的那一行。

    产物里常有「上一版默认」「旧默认」这类对照行，不能靠 `"默认" in variant` 简单筛 ——
    必须先排除对照行，再优先取明确写了「本变种默认」的，其次取带「（… 默认）」的。
    """
    picked = {}
    for r in _load_jsonl(rel):
        leg = r.get("leg")
        if leg not in ("ndx", "spx"):
            continue
        name = str(r.get("variant") or "")
        if "默认" not in name or "上一版" in name or "旧默认" in name or "对照" in name:
            continue
        prev = picked.get(leg)
        rank = 2 if "本变种默认" in name else 1
        if prev is None or rank > prev[0]:
            picked[leg] = (rank, r)
    return [mk(leg, vx, label, r, rel, fee="统一单边 0.10% / 空仓 1.40%", note=note)
            for leg, (_rank, r) in picked.items()]


def rows_v13():
    out = []
    for r in _load_jsonl("v13_quad_9grid/backtest/_v13_quad_9grid_bt.jsonl"):
        leg = r.get("leg")
        if leg not in rule.LEGS or r.get("ver") != "文档版":
            continue
        out.append(mk(leg, "v13", "V13 趋势九宫格（文档版）", r,
                      "v13_quad_9grid/backtest/_v13_quad_9grid_bt.jsonl",
                      fee="含费（场内 ETF 单边 5bp）",
                      note="场内 7 腿 OHLCV；许可上限层，不是 0/1 择时"))
    return out


def rows_bh():
    """买入持有基准：取 v1_1 产物里的 bh_*（该腿场外净值的全历史）。"""
    out = []
    for r in _load_jsonl("v1_price_volume/v1_1_ma20/backtest/_v1_1_ma20_bt.jsonl"):
        leg = r.get("leg")
        if leg not in rule.LEGS:
            continue
        out.append(mk(leg, "BH", "买入持有（基准）", r,
                      "v1_price_volume/v1_1_ma20/backtest/_v1_1_ma20_bt.jsonl",
                      ann=r.get("bh_ann"), mdd=r.get("bh_mdd"), sharpe=r.get("bh_sharpe"),
                      vol=r.get("bh_vol"), exposure=1.0, trades=0,
                      fee="不计费", bench=True,
                      note="这条腿的场外复权净值全历史；所有时机策略的及格线"))
    return out


def collect():
    rows = []
    for fn in (rows_ladder, rows_bh, rows_v5, rows_v6, rows_v13,
               lambda: rows_simple("v1_price_volume/v1_1_ma20/backtest/_v1_1_ma20_bt.jsonl",
                                   "v1", "V1 MA20 穿越（下车）",
                                   "含费（A 类 0.10/0.50 · C 类 0）",
                                   "走或留：场外净值站上/跌破 MA20（V1 的默认实现）"),
               lambda: rows_simple("v2_boll_breakout/backtest/_v2_boll_breakout_bt.jsonl",
                                   "v2", "V2 BOLL(35,2) 上轨突破",
                                   "含费（A 类 0.10/0.50 · C 类 0）",
                                   "上穿买入 / 跌回中轨卖出；换手最低"),
               lambda: rows_simple("v3_macd_boll/backtest/_v3_macd_boll_bt.jsonl",
                                   "v3", "V3 MACD + BOLL 开口突破",
                                   "含费（A 类 0.10/0.50 · C 类 0）",
                                   "MACD 定多空 + 布林开口入场；追涨型"),
               rows_v4,
               lambda: rows_us("v7_us_gate/backtest/_v7_us_gate_bt.jsonl",
                               "v7", "V7 年线闸门（2 月确认 + 深破 + TV20）",
                               "月末看盘，美股两腿专用"),
               lambda: rows_us("v8_golden_cross/backtest/_v8_golden_cross_bt.jsonl",
                               "v8", "V8 金叉 50/200 × 波动层",
                               "日频，对照组/基线"),
               lambda: rows_us("v9_vol_target/backtest/_v9_vol_target_bt.jsonl",
                               "v9", "V9 波动率目标（TV15 + 月中补丁）",
                               "只调风险预算、不判方向"),
               lambda: rows_us("v10_vol_layer/backtest/_v10_vol_layer_bt.jsonl",
                               "v10", "V10 纯波动层 TV20（日频）",
                               "无方向，只按波动缩放仓位")):
        got = fn()
        print(f"  {fn.__name__ if hasattr(fn, '__name__') else 'adapter'}: {len(got)} 行")
        rows.extend(got)
    return rows


# ================================================================ 选优
def pick(rows):
    """-> {leg: dict(rows=..., best=..., best_ann=..., max_years=...)}。"""
    out = {}
    for leg in LEGS:
        mine = [r for r in rows if r["leg"] == leg and r["ann"] is not None]
        if not mine:
            continue
        legs_only = [r for r in mine if r["layer"] == "leg"]
        max_years = max((r["years"] or 0.0) for r in legs_only) or 0.0
        starts = [d for d in (r["start"] for r in legs_only) if d]
        earliest = min(starts) if starts else None

        def _gap_years(d):
            if not d or not earliest:
                return None
            try:
                from datetime import date
                return (date.fromisoformat(d[:10]) - date.fromisoformat(earliest[:10])).days / 365.25
            except Exception:  # noqa: BLE001
                return None

        for r in mine:
            gap = _gap_years(r["start"])
            short = (r["years"] or 0.0) < MIN_YEARS
            if gap is not None:
                short = short or gap > MAX_SHORT_GAP
            else:   # 缺 start：退回「样本占最长样本比例」的弱判据
                short = short or (r["years"] or 0.0) < MAX_SHORT_RATIO * max_years
            r["short"] = short
            r["start_gap"] = gap
        eligible = [r for r in mine
                    if r["layer"] == "leg" and not r["short"]
                    and (r["bench"] or (r["trades"] or 0) > 0)]
        if not eligible:
            eligible = [r for r in mine if r["layer"] == "leg" and r["bench"]]
        eligible.sort(key=lambda r: (r["sharpe"] if r["sharpe"] is not None else -99,
                                     r["ann"]), reverse=True)
        ann_sorted = sorted(eligible, key=lambda r: r["ann"], reverse=True)
        out[leg] = dict(rows=sorted(mine, key=lambda r: (r["sharpe"] if r["sharpe"] is not None else -99,
                                                         r["ann"]), reverse=True),
                        best=eligible[0] if eligible else None,
                        best_ann=ann_sorted[0] if ann_sorted else None,
                        max_years=max_years,
                        bh=next((r for r in mine if r["vx"] == "BH"), None))
    return out


# ================================================================ 渲染
def pct(x, nd=2, sign=False):
    if x is None:
        return "—"
    return (f"{x:+.{nd}%}" if sign else f"{x:.{nd}%}")


def f2(x):
    return "—" if x is None else f"{x:.2f}"


def render(picked, rows):
    gen = None
    try:
        import datetime
        gen = datetime.date.today().isoformat()
    except Exception:  # noqa: BLE001
        gen = "—"

    L = []
    L.append("# 波动资产 · 各标的「当前最优策略」清单")
    L.append("")
    L.append(f"> 生成 {gen} · 本文件由 `strategies/vol_legs/research/best_by_leg/_best_by_leg.py` "
             "**只读各变种回测产物**汇编，不重跑回测。")
    L.append("> 重跑：`python strategies/vol_legs/research/best_by_leg/_best_by_leg.py`"
             "（任一变种回测产物更新后重跑一次即可）")
    L.append("> 七条腿的定义与执行代码见 `strategies/vol_legs/README.md` §2.1；"
             "本文件 = 该家族**所有变种的横向汇编**（完整候选明细见 §1）。模拟结果，非投资建议。")
    L.append("")

    # ---- 一页结论
    L.append("## 0. 一页结论")
    L.append("")
    L.append("| 标的 | 执行代码 | 最优策略（夏普） | 年化 | 最大回撤 | 夏普 | Calmar "
             "| 样本 | 买入持有同腿对照 | 结论 |")
    L.append("|---|---|---|---:|---:|---:|---:|---|---|---|")
    for leg in LEGS:
        info = picked.get(leg)
        if not info or not info["best"]:
            L.append(f"| {rule.LEGS[leg]['name']} | `{rule.LEGS[leg]['exec']}` | — | — | — | — | — "
                     "| — | — | 无可用产物 |")
            continue
        b, bh = info["best"], info["bh"]
        name = rule.LEGS[leg]["name"]
        code = rule.LEGS[leg]["exec"]
        bh_txt = (f"{pct(bh['ann'])} / {pct(bh['mdd'])} / {f2(bh['sharpe'])}"
                  if bh else "—")
        if b["bench"]:
            verdict = "**时机策略全部跑不赢一直拿着** → 不择时"
        else:
            d = (b["ann"] - bh["ann"]) * 100 if bh and bh["ann"] is not None else None
            if d is None:
                verdict = "—"
            elif d >= 0:
                verdict = f"优于买入持有 {d:+.2f}pp/年"
            else:
                verdict = (f"年化 {d:+.2f}pp，换回撤 {pct(b['mdd'])} vs {pct(bh['mdd'])}"
                           f"（夏普 {f2(b['sharpe'])} vs {f2(bh['sharpe'])}）")
        L.append(f"| **{name}** | `{code}` | {b['label']} | {pct(b['ann'])} | {pct(b['mdd'])} "
                 f"| **{f2(b['sharpe'])}** | {f2(b['calmar'])} | {b['years']:.1f} 年 "
                 f"| {bh_txt} | {verdict} |")
    L.append("")
    L.append("> 全部数字为**含费**口径（各自默认费率，见 §3-②）。"
             "「买入持有同腿对照」= 年化 / 最大回撤 / 夏普，取自 `v1_1_ma20` 产物里的 `bh_*` 字段"
             "（该腿场外复权净值全历史），是每条腿的**及格线**：落在它下面的择时策略不如一直拿着。")
    L.append(">")
    L.append("> 选优只看**夏普**（风险调整后收益）；要绝对收益就取「夏普 ≠ 收益最高」里的后者，"
             "代价通常是更深的回撤。")
    L.append("")

    # ---- 各腿最高年化与夏普最优不一致的提示
    diff = [leg for leg, i in picked.items()
            if i["best"] and i["best_ann"] and i["best"]["vx"] != i["best_ann"]["vx"]]
    if diff:
        L.append("**夏普最优 ≠ 收益最高**（换收益要付回撤代价，按自己的目标取）：")
        for leg in diff:
            i = picked[leg]
            L.append(f"- {rule.LEGS[leg]['name']}：夏普最优 **{i['best']['label']}**"
                     f"（{pct(i['best']['ann'])} / {pct(i['best']['mdd'])} / {f2(i['best']['sharpe'])}）"
                     f" vs 收益最高 **{i['best_ann']['label']}**"
                     f"（{pct(i['best_ann']['ann'])} / {pct(i['best_ann']['mdd'])} / "
                     f"{f2(i['best_ann']['sharpe'])}）")
        L.append("")

    # ---- 分标的明细
    L.append("## 1. 分标的候选明细")
    L.append("")
    L.append(f"`★` = 该标的最优（夏普，且样本 ≥ {MIN_YEARS:.0f} 年、起点不晚于该腿最早起点 "
             f"{MAX_SHORT_GAP:.0f} 年）；`↑` = 年化最高；`·短样本` = 样本不够，只列不选"
             "（跨年不可硬比）。**本表全部为含费口径**（零费版见各产物 `ann` / `sharpe` 字段）。")
    L.append("")
    for leg in LEGS:
        info = picked.get(leg)
        if not info:
            continue
        L.append(f"### {rule.LEGS[leg]['name']}（`{rule.LEGS[leg]['exec']}` · "
                 f"{rule.LEGS[leg]['role']} · 带 {rule.LEGS[leg]['band']:g}pp）")
        L.append("")
        L.append("| 策略 | 年化 | 最大回撤 | 夏普 | Calmar | 波动 | 平均仓位 | 笔数 | 样本 "
                 "| 费率口径 | 出处 |")
        L.append("|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|")
        for r in info["rows"]:
            mark = ""
            if info["best"] is not None and r is info["best"]:
                mark += " ★"
            if info["best_ann"] is not None and r is info["best_ann"] and mark.find("★") < 0:
                mark += " ↑"
            elif info["best_ann"] is not None and r is info["best_ann"]:
                mark += "↑"
            if r["short"]:
                mark += " ·短样本"
            if r["layer"] == "股债":
                mark += " ·股债层"
            elif r["layer"] == "基准对照":
                mark += " ·同窗口基准"
            trades = "—" if r["trades"] is None else str(r["trades"])
            yrs = "—" if r["years"] is None else f"{r['years']:.1f}y"
            expo = "—" if r["exposure"] is None else f"{r['exposure']:.0%}"
            L.append(f"| {r['label']}{mark} | {pct(r['ann'])} | {pct(r['mdd'])} | {f2(r['sharpe'])} "
                     f"| {f2(r['calmar'])} | {pct(r['vol'])} | {expo} "
                     f"| {trades} | {yrs} | {r['fee']} | `{r['src']}` |")
        L.append("")

    # ---- 未纳入
    L.append("## 2. 没有进清单的策略及原因")
    L.append("")
    L.append("| 变种 | 为什么不参与排序 |")
    L.append("|---|---|")
    L.append("| `v11_support_pressure` 支撑/压力位 | **事件研究，不是仓位规则**：按文档 §4.3 只报信号日后 "
             "20/60/120 日前向收益，刻意不报年化/回撤/夏普（不是漏写） |")
    L.append("| `v11_support_pressure/gate/` 支撑位闸门 | 回测后**判定不上线**：撤掉支撑层年化 +8.99% → +12.37%，"
             "回撤不变；它自己的产物就是「负贡献」的证据，不构成候选 |")
    L.append("| `v4_erp_band` ERP 股债通道 | **层次不同**：它管「沪深300 与国债之间摆 0/100」，"
             "不是沪深300 这条腿的择时，故列在明细表里但不参与选优（带 `·股债层` 标记） |")
    L.append("| `v13` 的部分腿 | 场内 ETF 上市太晚：A500 的 V13 只有 0.9 年；"
             "沪深300 的 V13 起点晚 4 年以上（缺 2009-2014 那段 A 股熊市）。"
             "一律标 `·短样本`，只列不选 |")
    L.append("| 主策略的科创50 / 恒生科技 / 黄金腿 | 主策略只有组合层复权净值，"
             "**没有这三条腿的独立单腿产物**（`_vol_legs_bt.json::singles` 只有 4 条腿），"
             "故主策略在这三条腿上无法与变种同尺比较 |")
    L.append("")

    # ---- 口径
    L.append("## 3. 口径与维护")
    L.append("")
    L.append("1. **净值口径分两类**：V1 / V2 / V3 / V4 / V5 / V6 / 主策略 = **场外复权净值**"
             "（含分红再投与汇率，是账户上能拿到的收益）；V13 = **场内 ETF 量价**"
             "（信号更纯，但要扣场内佣金与溢价）。跨这两类比较时先看「费率口径」列。")
    L.append("2. **费率一律取含费**：V1 / V2 / V3 的产物把零费当主列（`ann` / `sharpe`）、"
             "含费放在 `ann_fee` / `sharpe_fee`，本清单**取含费**（A 类腿 0.10% 买 + 0.50% 卖）；"
             "V5 / V6 / V7~V10 / V13 / 主策略的默认口径本身含费；V4 含月末回锚费用。"
             "差异极大，别忽略：沪深300 腿上 V1 MA20 零费 +4.23% → 含费 **-4.28%**"
             "（一年 235 笔，成本吃光全部超额）。")
    L.append("3. **样本必须看**：`规则信号用全历史算、统计窗口另截` 是仓库惯例，"
             "各行 `start`/`end` 差异很大（科创50/恒生科技只有 4~5 年，"
             "A500/沪深300 有 16~17 年）。年化差 ±3pp 内属抽样噪声。")
    L.append("4. **A500 与沪深300 不是两份独立证据**：2025-03-27 之前两条腿的场外序列"
             "同源自 `000051`，这两年半里它们的成交日、回撤、档位完全一致"
             "（见 `README.md` §2.3）。")
    L.append("5. **维护方式**：某变种重跑回测 → 重跑本脚本 → 清单自动更新。"
             "新增变种时只需在 `_best_by_leg.py` 的 `collect()` 里加一个适配器"
             "（取它的 `backtest/_*_bt.jsonl` 并筛出默认口径那一行），"
             "同时在本文件 §2 说明它是否参与排序。")
    L.append("6. **每日流程里的位置**：本清单是**横向决策参考**，不是每日信号。"
             "每日动作仍由 `run_daily_vol_legs.ps1` 产出（主策略七腿 + 变种层 v2/v3/v4/v6/v9）。")
    L.append("")
    return "\n".join(L) + "\n"


def main():
    print("读取各变种回测产物 …")
    rows = collect()
    print(f"共 {len(rows)} 行（标的 × 策略）")
    picked = pick(rows)
    md = render(picked, rows)
    with io.open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(md)
    with io.open(OUT_JSONL, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[ok] {OUT_MD}")
    print(f"[ok] {OUT_JSONL}")
    print("")
    for leg in LEGS:
        info = picked.get(leg)
        if not info or not info["best"]:
            print(f"  {rule.LEGS[leg]['name']:<5} —")
            continue
        b = info["best"]
        print(f"  {rule.LEGS[leg]['name']:<5} {b['label']:<34} "
              f"年化 {b['ann']:+.2%} · 回撤 {b['mdd']:.2%} · 夏普 {b['sharpe']:.2f} "
              f"· {b['years']:.1f}y")


if __name__ == "__main__":
    main()
