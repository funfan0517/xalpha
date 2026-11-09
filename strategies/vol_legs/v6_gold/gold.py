# -*- coding: utf-8 -*-
"""波动资产 · V6 变种：黄金 ETF 三口径策略（A 长期配置 / B 控回撤 / C 低仓位占坑）。

**本变种只做一个标的**：黄金。场内信号 `518880`（黄金ETF华安）、场外执行 `000216`
（华安黄金ETF联接A）。它不发生在七腿内部，只回答「这笔投给黄金的钱该怎么拿」。

为什么单独立案：黄金**没有现金流锚** —— PE / ERP / 股息率 / 股债利差全都不适用，
价格本身是唯一信息源。仓库里 20 多条时机规则在黄金上的实测结论一致：**没有一条跑赢买入持有
的年化**，能赚到的只有「压回撤」和「少占用资金」。所以本变种给三套口径，各自服务一种人：

| 口径 | 一句话 | 参考结果（000216 场外 · 12 年） |
|---|---|---|
| **A 长期配置** | 满带持有 + 年线下满 60 日降到半仓 + 回撤阶梯越跌越买 | 见 `backtest/_v6_gold_bt.md` §2 |
| **B 控回撤** | 年线四象限闸门 + 波动目标层（15%/120 日，只压满带） | 回撤 -32.8% → -22% 量级 |
| **C 低仓位占坑** | MACD(12,26) 金叉持有 / 死叉空仓，只用约一半资金 | 占坑 50% 左右 |

**A 的规则逐条**（对应主策略 `../rule.py::GoldPolicy` §5）：

1. 目标带 = 10pp（`BAND_PP`），本变种内部一律记成「占带的百分比」：1.0 = 满带；
2. `close < MA250` → **只停加仓，不清仓**（黄金的宽松版趋势闸门）；
3. 连续在年线下方满 `DEGRADE_DAYS=60` 个交易日 → 目标带上限降到 0.5（保险丝），
   收复年线后回带（`RECOVER_LAG` 个交易日，默认 0 = 次日；主策略里由季度再平衡执行）；
4. 回撤阶梯：`dd = 1 − close/近 250 日最高`，`dd ≥ 10/15/20%` **且 close ≥ MA250** 时
   分别补 1 / 1.5 / 2 份，1 份 = 带的 20%；每次只补最低的未填档位；
5. `close` 创 52 周新高 → 阶梯计数归零；
6. FOMC 决议日 ±5 个交易日 → 不加仓（输入 `data/_val_inputs.json::fomc_days`，留空即不生效）。

**B 的规则**：直接复用 `../v5_trend_gate/trend_gate.py` 的档位状态机（四象限 {1.0/0.5/0.2}
+ 5/20 日阶梯 + 5 日间隔帽），外面套一层**波动目标** `scale = clip(0.15 / 120日已实现波动, 0.2, 1)`，
只压满带。`GATE_ON=False` 退化为「纯波动层、不做趋势判断」——`research/_vol_overlay.md` 里
那一档 Calmar 更高（0.37 vs 0.25），所以两条都留着对照。

**C 的规则**：`DIF = EMA12 − EMA26`、`DEA = EMA9(DIF)`；金叉（DIF 上穿 DEA）持有、死叉空仓，
**不看零轴**（与 `tech_indicators/v13_macd/rule.py` 同一口径）。它买的是「只用一半资金」，
不是更高收益 —— 场外 A 类（申 0.1% + 赎 0.5%）下每年 10 次进出会吃掉大半超额，**C 只在场内低佣金账户成立**，
详见 `backtest/_v6_gold_bt.md` §5。

**成交与时序**（与 v1/v2/v3/v5 同一纪律）：`state[t]` = t 日收盘成交后的仓位；日收益用
`state.shift(1)`；空仓部分按货基 `rule.CASH_ANNUAL`（1.40%）逐日计息；成本按 `|Δstate| × 费率`。

模拟结果，非投资建议。
"""
import importlib.util
import math
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))              # .../vol_legs/v6_gold
_VL = os.path.dirname(_HERE)                                    # strategies/vol_legs
_V5 = os.path.join(_VL, "v5_trend_gate")
_ROOT = os.path.dirname(os.path.dirname(_VL))                   # 仓库根
for _p in (_HERE, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import data as vdata    # noqa: E402
import engine           # noqa: E402
import rule             # noqa: E402

LABEL = "V6 黄金三口径"

# ---------------------------------------------------------------- 1. 参数（唯一权威源）
LEG = "gold"
NAME = "黄金"
BAND_PP = float(rule.LEGS[LEG]["band"])          # 10pp（主策略目标带）
SIGNAL_CODE = rule.LEGS[LEG]["signal"]           # 518880（场内信号）
EXEC_CODE = rule.LEGS[LEG]["otc"]                # 000216（场外执行）

# ---- A 长期配置（主策略 §5 黄金口径）
MA_WIN = 250             # 年线（A 股口径 250，不是美股 200）
NEW_HIGH_WIN = 250       # 52 周新高参照窗口
DEGRADE_DAYS = 60        # 连续在年线下方满 60 日 -> 目标带上限 10pp → 5pp
FLOOR = 0.5              # 保险丝生效时的带上限（占带比例）
RECOVER_LAG = 0          # 收复年线后回带的延迟交易日（0 = 次日；主策略由季度再平衡执行）
LADDER_DD = (10.0, 15.0, 20.0)      # 回撤阶梯阈值（%）
LADDER_UNITS = (1.0, 1.5, 2.0)      # 各档份数
UNIT_FRAC = 0.20         # 1 份 = 带的 20%（与 rule.unit_pp 同比例：10pp 带 → 2pp）
ACTION_GAP_A = 5         # A 的阶梯加仓最小间隔（交易日）；保险丝卖出不受此限
START_A = 1.0            # 起手 = 满带（实盘口径）；0.9 = 「阶梯已填满」口径

# ---- B 控回撤（v5 四象限闸门 + 波动目标层）
# 默认 `GATE_ON=False` = 只做波动目标（不做趋势判断）。实测（12 年场外）：
#   纯波动层 15%  +11.71% / -22.00% / 夏普 0.77 / Calmar 0.53
#   闸门+波动 15% +9.28%  / -20.79% / 夏普 0.67 / Calmar 0.45
# 闸门在黄金上纯付成本（与 v5 的逐腿结论一致：黄金跌破年线后多次 V 型反转），故默认关掉；
# 要看那条口径把 GATE_ON 设 True。
GATE_ON = False          # True = 年线四象限闸门 + 波动层（v5 口径）；False = 纯波动层
SLOPE_N = 20             # 年线斜率回看
REDUCE_DAYS = 5          # 跌破年线满 5 日 -> 半仓
CLEAR_DAYS = 20          # 满 20 日 -> 观察仓
ACTION_GAP = 5           # 同腿两次调仓最小间隔
FULL, HALF, OBSERVE = 1.0, 0.5, 0.2
VOL_TARGET = 0.15        # 波动目标（年化）；黄金价格年化波动约 15%，照抄 18% 会空转
VOL_WINDOW = 120         # 已实现波动窗口
VOL_DEADBAND = 0.15      # 系数变化不足该幅度不动
VOL_MIN_GAP = 10         # 两次缩放变化的最小间隔
VOL_MIN_LEVEL = 0.20     # 缩放下限
VOL_MIN_HIST = 500       # 分位/参考的最少样本（fixed 模式下不影响结果）

# ---- C 低仓位占坑（MACD 金叉死叉）
# 2026-11-09 按用户要求把快慢线从 12/26 换成 50/200（长周期口径）：
# 12/26 是日线短打，在黄金上每年约 19 次调仓、成本吃掉全部 alpha；
# 50/200 接近「中期/长期」分界，信号稀疏、换手低，更贴合「占坑」的定位。
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 50, 200, 9
# EMA200 的初始值残留 = (1-2/201)^N，N=250 时仍有约 8% 偏差，故预热提到 2×slow
C_WARMUP = 400
C_TREND_FILTER = False   # True = 只在年线上方允许持仓（敏感性用，默认关闭）

# ---- 成本与现金（场外 A 类 / 场内 ETF 两套）
SLIP = 0.0010            # 场外单边滑点 0.1%（与 v1~v5 同一口径）
FEE_OTC = (rule.FEE_BUY, rule.FEE_SELL)          # (0.10%, 0.50%)
SLIP_IN = 0.0005         # 场内单边滑点 0.05%（黄金 ETF 盘口厚，取 1~2 个 tick）
FEE_IN = (0.0003, 0.0003)                        # 场内佣金万 0.3，无印花税
CASH_ANNUAL = rule.CASH_ANNUAL
CASH_DAILY = (1.0 + CASH_ANNUAL) ** (1.0 / 252) - 1.0

QUADS = ("上涨趋势", "反弹", "高位回调", "下跌趋势")


def params(**kw):
    """参数打包：敏感性扫描用 `**{...}` 覆盖，不必改模块常量。"""
    p = dict(MA_WIN=MA_WIN, NEW_HIGH_WIN=NEW_HIGH_WIN, DEGRADE_DAYS=DEGRADE_DAYS,
             FLOOR=FLOOR, RECOVER_LAG=RECOVER_LAG,
             LADDER_DD=LADDER_DD, LADDER_UNITS=LADDER_UNITS, UNIT_FRAC=UNIT_FRAC,
             ACTION_GAP_A=ACTION_GAP_A, START_A=START_A,
             GATE_ON=GATE_ON, SLOPE_N=SLOPE_N, REDUCE_DAYS=REDUCE_DAYS,
             CLEAR_DAYS=CLEAR_DAYS, ACTION_GAP=ACTION_GAP,
             FULL=FULL, HALF=HALF, OBSERVE=OBSERVE,
             VOL_TARGET=VOL_TARGET, VOL_WINDOW=VOL_WINDOW, VOL_DEADBAND=VOL_DEADBAND,
             VOL_MIN_GAP=VOL_MIN_GAP, VOL_MIN_LEVEL=VOL_MIN_LEVEL,
             VOL_MIN_HIST=VOL_MIN_HIST,
             MACD=(MACD_FAST, MACD_SLOW, MACD_SIGNAL), C_WARMUP=C_WARMUP,
             C_TREND_FILTER=C_TREND_FILTER,
             SLIP=SLIP, FEE_OTC=FEE_OTC)
    p.update(kw)
    return p


# ---------------------------------------------------------------- 2. 数据
def load(mode="otc", refresh=True):
    """黄金价格序列。`otc` = 场外 000216 复权净值（默认）；`exchange` = 场内 518880 收盘。"""
    if mode == "otc":
        if not refresh:
            # data.py 用「_OTC_ONCE 非空」表示本进程已决定过要不要刷新；塞一个空结果 =
            # 与「本地不陈旧」同义，即直接用缓存、不联网（与 refresh_otc 的跳过分支一致）。
            vdata._OTC_ONCE.append(({}, {}))
        return vdata.series_for_leg(LEG).dropna()
    if mode == "exchange":
        return vdata.load_klines(SIGNAL_CODE).dropna()
    raise ValueError(f"未知 mode: {mode}（可选 otc / exchange）")


def indicators(px, p=None):
    """A 用指标表：close / ma / high52w / dd / above / below_days / new_high / ready。"""
    p = p or params()
    I = rule.add_indicators(px)
    if int(p["MA_WIN"]) != rule.MA_WIN or int(p["NEW_HIGH_WIN"]) != rule.MA_WIN:
        c = px.astype("float64")
        I["ma"] = c.rolling(int(p["MA_WIN"]), min_periods=int(p["MA_WIN"])).mean()
        I["high52w"] = c.rolling(int(p["NEW_HIGH_WIN"]), min_periods=int(p["NEW_HIGH_WIN"])).max()
        I["dd"] = 1.0 - c / I["high52w"]
        I["ready"] = I["ma"].notna() & I["high52w"].notna()
        I["above"] = (c >= I["ma"]) & I["ready"]
        I["below_days"] = _streak(~I["above"]).where(I["ready"], 0)
        I["new_high"] = (c >= I["high52w"] - 1e-12) & I["ready"]
    return I


def _streak(cond):
    """cond 连续为 True 的计数（遇 False 归零）。"""
    grp = (~cond.fillna(False)).cumsum()
    return cond.fillna(False).astype(int).groupby(grp).cumsum()


def _fomc_set(idx):
    """FOMC 禁新仓日期集合（输入留空 -> 空集，即该条规则不生效）。"""
    vi = vdata.val_inputs()
    if not vi.get("fomc_days"):
        return set()
    return vdata.fomc_blackout_set(idx, vi["fomc_days"])


# ---------------------------------------------------------------- 3. A 长期配置
def state_a(px, **kw):
    """-> DataFrame(state / decide / done / target / degraded / below_days / quad 指标)。

    `state[t]` = t 日收盘成交后「占目标带的比例」。区间 [0.5, 1.0]（保险丝下限 0.5），
    除起手设 0.9 的档位外不会低于 0.5 —— A 从不清仓，它只决定「满带还是半仓」。
    """
    p = params(**kw)
    I = indicators(px, p)
    idx = px.index
    n = len(px)
    above = I["above"].to_numpy(dtype=bool)
    ready = I["ready"].to_numpy(dtype=bool)
    below = I["below_days"].to_numpy(dtype=int)
    dd = I["dd"].to_numpy(dtype=float)
    nh = I["new_high"].to_numpy(dtype=bool)
    fomc = _fomc_set(idx)

    hold = float(p["START_A"])
    state = np.zeros(n)
    decide = np.empty(n, dtype=object)
    decide[:] = ""
    done = np.empty(n, dtype=object)
    done[:] = ""
    flags = np.zeros(n)                 # 0=正常 1=保险丝生效
    pend = None
    pend_note = ""
    last_act = -10 ** 9
    degraded = False
    recover_left = None
    tiers = [False] * len(p["LADDER_DD"])

    for t, d in enumerate(idx):
        if pend is not None:                    # 执行上一根的决策（T+1 收盘成交）
            hold = float(pend)
            last_act = t
            done[t] = pend_note
            pend, pend_note = None, ""
        state[t] = hold
        flags[t] = 1.0 if degraded else 0.0
        if not ready[t]:
            continue

        want, note, urgent = hold, "", False

        # ---- 1) 保险丝：年线下满 DEGRADE_DAYS 日 -> 上限 0.5（立即卖，不等间隔帽）
        if not above[t] and below[t] >= int(p["DEGRADE_DAYS"]):
            if not degraded:
                degraded, recover_left = True, int(p["RECOVER_LAG"])
            if hold > float(p["FLOOR"]) + 1e-9:
                want, urgent = float(p["FLOOR"]), True
                note = f"年线下第 {below[t]} 日：目标带上限 100% → 50%"
        elif above[t]:
            if degraded:                        # 收复年线 -> 目标带恢复
                degraded = False
            if recover_left is not None:
                recover_left -= 1
                if recover_left <= 0 and hold < 1.0 - 1e-9:
                    want, note = 1.0, "收复年线：回带至满带"
                    recover_left = None

        # ---- 2) 52 周新高 -> 阶梯计数归零
        if nh[t] and any(tiers):
            tiers = [False] * len(tiers)

        # ---- 3) 回撤阶梯：仅在年线上方、未降档、非 FOMC 窗口，且有空位
        if not note and above[t] and not degraded and d not in fomc:
            for i, th in enumerate(p["LADDER_DD"]):
                if dd[t] * 100.0 < th:
                    continue
                if tiers[i]:
                    break
                room = 1.0 - hold
                unit = float(p["LADDER_UNITS"][i]) * float(p["UNIT_FRAC"])
                tiers[i] = True
                if room >= float(p["UNIT_FRAC"]) * 0.2:      # 剩余额度 < 0.2 份不执行
                    delta = min(unit, room)
                    want = hold + delta
                    note = f"回撤阶梯第{i + 1}档(dd≥{th:.0f}% → {p['LADDER_UNITS'][i]:g} 份)"
                break

        # ---- 4) 下单：保险丝卖出立即执行；其余受间隔帽约束
        if abs(want - hold) > 1e-9 and (urgent or (t - last_act) >= int(p["ACTION_GAP_A"])):
            pend, pend_note = want, note
            decide[t] = "buy" if want > hold else "sell"

    out = pd.DataFrame(dict(state=state, decide=decide, done=done, degraded=flags), index=idx)
    return out.join(I)


# ---------------------------------------------------------------- 4. B 控回撤
def _tg():
    """按绝对路径载入 v5 的 trend_gate（避免与 tech_indicators/v17 的同名模块撞名）。"""
    path = os.path.join(_V5, "trend_gate.py")
    name = "_v6_gold_trend_gate"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def b_params(p):
    """B 传给 v5 状态机的参数（显式写全，避免 v5 默认值变化悄悄改变 B 的结论）。"""
    return dict(MA_WIN=250, SLOPE_N=int(p["SLOPE_N"]),
                REDUCE_DAYS=int(p["REDUCE_DAYS"]), CLEAR_DAYS=int(p["CLEAR_DAYS"]),
                ACTION_GAP=int(p["ACTION_GAP"]),
                FULL=1.0, HALF=0.5, OBSERVE=0.2,
                GATE_ALL=True, START="full", MODE="quad",
                VOL_USE=True, VOL_APPLY="full_only", VOL_MODE="fixed",
                VOL_TARGET=float(p["VOL_TARGET"]), VOL_WINDOW=int(p["VOL_WINDOW"]),
                VOL_DEADBAND=float(p["VOL_DEADBAND"]), VOL_MIN_GAP=int(p["VOL_MIN_GAP"]),
                VOL_MIN_LEVEL=float(p["VOL_MIN_LEVEL"]),
                VOL_MIN_HIST=int(p["VOL_MIN_HIST"]))


def state_b(px, **kw):
    """-> DataFrame(state / state_trend / vol_scale / quad)。

    `GATE_ON=True`：四象限三档 × 波动系数（只压满带）；
    `GATE_ON=False`：不做趋势判断，状态 = 波动系数（满带 1.0 被压到 scale）。
    """
    p = params(**kw)
    tg = _tg()
    bp = b_params(p)
    if p["GATE_ON"]:
        sm = tg.state_machine(px, LEG, **bp)
        return pd.DataFrame(dict(state=sm["state"], state_trend=sm["state_trend"],
                                 vol_scale=sm["vol_scale"], quad=sm["quad"],
                                 ready=sm["ready"]), index=px.index)
    sc = tg.vol_scale(px, **bp).clip(0.0, 1.0)
    quad = pd.Series("", index=px.index, dtype=object)
    # 预热与 A 对齐（同样等 MA250）：三条口径从同一天起算，主表才可直接比。
    ready = ((px.rolling(int(p["MA_WIN"]) + 1, min_periods=int(p["MA_WIN"]) + 1)
              .count() > int(p["MA_WIN"]))
             & (px.rolling(int(p["VOL_WINDOW"]) + 1, min_periods=int(p["VOL_WINDOW"]) + 1)
                .count() > int(p["VOL_WINDOW"])))
    return pd.DataFrame(dict(state=sc, state_trend=pd.Series(1.0, index=px.index),
                             vol_scale=sc, quad=quad, ready=ready), index=px.index)


# ---------------------------------------------------------------- 5. C 低仓位占坑
def macd_lines(px, p=None):
    """-> (DIF, DEA) 两条 EMA 线（与 v13_macd 同参数：12/26/9）。"""
    p = p or params()
    fast, slow, sig = p["MACD"]
    dif = px.ewm(span=int(fast), adjust=False).mean() - px.ewm(span=int(slow), adjust=False).mean()
    dea = dif.ewm(span=int(sig), adjust=False).mean()
    return dif, dea


def state_c(px, **kw):
    """-> Series(state)。金叉后 1.0、死叉后 0.0；预热 C_WARMUP 个交易日内不持仓。

    `C_TREND_FILTER=True` 时加一层「只有 close ≥ MA250 才允许持有」，用来检验
    「MACD 的假信号能不能被年线过滤掉」。
    """
    p = params(**kw)
    dif, dea = macd_lines(px, p)
    a = dif.to_numpy(dtype=float)
    b = dea.to_numpy(dtype=float)
    c = px.to_numpy(dtype=float)
    ma = px.rolling(int(p["MA_WIN"]), min_periods=int(p["MA_WIN"])).mean().to_numpy(dtype=float)
    n = len(px)
    st = np.zeros(n)
    pos = 0
    for t in range(n):
        if t < int(p["C_WARMUP"]) or not np.isfinite(a[t]) or not np.isfinite(b[t]):
            st[t] = 0.0
            continue
        if pos == 0 and a[t] > b[t] and a[t - 1] <= b[t - 1]:
            pos = 1
        elif pos == 1 and a[t] < b[t] and a[t - 1] >= b[t - 1]:
            pos = 0
        if p["C_TREND_FILTER"] and pos == 1 and np.isfinite(ma[t]) and c[t] < ma[t]:
            pos = 0                      # 年线下方强制空仓（不改 MACD 状态机本身）
        st[t] = float(pos)
    return pd.Series(st, index=px.index)


# ---------------------------------------------------------------- 6. 回测与指标
def run_leg(state, px, fee=(0.0, 0.0), slip=SLIP, cash_annual=None):
    """单腿净值（起点 = 1）。持仓按 `state.shift(1)` 赚当日收益，成本 = |Δstate| × 费率。

    首日：`state[0]` 视为「已在起点收盘建好仓」（当日收益 0、成本按 state[0] 计），
    这样恒持满带（零成本）与买入持有逐日严格相等；空仓起步则首日就吃货基利息。
    """
    cash = CASH_DAILY if cash_annual is None else (1.0 + float(cash_annual)) ** (1.0 / 252) - 1.0
    ret = px.pct_change().fillna(0.0)
    first = float(state.iloc[0]) if len(state) else 0.0
    hold = state.shift(1).fillna(first).astype(float)
    r = hold * ret + (1.0 - hold) * cash
    d = state.diff().fillna(state).astype(float)
    r = r - d.clip(lower=0.0) * (float(fee[0]) + float(slip)) \
          + d.clip(upper=0.0) * (float(fee[1]) + float(slip))
    return (1.0 + r).cumprod()


def fee_of(mode, p=None):
    """-> (fee_tuple, slip)。场外 A 类 vs 场内 ETF 两套成本。"""
    p = p or params()
    if mode == "otc":
        return tuple(p["FEE_OTC"]), float(p["SLIP"])
    return FEE_IN, SLIP_IN


def actions(state):
    """按 state 变化折出调仓动作 -> [{date, frm, to, kind, gap}]。"""
    s = state.to_numpy(dtype=float)
    idx = state.index
    out, prev = [], 0
    for i in range(1, len(s)):
        if abs(s[i] - s[i - 1]) > 1e-9:
            out.append(dict(date=idx[i], frm=float(s[i - 1]), to=float(s[i]),
                            kind="buy" if s[i] > s[i - 1] else "sell", gap=i - prev))
            prev = i
    return out


def whipsaw(state, px, window=20):
    """打脸：减仓后 window 个交易日内又加回的次数，以及期间让掉的价格变动。"""
    acts = []
    s = state.to_numpy(dtype=float)
    for i in range(1, len(s)):
        if abs(s[i] - s[i - 1]) > 1e-9:
            acts.append((i, "buy" if s[i] > s[i - 1] else "sell"))
    val = px.to_numpy(dtype=float)
    n, miss = 0, []
    for a in range(len(acts) - 1):
        i, kind = acts[a]
        j, kind2 = acts[a + 1]
        if kind == "sell" and kind2 == "buy" and j - i <= window:
            n += 1
            miss.append(val[j] / val[i] - 1.0)
    return dict(n=n, avg_miss=(float(np.mean(miss)) if miss else 0.0))


def perf(nav, rf=None):
    """绩效指标（复用主策略 engine.metrics，无风险利率 = 货基 CASH_ANNUAL）。"""
    return engine.metrics(nav, rf=CASH_ANNUAL if rf is None else rf)


def calmar(m):
    return (m["ann"] / abs(m["mdd"])) if abs(m["mdd"]) > 1e-9 else float("nan")


def exposure(state):
    return float(state.mean()) if len(state) else 0.0


def cost_drag(state, mode, p=None):
    """累计成本占净值的比例（pp，按 |Δstate| × 费率累加，不含滑点外的复利效应）。"""
    fee, slip = fee_of(mode, p)
    s = state.to_numpy(dtype=float)
    tot = 0.0
    for i in range(1, len(s)):
        d = s[i] - s[i - 1]
        tot += (abs(d) * ((fee[0] if d > 0 else fee[1]) + slip))
    return tot


def slice_from(ind_like):
    """指标表 -> 第一个 ready 的日期（各口径共同的起算规则）。"""
    s = ind_like["ready"] if "ready" in ind_like else ind_like.notna()
    ok = s[s.fillna(False)]
    return ok.index[0] if len(ok) else ind_like.index[0]


def build(mode="otc", refresh=True, **kw):
    """-> dict(px, A, B, C, start)。三个口径都从「各自预热完成」的日期起算。"""
    p = params(**kw)
    px = load(mode, refresh=refresh)
    A = state_a(px, **kw)
    B = state_b(px, **kw)
    C = state_c(px, **kw)
    start_a = A.index[A["ready"].fillna(False).to_numpy(dtype=bool)][0]
    start_b = B.index[B["ready"].fillna(False).to_numpy(dtype=bool)][0]
    start_c = px.index[min(int(p["C_WARMUP"]), len(px) - 2)]
    return dict(px=px, A=A, B=B, C=C, start=dict(A=start_a, B=start_b, C=start_c), mode=mode, params=p)


def _self_check():
    """快速自检：两条序列可读、三口径档位语义正确、无前视。"""
    for mode in ("otc", "exchange"):
        d = build(mode, refresh=False)
        px, A, B, C = d["px"], d["A"], d["B"], d["C"]
        assert len(A) == len(B) == len(C) == len(px), "长度不一致"
        for nm, s in (("A", A["state"]), ("B", B["state"]), ("C", C)):
            assert float(s.min()) >= -1e-9 and float(s.max()) <= 1.0 + 1e-9, f"{nm} 越界"
        assert float(A["state"].min()) >= FLOOR - 1e-9, "A 不应低于保险丝下限"
        assert bool((B["state"] <= B["state_trend"] + 1e-9).all()), "B 的波动层只压不抬"
        assert set(np.round(C.unique(), 6)) <= {0.0, 1.0}, C.unique()
        code = EXEC_CODE if mode == "otc" else SIGNAL_CODE
        print(f"{mode:8s} {code} {px.index[0].date()} ~ {px.index[-1].date()} n={len(px):5d} "
              f"A起 {d['start']['A'].date()} 档位={A['state'].iloc[-1]:.0%} "
              f"B起 {d['start']['B'].date()} 档位={B['state'].iloc[-1]:.0%} "
              f"C起 {d['start']['C'].date()} 仓位={C.iloc[-1]:.0%}")
    print(f"带 {BAND_PP:g}pp · 年线 MA{MA_WIN} · 保险丝 {DEGRADE_DAYS} 日 → {FLOOR:.0%} · "
          f"阶梯 {LADDER_DD} → {LADDER_UNITS} 份(1 份={UNIT_FRAC:.0%} 带)")
    print(f"B: 闸门={'开' if GATE_ON else '关'} · 波动目标 {VOL_TARGET:.0%}/{VOL_WINDOW} 日 · "
          f"死区 {VOL_DEADBAND:g} · 限速 {VOL_MIN_GAP} 日")
    print(f"C: MACD{MACD_FAST}/{MACD_SLOW}/{MACD_SIGNAL} · 预热 {C_WARMUP} 日 · "
          f"年线过滤={'开' if C_TREND_FILTER else '关'}")
    print(f"成本: 场外 {FEE_OTC[0]:.2%}/{FEE_OTC[1]:.2%} + 滑点 {SLIP:.2%} · "
          f"场内 {FEE_IN[0]:.2%}/{FEE_IN[1]:.2%} + 滑点 {SLIP_IN:.2%} · 现金 {CASH_ANNUAL:.2%}")


if __name__ == "__main__":
    _self_check()
