# -*- coding: utf-8 -*-
"""V12 · 量能口径探针（ETF 自身量 vs 指数量能）—— 这条策略最重要的一次口径检验。

**问题**：八态的强弱回调/反弹全靠量比 `rvol = 当日量 / 20 日均量` 分档。ETF 的成交量能不能当分母？

**做法**：价格固定（510300 前复权 / 中证300 指数收盘各跑一遍），只换 `rvol` 的分母，
看规格 §3 的**四组配对分离度**（急涨−慢涨、弱回调−强回调、强反弹−弱反弹、急跌−慢跌）
是否还全为正。

**结论**（`_probe_volume.md` 里有完整表）：
- `510300` **自身成交量** → 弱回调−强回调 **−0.77pp（符号反了）**，其余三组仍为正
  （所以只看总收益看不出来，必须逐对看分离度）；
- 换成 `SH000300` 指数量能 → +0.20pp（2013 起）/ **+1.20pp**（2005 起）；
- 换成 `SH000001` 上证指数量能 → +0.64pp / **+2.02pp**。

**为什么**：`v18_volume_notes/ohlcv.py::EDGE` 那张表已经写过 ——「宽基 ETF 的量由做市与
申赎套利主导，高量多是活跃度噪声」。本探针是它在八态上的独立复现。

→ 故 `rule.VOL_MODE = "market"`：A股 3 腿用 `SH000300` 指数量能。

用法：`python strategies/vol_legs/v12_octo_trend/research/probe_volume/_probe_volume.py`
"""
import io
import json
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_RES = os.path.dirname(_HERE)
_V12 = os.path.dirname(_RES)
if _V12 not in sys.path:
    sys.path.insert(0, _V12)

import rule           # noqa: E402
import data as vdata  # noqa: E402
import engine         # noqa: E402

OUT_MD = os.path.join(_HERE, "_probe_volume.md")
CSI = os.path.join(rule._ROOT, "data", "_bt_caches", "csi_000300.csv")


def _idx(code):
    """`_cn_index_klines.json` 的一列 -> Series。"""
    node = json.loads(io.open(vdata.CN_IDX_LIB, encoding="utf-8").read())[code]
    return pd.Series(node["volume"], index=pd.to_datetime(node["dates"]), dtype="float64")


def csi_close():
    """中证300 指数收盘（`data/_bt_caches/csi_000300.csv`，2005 起，无成交量）。"""
    d = pd.read_csv(CSI)
    d["date"] = pd.to_datetime(d["date"])
    return d.set_index("date")["close"].astype(float).sort_index()


def probe(price, vol, tag, start):
    """固定价格、换量能 -> (四组配对分离度, 逐态 fwd20, 实际起始日)。

    ⚠ 量能序列覆盖不到的日期会被 `dropna()` 丢掉，**实际样本起点可能晚于 `start`** ——
    所以返回值里带上真实起点，表里必须显示它，否则会拿 2013 起的样本冒充 2005 起。
    """
    px = price[price.index >= pd.Timestamp(start)]
    df = pd.DataFrame({"close": px, "volume": vol.reindex(px.index)}).dropna()
    ind = engine.indicators(df)
    cx = engine.classify(ind)
    ss = engine.state_stats(df["close"], cx)
    return dict(tag=tag, ss=ss, n=ss["days"], real_start=str(df.index[0].date()))


def main():
    etf = vdata.frame("hs300", vol_mode="etf")     # 原始 ETF 自身量（不走市场量能替换）
    v_etf = etf["volume"]
    v_300 = _idx("SH000300")
    v_001 = _idx("SH000001")
    idx_px = csi_close()

    cases = []
    # A. 价格 = 510300 前复权（执行口径），只换量能
    for tag, vol in (("510300 自身成交量", v_etf),
                     ("SH000300 指数成交量", v_300),
                     ("SH000001 上证成交量", v_001)):
        cases.append(probe(etf["close"], vol, f"510300 价 · {tag}", "2013-01-04"))
    # B. 价格 = 中证300 指数（规格标定口径），样本前推到 2005；ETF 量 2013 才有 -> 只作 C 组
    for tag, vol in (("SH000300 指数成交量", v_300),
                     ("SH000001 上证成交量", v_001)):
        cases.append(probe(idx_px, vol, f"中证300 指数价 · {tag}", "2005-01-04"))
    cases.append(probe(idx_px, v_etf, "中证300 指数价 · 510300 自身成交量", "2013-01-04"))

    L = [
        "# V12 · 量能口径探针（八态的 rvol 该用什么分母）",
        "",
        "> 由 `research/probe_volume/_probe_volume.py` 生成。",
        "> 价格固定、只换 `rvol` 的分母，看规格 §3 的四组配对分离度是否还成立。",
        "> 判定标准：**四组全为正才算口径成立**；某组为负说明这条量能序列不能用来分强弱。",
        "",
        "## 1. 四组配对分离度（fwd20 差）",
        "",
        "| 价格口径 | 量能 | 急涨−慢涨 | 弱回调−强回调 | 强反弹−弱反弹 | 急跌−慢跌 | 有效天 | 实际起点 |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for c in cases:
        by = {f"{p['a']}|{p['b']}": p for p in c["ss"]["pairs"]}
        cells = []
        for a, b in (("急涨", "慢涨"), ("弱回调", "强回调"), ("强反弹", "弱反弹"), ("急跌", "慢跌")):
            p = by.get(f"{a}|{b}")
            cells.append("—" if not p else f"{p['dfwd'] * 100:+.2f}pp")
        ok = all(x != "—" and not x.startswith("-") for x in cells)
        flag = "" if ok else " ⚠"
        L.append(f"| {c['tag'].split(' · ')[0]} | {c['tag'].split(' · ')[1]} | "
                 + " | ".join(cells) + f" | {c['n']} | {c['real_start']}{flag} |")

    L += ["", "⚠ = 有配对为负。**ETF 自身成交量那两行是唯一出现符号反转的口径** —— "
              "而且它们只反转「强弱回调」这一对，总收益上看不出来（反而因为抬高了整体仓位更好看），"
              "这正是必须逐对查分离度、不能只看年化的原因。", ""]

    L += ["## 2. 逐态 fwd20（同一价格口径下三种量能的对照）", ""]
    for c in cases:
        by = {x["state"]: x for x in c["ss"]["rows"]}
        L.append(f"**{c['tag']}**（{c['n']} 天）")
        L.append("")
        L.append("| " + " | ".join(rule.STATE_ORDER) + " |")
        L.append("|" + "---:|" * len(rule.STATE_ORDER))
        L.append("| " + " | ".join(
            f"{by[s]['fwd'] * 100:+.2f}%" if s in by else "—" for s in rule.STATE_ORDER) + " |")
        L.append("")

    L += [
        "## 3. 结论",
        "",
        "1. **ETF 自身成交量不能当 `rvol` 的分母**：两个价格口径下「弱回调 − 强回调」都是**负的**"
        "（−0.77pp / −0.82pp），而规格的原始结论是弱回调 +1.87pp 更好；"
        "「强反弹 − 弱反弹」也随之掉到 +0.30 / −0.03。原因不是八态写错了，"
        "是宽基 ETF 的量由做市与申赎套利主导（`v18_volume_notes/ohlcv.py::EDGE` 同一个结论）。",
        "2. **指数量能把符号修回来了**：`SH000300` 两个窗口四组**全为正**（+0.20 / +1.20）；"
        "`SH000001` 的弱回调分离度更高（+0.64 / +2.02），但长样本里「强反弹 − 弱反弹」"
        "是 **−0.08**（没有通过）。取 `SH000300` 的理由因此有两条：四组全正 + 它就是规格标定时"
        "用的那一条量能序列。",
        "3. **2013 起的窗口里分离度明显更小**：那 13 年沪深300 是宽幅震荡市，弱回调样本只有 157 天。"
        "所以本策略的 `弱回调=满仓` 这一格在近几年样本里**边际很薄**（+0.20pp），"
        "真正的收益贡献来自「急涨满仓 / 慢跌压到 20%」这两端 —— 与规格 §4① 一致。",
        "4. **一句话给以后改口径的人**：八态里凡是带「强弱」的态，量能口径比价格口径重要；"
        "换价格源（指数 ↔ ETF）四组只差 0.1~0.3pp，换量能源（ETF ↔ 指数）能直接翻转符号。",
        "",
        "> 机械规则输出，非投资建议。",
    ]

    with io.open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))
    print("报告:", OUT_MD)
    for c in cases:
        by = {f"{p['a']}|{p['b']}": p for p in c["ss"]["pairs"]}
        cells = []
        for a, b in (("急涨", "慢涨"), ("弱回调", "强回调"), ("强反弹", "弱反弹"), ("急跌", "慢跌")):
            p = by.get(f"{a}|{b}")
            cells.append("—" if not p else f"{p['dfwd'] * 100:+.2f}")
        print(f"  {c['tag']:34s} " + " / ".join(cells) + f"  (n={c['n']})")


if __name__ == "__main__":
    main()
