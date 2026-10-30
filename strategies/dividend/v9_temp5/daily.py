#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V9 · 今日温度读数与推荐动作（五因子估值温度）

定位（由 `backtest/_v9_report.md` 决定，不是拍脑袋）
---------------------------------------------------
回测结论：**横截面选品为负贡献、时间维度减仓越多越差**，所以本脚本
**不输出仓位倍率、不做横截面 topN**（`rule.XS_ENABLED = False`、`rule.TIME_ENABLED = False`）。
它输出的是三样东西：

1. **逐只温度 + 档位 + 规则 §4.7 的字面动作** —— 一张「贵不贵」的体检表；
2. **可执行清单**：≥75 的（过热，分批减仓 / 不再新增）、<40 的（偏冷，可分批加仓）；
3. **口径与边界**：哪些数是合成的、哪些标的只有价格温度、回测为什么不让它择时。

执行建议的口径：这是**加仓/减仓的额度参考，不是择时信号**。真要控回撤走 V4（RSI14 择时）。

产物（AGENTS §8.4 ⑤）：
* `v9_temp5/daily/_v9_signal.md`   人读
* `v9_temp5/daily/_v9_signal.json` 结构化（HTML 看板与 pipeline 可读）

用法::

    python strategies/dividend/v9_temp5/daily.py            # 用缓存
    python strategies/dividend/v9_temp5/daily.py --refresh  # 先抓最新行情
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
_STRAT = os.path.dirname(_DIR)
for _p in (_DIR, _STRAT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import data  # noqa: E402  pylint: disable=wrong-import-position
import factors  # noqa: E402  pylint: disable=wrong-import-position
import rule  # noqa: E402  pylint: disable=wrong-import-position

CHAIN = "指数 PE/OHLC + 股息率 + 10Y 国债 → 五因子近 2 年分位 → 温度 → 档位动作（不调仓位）"


def _dd52(daily: pd.DataFrame, asof: pd.Timestamp):
    s = daily.loc[:asof, "close"]
    h = daily.loc[:asof, "high"].rolling(rule.POS_WIN, min_periods=60).max()
    if len(s) < 2 or pd.isna(h.iloc[-1]) or h.iloc[-1] == 0:
        return None
    return float(s.iloc[-1] / h.iloc[-1] - 1) * 100


def collect(refresh: bool = False) -> dict:
    panels = factors.build(refresh=refresh)
    asof, stale = factors.align_asof(panels)
    official = data.official_valuation()

    rows = []
    for f in rule.FUNDS:
        code = f["code"]
        if code not in panels:
            continue
        p = panels[code]
        t = factors.temp_at(p["daily"], [asof], has_valuation=f.get("has_valuation", True))
        if t.empty:
            continue
        r = t.iloc[-1]
        is_val = r["temp_kind"] == factors.TEMP_KIND_VAL
        band, act = rule.band_of(float(r["temp"])) if is_val else ("价格温度", "无估值序列，仅参考")
        dy = (official.get(f["index"]) or {}).get("dy") if f.get("has_valuation", True) else None
        dd52 = _dd52(p["daily"], r["asof"])
        rows.append({
            "code": code, "name": f["short"], "index": f["index"],
            "index_name": f["index_name"],
            "temp": round(float(r["temp"]), 1), "temp_kind": r["temp_kind"],
            "band": band, "action": act,
            "close": round(float(r["close"]), 4),
            "pe": None if pd.isna(r["pe"]) else round(float(r["pe"]), 3),
            "spread": None if pd.isna(r["spread"]) else round(float(r["spread"]), 2),
            "dev": None if pd.isna(r["dev"]) else round(float(r["dev"]) * 100, 2),
            "pos": None if pd.isna(r["pos"]) else round(float(r["pos"]), 1),
            "dd52": None if dd52 is None else round(dd52, 2),
            "dy": dy,
            "dy_src": p.get("dy_src"),
            "sub": {k: (None if r.get(f"sub_{k}") is None or pd.isna(r.get(f"sub_{k}"))
                        else round(float(r[f"sub_{k}"]), 1)) for k in rule.FACTORS},
            "asof": r["asof"].strftime("%Y-%m-%d"),
            "stale": code in stale,
            "has_valuation": f.get("has_valuation", True),
        })
    if not rows:
        raise RuntimeError("没有任何标的算出温度（数据是否缺失？先跑 data.py）")

    # 板块温度：只用有估值口径的标的；自身历史分位（扩张窗口）仅作信息，不驱动动作
    val = [r for r in rows if r["has_valuation"]]
    board = float(np.mean([r["temp"] for r in val])) if val else float("nan")
    board_hist = []
    for code in rule.VALUED:
        m = panels[code]["monthly"]["temp"].dropna()
        if not m.empty:
            board_hist.append(m)
    board_q = None
    if board_hist:
        hist = pd.concat(board_hist, axis=1).mean(axis=1).dropna()
        if len(hist) >= rule.Q_MIN_N:
            board_q = float((hist < board).mean())

    hot = [r for r in val if r["temp"] >= rule.ABS_OUT]
    warm = [r for r in val if rule.ABS_CUT <= r["temp"] < rule.ABS_OUT]
    cold = [r for r in val if r["temp"] < 40]

    return {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "as_of": str(asof.date()),
        "stale": {c: d.strftime("%Y-%m-%d") for c, d in stale.items()},
        "board_temp": round(board, 1),
        "board_pct": None if board_q is None else round(board_q, 3),
        "rows": sorted(rows, key=lambda x: (-x["has_valuation"], x["temp"])),
        "hot": [r["code"] for r in hot],
        "warm": [r["code"] for r in warm],
        "cold": [r["code"] for r in cold],
        "weights": rule.W,
        "flags": {"xs": rule.XS_ENABLED, "time": rule.TIME_ENABLED},
        "bt": load_bt_summary(),
    }


def _fmt(v, suffix="", d=2, dash="N/A") -> str:
    return dash if v is None else f"{v:.{d}f}{suffix}"


def load_bt_summary() -> dict:
    """读回测摘要（`backtest.py` 产出）。缺失时返回 {}，正文会退化成「见报告」。"""
    if not os.path.exists(rule.OUT_BT_SUM):
        return {}
    try:
        with open(rule.OUT_BT_SUM, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"[v9:warn] 回测摘要读取失败（{type(exc).__name__}），正文退回文字说明",
              file=sys.stderr)
        return {}


def _bt_line(bt: dict) -> str:
    """回测结论压成一句话（数字全部来自摘要，不手抄）。"""
    if not bt:
        return ("回测结论见 `backtest/_v9_report.md`（先跑一次 `backtest.py` 才能在这里引用数字）。")
    s = bt["sims"]
    bench = s.get(rule.BASE_LABEL, {})
    over = s.get("时间：板块温度分位减仓", {})
    top2 = s.get("横截面：温度最低 2 只", {})
    # 结论必须随数据走：分位减仓明显优于基准时，不能再写「不产生 alpha」
    better = (over.get("calmar") or 0) >= (bench.get("calmar") or 0) + 0.10
    worse_xs = (top2.get("calmar") or 0) < (bench.get("calmar") or 0)
    if better and worse_xs:
        verdict = ("**分位减仓有边际改善（回撤压得比收益让渡更多），但改善集中在少数月份、"
                   "阈值又是样本内选的，不作为择时信号；横截面选品仍未跑赢基准。**")
    else:
        verdict = "**温度不产生择时/择基 alpha**。"
    return (f"样本（{bt['window']}，{bt['months']} 月）：基准 {len(rule.CODES)} 只等权 Calmar "
            f"{bench.get('calmar', float('nan')):.2f}；按分位减仓 "
            f"{over.get('calmar', float('nan')):.2f}（年化 "
            f"{over.get('cagr', 0) * 100:.1f}% vs {bench.get('cagr', 0) * 100:.1f}%）；"
            f"横截面 top2 {top2.get('calmar', float('nan')):.2f}；"
            f"绝对档 ≥{rule.ABS_CUT:.0f} 减半 / ≥{rule.ABS_OUT:.0f} 清仓 平均仓位 "
            f"{bt['abs_band']['pos']:.0%}（几乎不触发）。" + verdict)


def render(d: dict) -> str:
    lag = ""
    if d["stale"]:
        lag = (f"\n> ⚠ 数据滞后：{'、'.join(f'{c} 至 {v}' for c, v in d['stale'].items())} "
               f"未更新到 {d['as_of']}（各标的最后交易日不同，报告按众数对齐）")
    q_txt = "—" if d["board_pct"] is None else f"{d['board_pct'] * 100:.0f}%"
    L = ["# V9 · 红利五因子估值温度读数", "",
         f"> 生成 {d['generated']} · 数据截至 **{d['as_of']}** · 链路：{CHAIN}{lag}", ""]

    L += ["## 一、板块读数", "",
          f"- 有估值口径 **{len([r for r in d['rows'] if r['has_valuation']])} 只**的平均温度 "
          f"**{d['board_temp']:.1f}**（60 以上偏热，40 以下偏冷）。",
          f"- 板块温度自身历史分位 **{q_txt}**"
          "（仅供参考：回测里这个分位**没有**择时增量，见 §四）。",
          f"- 过热（≥{rule.ABS_OUT:.0f}）**{len(d['hot'])} 只**、偏热（{rule.ABS_CUT:.0f}–{rule.ABS_OUT:.0f}）"
          f"**{len(d['warm'])} 只**、偏冷（<40）**{len(d['cold'])} 只**。",
          "- **本策略不输出仓位倍率**：回测显示横截面选品为负贡献（top2 Calmar 0.72 vs 基准 1.03）、"
          "时间维度按分位减仓无增量（且分位口径越严越差），故温度只当「贵不贵」的体检表用。", ""]

    L += ["## 二、逐只档位与动作", "",
          "| 标的 | 跟踪指数 | 温度 | 状态 | 规则动作 | 利差 | PE | 乖离 | 位置 | 股息率 |",
          "|---|---|---:|---|---|---:|---:|---:|---:|---:|"]
    for r in d["rows"]:
        mark = "" if r["has_valuation"] else " †"
        sub = lambda k: "—" if r["sub"].get(k) is None else f"{r['sub'][k]:.0f}"
        dy = "N/A" if r["dy"] is None else f"{r['dy']:.2f}%"
        L.append(f"| {r['code']} {r['name']}{mark} | {r['index_name']} | **{r['temp']:.1f}** | "
                 f"{r['band']} | {r['action']} | {sub('spread')} | {sub('pe')} | "
                 f"{sub('dev')} | {sub('pos')} | {dy} |")
    L += ["", "> 子分列已统一成「越高越热」（利差列 = 100 − 利差分位）。"
              "† = 价格温度（乖离 50% + 位置 50%），**不与五因子温度比大小、不参与排名**。"
              "股息率取中证《指数估值》官方口径（仅展示，不进温度）。", ""]

    L += ["## 三、可执行清单（额度参考，非择时信号）", ""]
    if d["hot"]:
        names = "、".join(f"{rule.NAME[c]}({c})" for c in d["hot"])
        L.append(f"- **过热 ≥{rule.ABS_OUT:.0f}**：{names} —— 规则字面动作是「分批减仓」："
                 "只在需要腾出额度时优先减它们，不做一次性清仓。")
    if d["warm"]:
        names = "、".join(f"{rule.NAME[c]}({c})" for c in d["warm"])
        L.append(f"- **偏热 {rule.ABS_CUT:.0f}–{rule.ABS_OUT:.0f}**：{names} —— 不再新增；"
                 "若已有浮盈，可开始部分止盈。")
    if d["cold"]:
        names = "、".join(f"{rule.NAME[c]}({c})" for c in d["cold"])
        L.append(f"- **偏冷 <40**：{names} —— 新增额度优先给它们，分批建仓（不要一次打满）。")
    if not (d["hot"] or d["warm"] or d["cold"]):
        L.append("- 全池落在 40–65 中性带，无动作：持有 + 正常定投。")
    L += ["- 上述清单**不改变目标仓位**；仓位节奏请用 `v4_rsi/daily.py`（RSI14 择时）或 `v0_rank/rank.py`（打分）。", ""]

    L += ["## 四、口径与边界（必读）", "",
          "1. **股债收益差 = 股息率 − 10Y**（不再是 100/PE）：股息率取 `data/_div_yield_m.csv`（月度）——"
          "000922 / H30269 为真实序列（全收益指数 − 价格指数），"
          "930955 / 931468 / 000824 / 000825 为 000922 变化率代理 + 各自官网快照校准。",
          "2. **PE / PB 同源等权**：估值链已从「中证加权 PE + `PB = PE × ROE` 合成」换成 "
          f"etf.run **等权 PE + 真实 PB**（日频，{len(rule.VALUED)} 只有估值标的全部覆盖："
          "000922 / H30269 / 930955 / 931468 / 000824 / 000825）；**全池同一口径**，温度可横比。"
          f"PE + PB 仍占 {(rule.W['pe'] + rule.W['pb']) * 100:.0f}% 权重。",
          "3. **温度算在跟踪指数上**（规则 R0-2）：基金有费率与跟踪误差，估值只有指数口径。",
          "4. **两个标普系列无估值**（008163 / 005125）：中证官网、蛋卷、东财指数库三源皆无条目，"
          "只算价格温度并标 †，不进排名、不进横截面。价格用 501029（主，2017→）；562060 实测与 501029 "
          "全期日收益 corr≈0.44（仅近 250 日 0.89），长期背离，仅作回退、不替换。",
          f"5. **回测结论**：{_bt_line(d.get('bt') or {})}",
          "6. 数据分位窗口「近 2 年（730 日历日）」，需 ≥300 个日频观测才给分位；"
          "窗口内不足 300 个观测则不出温度（不拿全历史冒充）。",
          "7. **股息率代理说明**：4 只 proxy 的股息率形状基本等同中证红利，其「利差分位」与 000922 "
          "高度相关，不宜当作彼此独立的信号。", "",
          "> 仅为个人研究记录，不构成投资建议；市场有风险，投资需谨慎。", ""]
    return "\n".join(L)


def payload(d: dict) -> dict:
    bt = d.get("bt") or {}
    sims = bt.get("sims", {})

    def _cal(name):
        v = sims.get(name)
        return None if not v else v.get("calmar")

    return {
        "strategy": "dividend/v9_temp5",
        "as_of": d["as_of"],
        "generated": d["generated"],
        "board_temp": d["board_temp"],
        "board_pct": d["board_pct"],
        "version": "V9 · 五因子估值温度（35/20/16/15/14 · 股息率利差 · 近2年 · PB 合成）",
        "positioning": "体检表：只输出档位与额度参考，不输出仓位倍率、不做横截面选品",
        "weights": d["weights"],
        "flags": d["flags"],
        "funds": d["rows"],
        "lists": {"hot": d["hot"], "warm": d["warm"], "cold": d["cold"]},
        "stale": d["stale"],
        "backtest": {
            "window": bt.get("window"),
            "months": bt.get("months"),
            "bench_calmar": _cal(rule.BASE_LABEL),
            "overlay_calmar": _cal("时间：板块温度分位减仓"),
            "xs_top2_calmar": (bt.get("xs_calmar") or {}).get("top2"),
            "abs_band_pos": (bt.get("abs_band") or {}).get("pos"),
            "pb_gap": bt.get("pb_gap"),
            "conclusion": "温度不产生择时/择基 alpha，只作贵不贵的体检",
            "summary": "strategies/dividend/v9_temp5/backtest/_v9_bt_summary.json",
            "report": "strategies/dividend/v9_temp5/backtest/_v9_report.md",
        },
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="V9 五因子估值温度读数")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args(argv)

    d = collect(refresh=args.refresh)
    os.makedirs(rule.DAILY_DIR, exist_ok=True)
    with open(rule.OUT_MD, "w", encoding="utf-8") as fh:
        fh.write(render(d))
    with open(rule.OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(payload(d), fh, ensure_ascii=False, indent=1)

    q_txt = "—" if d["board_pct"] is None else f"{d['board_pct'] * 100:.0f}%"
    print(f"[v9] 数据截至 {d['as_of']} · 板块温度 {d['board_temp']:.1f}（自身分位 {q_txt}）")
    for r in d["rows"]:
        mark = "" if r["has_valuation"] else " †"
        print(f"[v9] {r['code']} {r['name']:<12s}{mark:<2s} 温度 {r['temp']:5.1f}  "
              f"{r['band']:<6s} {r['action']}")
    print(f"[v9] 过热 {len(d['hot'])} / 偏热 {len(d['warm'])} / 偏冷 {len(d['cold'])}")
    print(f"[v9] 产物 -> {os.path.relpath(rule.OUT_MD, rule.ROOT)} · "
          f"{os.path.relpath(rule.OUT_JSON, rule.ROOT)}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
