# -*- coding: utf-8 -*-
"""永久投资组合(8 项固定基准 + 每项 ±50% 倾斜) · 规则定义 —— 唯一权威源。

基准(用户设定, 合计 100):
  短中长期国债 30 / 红利低波 20 / 纳指100 10 / 标普500 10
  黄金 10 / 货币基金 10 / 中证A500 5 / 科创50 5

口径(见 README §1):
  8 项合计恰好 100, 无归一化歧义。倾斜后互相归一化回 100, 因此任一项加仓必然
  由其余项让出 —— 这与「货币基金是否算组合内」无关(CASH_OUTSIDE=False)。
  若将来想改回「货基作为组合外流动性储备」的口径, 把 CASH_OUTSIDE 设为 True。

倾斜规则(唯一实现, 供 _signal.py 与 _backtest.py 共用):
  每项得分 score ∈ (-1, 1) → 目标倍数 k = 1 + TILT*score ∈ [0.5, 1.5]
  → w = base*k 归一化到 100, 再投影回 [0.5*base, 1.5*base] 的可行域。
  **「每项上下浮动 50%」即 TILT=0.5 的倍数区间**, 是硬边界, 任何信号都不能突破。

得分构成(全部可由净值/价格序列本地算出, 零外部接口依赖):
  0.55 × 趋势   close/MA200-1, 截断 ±10% 映射到 ±1 (百分比量纲, 可跨类比较)
  0.45 × 动量   12 个月收益, 截断 ±20% 映射到 ±1 (同上, 可跨类比较)
  现金腿无价格信号 → score = -CASH_BETA × 权益组平均得分 (风险高时加现金)

为什么刻意**不用**组内 z-score 做动能/低波:
  初版用「组内 z」比同类强弱, 回测暴露两个问题 —— ①债券/货基天然低波被误判为「看好」,
  顶在上限; ②评分只反映「同类里谁更强」, 而「每项该配多少」需要的是各项自身的绝对好坏。
  改为绝对口径后, 8 项在同一把尺子上比较(偏离度 %、12 个月收益 %)。

为什么不用估值分位(与 core_rotation 的差异):
  8 项里只有 4 项有免费且历史可回溯的估值序列(红利/A500/科创/纳指), 标普 Forward PE
  与黄金实际利率无免费历史源 → 用估值驱动会让「可回测」和「口径一致」同时失效。
  估值信息在报告中单列为人工参考项, 不进入权重计算。
"""
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- 1. 基准权重
# 原始设定(用户给定), 合计 100
RAW = {
    "bond": 30.0,    # 短中长期国债(项内三等分: 短/中/长)
    "divlv": 20.0,   # 红利低波
    "ndx": 10.0,     # 纳指100
    "spx": 10.0,     # 标普500
    "gold": 10.0,    # 黄金
    "cash": 10.0,    # 货币基金
    "a500": 5.0,     # 中证A500
    "kc": 5.0,       # 科创50
}

# False(默认): 8 项一起归一化到 100% —— 货基是组合内一项, 加仓它同样要别人让出
# True        : 货基 10% 记为组合外流动性储备, 其余 7 项独立归一化到 100%
CASH_OUTSIDE = False

# ---------------------------------------------------------------- 2. 倾斜强度
TILT = 0.5          # 每项倍数区间 [1-TILT, 1+TILT] = [0.5, 1.5] —— 用户要求的 ±50%
W_TREND = 0.55      # close/MA200-1 偏离度
W_MOM = 0.45        # 12 个月绝对收益
TREND_FULL = 0.10   # 偏离 ±10% 视为趋势满分(债券常年 ±2%, 权益 ±15%)
MOM_FULL = 0.20     # 12 个月 ±20% 视为动量满分
SCORE_GAIN = 1.0    # tanh 前的放大系数(回测扫描显示 >1.0 只会放大换手, 无增益)
CASH_BETA = 0.40    # 现金腿对权益平均得分的反向敏感度

MA_WIN = 200
VOL_WIN = 60
MOM_WIN = 250

# 货基收益率假设(万份收益口径, 净值不走): 用于回测与净值曲线外推。
# 2026-10 全市场货基 7 日年化约 1.3-1.5%, 取 1.40%; 调这里不影响任何倾斜逻辑。
CASH_ANNUAL = 0.014

# ---------------------------------------------------------------- 3. 标的与项
# item -> 中文名 + 项内腿(等比例)。场外代码为唯一执行通道。
ITEMS = {
    "bond": dict(name="短中长期国债", legs=["bond_s", "bond_m", "bond_l"]),
    "divlv": dict(name="红利低波", legs=["divlv"]),
    "ndx": dict(name="纳指100", legs=["ndx"]),
    "spx": dict(name="标普500", legs=["spx"]),
    "gold": dict(name="黄金", legs=["gold"]),
    "cash": dict(name="货币基金", legs=["cash"]),
    "a500": dict(name="中证A500", legs=["a500"]),
    "kc": dict(name="科创50", legs=["kc"]),
}
ITEM_ORDER = ["bond", "divlv", "ndx", "spx", "gold", "cash", "a500", "kc"]

# 腿 -> 场外代码 / 简称 / 所属组(仅用于现金腿识别权益组) / 是否参与价格信号
LEGS = {
    "bond_s": dict(code="012265", name="短债30天滚动", group="bond", px=True),
    "bond_m": dict(code="001512", name="中债3-5年国债", group="bond", px=True),
    "bond_l": dict(code="003377", name="长债7-10年国开", group="bond", px=True),
    "divlv": dict(code="008163", name="红利低波50", group="equity", px=True),
    "ndx": dict(code="270042", name="纳指100", group="equity", px=True),
    "spx": dict(code="017641", name="标普500", group="equity", px=True),
    "gold": dict(code="000216", name="黄金", group="gold", px=True),
    # 货基净值恒 1.0000(收益走万份收益), 无有效价格序列 → 回测用 CASH_ANNUAL 常数收益
    "cash": dict(code=None, name="货币基金", group="cash", px=False),
    "a500": dict(code="023299", name="中证A500", group="equity", px=True),
    "kc": dict(code="011609", name="科创50", group="equity", px=True),
}
LEG_ORDER = ["bond_s", "bond_m", "bond_l", "divlv", "ndx", "spx", "gold", "a500", "kc",
             "cash"]


# ---------------------------------------------------------------- 4. 工具
def _clip(x, lo, hi):
    return max(lo, min(hi, x))


# ---------------------------------------------------------------- 5. 基准换算
def base_weights():
    """-> {item: 归一化基准权重%}, 合计 100(或 100+货基, 见 CASH_OUTSIDE)。"""
    if CASH_OUTSIDE:
        invest = {k: v for k, v in RAW.items() if k != "cash"}
        s = sum(invest.values())
        out = {k: v / s * 100.0 for k, v in invest.items()}
        out["cash"] = RAW["cash"] / s * 100.0   # 相对投资本金的比例, 不是 10
        return out
    s = sum(RAW.values())
    return {k: v / s * 100.0 for k, v in RAW.items()}


def base_leg_weights():
    """-> {leg: 基准权重%} (项内腿等比例)。"""
    bw = base_weights()
    out = {}
    for item, spec in ITEMS.items():
        legs = spec["legs"]
        for leg in legs:
            out[leg] = bw[item] / len(legs)
    return out


def bounds():
    """每项硬边界 [0.5*base, 1.5*base] —— 「上下浮动 50%」。"""
    bw = base_weights()
    return {k: (v * (1 - TILT), v * (1 + TILT)) for k, v in bw.items()}


# ---------------------------------------------------------------- 6. 信号
def leg_metrics(nav):
    """nav: pandas.Series(index=date, value=净值/收盘) -> dict。"""
    s = [float(x) for x in nav if x == x]      # 去 NaN
    if len(s) < 30:
        raise ValueError("净值序列过短(需 >=30 个交易日)")
    c = s[-1]

    def _ret(n):
        return (c / s[-1 - n] - 1.0) if len(s) > n else 0.0

    ma = sum(s[-MA_WIN:]) / len(s[-MA_WIN:])
    rets = [s[i] / s[i - 1] - 1.0 for i in range(1, len(s)) if s[i - 1] > 0]
    tail = rets[-VOL_WIN:]
    if len(tail) >= 10:
        mu = sum(tail) / len(tail)
        vol = math.sqrt(sum((x - mu) ** 2 for x in tail) / len(tail)) * math.sqrt(252.0)
    else:
        vol = 0.0
    return dict(close=c, ma=ma, r20=_ret(20), r120=_ret(120), r250=_ret(250), vol=vol)


def leg_scores(metrics):
    """{leg: metrics} -> {leg: score in (-1,1)}。

    刻意用**绝对口径**而非组内 z-score:
      「每项该配多少」比的是各项自身的绝对好坏, 组内标准化只回答「同类里谁更强」,
      且会把债券/货基的天然低波误判为「看好」而顶在上限(初版实测就是这样)。
      趋势偏离与 12 个月收益都是百分比量纲, 跨资产可直接比较。
    """
    trend, out = {}, {}
    for leg, m in metrics.items():
        if not LEGS[leg]["px"]:
            continue
        trend[leg] = _clip((m["close"] / m["ma"] - 1.0) / TREND_FULL, -1.0, 1.0)
        mom = _clip(m["r250"] / MOM_FULL, -1.0, 1.0)
        out[leg] = math.tanh(SCORE_GAIN * (W_TREND * trend[leg] + W_MOM * mom))

    # 现金腿: 无有效价格信号, 取权益组平均得分的反向(风险高时加现金)
    eq = [out[l] for l in out if LEGS[l]["group"] == "equity"]
    eq_mean = sum(eq) / len(eq) if eq else 0.0
    out["cash"] = math.tanh(SCORE_GAIN * (-CASH_BETA * eq_mean))
    return out


def item_scores(scores):
    """腿得分 -> 项得分(项内等权平均)。"""
    out = {}
    for item, spec in ITEMS.items():
        out[item] = sum(scores[l] for l in spec["legs"]) / len(spec["legs"])
    return out


# ---------------------------------------------------------------- 7. 目标权重
def _project(w, lo, hi, total=100.0, iters=200):
    """归一化到 total 且把越界项钉在边界上, 剩余项重新分配(water-filling)。"""
    n = len(w)
    fixed = [False] * n
    for _ in range(iters):
        free = [i for i in range(n) if not fixed[i]]
        used = sum(w[i] for i in range(n) if fixed[i])
        free_sum = sum(w[i] for i in free)
        if free_sum <= 1e-12:
            break
        for i in free:
            w[i] = w[i] * (total - used) / free_sum
        changed = False
        for i in free:
            if w[i] < lo[i] - 1e-9:
                w[i], fixed[i], changed = lo[i], True, True
            elif w[i] > hi[i] + 1e-9:
                w[i], fixed[i], changed = hi[i], True, True
        if not changed:
            break
    s = sum(w)
    return [x * total / s for x in w] if s > 0 else w


def target_weights(scores):
    """项得分 -> 目标权重% (各项落在 ±50% 边界内)。

    合计 = sum(base_weights()): CASH_OUTSIDE=True 时为 110(投资 7 项 100 + 货基 10),
    False 时为 100 —— 两者都不改变「货基之外的相对关系」。
    返回: (weights, k)  weights={item:%}, k={item:倍数}
    """
    bw = base_weights()
    bd = bounds()
    items = list(ITEM_ORDER)
    base = [bw[i] for i in items]
    total = sum(base)
    k = [1.0 + TILT * _clip(scores[i], -1.0, 1.0) for i in items]
    raw = [b * kk for b, kk in zip(base, k)]
    lo = [bd[i][0] for i in items]
    hi = [bd[i][1] for i in items]
    w = _project(raw, lo, hi, total=total)
    return (dict(zip(items, w)), dict(zip(items, k)))


def leg_weights(item_w):
    """项权重 -> 腿权重(项内等比例)。"""
    out = {}
    for item, spec in ITEMS.items():
        legs = spec["legs"]
        for leg in legs:
            out[leg] = item_w[item] / len(legs)
    return out
