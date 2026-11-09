# -*- coding: utf-8 -*-
"""V18 · 成交量操盘笔记（阿牛智投《最新笔记2.0》第一章）· 规则 + 状态机 + 记账。

来源文档：`E:/qianwen/jijin/成交量操盘笔记-回测规格.md`（下称「规格」）。
规格已把原文 50 条短句压成可执行判据，本文件是它的**唯一实现**：
参数权威源 = `CFG`（规格 §9 的 JSON 逐字镜像 + 本实现补充项），文档只作说明。

## 一句话
以**高量日为锚**（D0）→ 定 3 天观察窗（D8）→ 在窗内按 **价格相对支撑 S / 压力 R + 量态 + K 线形态**
决定 **观 / 减 / 清 / 上车**。原文是**个股短线风控清单**，不是买入信号发生器。

## 实现分四层（与规格 §2~§4 一一对应）
1. **定义层** `prepare()`：D1/D1b/D1c 高量三档、D2 量态、D3 实体影线、D4 支撑、D5 压力、
   D6/D7 趋势、ATR20。
2. **信号层** `signals()`：买入 B1~B4、减/清 S1~S10、观望 W1~W8、趋势 T1~T9。
   全部**因果**（只用 ≤t 的数据），逐条判据写在函数体注释里。
3. **冲突消解** `resolve()`：规格 §4 优先级 `清仓 > 减仓 > 买入 > 观望`。
   W9「同日买卖矛盾→观望」在 §4 优先级下与「减仓生效」等效，故**不单独建分支**，
   而是按 §4 要求把**冲突率**统计出来（规格自己说「冲突率就是这个判断的直接证据」）；
   想验证 W9 的原意，把 `CONFLICT_MODE = "watch"`（冲突日不动）即可，回测里有这一档。
4. **仓位映射** `state_series()`：动作 × 基准仓位（= 该腿目标带），
   `清仓 0% / 减仓 ×0.5 / 观望 不变 / 上车 ×1.0 / 重仓 ×1.5（受腿上限约束）`。

## 三个必须知道的实现裁定（数字对不上时先看这里）
### ① 「重仓 ×1.5」在「带=上限」口径下被截断
家族统一口径是「目标带 = 该腿可持有的上限」（主策略 `LG.LEGS[k]["band"]`），
所以 `重仓 ×1.5` 一出手就顶穿上限，实际仓位仍 = 带。本实现的处理：
- 默认 `POS_CAP = 1.0`（重仓与上车同效），**B4 的差别只在信号层体现**（先验分布单列）；
- `HEAVY_UNCAP=True` 可放开到 1.5 倍带，作为「这段截断值多少钱」的诊断档（回测里有）。
不这么做的话，要么篡改「带=上限」这条家族不变量，要么凭空造一个「基准仓位」。

### ② 支撑 S / 压力 R 的有效期与观察窗是两件事
- **S** = **最近一个**高量日的实体低点 `min(O,C)`（D4，不是影线低点）；只要没有新的高量日，
  它就一直是最新支撑 —— 这与「支撑位」在盘面上的读法一致。
- **观察窗** D8 = 高量日后 `D0+1 … D0+3`，**到期不重置、不滚动**；窗只约束
  S1（高量破位）/ S3（窗内高低点下移）/ S2（破位次日收阳）这三条。
- **R** = 最近 `resist_n_highvol=3` 个高量日的实体高点 `max(O,C)`（D5）。

### ③ 买点 B1/B2 要求「价在 S 上方」，B3/B4 是形态、不要求窗口
B1（支撑上方底分型）/ B2（支撑上方红三兵）都引用 S，所以天然要求 S 存在；
B3/B4（上影线被阳线实体突破）是**两根 K 线的关系**，与窗口无关 —— 原文也没把它绑在观察窗里。

## 执行与成本（规格 §5）
- **T 日收盘确认信号 → T+1 开盘成交**（`exec="next_open"`，与规格一致；本变种有开盘价，
  所以真的按开盘撮合，而不是像 `v1_2` 那样退化成次日收盘）。
- **一字板顺延**：`|涨跌| ≥ 9.8%` 且 `H≈L` 的 bar 视为买不进/卖不出，仓位保持、当日不成交，
  单独计数（`deferred`）—— 规格 §5 明令「不许当没发生」。
- 场内 ETF 成本 `comm 0.02% 双边 + slip 0.05%` → **单边 7bp**（免印花税）。
- 空仓按货基 `LG.CASH_ANNUAL` 逐日计息。

机械规则输出，非投资建议。
"""
import math
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../v18_volume_notes
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))   # 仓库根

if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import legs as LG                  # noqa: E402  本变种腿表 / 现金利率（冻结自 vol_legs/rule.py）
import metrics as _metrics   # noqa: E402  本变种绩效函数（冻结自 vol_legs/engine.py）
import ohlcv                 # noqa: E402  七腿场内 ETF OHLCV（仓库 data/）

# ---------------------------------------------------------------- 1. 参数（规格 §9 镜像）
CFG = {
    "high_vol_mode": "quantile",     # D1c 默认 —— 规格 §7：直接用 D1（25% 频率）测的是噪声
    "high_vol_quantile": 0.95,
    "high_vol_lookback": 60,
    "high_vol_mult_alt": 2.0,
    "vol_ma": 20,
    "shrink": 0.7,
    "expand": 1.3,
    "window_days": 3,
    "support": "body_low_of_highvol_day",
    "resist_n_highvol": 3,
    "trend_len": 3,
    "shadow_body_ratio": 2.0,        # 上影/下影 ≥ 2×实体 → 「大长腿」（笔记23/13）
    "body_atr_ratio": 0.3,           # 实体 ≤ 0.3×ATR20 → 「实体小」（笔记38）
    "priority": ["clear", "reduce", "buy", "watch"],
    "position": {"clear": 0.0, "reduce": 0.5, "watch": 1.0, "buy": 1.0, "heavy": 1.5},
    "cost_etf": {"comm": 0.0002, "slip": 0.0005},
    "exec": "next_open",
    "min_signals_for_portfolio": 50,   # 规格 §1 红线：<50 个信号只报分布，不报年化
    "segments": [["2011-01-01", "2018-12-31"], ["2019-01-01", "2026-11-07"]],
}

ATR_N = 20
READY_N = max(CFG["high_vol_lookback"] + 1, CFG["vol_ma"], ATR_N, 20) + 1

CONFLICT_MODE = "priority"   # "priority"（§4 优先级）/ "watch"（冲突日不动 = W9 原意）
POS_CAP = 1.0                # 重仓的上限（「受腿上限约束」，见 docstring ①）
START_MULT = 1.0             # 起始仓位（占该腿带）。1.0 = 你本来满带持有，本规则只管 观/减/清/上车
HEAVY_UNCAP = False          # 诊断开关：重仓放开到 1.5 倍带
MIN_GAP = 0                  # 同向动作最小间隔（交易日）。规格 §5「不加额外频率」→ 默认 0
REBAL_EVERY = 1              # 调仓频率：每 N 个交易日才允许改一次仓位。1 = 逐日（规格口径）
                             # 诊断开关，只改「多久执行一次」不改「看到什么」；>1 时 action 列按实际变动重算

CASH_ANNUAL = LG.CASH_ANNUAL
CASH_DAILY = (1.0 + CASH_ANNUAL) ** (1.0 / 252) - 1.0
FEE_ETF = CFG["cost_etf"]["comm"] + CFG["cost_etf"]["slip"]     # 单边 7bp
LIMIT_PCT = 0.098            # 一字板判定：|涨跌| ≥ 9.8%
LIMIT_FLAT = 0.0005          # ……且 H≈L（相对收盘 5bp 以内）

LEGS = LG.LEG_ORDER
POOL = ohlcv.POOL
W = {k: float(LG.LEGS[k]["band"]) / float(LG.SLEEVE_PP) for k in LEGS}

# 动作归类（规格 §3.2/§3.1；§4 优先级）
CLEAR = ("S1", "S10")
REDUCE = ("S3", "S4", "S5", "S6", "S7", "S8", "S9", "T7")
BUY = ("B1", "B2", "B3")
HEAVY = ("B4",)
# 只计数、不产生动作（规格 §10：补不出阈值，只能做描述性统计）
INFO = ("T1", "T2", "T3", "T4", "T5", "T6", "T8", "T9", "W2", "W3", "W4", "W5", "W6", "W7", "W8")
WATCH_ONLY = ("S2", "W1")

SIGNAL_DESC = {
    "B1": "支撑上方底分型", "B2": "支撑上方红三兵", "B3": "上影线被阳线实体突破",
    "B4": "高量上影线被阳线实体突破", "S1": "高量破位", "S2": "高量破位次日收阳(观望)",
    "S3": "窗内高低点下移", "S4": "压力线下方顶分型", "S5": "压力线下方绿三兵",
    "S6": "上影线碰压力过不去", "S7": "连红≥4天见放量阴", "S8": "缩量下影线次日收阴",
    "S9": "上涨趋势高量大长腿", "S10": "大量次日阴", "T1": "上涨梯量", "T2": "下跌趋势间断三次高量",
    "T3": "上涨趋势间断三次高量", "T4": "下跌趋势揉搓线", "T5": "下影线累积", "T6": "量大实体小",
    "T7": "价不过高量过高", "T8": "缩量创新高", "T9": "缺口不灌破", "W1": "高量第1天(不加仓)",
    "W2": "阳上阴观", "W3": "假阴/假阳", "W4": "上天入地", "W5": "缩量大长腿",
    "W6": "过顶穿底", "W7": "下跌趋势缩量平量反弹", "W8": "下跌趋势第一次高量",
}


def cfg(**kw):
    """参数打包（回测敏感性扫描时用 `**{...}` 覆盖，不改模块常量）。"""
    p = dict(CFG)
    p.update(CONFLICT_MODE=CONFLICT_MODE, POS_CAP=POS_CAP, START_MULT=START_MULT,
             HEAVY_UNCAP=HEAVY_UNCAP, MIN_GAP=MIN_GAP, REBAL_EVERY=REBAL_EVERY)
    p.update(kw)
    return p


# ---------------------------------------------------------------- 2. 定义层
def _atr(high, low, close, n=ATR_N):
    pc = close.shift(1)
    tr = pd.concat([(high - low).abs(), (high - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def prepare(df, **kw):
    """OHLCV -> 定义层指标面板（D1/D1b/D1c/D2/D3/D6/D7/ATR20 + 锚点列）。

    关键列：`hv`（高量，口径随 `high_vol_mode`）、`rvol`（量比 V/MA20）、
    `body/up/dn`（D3）、`d6/d7`（下跌/上涨趋势）、`atr`、`anchor_idx`（最近锚点，≤t-1）、
    `in_window`（D8）、`S`/`R`（D4/D5）、`ready`（预热完成）。
    """
    p = cfg(**kw)
    out = df.copy()
    o, h, l, c, v = (out[k].astype("float64") for k in ("open", "high", "low", "close", "volume"))

    out["body"] = (c - o).abs()
    out["up"] = h - np.maximum(o, c)
    out["dn"] = np.minimum(o, c) - l
    out["yang"] = c > o
    out["yin"] = c < o
    out["body_low"] = np.minimum(o, c)
    out["body_high"] = np.maximum(o, c)
    out["ret"] = c.pct_change()

    out["mav"] = v.rolling(int(p["vol_ma"]), min_periods=int(p["vol_ma"])).mean()
    out["rvol"] = v / out["mav"]
    out["atr"] = _atr(h, l, c)

    # D1 / D1b / D1c —— 三档高量判据（回测里做对比，默认取 D1c）
    d1 = v > v.shift(1).rolling(3).max()
    d1b = v >= float(p["high_vol_mult_alt"]) * out["mav"]
    lb = int(p["high_vol_lookback"])
    d1c = v >= v.shift(1).rolling(lb, min_periods=lb).quantile(float(p["high_vol_quantile"]))
    out["d1"], out["d1b"], out["d1c"] = d1.fillna(False), d1b.fillna(False), d1c.fillna(False)
    mode = str(p["high_vol_mode"])
    out["hv"] = ({"d1": d1, "strict_b": d1b, "quantile": d1c, "mult": d1b}[mode]
                 .fillna(False).astype(bool))

    # D6/D7 趋势：连续 3 天高低点同向下移/上移
    tl = int(p["trend_len"])
    hh, ll = h, l
    down = pd.Series(True, index=out.index)
    up = pd.Series(True, index=out.index)
    for i in range(1, tl):
        down &= hh.shift(i - 1) < hh.shift(i)
        down &= ll.shift(i - 1) < ll.shift(i)
        up &= hh.shift(i - 1) > hh.shift(i)
        up &= ll.shift(i - 1) > ll.shift(i)
    out["d6"], out["d7"] = down.fillna(False), up.fillna(False)

    # 锚点：最近一个高量日（严格 ≤ t-1），观察窗与 S/R 都挂在它上面
    n = len(out)
    hv = out["hv"].to_numpy(dtype=bool)
    pos = np.arange(n)
    last = pd.Series(np.where(hv, pos, -1)).cummax().to_numpy()
    anchor = np.full(n, -1, dtype=int)
    anchor[1:] = last[:-1]
    out["anchor_idx"] = anchor
    out["anchor_age"] = np.where(anchor >= 0, pos - anchor, 10 ** 6)
    out["in_window"] = out["anchor_age"] <= int(p["window_days"])

    bl = out["body_low"].to_numpy(dtype=float)
    bh = out["body_high"].to_numpy(dtype=float)
    s = np.full(n, np.nan)
    r = np.full(n, np.nan)
    recent = []
    keep = int(p["resist_n_highvol"])
    for t in range(n):
        if t > 0 and hv[t - 1]:                      # t 日开盘前，最新锚点是 t-1
            recent.append(bh[t - 1])
            recent = recent[-keep:]
        if anchor[t] >= 0:
            s[t] = bl[anchor[t]]
        if recent:
            r[t] = max(recent)
    out["S"], out["R"] = s, r

    out["ready"] = (out["mav"].notna() & out["atr"].notna()
                    & (pos >= READY_N) & out["S"].notna())
    return out


# ---------------------------------------------------------------- 3. 信号层
def signals(ind, **kw):
    """定义层面板 -> {信号ID: bool 数组}。全部因果（只用 ≤t 的数据）。

    规格 §3 的每一条都在这里落地；`INFO` 组（T1~T6/T8/T9/W组）只计数不产动作，
    因为规格 §10 已判定它们的阈值补不出来（「高位」「慢慢涨」「吃货」属主力意图叙事）。
    """
    p = cfg(**kw)
    o, h, l, c, v = (ind[k] for k in ("open", "high", "low", "close", "volume"))
    body, up, dn = ind["body"], ind["up"], ind["dn"]
    yang, yin = ind["yang"], ind["yin"]
    hv, S, R, win = ind["hv"], ind["S"], ind["R"], ind["in_window"]
    ratio = float(p["shadow_body_ratio"])
    d6, d7 = ind["d6"], ind["d7"]
    rvol = ind["rvol"]
    shrink, expand = float(p["shrink"]), float(p["expand"])

    # 分型（3 根 K 线，中间那根是极值；在 t 日确认）
    bot = (l.shift(1) < l.shift(2)) & (l.shift(1) < l)
    top = (h.shift(1) > h.shift(2)) & (h.shift(1) > h)

    def prev(x, k=1):
        """布尔/数值序列的因果滞后（布尔序列 shift 会产生 object dtype，这里统一转回 bool）。"""
        return pd.Series(x, index=ind.index).astype("float64").shift(k).fillna(0.0) > 0.5

    hv_p1 = prev(hv)
    yang_p1, yang_p2 = prev(yang), prev(yang, 2)
    yin_p1, yin_p2 = prev(yin), prev(yin, 2)
    s1_today = win & (c < S)

    s = {}

    def put(k, val):
        s[k] = pd.Series(val, index=ind.index).fillna(False).to_numpy(dtype=bool)

    # --- 买入类（规格 §3.1，原文只有 4 条）
    put("B1", (c > S) & bot)                                            # 支撑上方底分型
    put("B2", (c > S) & yang & yang_p1 & yang_p2                        # 支撑上方红三兵
        & (c > c.shift(1)) & (c.shift(1) > c.shift(2)))
    put("B3", (up.shift(1) >= body.shift(1)) & yang & (c > h.shift(1)))  # 上影被阳实体突破
    put("B4", (up.shift(1) >= body.shift(1)) & yang & (c > h.shift(1)) & hv_p1)

    # --- 减仓/清仓类（规格 §3.2）
    put("S1", s1_today)                                                 # 观察窗内高量破位 -> 清仓
    put("S2", prev(s1_today) & yang)                                    # 破位次日收阳 -> 观望
    put("S3", win & (h < h.shift(1)) & (l < l.shift(1)))                # 窗内高低点下移 -> 减
    put("S4", (c < R) & top)                                            # 压力下方顶分型
    put("S5", (c < R) & yin & yin_p1 & yin_p2                           # 压力下方绿三兵
        & (c < c.shift(1)) & (c.shift(1) < c.shift(2)))
    put("S6", (h >= R) & (c < R))                                       # 上影碰压力过不去
    put("S7", prev(yang, 1) & prev(yang, 2) & prev(yang, 3) & prev(yang, 4)  # 连红≥4
        & yin & (v > v.shift(1)))                                       # 第5日放量阴
    put("S8", (rvol.shift(1) < shrink) & (dn.shift(1) >= body.shift(1)) & yin)  # 缩量下影次日阴
    put("S9", d7 & hv & ((up + dn) >= ratio * body))                    # 上涨趋势高量大长腿
    put("S10", hv_p1 & yin)                                             # 大量次日阴 -> 清仓

    # --- 趋势条件类（规格 §3.4，多数只作统计）
    put("T1", (v > v.shift(1)) & (v.shift(1) > v.shift(2)))             # 上涨梯量
    cnt3 = pd.Series(hv, index=ind.index).astype(int).rolling(20, min_periods=20).sum() == 3
    adj = (pd.Series(hv, index=ind.index) & hv_p1).astype(int) \
        .rolling(20, min_periods=20).sum() == 0
    put("T2", cnt3 & adj & d6)                                          # 下跌趋势间断三次高量
    put("T3", cnt3 & adj & d7)
    put("T4", (up.shift(1) >= ratio * body.shift(1))                    # 揉搓线：长上影后接长下影
        & (dn >= 2.0 * body) & (body <= float(p["body_atr_ratio"]) * ind["atr"]))
    dnshare = (dn / (h - l).replace(0.0, np.nan))
    put("T5", (dnshare > 0.4).astype(float).rolling(20, min_periods=20).sum() >= 8)
    put("T6", (v >= float(p["high_vol_mult_alt"]) * ind["mav"])         # 量大实体小
        & (body <= float(p["body_atr_ratio"]) * ind["atr"]))
    put("T7", (c <= c.shift(1).rolling(20, min_periods=20).max())       # 价不过高量过高 -> 逃顶
        & (v > v.shift(1).rolling(20, min_periods=20).max()))
    put("T8", (rvol < shrink) & (c >= c.rolling(20, min_periods=20).max()))  # 缩量创新高 -> 持有
    put("T9", (l > h.shift(1)))                                         # 向上跳空

    # --- 观望类（规格 §3.3）
    put("W1", hv)                                                       # 高量当天不加仓
    put("W2", win & (c > S) & yin)                                      # 窗内阳持有、阴观望
    put("W3", ((c > c.shift(1)) & yin) | ((c < c.shift(1)) & yang))     # 假阴/假阳
    put("W4", (up + dn) >= ratio * body)                                # 上天入地
    put("W5", (rvol < shrink) & ((up + dn) >= ratio * body))            # 缩量大长腿
    put("W6", (c > c.shift(1).rolling(20, min_periods=20).max())        # 过顶又穿底
        & (c.shift(1) < l.shift(1).rolling(20, min_periods=20).min() * 1.0)
        & (c > c.shift(1)))
    put("W7", d6 & (rvol < expand) & yang)                              # 下跌趋势缩/平量反弹
    put("W8", d6 & hv)                                                  # 下跌趋势里的高量
    return s


# ---------------------------------------------------------------- 4. 冲突消解 + 状态机
def _rank(ids, p):
    """信号集合 -> (动作, 触发ID 列表)。动作 ∈ clear/reduce/buy/heavy/watch。"""
    order = list(p["priority"])
    groups = {"clear": CLEAR, "reduce": REDUCE, "buy": BUY, "watch": WATCH_ONLY}
    hit = {g: [i for i in ids if i in groups[g]] for g in groups}
    heavy = [i for i in ids if i in HEAVY]
    for g in order:
        if g == "buy":
            # B4 ⊂ B3（B4 = B3 且前一日高量），故先判重仓档，否则 B4 永远被 B3 吞掉
            if heavy:
                return "heavy", heavy + hit["buy"]
            if hit["buy"]:
                return "buy", hit["buy"]
            continue
        if hit.get(g):
            return g, hit[g]
    return "", []


def state_series(df, leg, **kw):
    """OHLCV -> 决策序列（每日一行）。

    `mult[t]` = t 日收盘做出、**t+1 开盘成交**的目标仓位（占该腿目标带）。
    `action[t]` ∈ {清仓, 减仓, 上车, 重仓, 观望, 持有, 预热}；`rule[t]` 是触发的信号 ID。
    冲突（同日买+卖）在 `conflict[t]` 标出，供规格 §4 的冲突率统计。
    """
    p = cfg(**kw)
    ind = prepare(df, **kw)
    sig = signals(ind, **kw)
    n = len(ind)
    idx = ind.index

    mult = np.empty(n)
    action = np.empty(n, dtype=object)
    rule_hit = np.empty(n, dtype=object)
    why = np.empty(n, dtype=object)
    conflict = np.zeros(n, dtype=bool)
    for a in (action, rule_hit, why):
        a[:] = ""

    cur = float(p["START_MULT"])
    last_act_i = -10 ** 6
    for t in range(n):
        mult[t] = cur
        if not bool(ind["ready"].iat[t]):
            action[t], why[t] = "预热", "D1c 分位/ATR20/锚点未成形"
            continue
        ids = [k for k in sig if sig[k][t]]
        buy_side = [i for i in ids if i in BUY or i in HEAVY]
        sell_side = [i for i in ids if i in CLEAR or i in REDUCE]
        conflict[t] = bool(buy_side) and bool(sell_side)

        # W1：高量当天不加仓（规格 §3.3），把买类从候选里摘掉
        if bool(ind["hv"].iat[t]):
            ids = [i for i in ids if i not in BUY and i not in HEAVY]
        act, hits = _rank(ids, p)
        if p["CONFLICT_MODE"] == "watch" and conflict[t]:
            act, hits = "watch", hits
        # 同向动作限速（默认 0 = 不限制，规格 §5「不加额外频率」）
        if act in ("buy", "heavy") and (t - last_act_i) < int(p["MIN_GAP"]):
            act, hits = "", hits
        rule_hit[t] = "+".join(hits)

        if act == "clear":
            new = 0.0
        elif act == "reduce":
            new = 0.0 if cur <= 1e-9 else min(cur, float(p["position"]["reduce"]))
        elif act in ("buy", "heavy"):
            m = float(p["position"][act])
            if act == "heavy" and not p["HEAVY_UNCAP"]:
                m = min(m, float(p["POS_CAP"]))
            new = min(m, float(p["POS_CAP"]) if not p["HEAVY_UNCAP"] else m)
        else:
            new = cur
        if act:
            action[t] = {"clear": "清仓", "reduce": "减仓", "buy": "上车",
                         "heavy": "重仓", "watch": "观望"}[act]
            why[t] = " + ".join(f"{i} {SIGNAL_DESC.get(i, '')}" for i in hits) if hits else "同日多信号冲突(W9)"
            if abs(new - cur) > 1e-9:
                last_act_i = t
        else:
            action[t] = "持有" if cur > 1e-9 else "空仓"
            why[t] = _idle_reason(ind, t, cur, sig) if cur <= 1e-9 else ""
        cur = new
        mult[t] = cur

    out = ind.copy()
    out["mult"] = _thin(mult, int(p["REBAL_EVERY"]))
    out["action"] = action
    out["rule"] = rule_hit
    out["why"] = why
    out["conflict"] = conflict
    for k, arr in sig.items():
        out["sig_" + k] = arr
    if int(p["REBAL_EVERY"]) > 1:
        # 降频后 action / why 与真实成交不再对应，只能按「实际仓位怎么变」重写标签
        d = np.diff(np.concatenate([[0.0], out["mult"].to_numpy(dtype=float)]))
        mv = out["mult"].to_numpy(dtype=float)
        out["action"] = np.where(d < -1e-9, np.where(mv <= 1e-9, "清仓", "减仓"),
                                 np.where(d > 1e-9, "上车",
                                          np.where(mv > 1e-9, "持有", "空仓")))
        out["rule"] = np.where(np.abs(d) > 1e-9, out["rule"], "")
        out["why"] = ""
    return out


def _thin(mult, n, off=0):
    """每 n 个交易日才允许改一次仓位（`REBAL_EVERY`）；`off` = 相位偏移（稳健性检验用）。"""
    if n <= 1:
        return mult
    arr = np.asarray(mult, dtype=float).copy()
    hold = arr[0] if len(arr) else 0.0
    for i in range(len(arr)):
        if (i - off) % n == 0:
            hold = arr[i]
        arr[i] = hold
    return arr


def _idle_reason(ind, t, cur, sig):
    """空仓/持有中的一句话状态（每日报告用）。"""
    if not bool(ind["ready"].iat[t]):
        return "预热期"
    age = int(ind["anchor_age"].iat[t])
    if cur <= 1e-9:
        if age > int(CFG["window_days"]):
            return f"空仓等待新的高量锚点（上一个锚 {age} 日前）"
        return "观察窗内破位后空仓，等 B1/B2/B3 上车信号"
    return ""


# ---------------------------------------------------------------- 5. 记账（T+1 开盘成交）
def _limit_locked(df, t):
    """t 日是否「一字板」买不进/卖不出（境内 ETF ±10%，留 20bp 余量）。"""
    if t < 1:
        return False
    c0 = float(df["close"].iat[t - 1])
    o, h, l, c = (float(df[k].iat[t]) for k in ("open", "high", "low", "close"))
    if not np.isfinite(c0) or c0 <= 0:
        return False
    move = abs(c / c0 - 1.0)
    return move >= LIMIT_PCT and (h - l) <= LIMIT_FLAT * c


def leg_returns(state, df, fee=None, cost=True):
    """单腿逐日收益（T+1 **开盘**成交）-> (return Series, w Series, deferred 次数)。

    r_t = w_{t-1}·(open_t/close_{t-1} − 1) + w_t·(close_t/open_t − 1) − |Δw|·fee。
    一字板日：w 保持 w_{t-1}，目标顺延到下一个可成交日（规格 §5「顺延并单独计数」）。
    """
    fee = FEE_ETF if fee is None else float(fee)
    n = len(df)
    o, c = df["open"].to_numpy(dtype=float), df["close"].to_numpy(dtype=float)
    tgt = state.reindex(df.index).fillna(0.0).to_numpy(dtype=float)
    w = np.zeros(n)
    r = np.zeros(n)
    deferred = 0
    for t in range(n):
        want = tgt[t - 1] if t else 0.0            # 昨日决策 -> 今日开盘成交
        prev = w[t - 1] if t else 0.0
        if t == 0 or not np.isfinite(o[t]) or o[t] <= 0 or c[t - 1] <= 0:
            w[t] = 0.0 if t == 0 else prev
            r[t] = 0.0
            continue
        if _limit_locked(df, t):
            w[t] = prev
            deferred += 1
        else:
            w[t] = want
        gap = o[t] / c[t - 1] - 1.0
        intra = c[t] / o[t] - 1.0
        cost_t = abs(w[t] - prev) * fee if cost else 0.0
        r[t] = prev * gap + w[t] * intra - cost_t
    return pd.Series(r, index=df.index), pd.Series(w, index=df.index), deferred


def run_leg(state, df, fee=None, cost=True):
    """单腿净值（起点 = 1），空仓部分按货基计息。"""
    r, w, deferred = leg_returns(state, df, fee=fee, cost=cost)
    r = r + (1.0 - w) * CASH_DAILY
    nav = (1.0 + r).cumprod()
    return nav, r, w, deferred


def portfolio(frame, rets, weight=None, cost=True):
    """组合层净值：Σ w_i·r_i（各腿 r_i 已含「未投部分计息」）+ 无风险部分不重复计息。"""
    weight = weight or W
    tot = None
    for k in frame.columns:
        rr = rets[k].reindex(frame.index).fillna(0.0) * float(weight[k])
        tot = rr if tot is None else tot + rr
    # r_i 已把 (1−w) 的货基利息算进去，加权后 Σ(1−w_i)·cash 仍留在这里；
    # 权重未用满的部分（Σw<1）需要补一次，避免空转。
    gap = max(0.0, 1.0 - sum(float(weight[k]) for k in frame.columns))
    if gap > 0:
        tot = tot + gap * CASH_DAILY
    return (1.0 + tot).cumprod()


# ---------------------------------------------------------------- 6. 统计
def perf(nav):
    m = _metrics.metrics(nav)
    m["calmar"] = m["ann"] / abs(m["mdd"]) if abs(m["mdd"]) > 1e-9 else 0.0
    return m


def actions(state):
    """仓位变化折成动作（成交日 = 决策次日开盘）。"""
    s = state.to_numpy(dtype=float)
    idx = state.index
    out = []
    for i in range(1, len(s)):
        if abs(s[i] - s[i - 1]) > 1e-9:
            out.append(dict(date=idx[i], frm=float(s[i - 1]), to=float(s[i]),
                            kind="buy" if s[i] > s[i - 1] else "sell"))
    return out


def exposure(w):
    """平均投入（按开盘成交后的权重）。"""
    return float(np.mean(w)) if len(w) else 0.0


def fwd_stats(close, mask, horizons=(20, 60, 120)):
    """信号日后 D+n 收益分布 + 基线（同期全样本均值）。规格 §6「信号层」口径。"""
    c = close.to_numpy(dtype=float)
    m = np.asarray(mask, dtype=bool)
    out = {}
    base = {}
    for h in horizons:
        fwd = np.full(len(c), np.nan)
        if len(c) > h:
            fwd[:-h] = c[h:] / c[:-h] - 1.0
        ok = np.isfinite(fwd)
        out[h] = fwd[ok & m]
        base[h] = float(np.nanmean(fwd[ok])) if ok.any() else float("nan")
    return out, base


def trade_stats(rets, w):
    """按「0 → >0 → 0」切出单笔（净值口径），给胜率/盈亏比（规格 §1 红线：必须同时报）。"""
    idx = w.index
    wv = w.to_numpy(dtype=float)
    nav = (1.0 + rets.to_numpy(dtype=float)).cumprod()
    tr, entry = [], None
    for i, v in enumerate(wv):
        if v > 0 and entry is None:
            entry = i
        elif v <= 0 and entry is not None:
            tr.append(dict(entry=idx[entry], exit=idx[i], bars=i - entry,
                           ret=float(nav[i] / nav[entry] - 1.0)))
            entry = None
    if entry is not None:
        tr.append(dict(entry=idx[entry], exit=idx[-1], bars=len(wv) - 1 - entry,
                       ret=float(nav[-1] / nav[entry] - 1.0)))
    if not tr:
        return dict(n=0, win_rate=0.0, payoff=0.0, exp=0.0, bars=0.0)
    win = [t["ret"] for t in tr if t["ret"] > 0]
    loss = [t["ret"] for t in tr if t["ret"] <= 0]
    aw = sum(win) / len(win) if win else 0.0
    al = -sum(loss) / len(loss) if loss else 0.0
    return dict(n=len(tr), win_rate=len(win) / len(tr),
                payoff=(aw / al) if al > 1e-12 else float("inf") if aw else 0.0,
                exp=sum(t["ret"] for t in tr) / len(tr),
                bars=sum(t["bars"] for t in tr) / len(tr))


# ---------------------------------------------------------------- 7. 入口辅助
def load(leg, refresh_=False, fails=None, **kw):
    """该腿决策表（指标 + 信号 + 目标仓位）。"""
    return state_series(ohlcv.frame(leg, refresh_=refresh_, fails=fails), leg, **kw)


def latest_state(sim):
    """最后一根的状态（每日扫描用）。"""
    r, p = sim.iloc[-1], sim.iloc[-2] if len(sim) > 1 else sim.iloc[-1]
    hits = [k[4:] for k in sim.columns if k.startswith("sig_") and bool(r[k])]
    return dict(
        date=sim.index[-1].date().isoformat(), close=float(r["close"]),
        anchor_idx=int(r["anchor_idx"]), anchor_age=int(r["anchor_age"]),
        in_window=bool(r["in_window"]), S=r["S"], R=r["R"],
        hv=bool(r["hv"]), rvol=float(r["rvol"]), body=float(r["body"]),
        up=float(r["up"]), dn=float(r["dn"]), atr=float(r["atr"]),
        mult=float(r["mult"]), prev_mult=float(p["mult"]),
        action=str(r["action"]), rule=str(r["rule"]), why=str(r["why"]),
        conflict=bool(r["conflict"]), signals=hits,
        dist_S=(float(r["close"]) / float(r["S"]) - 1.0) if np.isfinite(r["S"]) else None,
        dist_R=(float(r["close"]) / float(r["R"]) - 1.0) if np.isfinite(r["R"]) else None,
    )


def fmt_pct(x, nd=2):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x * 100:+.{nd}f}%"


def fmt_x(x, nd=2):
    if x is None:
        return "—"
    if isinstance(x, float) and math.isinf(x):
        return "∞"
    return f"{x:.{nd}f}"


def _self_check():
    print(f"参数口径: 高量={CFG['high_vol_mode']} q{CFG['high_vol_quantile']:.2f} / "
          f"窗{CFG['window_days']} 日 / 优先级 {'>'.join(CFG['priority'])} / 执行 {CFG['exec']}")
    print(f"上线池 POOL={POOL}（{'/'.join(ohlcv.EDGE_LABEL[ohlcv.EDGE[k][0]] for k in POOL)}）"
          f" · 仅对照={ohlcv.REFERENCE}")
    for k in LEGS:
        df = ohlcv.frame(k)
        sim = state_series(df, k)
        i0 = int(np.argmax(sim["ready"].to_numpy(dtype=bool)))     # 预热后才是有效样本
        sim = sim.iloc[i0:]
        st = latest_state(sim)
        nav, r, w, d = run_leg(sim["mult"], df.loc[sim.index])
        m = perf(nav)
        print(f"  {k:6s} {LG.LEGS[k]['name']:8s} {st['date']} 收盘 {st['close']:.3f} "
              f"S {st['S']:.3f} R {st['R']:.3f} 量比 {st['rvol']:.2f} "
              f"仓位 {st['mult']:.0%} [{st['action']}] 年化 {fmt_pct(m['ann'])} "
              f"回撤 {fmt_pct(m['mdd'])} 顺延 {d}")


if __name__ == "__main__":
    _self_check()
