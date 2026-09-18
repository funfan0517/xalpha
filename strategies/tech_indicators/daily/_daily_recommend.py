# -*- coding: utf-8 -*-
"""技术指标策略 · 今日操作建议（纯 MACD + 打分制）。

流程（固定）：
1. **先刷新行情**：拉取最新日线并重算指标（见 _refresh_data.py）；
2. 基于刷新后的最新一根 K 线，对 55 只场内 ETF 给出「纯 MACD」与「打分制(≥5/≤2)」的当日动作：
   买入(0→1) / 卖出(1→0) / 持有(维持1) / 空仓(维持0)；
3. **非最新数据一律标注**：以全池最新交易日为基准，晚于它的标的标「陈旧(日期)」，刷新失败标「失败」。

产物：strategies/tech_indicators/daily/_daily_recommend.md。机械规则输出，非投资建议。
"""
import importlib.util
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)                    # strategies/tech_indicators
_ROOT = os.path.dirname(os.path.dirname(_PARENT))   # 仓库根

_spec = importlib.util.spec_from_file_location("ti_base", os.path.join(_PARENT, "backtest.py"))
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)

# 公用行情刷新器（data/_refresh_data.py）
sys.path.insert(0, os.path.join(_ROOT, "data"))
import _refresh_data  # noqa: E402

DATA = base.DATA
OUT = os.path.join(_HERE, "_daily_recommend.md")
SCORE_ENTER, SCORE_EXIT = 5, 2


def load_names():
    m = {}
    p = os.path.join(DATA, "_universe.md")
    for line in open(p, encoding="utf-8"):
        if not line.startswith("|"):
            continue
        f = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(f) >= 6 and len(f[4]) == 6 and f[4].isdigit():
            m[f[4]] = f[5] or f[4]
    return m


def aligned(code, ind, lk):
    """按日期对齐：返回共同日期、对应收盘价、以及对齐后的指标字典。"""
    di = {d: i for i, d in enumerate(lk[code]["dates"])}
    ii = {d: i for i, d in enumerate(ind[code]["dates"])}
    common = [d for d in ind[code]["dates"] if d in di]
    close = base._arr([lk[code]["close"][di[d]] for d in common])
    r = {col: [vals[ii[d]] for d in common] for col, vals in ind[code].items() if col != "dates"}
    return common, close, r


def build_score(close, r, enter=SCORE_ENTER, exit_th=SCORE_EXIT):
    n = len(close)
    ma20, ma60 = base._arr(r["MA20"]), base._arr(r["MA60"])
    dif, dea = base._arr(r["DIF"]), base._arr(r["DEA"])
    k, d = base._arr(r["K"]), base._arr(r["D"])
    mid = base._arr(r["BOLL_MID"])
    vol = base._arr(r["volume"])
    vol_ma5 = pd.Series(vol).rolling(5).mean().to_numpy()
    prev = np.concatenate([[np.nan], close[:-1]])
    sig = np.zeros(n, dtype=int)
    p = 0
    for t in range(1, n):
        if any(np.isnan(x) for x in (ma20[t], ma60[t], dif[t], dea[t], k[t], d[t], mid[t], vol_ma5[t])):
            sig[t] = p
            continue
        sc = (int(close[t] > ma20[t]) + int(close[t] > ma60[t]) + int(dif[t] > dea[t])
              + int(dif[t] > 0) + int(k[t] > d[t]) + int(close[t] > mid[t])
              + int(vol[t] > vol_ma5[t] and close[t] > prev[t]))
        if p == 0 and sc >= enter:
            p = 1
        elif p == 1 and sc <= exit_th:
            p = 0
        sig[t] = p
    return sig


def last_score(close, r):
    ma20, ma60 = base._arr(r["MA20"]), base._arr(r["MA60"])
    dif, dea = base._arr(r["DIF"]), base._arr(r["DEA"])
    k, d = base._arr(r["K"]), base._arr(r["D"])
    mid = base._arr(r["BOLL_MID"])
    vol = base._arr(r["volume"])
    vol_ma5 = pd.Series(vol).rolling(5).mean().to_numpy()
    t = len(close) - 1
    return (int(close[t] > ma20[t]) + int(close[t] > ma60[t]) + int(dif[t] > dea[t])
            + int(dif[t] > 0) + int(k[t] > d[t]) + int(close[t] > mid[t])
            + int(vol[t] > vol_ma5[t] and close[t] > close[t - 1]))


def act(sig):
    cur, prev = int(sig[-1]), int(sig[-2])
    if cur == 1 and prev == 0:
        return "买入"
    if cur == 0 and prev == 1:
        return "卖出"
    return "持有" if cur == 1 else "空仓"


def freshness(code, date, maxdate, fails):
    if code in fails:
        return "❌刷新失败"
    if date == maxdate:
        return "✅最新"
    return f"⚠️陈旧 {date}"


def main():
    latest, fails = _refresh_data.ensure_fresh()
    maxdate = max(latest.values())
    ind, lk = base.load()
    names = load_names()
    rows = []
    for code in ind:
        if code not in lk:
            continue
        dates, close, r = aligned(code, ind, lk)
        sm = base.build_signals(close, r)["MACD"]
        rows.append({
            "code": code, "name": names.get(code, code), "date": dates[-1], "close": close[-1],
            "macd": act(sm), "score": last_score(close, r), "score_act": act(build_score(close, r)),
            "fresh": freshness(code, dates[-1], maxdate, fails),
        })

    stale = [x for x in rows if x["fresh"].startswith("⚠️") or x["fresh"].startswith("❌")]

    def mk_table(items):
        out = ["| 代码 | 名称 | 收盘 | 数据 | MACD | 打分 | 打分制 |", "|---|---|---|---|---|---|---|"]
        for x in sorted(items, key=lambda z: z["code"]):
            out.append(f"| {x['code']} | {x['name']} | {x['close']:.3f} | {x['fresh']} | "
                       f"{x['macd']} | {x['score']}/7 | {x['score_act']} |")
        return out

    buys = [x for x in rows if x["macd"] == "买入" or x["score_act"] == "买入"]
    sells = [x for x in rows if x["macd"] == "卖出" or x["score_act"] == "卖出"]

    L = [
        "# 技术指标策略 · 今日操作建议",
        f"> 已先刷新行情 · **全池最新交易日：{maxdate}** · 纯 MACD 与 打分制(≥{SCORE_ENTER}/≤{SCORE_EXIT})。",
        f"> 共 {len(rows)} 只：最新 {len(rows) - len(stale)} 只 · ⚠️陈旧 {sum(1 for x in stale if x['fresh'].startswith('⚠️'))} 只 · "
        f"❌刷新失败 {len(fails)} 只。**陈旧/失败的信号不代表最新，请谨慎使用。**",
        "> 机械规则输出，**非投资建议**。",
        "",
        "## 动作汇总",
        "",
        "| 策略 | 买入 | 卖出 | 持有 | 空仓 |",
        "|---|---|---|---|---|",
    ]
    for key, lab in (("macd", "纯 MACD"), ("score_act", "打分制")):
        L.append(f"| {lab} | "
                 f"{sum(1 for x in rows if x[key] == '买入')} | {sum(1 for x in rows if x[key] == '卖出')} | "
                 f"{sum(1 for x in rows if x[key] == '持有')} | {sum(1 for x in rows if x[key] == '空仓')} |")

    L += ["", "## 今日买入（新开仓）", ""] + (mk_table(buys) if buys else ["（无）"])
    L += ["", "## 今日卖出（平仓）", ""] + (mk_table(sells) if sells else ["（无）"])
    L += ["", "## 非最新数据（陈旧/刷新失败）", ""] + (mk_table(stale) if stale else ["（全部为最新）"])
    L += ["", "## 全标的明细", ""] + mk_table(rows)

    open(OUT, "w", encoding="utf-8").write("\n".join(L))
    print("建议:", OUT)
    print("最新交易日:", maxdate, "| 陈旧", len(stale), "| 失败", len(fails))
    print("MACD 买入:", [x["code"] for x in rows if x["macd"] == "买入"])
    print("MACD 卖出:", [x["code"] for x in rows if x["macd"] == "卖出"])
    print("打分制 买入:", [x["code"] for x in rows if x["score_act"] == "买入"])
    print("打分制 卖出:", [x["code"] for x in rows if x["score_act"] == "卖出"])
    if fails:
        print("刷新失败:", fails)


if __name__ == "__main__":
    main()
