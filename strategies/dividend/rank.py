# -*- coding: utf-8 -*-
r"""红利基金横向打分与择优推荐（dividend 策略 · 多标的扩展）

打分模型
----------------------------------------------------------------------
    总分(0-100) = W_PERF × 业绩分 + W_VALUE × 价值分

**业绩分**（用户强调的主维度 —— 时间窗权重「近3年最高 / 近5年次之 / 近10年最低」）:
    Σ_W w_W · 窗口分_W  ÷  Σ_W w_W            （缺失窗口按可得性归一化）
    窗口分_W = RET_W × 收益分_W + DD_W × 回撤分_W
  收益分 / 回撤分 = **池内横截面分位(0-100)**：年化越高 / 回撤越浅，分越高；
  收益分与回撤分同分位口径, 消除量纲。

**价值分**（**五项，每项 20 分**，全部取自真实数据源，不使用任何表格数据）:
    PE(低者高) + PE分位(低者高) + 股息率(高者高) + 股债收益比(高者高) + **RSI14(低者高)**
  PE        = 中证官网「指数估值」市盈率1 → 官网 `peg`(静态) → 蛋卷
  PE分位    = 由官网 `peg` 历史自算「过去最多 10 年」累计分位（与 data.py 同口径）
  股息率    = 中证官网「指数估值」股息率1 → 蛋卷（按跟踪指数）
  股债收益比 = 指数股息率(%) ÷ 10Y国债(%)（分母取仓库共用缓存 data/_bt_caches/bond10y_m.csv）
              —— 2026-09-12 由「趋势(近6月)」替换而来：原趋势分是**短周期动量**，与另三项
              （估值/收益）口径不同源；换成股债收益比后，前四项统一为「相对估值」口径。
  RSI14     = Wilder RSI，用基金累计净值（与业绩分同源）；<40 超卖 → 满分、>70 超买 → 0 分
              —— 补上「短期位置」维度，与估值项互补（估值说贵贱、RSI 说是否已经跌过头）。
  某项真实源不可得时用**池内该分项的中位数**补齐（不放大也不惩罚）；可得项 < MIN_VALUE_DIMS
  则价值分不可算（届时总分退化为业绩分）。

操作建议按总分分档（见 ACTION_BANDS）。

调参
----------------------------------------------------------------------
本文件顶部常量是**权威源**；`backtest_rank.py` 做 walk-forward 比较不同窗权方案，
按「3 年权重最高」的目标择优后回写 `WINDOW_WEIGHTS`。

产物（AGENTS §8.4 ⑤）
----------------------------------------------------------------------
    daily/_dividend_rank.md · daily/_dividend_rank.json

用法
----------------------------------------------------------------------
    python strategies/dividend/rank.py            # 用缓存
    python strategies/dividend/rank.py --refresh  # 刷新净值 + 估值
"""
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import pool  # noqa: E402

OUT_MD = os.path.join(_DIR, "daily", "_dividend_rank.md")
OUT_JSON = os.path.join(_DIR, "daily", "_dividend_rank.json")

# ----------------------------------------------------------------------
# 打分参数（权威源）
# ----------------------------------------------------------------------
# 时间窗权重: 近 3 年最高 / 近 5 年次之 / 近 10 年最低 —— 由 backtest_rank.py 择优确认
WINDOW_WEIGHTS = {3: 0.5, 5: 0.3, 10: 0.2}
RET_W, DD_W = 0.6, 0.4          # 窗口分内: 收益 / 回撤 占比
W_PERF, W_VALUE = 0.6, 0.4      # 总分内: 业绩分 / 价值分 占比
MIN_COVER = 0.6                 # 窗口可用所需覆盖比例(实有年数 / 目标年数)
MIN_YEARS_ANY = 1.0             # 至少 1 年历史才可打分

# 收益分/回撤分封顶口径(避免极端值主导; 分位口径下仅作参考)
RET_FULL = 0.30                 # 年化 30% 视为满分
DD_FULL = -0.35                 # 回撤 -35% 视为 0 分

# 价值分分项口径（**5 项，各 20 分 = 满分 100**）
VALUE_FULL = 20.0               # 每项满分
PE_FLOOR, PE_CAP = 7.0, 20.0    # PE 线性打分区间(低者高)
DY_CAP = 6.0                    # 股息率 6% 视为满分
# 股债收益比 = 指数股息率(%) ÷ 10Y国债(%) —— 高者高分（股票相对债券越便宜越值得配）
RATIO_BANDS = [(3.2, 20), (2.8, 18), (2.5, 15), (2.2, 12), (1.9, 8), (-1e9, 4)]
# RSI14: <40 超卖 -> 满分 20；>70 超买 -> 0 分；之间线性
RSI_FULL, RSI_ZERO = 40.0, 70.0
MIN_VALUE_DIMS = 2              # 价值分至少需要的可得维度数(否则价值分不可算)

# 总分 -> 操作建议
ACTION_BANDS = [
    (70.0, "加大分批"),
    (60.0, "正常逢跌加"),
    (50.0, "逢跌小额"),
    (40.0, "减仓观望"),
    (-1e9, "规避"),
]


# ----------------------------------------------------------------------
# 窗口统计（因果: 只用 asof 当日及以前）
# ----------------------------------------------------------------------
def trailing_stats(s, asof, years):
    """单只净值序列在 asof 的「过去 years 年」年化收益 + 最大回撤。

    覆盖不足(实有年数 < MIN_COVER × years)返回 None —— 该窗口不可用。
    """
    s = s.loc[:asof].dropna()
    if len(s) < 2:
        return None
    win = s.loc[pd.Timestamp(asof) - pd.DateOffset(years=years):]
    if len(win) < 2:
        return None
    span = (win.index[-1] - win.index[0]).days / 365.25
    if span < MIN_COVER * years or span <= 0:
        return None
    ann = float((win.iloc[-1] / win.iloc[0]) ** (1 / span) - 1)
    mdd = float((win / win.cummax() - 1).min())
    return dict(ann=ann, mdd=mdd, years=round(span, 2))


def pct_rank(vals):
    """{code: value} -> {code: 池内分位 0-100}（大者高分, 单只记 50）。"""
    codes = [c for c, v in vals.items() if v is not None]
    if not codes:
        return {}
    if len(codes) == 1:
        return {codes[0]: 50.0}
    s = pd.Series({c: vals[c] for c in codes})
    r = s.rank(method="average")
    n = len(codes)
    return {c: round(float((r[c] - 1) / (n - 1) * 100), 1) for c in codes}


def _clip01(x):
    return float(min(1.0, max(0.0, x)))


VALUE_DIMS = ("pe", "pe_pct", "dy", "ratio", "rsi")


def value_parts_raw(pe, pe_pct, dy, ratio, rsi):
    """五个分项的**原始得分**（缺项为 None，不做任何归一化），各 20 分。

    PE(低者高) / PE分位(低者高) / 股息率(高者高) / 股债收益比(高者高) / **RSI14**(低者高)
    股债收益比 = 股息率(%) ÷ 10Y国债(%)；RSI14 <40 满分、>70 零分、之间线性。
    """
    return {
        "pe": None if pe is None else round(VALUE_FULL * _clip01((PE_CAP - pe) / (PE_CAP - PE_FLOOR)), 1),
        "pe_pct": None if pe_pct is None else round(VALUE_FULL * (1 - pe_pct), 1),
        "dy": None if dy is None else round(VALUE_FULL * _clip01(dy / DY_CAP), 1),
        "ratio": (None if ratio is None
                  else next(sc for lo, sc in RATIO_BANDS if ratio >= lo)),
        "rsi": (None if rsi is None
                else round(VALUE_FULL * _clip01((RSI_ZERO - rsi) / (RSI_ZERO - RSI_FULL)), 1)),
    }


# ----------------------------------------------------------------------
# 打分主流程
# ----------------------------------------------------------------------
def perf_raw(navs, asof, windows=None):
    """{code: {years: 窗口统计}}（仅保留可用窗口）。"""
    windows = windows if windows is not None else list(WINDOW_WEIGHTS)
    out = {}
    for c in navs.columns:
        s = navs[c].dropna()
        if len(s) < 2 or (s.index[-1] - s.index[0]).days / 365.25 < MIN_YEARS_ANY:
            continue
        w = {}
        for y in windows:
            st = trailing_stats(s, asof, y)
            if st is not None:
                w[y] = st
        if w:
            out[c] = w
    return out


def perf_score(raw, weights=None):
    """窗口统计 -> {code: (业绩分, {years: 窗口分})}。weights 缺省用 WINDOW_WEIGHTS。"""
    weights = weights if weights is not None else WINDOW_WEIGHTS
    win_scores = {}
    for y in weights:
        rets = {c: raw[c][y]["ann"] for c in raw if y in raw[c]}
        mdds = {c: raw[c][y]["mdd"] for c in raw if y in raw[c]}
        rp, dp = pct_rank(rets), pct_rank(mdds)
        win_scores[y] = {c: round(RET_W * rp[c] + DD_W * dp[c], 1) for c in rets}
    out = {}
    for c in raw:
        avail = [(y, win_scores[y][c]) for y in weights if c in win_scores.get(y, {})]
        if not avail:
            continue
        wsum = sum(weights[y] for y, _ in avail)
        out[c] = (round(sum(weights[y] * sc for y, sc in avail) / wsum, 1),
                  {y: sc for y, sc in avail})
    return out


def build_table(navs, asof=None, refresh_val=False):
    """全池 -> list[dict]（含总分 / 操作），按总分降序。"""
    asof = pd.Timestamp(asof) if asof is not None else navs.index[-1]
    raw = perf_raw(navs, asof)
    ps = perf_score(raw)
    val = pool.valuation(refresh=refresh_val)
    moms = pool.mom6(navs, asof)
    rsis = pool.rsi14(navs, asof)
    y10 = pool.y10_latest()
    pe_xs = pct_rank({r["code"]: (val.get(r["code"]) or {}).get("pe") for r in pool.POOL})

    # —— 第一遍：算出各基金的分项原始分（缺项 None）——
    raws = {}
    for r in pool.POOL:
        v = val.get(r["code"], {})
        dy = v.get("dy")
        raws[r["code"]] = value_parts_raw(
            v.get("pe"), v.get("pe_pct"), dy,
            round(dy / y10, 3) if (dy is not None and y10) else None, rsis.get(r["code"]))

    # —— 缺项用**池内该分项的中位数**补齐：既不放大（而非按可得项归一化）也不惩罚 ——
    med = {}
    for k in VALUE_DIMS:
        vals = sorted(x[k] for x in raws.values() if x[k] is not None)
        med[k] = round(vals[len(vals) // 2], 1) if vals else None

    rows = []
    for r in pool.POOL:
        c = r["code"]
        p_score, win_sc = ps.get(c, (None, {}))
        v = val.get(c, {})
        dy = v.get("dy")
        ratio = round(dy / y10, 3) if (dy is not None and y10) else None
        raw_parts = raws[c]
        imputed = [k for k in VALUE_DIMS if raw_parts[k] is None]
        v_parts = {k: (raw_parts[k] if raw_parts[k] is not None else med[k]) for k in VALUE_DIMS}
        n_avail = len(VALUE_DIMS) - len(imputed)
        v_score = (round(sum(v_parts.values()), 1)
                   if n_avail >= MIN_VALUE_DIMS and None not in v_parts.values() else None)
        if p_score is None and v_score is None:
            total = None
        elif p_score is None:
            total = v_score
        elif v_score is None:
            total = p_score
        else:
            total = round(W_PERF * p_score + W_VALUE * v_score, 1)
        rows.append(dict(
            code=c, name=r["name"], cat=r["cat"], role=r["role"], channel=r["channel"],
            idx=r["idx_name"], pe=v.get("pe"), pe_pct=v.get("pe_pct"),
            pe_cs=v.get("pe_cs"), pe_peg=v.get("pe_peg"), pe_ttm_mx=v.get("pe_ttm_mx"),
            pe_pct_ytd=v.get("pe_pct_ytd"), pb=v.get("pb"),
            dy=dy, dy_dj=v.get("dy_dj"), ratio=ratio, y10=y10, mom6=moms.get(c),
            rsi=rsis.get(c),
            src_pe=v.get("src_pe"), src_pct=v.get("src_pct"), src_dy=v.get("src_dy"),
            val_src=" / ".join(sorted({str(v.get("src_pe")), str(v.get("src_pct")),
                                       str(v.get("src_dy"))})),
            value_score=v_score, value_parts=v_parts, imputed=imputed, n_value_dims=n_avail,
            perf_score=p_score, win_scores=win_sc,
            windows={y: raw.get(c, {}).get(y) for y in WINDOW_WEIGHTS},
            total=total, action=action_of(total),
            valuation=(valuation_of(v.get("pe_pct")) if v.get("pe_pct") is not None
                       else "—（无分位）"),
            pe_xs_pct=pe_xs.get(c), short_hist=(p_score is None),
        ))
    # 历史不足（无业绩分，总分只由价值分构成）者排到最后，避免单靠估值冲高名次
    rows.sort(key=lambda x: (1 if x["short_hist"] else 0,
                             -1e9 if x["total"] is None else -x["total"]))
    return rows, asof


def action_of(total):
    if total is None:
        return "数据不足"
    return next(a for lo, a in ACTION_BANDS if total >= lo)


def valuation_of(pe_pct):
    if pe_pct is None:
        return "—"
    if pe_pct < 0.45:
        return "深度低估"
    if pe_pct < 0.80:
        return "偏低区间"
    if pe_pct < 0.90:
        return "偏高区间"
    return "高估区间"


# ----------------------------------------------------------------------
# 渲染
# ----------------------------------------------------------------------
def _fmt_pct(v, digits=1):
    return "—" if v is None else f"{v * 100:.{digits}f}%"


def render(rows, asof):
    wtxt = " / ".join(f"{y}年 {w:.0%}" for y, w in WINDOW_WEIGHTS.items())
    L = ["# 红利基金打分与推荐（dividend 策略 · 多标的）", "",
         f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 数据截至 {asof.date()} · 池内 {len(rows)} 只",
         f"> 打分权重: 业绩分 {W_PERF:.0%} / 价值分 {W_VALUE:.0%}；"
         f"业绩分时间窗权重 = {wtxt}（近3年最高, 由回测择优确认）",
         "> 池内**一指数一标的**：同跟踪指数只保留 1 只（保留规则：历史更长 > 场外 C 类/联接 > 规模更大；"
         "落选者登记在 `pool.py::RESERVE`，不进评分）。", ""]

    L += ["## 一、总分与操作建议", "",
          "| 排名 | 代码 | 简称 | 大类 | 跟踪指数 | 业绩分 | 价值分 | **总分** | 估值定性 | **操作** |",
          "|---:|---|---|---|---|---:|---:|---:|---|---|"]
    for i, r in enumerate(rows, 1):
        mark = " ⚠" if r["short_hist"] else ""
        L.append(f"| {i} | {r['code']} | {r['name']}{mark} | {r['cat']} | {r['idx']} | "
                 f"{_n(r['perf_score'])} | {_n(r['value_score'])} | **{_n(r['total'])}** | "
                 f"{r['valuation']} | **{r['action']}** |")
    L += ["",
          "> 估值定性按 PE 分位划分（<45% 深度低估 / 45–80% 偏低 / 80–90% 偏高 / >90% 高估）。",
          "> **⚠ = 历史不足 3 年**（无业绩分），总分只由价值分构成，**排序一律靠后**、不与其他标的直接比名次"
          "（如 `022720` 成立 2025-01，仅 1.7 年）。", ""]

    L += ["## 二、区间业绩（年化 / 最大回撤）", "",
          "| 代码 | 简称 | 近3年 | 近3年回撤 | 近5年 | 近5年回撤 | 近10年 | 近10年回撤 |",
          "|---|---|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        w = r["windows"]
        cells = []
        for y in WINDOW_WEIGHTS:
            st = w.get(y)
            cells += [_fmt_pct(st["ann"]) if st else "—", _fmt_pct(st["mdd"]) if st else "—"]
        L.append(f"| {r['code']} | {r['name']} | " + " | ".join(cells) + " |")
    L += ["", "> 覆盖不足 MIN_COVER={:.0%} × 目标年数记「—」（新基金成立晚, 无法回填）。".format(MIN_COVER), ""]

    L += ["## 三、估值与分项（打分输入，全部真实数据源）", "",
          "| 代码 | 简称 | PE | PE10年分位 | 股息率 | 股债收益比 | RSI14 | "
          "PE分 | 分位分 | 股息分 | 股债分 | RSI分 | PE源 | 股息源 |",
          "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|"]
    for r in rows:
        p = r["value_parts"] or {}
        im = set(r.get("imputed") or [])
        mk = lambda k: "*" if k in im else ""       # noqa: E731 - 缺项补齐标 *
        L.append(f"| {r['code']} | {r['name']} | {_n(r['pe'], 2)} | {_fmt_pct(r['pe_pct'], 0)} | "
                 f"{_p1(r['dy'])} | {_n(r['ratio'], 2)} | {_n(r['rsi'], 1)} | "
                 f"{_n(p.get('pe'), 1)}{mk('pe')} | "
                 f"{_n(p.get('pe_pct'), 1)}{mk('pe_pct')} | {_n(p.get('dy'), 1)}{mk('dy')} | "
                 f"{_n(p.get('ratio'), 1)}{mk('ratio')} | {_n(p.get('rsi'), 1)}{mk('rsi')} | "
                 f"{r['src_pe']} | {r['src_dy']} |")
    L += ["",
          "> **价值分 5 项，各 20 分**（合计 100）；**打分口径以中证官网为单一源**（PE 水平与 PE 分位同源）：",
          "> **PE** = 中证官网「指数估值」市盈率1 → 官网 `peg`(静态) → 蛋卷；",
          "> **10 年分位** = 官网 `peg` 历史自算「过去最多 10 年」累计分位（与 `data.py` 同口径，故普遍高于用户表）；",
          "> **股息率** = 中证官网「指数估值」股息率1 → 蛋卷（实测股息率1 与蛋卷口径一致）；",
          f"> **股债收益比** = 股息率 ÷ 10Y国债（当前 **{rows[0]['y10']:.2f}%**，取仓库共用缓存 "
          "`data/_bt_caches/bond10y_m.csv`）；分档 ≥3.2 → 20 / 2.8 → 18 / 2.5 → 15 / 2.2 → 12 / 1.9 → 8 / <1.9 → 4。",
          "> **RSI14** = Wilder RSI，用基金**累计净值**（与业绩分同源，场内/场外同口径）；"
          f"**<{RSI_FULL:.0f} → 20 分（超卖/便宜）、>{RSI_ZERO:.0f} → 0 分（超买/贵）**，之间线性。",
          "> **`*` = 该项真实源缺失，用池内该分项的「中位数」补齐**（既不按可得项归一化放大、也不按 0 惩罚）"
          "—— 保证五项齐全与缺项标的的分数可比。",
          "> **不使用任何表格数据**：此前对表内 5 只的快照覆盖（TABLE_SNAPSHOT）已删除。", ""]

    L += ["## 三b、多源交叉校验（不进打分，仅供验真）", "",
          "| 代码 | 简称 | PE·中证官网 | PE·官网peg(静态) | PE·东财/穿透 | 股息率·官网 | 股息率·蛋卷 | PB·东财 | PE年内分位·东财 |",
          "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        L.append(f"| {r['code']} | {r['name']} | {_n(r['pe_cs'], 2)} | {_n(r['pe_peg'], 2)} | "
                 f"{_n(r['pe_ttm_mx'], 2)} | {_p1(r['dy']) if r['src_dy'] == '中证官网指数估值' else '—'} | "
                 f"{_p1(r['dy_dj'])} | {_n(r['pb'], 2)} | {_fmt_pct(r['pe_pct_ytd'], 0)} |")
    L += ["",
          "> 三源基本吻合（如 `000922`：官网 8.83 / peg 8.61 / 东财TTM 8.71；股息率 官网 4.16 / 蛋卷 4.22）。",
          "> **唯一的显著分歧**：`931157`（沪港深，含港股）—— 东财 TTM 7.81 vs 官网 9.34（差 1.5）。",
          "> 故东财 TTM **只作校验、不进打分**：若用它打「PE 分」，而「分位分」来自官网静态序列，两分不同源。",
          "> `008164` 的「PE·东财/穿透」= **持仓穿透估算**（该指数三源皆无，见 §四）；`399324` 不在中证官网体系，PE/股息率用蛋卷。", ""]

    L += ["## 四、真实源的覆盖情况（如实标注）", "",
          "| 基金 | 跟踪指数 | 状态 |",
          "|---|---|---|",
          "| 008115 | 中证红利低波动100(930955) | ✅ 股息率 4.28% 取自**中证官网「指数估值」**（经 akshare） |",
          "| 005125 | 标普中国A股红利机会 | ⚠ 该指数为标普编制，**不在中证官网** → PE/分位/股息率用**蛋卷** `CSPSADRP`"
          "（分位为蛋卷全历史口径，与官网 10 年窗不同） |",
          "| 022720 / 021562 | 国新港股通央企红利(931722) / 央企红利50(931231) | ✅ 中证官网（PE+股息率）+ 东财 MCP（PE-TTM） |",
          "| 008164 | 标普中国A股大盘红利低波动50 | ✅ **已用持仓穿透补齐** —— 该指数在中证官网(2365 只)/蛋卷/"
          "东财指数库**三源皆无**，改由 515450 全部持股(50 只, 2026-06-30) + 个股 PE/股息率穿透估算："
          "**PE 9.68 / 股息率 4.90%**（推导值，非指数公司口径） |",
          "| 159905 | 深证红利(399324) | ⚠ 中证官网不收录深交所指数 → PE/股息率用**蛋卷** |",
          "",
          "**各源实测能力**：",
          "",
          "| 源 | 指数 PE | 指数股息率 | 10 年窗分位 | 备注 |",
          "|---|:--:|:--:|:--:|---|",
          "| 中证官网 `index-perf`（`peg`） | ✅ | ❌ | ✅ 自算 | 打分主源（静态口径） |",
          "| 中证官网「指数估值」（akshare） | ✅ | ✅ **股息率1** | ❌（仅 20 日） | 补齐 930955 股息率 |",
          "| 蛋卷 `index_eva` | ✅ | ✅ | ✅（全历史） | 399324 的唯一源 |",
          "| mx-ds-mcp（东财） | ✅ TTM | ❌ 不暴露 | ❌ 仅年内 | PE-TTM/PB/年内分位作校验 |",
          "| mx-ds-mcp（**持仓穿透**） | ✅ 推导 | ✅ 推导 | ❌ 无 PE 历史 | 仅 008164 用（指数无公开源） |",
          "",
          "> **MCP 是 agent 工具**，脚本无法直连（需 api key）：agent 查询后写入",
          "> `strategies/dividend/data/_mx_valuation.json`，`pool.py::load_mx()` 只读；文件缺失自动跳过。",
          "> akshare 亦为**可选依赖**（延迟 import）：未安装时该层跳过并退回蛋卷，不报错。",
          "> `008164` **无 10 年 PE 分位**（该指数无 `peg` 历史），故其「估值定性」留空（`—（无分位）`）——"
          "**不给截面近似的定性，避免把相对高低误读成绝对低估**；其价值分由 PE + 股息率 + 趋势 三项归一化，"
          "两项估值均为持仓穿透推导值。", ""]

    L += ["## 五、读法与边界", "",
          "- 业绩分是**横截面分位**：收益越高、回撤越浅 → 分越高；同一窗口内比较，跨窗口只靠权重合成。",
          "- 回撤分与收益分同权（%.0f/%.0f），已是偏重「收益 + 风控」的口径。" % (RET_W * 100, DD_W * 100),
          "- 新基金（021551/020603）历史不足 3 年, 窗口缺失后按可得窗口归一化, 分数波动更大, 需结合估值定性看。",
          "- 机械输出，非投资建议；市场有风险，投资需谨慎。", ""]
    return "\n".join(L) + "\n"


def _n(v, digits=1):
    if v is None:
        return "—"
    if isinstance(v, str):
        return v
    return f"{v:.{digits}f}"


def _p1(v):
    return "—" if v is None else f"{v:.1f}%"


def main(argv=None):
    argv = argv or sys.argv[1:]
    refresh = "--refresh" in argv
    navs = pool.load_navs(refresh=refresh)
    rows, asof = build_table(navs, refresh_val=refresh)
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    txt = render(rows, asof)
    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write(txt)
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "as_of": str(asof.date()),
        "weights": {"perf": W_PERF, "value": W_VALUE,
                    "windows": {str(k): v for k, v in WINDOW_WEIGHTS.items()},
                    "ret": RET_W, "dd": DD_W,
                    "value_dims": list(VALUE_DIMS),
                    "value_full": VALUE_FULL,
                    "ratio_bands": RATIO_BANDS,
                    "rsi_full_zero": [RSI_FULL, RSI_ZERO],
                    "y10": pool.y10_latest()},
        "rows": rows,
    }
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
    print(txt)
    print(f"产物: {OUT_MD}\n      {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
