# -*- coding: utf-8 -*-
"""技术指标策略 · 今日操作建议（**固定 6 策略：MACD / V3 / V6 / V7 / V9 / V10**）。

流程（固定）：
1. **先刷新行情**：拉取最新日线并重算指标（data/_refresh_data.py）；
2. 基于刷新后的最新一根 K 线，对 55 只场内 ETF 依次跑**固定的六个策略**（见 `STRATS`）：
   - **MACD**：基础 MACD —— DIF 上穿 DEA（金叉）买，DIF 下穿 DEA（死叉）卖；
   - **V3**：变种 3 MACD+BOLL（自适应窗口）—— 零轴上 + 布林开口 + 收盘上穿上轨买；
     MACD 死叉 或 收盘下穿布林中轨卖；
   - **V6**：变种 6 打分制（4 项看多票数，阈值 = 参数网格最优 4/2）；
   - **V7**：变种 7 风格路由 —— 按 data/_universe.md 的风格列自动选策略：
     避险→买入持有 · 防御→BOLL · 中枢→MACD · 进攻→V3 · 其他→MACD；
   - **V9**：变种 9 右侧买入（五票确认）—— 状态机型，读 `v9_right_side/daily/_right_side_scan_out.jsonl`
     的当日扫描结果：READY→买入（右侧买点）· WATCH→观察（差一票）· VETO→否决 · NO→空仓；
   - **V10**：变种 10 亮灯（门槛层 + 灯评分）—— 状态机型，读 `v10_lights/daily/_lights_scan_out.jsonl`
     的当日目标仓位：target>0→买入（建仓/持有）· 否则空仓；
   动作：买入(0→1) / 卖出(1→0) / 持有(维持1) / 空仓(维持0)；
   V9/V10 是**状态机型**（自带 rule/engine，需要 OHLCV 与门槛判定），不在本引擎内重算 ——
   由各自的 scan.py 先跑一遍落盘，本脚本只读产物，避免两套口径与重复抓数。
   本脚本**不负责**跑那两个 scan（入口见 run_daily_tech_indicators.ps1）。
3. **非最新数据一律标注**：以全池最新交易日为基准，晚于它的标的标「陈旧(日期)」，刷新失败标「失败」。

产物：strategies/tech_indicators/daily/_daily_recommend.md。机械规则输出，非投资建议。
"""
import importlib.util
import json
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
V6 = _load(os.path.join(_PARENT, "v6_score_wf", "backtest.py"), "ti_v6")

# 公用行情刷新器（data/_refresh_data.py）
sys.path.insert(0, os.path.join(_ROOT, "data"))
import _refresh_data  # noqa: E402

DATA = base.DATA
OUT = os.path.join(_HERE, "_daily_recommend.md")

# 状态机型变种 v9 / v10 的当日扫描产物（由各自 scan.py 生成）
V9_FILE = os.path.join(_PARENT, "v9_right_side", "daily", "_right_side_scan_out.jsonl")
V10_FILE = os.path.join(_PARENT, "v10_lights", "daily", "_lights_scan_out.jsonl")
V9_LABEL = {"READY": "买入", "WATCH": "观察", "VETO": "否决", "NO": "空仓"}
STATE_STRATS = ("V9", "V10")

BH = "买入持有"
V3_LB = "v3 MACD+BOLL"
# V7 路由：风格 -> 策略（与 v7_style_route/backtest.py::ROUTE 一致）
ROUTE = {"避险": BH, "防御": "BOLL", "中枢": "MACD", "进攻": V3_LB, "其他": "MACD"}
# ★ 每日固定跑这六个策略（顺序即报告表头顺序）—— 增删只改这一处
STRATS = ["MACD", "V3", "V6", "V7", "V9", "V10"]
HEAD = {"V9": "V9右侧", "V10": "V10亮灯"}          # 表头显示名（键仍是 V9/V10）
V6_E, V6_X = V6.DEFAULT_PARAM          # v6 采用阈值（4 票制的参数网格最优）
STRAT_NAME = {
    "MACD": "基础 MACD",
    "V3": "变种3 MACD+BOLL",
    "V6": f"变种6 打分制(≥{V6_E}/≤{V6_X})",
    "V7": "变种7 风格路由",
    "V9": "变种9 右侧买入(五票确认)",
    "V10": "变种10 亮灯(门槛+灯评分)",
}
RULE_DESC = {
    "MACD": "DIF 上穿 DEA（金叉）买入 · DIF 下穿 DEA（死叉）卖出",
    "V3": "DIF>0 且 DIF>DEA 且 布林开口放大 且 收盘上穿上轨 买入 · "
          "MACD 死叉 或 收盘下穿布林中轨 卖出",
    "V6": f"4 项看多票数（价>MA20且>MA60 · DIF>DEA且DIF>0 · K>D · 放量上涨）求和，"
          f"票数≥{V6_E} 买入、≤{V6_X} 卖出（阈值 = 参数网格最优）",
    "V7": "按标的风格自动选策略：避险→买入持有 · 防御→BOLL · 中枢/其他→MACD · 进攻→V3",
    "V9": "五票全过（结构止跌 · 结构翻越 · 均线转向 · 零轴上多头 · 放量上涨）且未被硬否决 = 右侧买点；"
          "四票标「观察」，等次日确认不提前埋伏",
    "V10": "门槛层（流动性/成立年限/中期趋势/暴涨/追高/资金衰竭/指标就绪）全过，"
           "再加灯评分达 enter_min 与必备灯 → 目标仓位 > 0；否则空仓",
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
    """返回 ({策略标签: 信号序列}, V7 实际路由到的策略) —— 引擎内的 MACD / V3 / V6 / V7。

    V9/V10 是状态机型变种，信号来自各自 scan.py 的产物，不在这里重算。
    """
    bs = base.build_signals(close, r)
    sigs = {"MACD": bs["MACD"], "BOLL": bs["BOLL"], V3_LB: V3.build_variant(close, r)}
    route = ROUTE.get(style, "MACD")
    sigs[BH] = sig_bh(close, r)
    sigs["V6"] = V6.build_score(close, r)
    four = [k for k in STRATS if k not in STATE_STRATS]
    return {k: sigs[{"V3": V3_LB, "V7": route}.get(k, k)] for k in four}, route


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


def _last_by_code(path):
    """jsonl -> {code: row}（同代码以最后一行为准；只取 ok 行）。"""
    out = {}
    if not os.path.exists(path):
        return out
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line.startswith("{"):
            continue
        r = json.loads(line)
        if r.get("ok"):
            out[str(r.get("inner"))] = r
    return out


def load_state_scans():
    """v9 / v10 当日扫描产物 -> ({code: 动作}, 数据日)。

    产物缺失（没跑 scan）时返回空映射与 None，表格里该列显示 `—` —— 让缺口可见，
    不用上一交易日的旧结论冒充今日信号。
    """
    v9 = {c: V9_LABEL.get(r.get("status"), "空仓") for c, r in _last_by_code(V9_FILE).items()}
    v10 = {c: ("买入" if float(r.get("target") or 0) > 0 else "空仓")
           for c, r in _last_by_code(V10_FILE).items()}
    d9 = max((r.get("date") for r in _last_by_code(V9_FILE).values() if r.get("date")), default=None)
    d10 = max((r.get("date") for r in _last_by_code(V10_FILE).values() if r.get("date")), default=None)
    return v9, d9, v10, d10


def mk_table(items):
    heads = [HEAD.get(k, k) for k in STRATS]
    out = ["| 代码 | 名称 | 收盘 | 数据 | 风格 | V7 路由 | " + " | ".join(heads) + " |",
           "|---|---|---|---|---|---|" + "---|" * len(STRATS)]
    for x in sorted(items, key=lambda z: z["code"]):
        acts = " | ".join(x["a"][k] for k in STRATS)
        out.append(f"| {x['code']} | {x['name']} | {x['close']:.3f} | {x['fresh']} | "
                   f"{x['style']} | {x['route']} | {acts} |")
    return out


def main():
    latest, fails = _refresh_data.ensure_fresh()
    maxdate = max(latest.values())
    ind, lk = base.load()
    meta = load_meta()
    v9, d9, v10, d10 = load_state_scans()
    rows = []
    for code in ind:
        if code not in lk:
            continue
        dates, close, r = aligned(code, ind, lk)
        name, style = meta.get(code, (code, "其他"))
        sigs, route = build_all(close, r, style)
        a = {k: act(v) for k, v in sigs.items()}
        a["V9"] = v9.get(code, "—")
        a["V10"] = v10.get(code, "—")
        rows.append({
            "code": code, "name": name, "date": dates[-1], "close": close[-1],
            "style": style, "route": route, "a": a,
            "fresh": freshness(code, dates[-1], maxdate, fails),
        })

    stale = [x for x in rows if x["fresh"].startswith("⚠️") or x["fresh"].startswith("❌")]
    buys = [x for x in rows if any(x["a"][k] == "买入" for k in STRATS)]
    sells = [x for x in rows if any(x["a"][k] == "卖出" for k in STRATS)]
    both = [x for x in rows if all(x["a"][k] == "买入" for k in STRATS)]
    both_sell = [x for x in rows if all(x["a"][k] == "卖出" for k in STRATS)]
    n_s = len(STRATS)

    L = [
        f"# 技术指标策略 · 今日操作建议（{' / '.join(STRATS)}）",
        f"> 固定 {n_s} 策略各跑一遍 · 已先刷新行情 · **全池最新交易日：{maxdate}**。",
        f"> 共 {len(rows)} 只：最新 {len(rows) - len(stale)} 只 · "
        f"⚠️陈旧 {sum(1 for x in stale if x['fresh'].startswith('⚠️'))} 只 · "
        f"❌刷新失败 {len(fails)} 只。**陈旧/失败的信号不代表最新，请谨慎使用。**",
        f"> V9/V10 为状态机型变种（v9 五票买点 · v10 门槛+灯评分目标仓位），读各自 scan 产物："
        f"v9 数据日 {d9 or '未扫描'} · v10 数据日 {d10 or '未扫描'}。"
        f"两者「买入」分别表示「当日右侧买点」与「目标仓位 > 0（建仓/持有）」。",
        "> 机械规则输出，**非投资建议**。",
        "",
        "## 策略口径",
        "",
        "| 标签 | 策略 | 规则 |",
        "|---|---|---|",
    ]
    for k in STRATS:
        L.append(f"| **{k}** | {STRAT_NAME[k]} | {RULE_DESC[k]} |")

    L += ["", "## 动作汇总", "",
          "| 策略 | 买入 | 卖出 | 持有 | 空仓 | 其他(观察/否决) |", "|---|---|---|---|---|---|"]
    for k in STRATS:
        vals = [sum(1 for x in rows if x["a"][k] == v) for v in ("买入", "卖出", "持有", "空仓")]
        other = len(rows) - sum(vals)
        L.append(f"| {k} | " + " | ".join(str(v) for v in vals) + f" | {other} |")

    L += ["", f"## {n_s} 策略共振", ""]
    L.append(f"- **{n_s} 策略同时买入**："
             f"{('、'.join(x['code'] + ' ' + x['name'] for x in both)) if both else '（无）'}")
    L.append(f"- **{n_s} 策略同时卖出**："
             f"{('、'.join(x['code'] + ' ' + x['name'] for x in both_sell)) if both_sell else '（无）'}")

    L += ["", "## 今日买入（新开仓 · 任一策略）", ""] + (mk_table(buys) if buys else ["（无）"])
    L += ["", "## 今日卖出（平仓 · 任一策略）", ""] + (mk_table(sells) if sells else ["（无）"])
    L += ["", "## 非最新数据（陈旧/刷新失败）", ""] + (mk_table(stale) if stale else ["（全部为最新）"])
    L += ["", "## 全标的明细", ""] + mk_table(rows)

    open(OUT, "w", encoding="utf-8").write("\n".join(L))
    print("建议:", OUT)
    print("最新交易日:", maxdate, "| 陈旧", len(stale), "| 失败", len(fails))
    for k in STRATS:
        print(f"{k} 买入:", [x["code"] for x in rows if x["a"][k] == "买入"],
              "| 卖出:", [x["code"] for x in rows if x["a"][k] == "卖出"])
    print(f"{n_s} 策略共振买入:", [x["code"] for x in both], "| 共振卖出:", [x["code"] for x in both_sell])
    print(f"V9/V10 数据日: {d9 or '未扫描'} / {d10 or '未扫描'}")
    if fails:
        print("刷新失败:", fails)


if __name__ == "__main__":
    main()
