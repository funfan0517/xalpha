# -*- coding: utf-8 -*-
"""技术指标策略 · 今日操作建议（MACD / V3 / V7 三策略）。

流程（固定）：
1. **先刷新行情**：拉取最新日线并重算指标（data/_refresh_data.py）；
2. 基于刷新后的最新一根 K 线，对 55 只场内 ETF 给出三个策略的当日动作：
   - **MACD**：基础 MACD —— DIF 上穿 DEA（金叉）买，DIF 下穿 DEA（死叉）卖；
   - **V3**：变种 3 MACD+BOLL（自适应窗口）—— 零轴上 + 布林开口 + 收盘上穿上轨买；
     MACD 死叉 或 收盘下穿布林中轨卖；
   - **V7**：变种 7 风格路由 —— 按 data/_universe.md 的风格列自动选策略：
     避险→买入持有 · 防御→BOLL · 中枢→MACD · 进攻→V3 · 其他→MACD；
   动作：买入(0→1) / 卖出(1→0) / 持有(维持1) / 空仓(维持0)；
3. **非最新数据一律标注**：以全池最新交易日为基准，晚于它的标的标「陈旧(日期)」，刷新失败标「失败」。

产物：strategies/tech_indicators/daily/_daily_recommend.md。机械规则输出，非投资建议。
"""
import importlib.util
import os
import sys

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)                    # strategies/tech_indicators
_ROOT = os.path.dirname(os.path.dirname(_PARENT))   # 仓库根


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


base = _load(os.path.join(_PARENT, "backtest.py"), "ti_base")
V3 = _load(os.path.join(_PARENT, "v3_macd_boll", "backtest.py"), "ti_v3")

# 公用行情刷新器（data/_refresh_data.py）
sys.path.insert(0, os.path.join(_ROOT, "data"))
import _refresh_data  # noqa: E402

DATA = base.DATA
OUT = os.path.join(_HERE, "_daily_recommend.md")

BH = "买入持有"
V3_LB = "v3 MACD+BOLL"
# V7 路由：风格 -> 策略（与 v7_style_route/backtest.py::ROUTE 一致）
ROUTE = {"避险": BH, "防御": "BOLL", "中枢": "MACD", "进攻": V3_LB, "其他": "MACD"}
COLS = ["MACD", "V3", "V7"]
RULE_DESC = {
    "MACD": "DIF 上穿 DEA（金叉）买入 · DIF 下穿 DEA（死叉）卖出",
    "V3": "DIF>0 且 DIF>DEA 且 布林开口放大 且 收盘上穿上轨 买入 · "
          "MACD 死叉 或 收盘下穿布林中轨 卖出",
    "V7": "按标的风格自动选策略：避险→买入持有 · 防御→BOLL · 中枢/其他→MACD · 进攻→V3",
}


def load_meta():
    """代码 -> (场内名称, 风格)，源自 data/_universe.md。"""
    m = {}
    p = os.path.join(DATA, "_universe.md")
    for line in open(p, encoding="utf-8"):
        if not line.startswith("|"):
            continue
        f = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(f) >= 10 and len(f[4]) == 6 and f[4].isdigit():
            m[f[4]] = (f[5] or f[4], f[9] or "其他")
    return m


def aligned(code, ind, lk):
    """按日期对齐：返回共同日期、对应收盘价、以及对齐后的指标字典。"""
    di = {d: i for i, d in enumerate(lk[code]["dates"])}
    ii = {d: i for i, d in enumerate(ind[code]["dates"])}
    common = [d for d in ind[code]["dates"] if d in di]
    close = base._arr([lk[code]["close"][di[d]] for d in common])
    r = {col: [vals[ii[d]] for d in common] for col, vals in ind[code].items() if col != "dates"}
    return common, close, r


def sig_bh(close, _r):
    return np.ones(len(close), dtype=int)


def build_all(close, r, style):
    """返回 {策略标签: 信号序列} —— MACD / V3 / V7 三策略。"""
    bs = base.build_signals(close, r)
    sigs = {"MACD": bs["MACD"], "BOLL": bs["BOLL"], V3_LB: V3.build_variant(close, r)}
    route = ROUTE.get(style, "MACD")
    sigs[BH] = sig_bh(close, r)
    return {"MACD": sigs["MACD"], "V3": sigs[V3_LB], "V7": sigs[route]}, route


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


def mk_table(items):
    out = ["| 代码 | 名称 | 收盘 | 数据 | 风格 | V7 路由 | MACD | V3 | V7 |",
           "|---|---|---|---|---|---|---|---|---|"]
    for x in sorted(items, key=lambda z: z["code"]):
        out.append(f"| {x['code']} | {x['name']} | {x['close']:.3f} | {x['fresh']} | "
                   f"{x['style']} | {x['route']} | {x['a']['MACD']} | {x['a']['V3']} | {x['a']['V7']} |")
    return out


def main():
    latest, fails = _refresh_data.ensure_fresh()
    maxdate = max(latest.values())
    ind, lk = base.load()
    meta = load_meta()
    rows = []
    for code in ind:
        if code not in lk:
            continue
        dates, close, r = aligned(code, ind, lk)
        name, style = meta.get(code, (code, "其他"))
        sigs, route = build_all(close, r, style)
        rows.append({
            "code": code, "name": name, "date": dates[-1], "close": close[-1],
            "style": style, "route": route,
            "a": {k: act(v) for k, v in sigs.items()},
            "fresh": freshness(code, dates[-1], maxdate, fails),
        })

    stale = [x for x in rows if x["fresh"].startswith("⚠️") or x["fresh"].startswith("❌")]
    buys = [x for x in rows if any(x["a"][k] == "买入" for k in COLS)]
    sells = [x for x in rows if any(x["a"][k] == "卖出" for k in COLS)]
    both = [x for x in rows if all(x["a"][k] == "买入" for k in COLS)]
    both_sell = [x for x in rows if all(x["a"][k] == "卖出" for k in COLS)]

    L = [
        "# 技术指标策略 · 今日操作建议（MACD / V3 / V7）",
        f"> 已先刷新行情 · **全池最新交易日：{maxdate}**。",
        f"> 共 {len(rows)} 只：最新 {len(rows) - len(stale)} 只 · "
        f"⚠️陈旧 {sum(1 for x in stale if x['fresh'].startswith('⚠️'))} 只 · "
        f"❌刷新失败 {len(fails)} 只。**陈旧/失败的信号不代表最新，请谨慎使用。**",
        "> 机械规则输出，**非投资建议**。",
        "",
        "## 策略口径",
        "",
        "| 标签 | 策略 | 规则 |",
        "|---|---|---|",
    ]
    for k in COLS:
        L.append(f"| **{k}** | {'基础 MACD' if k == 'MACD' else ('变种3 MACD+BOLL' if k == 'V3' else '变种7 风格路由')} | {RULE_DESC[k]} |")

    L += ["", "## 动作汇总", "", "| 策略 | 买入 | 卖出 | 持有 | 空仓 |", "|---|---|---|---|---|"]
    for k in COLS:
        L.append(f"| {k} | " + " | ".join(
            str(sum(1 for x in rows if x["a"][k] == v)) for v in ("买入", "卖出", "持有", "空仓")) + " |")

    L += ["", "## 三策略共振", ""]
    L.append(f"- **三策略同时买入**：{('、'.join(x['code'] + ' ' + x['name'] for x in both)) if both else '（无）'}")
    L.append(f"- **三策略同时卖出**：{('、'.join(x['code'] + ' ' + x['name'] for x in both_sell)) if both_sell else '（无）'}")

    L += ["", "## 今日买入（新开仓 · 任一策略）", ""] + (mk_table(buys) if buys else ["（无）"])
    L += ["", "## 今日卖出（平仓 · 任一策略）", ""] + (mk_table(sells) if sells else ["（无）"])
    L += ["", "## 非最新数据（陈旧/刷新失败）", ""] + (mk_table(stale) if stale else ["（全部为最新）"])
    L += ["", "## 全标的明细", ""] + mk_table(rows)

    open(OUT, "w", encoding="utf-8").write("\n".join(L))
    print("建议:", OUT)
    print("最新交易日:", maxdate, "| 陈旧", len(stale), "| 失败", len(fails))
    for k in COLS:
        print(f"{k} 买入:", [x["code"] for x in rows if x["a"][k] == "买入"],
              "| 卖出:", [x["code"] for x in rows if x["a"][k] == "卖出"])
    print("三策略共振买入:", [x["code"] for x in both], "| 共振卖出:", [x["code"] for x in both_sell])
    if fails:
        print("刷新失败:", fails)


if __name__ == "__main__":
    main()
