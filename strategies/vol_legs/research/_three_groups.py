# -*- coding: utf-8 -*-
"""分三组综合对比：A股5腿 / 美股2腿 / 组合层。

每组覆盖：
- A股5腿 = a500 / hs300 / kc50 / hkt / gold（v1~v6, v11~v13）
- 美股2腿 = ndx / spx（v7~v10, v11~v13）
- 组合层 = 各变种的默认组合净值（有就列）

只从各变种**已落盘的 backtest jsonl** 提取默认档，不重算、不改产物。
每个变种的 jsonl 结构不同，默认行识别规则按结构逐一定死，不用通用启发式：
- v1/v2/v3：扁平行，每腿一行，取 ann/mdd/sharpe（报告主表零费用口径；fee 列只作「扣费后年化」补充）。
- v5：扁平行，每腿一行，取 ann_fee/mdd_fee/sharpe_fee（报告主表即含费口径）。
- v4：组合层（无 leg），默认行 name 含「默认」，取 ann/mdd（资金曲线口径）。
- v6：扁平行，金腿用 key=B（控回撤，2026-11-09 起默认 TV10）。
- v7/v8/v9/v10：扁平行，默认行 variant 含「**...默认...**」，跳过「买入持有」。
- v11：嵌套行 {"leg": {...}}，取内层 gate（完整 v11-gate）。
- v12/v13：扁平行，每腿一行，取 ann/mdd/sharpe。
产物：`research/_three_groups.md`
"""
import io
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_VL = os.path.dirname(_HERE)
OUT = os.path.join(_HERE, "_three_groups.md")

GROUPS = {
    "a_share": dict(name="A股5腿", legs=["a500", "hs300", "kc50", "hkt", "gold"],
                    variants=["v1", "v2", "v3", "v4", "v5", "v6", "v11_gate", "v12", "v13"]),
    "us": dict(name="美股2腿", legs=["ndx", "spx"],
               variants=["v7", "v8", "v9", "v10", "v11_gate", "v12", "v13"]),
}
NAMES = dict(v1="V1 MA20", v2="V2 BOLL", v3="V3 MACD+BOLL", v4="V4 ERP带", v5="V5 趋势闸门",
             v6="V6 黄金", v7="V7 年线闸门", v8="V8 金叉×波动", v9="V9 波动目标",
             v10="V10 纯波动层", v11_gate="V11 gate", v12="V12 八态", v13="V13 九宫格")
PATHS = {
    "v1": "v1_price_volume/v1_1_ma20/backtest/_v1_1_ma20_bt.jsonl",
    "v2": "v2_boll_breakout/backtest/_v2_boll_breakout_bt.jsonl",
    "v3": "v3_macd_boll/backtest/_v3_macd_boll_bt.jsonl",
    "v4": "v4_erp_band/backtest/_v4_erp_band_bt.jsonl",
    "v5": "v5_trend_gate/backtest/_v5_trend_gate_bt.jsonl",
    "v6": "v6_gold/backtest/_v6_gold_bt.jsonl",
    "v7": "v7_us_gate/backtest/_v7_us_gate_bt.jsonl",
    "v8": "v8_golden_cross/backtest/_v8_golden_cross_bt.jsonl",
    "v9": "v9_vol_target/backtest/_v9_vol_target_bt.jsonl",
    "v10": "v10_vol_layer/backtest/_v10_vol_layer_bt.jsonl",
    "v11_gate": "v11_support_pressure/gate/backtest/_v11_gate_bt.jsonl",
    "v12": "v12_octo_trend/backtest/_v12_octo_trend_bt.jsonl",
    "v13": "v13_quad_9grid/backtest/_v13_quad_9grid_bt.jsonl",
}
LEG_NAMES = dict(a500="中证A500", hs300="沪深300", kc50="科创50", hkt="恒生科技",
                 gold="黄金", ndx="纳指100", spx="标普500")
FLAT_ZEROFEE = {"v1", "v2", "v3"}
FLAT_FEE = {"v5"}
US_VARIANTS = {"v7", "v8", "v9", "v10"}


def load(v):
    p = os.path.join(_VL, PATHS[v])
    if not os.path.exists(p):
        return []
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def _pack(r, fee=False):
    if fee:
        ann = r.get("ann_fee", r.get("ann"))
        mdd = r.get("mdd_fee", r.get("mdd"))
        shp = r.get("sharpe_fee", r.get("sharpe"))
    else:
        ann, mdd, shp = r.get("ann"), r.get("mdd"), r.get("sharpe")
    return dict(ann=ann, mdd=mdd, sharpe=shp, end=r.get("end", ""))


def pick(rows, v, leg):
    """返回该变种-该腿的默认档 dict(ann,mdd,sharpe,end)，缺则 None。"""
    if v == "v4":
        return None  # 组合层，无逐腿行
    if v == "v6":
        if leg != "gold":
            return None
        r = next((x for x in rows if x.get("key") == "B"), None)  # B 控回撤=默认 TV10
        return _pack(r) if r else None
    if v == "v11_gate":
        for row in rows:
            d = row.get("leg")
            if isinstance(d, dict) and d.get("leg") == leg:
                g = d.get("gate")  # 完整 v11-gate
                if g:
                    return dict(ann=g.get("ann"), mdd=g.get("mdd"),
                                sharpe=g.get("sharpe"), end=d.get("end", ""))
        return None
    if v in FLAT_ZEROFEE:
        for r in rows:
            if r.get("leg") == leg:
                return _pack(r, fee=False)
        return None
    if v in FLAT_FEE:
        for r in rows:
            if r.get("leg") == leg:
                return _pack(r, fee=True)
        return None
    if v in US_VARIANTS:
        for r in rows:
            if r.get("leg") != leg:
                continue
            var = str(r.get("variant", ""))
            if "默认" in var and "**" in var and "买入持有" not in var:
                return _pack(r)
        return None
    # v12 / v13：扁平，每腿一行
    for r in rows:
        if r.get("leg") == leg:
            return _pack(r)
    return None


def fmt(x, pct=False):
    if x is None:
        return "n/a"
    return f"{x:+.2%}" if pct else f"{x:.2f}"


def combos(v, rows):
    """组合层：从 jsonl 提取能代表组合口径的默认行。"""
    out = []
    if v == "v4":
        r = next((x for x in rows if x.get("kind") is None and "默认" in str(x.get("name", ""))), None)
        if r:
            mdd = r.get("mdd")
            if mdd is None:  # 从 summary 行补
                s = next((x for x in rows if x.get("kind") == "summary"), {})
                sm = s.get("summary", {})
                if isinstance(sm, dict):
                    mdd = sm.get("mdd")
            out.append((r.get("ann"), mdd, r.get("sharpe"), "沪深300股债两带·含费"))
    return out


def main():
    L = [
        "# 分三组综合对比（A股5腿 / 美股2腿 / 组合层）", "",
        "> 2026-11-16 生成。只汇总各变种已落盘的 `backtest/_*_bt.jsonl` 默认档，不重算、不改产物。"
        "各变种的样本区间、成本口径不同（详见各自报告），跨行比较请看脚注。",
        "> 默认档按各 jsonl 的实际结构逐一定死：美股 V7~V10 取 `variant` 含「默认」的加粗行（已排除买入持有），"
        "黄金 V6 取 B 控回撤（TV10 默认），V11 取完整 `gate`；口径按各报告主表：V1/V2/V3 零费用、V5 含费。", "",
        "## 1. A股5腿（v1~v6, v11~v13）", "",
        "| 腿 | 策略 | 年化 | 回撤 | 夏普 | 样本末日 |",
        "|---|---|---:|---:|---:|---|",
    ]
    data = {v: load(v) for v in PATHS}
    for leg in GROUPS["a_share"]["legs"]:
        for v in GROUPS["a_share"]["variants"]:
            x = pick(data[v], v, leg)
            if x is None:
                continue
            L.append(f"| {LEG_NAMES[leg]} | {NAMES[v]} | {fmt(x['ann'], True)} | "
                     f"{fmt(x['mdd'], True)} | {fmt(x['sharpe'])} | {str(x['end'])[:10]} |")
        L.append("")

    L += ["## 2. 美股2腿（v7~v10, v11~v13）", "",
          "| 腿 | 策略 | 年化 | 回撤 | 夏普 | 样本末日 |",
          "|---|---|---:|---:|---:|---|"]
    for leg in GROUPS["us"]["legs"]:
        for v in GROUPS["us"]["variants"]:
            x = pick(data[v], v, leg)
            if x is None:
                continue
            L.append(f"| {LEG_NAMES[leg]} | {NAMES[v]} | {fmt(x['ann'], True)} | "
                     f"{fmt(x['mdd'], True)} | {fmt(x['sharpe'])} | {str(x['end'])[:10]} |")
        L.append("")

    L += ["## 3. 组合层（默认组合净值）", "",
          "| 策略 | 组合年化 | 组合回撤 | 组合夏普 | 备注 |",
          "|---|---:|---:|---:|---|"]
    has_combo = False
    for v in ("v4", "v7", "v8", "v9", "v10", "v5", "v6"):
        for ann, mdd, shp, note in combos(v, data[v]):
            L.append(f"| {NAMES[v]} | {fmt(ann, True)} | {fmt(mdd, True)} | {fmt(shp)} | {note} |")
            has_combo = True
    if not has_combo:
        L.append("| — | n/a | n/a | n/a | 各 jsonl 未单列统一口径组合行，组合数字见各自 `backtest/_*.md` |")

    L += ["", "## 脚注", "",
          "- 口径按各报告主表：V1/V2/V3 主表零费用（`ann/mdd/sharpe`，fee 列只作「扣费后年化」补充）、"
          "V5 主表含费（`ann_fee/mdd_fee/sharpe_fee`）；美股 V7~V10 报告为含 10bp 成本口径；"
          "V4 是沪深300股债两带组合（无逐腿行），在组合层列；V6 只有黄金腿、取 B 控回撤（TV10，2026-11-09 起默认）；"
          "V11 取完整 `gate`；v12/v13 为逐腿默认行；美股 V7~V10 取各自 `variant` 含「默认」的加粗行。",
          "- A500 的 2005~2024 段是指数公司回溯模拟值，含前视偏差；美股是美元指数真实收盘；黄金是场内 518880。",
          "- 跨策略成本口径不同：A 类腿往返 60bp、美股 V7~V10 单边 10bp、V13 单边 5bp、黄金场内约 16bp、"
          "V4 含费，年化不可直接相减；同腿内夏普排序更有意义。",
          "- 若某腿某策略未出现，说明该变种池不含该腿或产物里没有对应默认行。", ""]
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"WROTE {OUT}")


if __name__ == "__main__":
    main()
