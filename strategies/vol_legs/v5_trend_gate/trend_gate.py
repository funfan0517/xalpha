# -*- coding: utf-8 -*-
"""波动资产 · V5 变种：趋势四象限闸门（年线位置 × 年线 20 日斜率）。

来源：用户 2026-11-02 的「趋势判断」说明。核心命题是**趋势判断不是预测**，而是把
已经发生的价格结构压缩成一个可计算的布尔值；工程上只有两个数：

- `close` 在 250 日均线（A 股年线）**上方还是下方**；
- 250 日均线**自身近 20 日朝上还是朝下**（同一根线的属性，不是新维度）。

两者都朝上才叫上涨趋势，**其余三格都不给加仓许可**：

|  | 均线朝上 | 均线朝下 |
|---|---|---|
| **close > ma250** | 上涨趋势 → 可加仓（满带） | 反弹 → 只持有，不给加仓许可 |
| **close < ma250** | 高位回调 → 降一档（封顶半仓），不新增 | 下跌趋势 → 5 日减半 → 20 日清至观察，等右侧 |

右上角那格是本变种存在的理由：多数人把「站上年线」当成趋势反转，但 A 股大量行情是
「跌透之后的暴力反弹先把价格推回均线上方、而均线还在下行」（2024-10、2025-09 都是）。
价格先动、均线后动，中间这段必须单独分格，否则「站上年线就加仓」会买在反弹的第一天。

## 1. 用在哪、不用在哪（这是规则的一部分，不是建议）

- **只用在没有现金流锚的资产**：科创50（亏损股权导致 ERP 失真）、恒生科技、黄金。
  这类资产价格本身就是唯一可靠的信息源，年线闸门是合理的。
- **不要用在宽基上**：510300 的回测里，纯估值分位调仓年化 6.45% / 回撤 18.24%，
  叠加「跌破 200 日均线就降仓」后只剩 4.10% / 16.47% —— 用 2.35pp 年化换 1.77pp 回撤，
  不划算。原因是宽基的估值锚本身已经隐含均值回归（跌多了 PE 分位自动变低、自动触发买入），
  再加一层趋势过滤，等于把同一个信号用两次并让它们互相抵消。
- 历史默认作用域是 `GATE_LEGS = ("kc50", "hkt", "gold")`（只套无现金流锚的三条腿），中证A500 /
  沪深300 / 纳指100 / 标普500 不套闸门（维持带内满仓）。`GATE_ALL=True` 把七腿全套上，用来验证
  「给宽基叠趋势过滤不划算」在本库净值上是否成立 —— 回测里它确实让渡约 2.4pp 年化、回撤无改善。
  **2026-11-03 起按用户要求，默认作用域改为全部七条腿**（`GATE_ALL=True`），三腿版降为对照。

## 2. 三个坑（写在参数里，不是写在注释里）

1. **滞后是设计特性不是 bug**。ma250 确认「进入下跌趋势」通常已经跌掉 15–25%（回测报告
   「滞后成本」表逐腿给出实测中位数）。目标是避开以年为单位的阴跌段，不是躲三个月的回调；
   拿它躲短期波动必然被反复打脸。
2. **250 和 200 不是一回事**。A 股一年约 240–245 个交易日，中文语境的「年线」取 250；
   美股习惯 200 日均线。混用会让两套回测结论看起来互相矛盾。这里 `MA_WIN=250` 全池统一。
3. **别加过滤器，加频率帽**。震荡市里假信号一定存在；工程解法不是再叠一个指标（会引入新维度），
   而是限制响应速度。本实现用两层频率帽，不带任何技术指标过滤：
   - 阶梯帽：跌破年线满 `REDUCE_DAYS=5` 日才减半、满 `CLEAR_DAYS=20` 日未收复才清至观察；
   - 间隔帽：同一腿两次调仓至少间隔 `ACTION_GAP=5` 个交易日。

## 3. 仓位语义（与 v1/v2/v3 不同）

本变种不是 0/1 择时信号，而是**仓位带上的闸门**：`state∈{1.0, 0.5, 0.2}` 是「占本腿目标带
的比例」。带位仍由主策略决定（`rule.LEGS[leg]["band"]`：科创50/恒生科技 2.5pp、黄金 10pp），
本变种只回答「这条带今天该露出多少」。所以它的基准对照是**静态带**（恒持满带），
不是「买入持有」（虽然逐腿也给买入持有列）。

观察仓 0.2 与主策略 `rule.OBSERVE_PP`（0.5pp / 2.5pp 带）同比例，减半 1.25pp 与之拉开一档。

## 3.5 读数口径（研究用；默认不动，报告见 `research/_v5_trend_sens.md`）

`KIND` 决定「趋势读数」是哪条线（`ma` / `donchian` / `dual_ma` / `momentum` / `macd`），
四象限与档位阶梯不变 —— **换口径只是换温度计，不是换规则**。15 档实测：更灵敏的确认确实存在
（MACD(12,26) 在距高点 −7.5% 就确认下跌，现行 −13.5%），但**没换来更高年化**
（各档 16.6%~18.1%，宽度 1.5pp），切换频率却从 6.6 次/年涨到 32 次/年。

唯一有结构性理由的方向是**非对称**：`MODE="asym"` + `DOWN_WIN=120` / `DOWN_SLOPE=5` ——
减仓用快线（MA120+5 日）、加仓仍由 MA250+20 把门（两套读数各算目标档位取 `min`，快口径
只能压更低、不能抬更高）。实测 +18.12% / 回撤 −11.61% / 夏普 1.58（默认 +17.83% / −12.07% /
1.53），调仓 25→32 次、三腿超额 −0.65pp→+0.80pp；幅度仍在噪声内，故**默认关闭**。

## 3.9 波动层（`VOL_USE`，默认开启）

四象限只回答「该露多少」，但**同一个档位在不同波动环境下承担的风险差一倍以上**。所以外面再套
一层「按已实现波动缩放」的风险预算（与 `growth_div_rotation/v5_vol_target` 同一思想）：

    scale_t = clip(target_t / 已实现年化波动_{t-1}, VOL_MIN_LEVEL, 1)     # 只减不加
    实际档位 = 趋势档位 × scale_t（VOL_APPLY="full_only" 时只压满带）    # 腾出的进现金

`target_t` 有两种取法（`VOL_MODE`）：

- **`"adaptive"`（默认，推荐）**：`target = VOL_GAIN × 该腿自身波动历史的 VOL_REF_Q 分位`。
  `q80 + gain 0.9` 的含义是「只在波动冲到自身历史前 20% 时压，门槛打九折」——参数与标的波动
  量级解耦，一套参数套七条腿，不必给每条腿挑绝对数。
- `"fixed"`：`target = VOL_TARGET`（绝对年化波动），需要逐腿标定，仅作对照保留。

⚠ 三个必须知道的点（`research/_vol_overlay.md` 实测）：

1. **`gain=1` 不等于「平均不压」**。`E[ref/vol] ≠ ref/E[vol]`（Jensen），中位参考 + gain=1 时
   平均曝光只有 63%，结构性地常年留 37% 现金。所以默认用**分位参考**（只在尾部压）。
2. **不是所有腿都受益**。长牛宽基（纳指/标普）的波动抬升常伴随趋势加速，压仓会砍在上涨里；
   分位参考把这一代价从 −3.4pp 收窄到「Calmar 由 0.52 变 0.56（反而更好）」，但仍非稳赚。
3. **改善集中在少数时段**（黄金 2023-2026、A股宽基 2010-2016），**不等于稳定 alpha**。
   默认开着是为了「结构统一」，不是因为它被证明能提高收益。

`VOL_MIN_HIST=500` 之前参考分位无法计算 → `scale` 恒为 1（该段等同于纯趋势版）。

## 4. 成交与时序（无前视）

`state[t]` = **t 日收盘成交完之后**的带内占比；t 日只是决策，成交在 t+1 收盘净值
（与 v1/v2/v3 同一纪律）。日收益归属用 `state.shift(1)`。空仓部分按货基 `rule.CASH_ANNUAL`
（1.40%）逐日计息，不假设 0 收益。

## 5. 与其它变种的关系

同属「逐腿进出时点」层，与主策略的组合层仓位带可叠加不可替代。与 v1_ma20 同向（都做多头
趋势），但 v1 是 0/1 全进全出、且用 MA20（月线级别），本变种是带内分档、用 MA250（年级别）；
与 v2/v3（突破追涨）方向不冲突（本变种不看突破），但不要和它们共用同一份额度。

模拟结果，非投资建议。
"""
import math
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_VL = os.path.dirname(_HERE)                        # strategies/vol_legs
_ROOT = os.path.dirname(os.path.dirname(_VL))       # 仓库根
for _p in (_HERE, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import data as vdata    # noqa: E402
import engine           # noqa: E402
import rule             # noqa: E402

LABEL = "V5 趋势四象限闸门"

# ---------------------------------------------------------------- 1. 参数（唯一权威源）
MA_WIN = 250            # 年线（A 股口径 250，不是美股 200）
SLOPE_N = 20            # 年线斜率回看交易日数

# 趋势「读数」口径：默认均线法。研究脚本 `research/_sens_trend.py` 会换这几档做灵敏度对比
# （它们全部映射成同一组 (above, up) 两个布尔量，档位阶梯不改）：
#   ma        价格 vs MA(MA_WIN)
#   donchian  价格 vs 唐奇安通道中轨 (N 日最高+最低)/2
#   dual_ma   价格 ≥ MA(FAST_WIN) 且 MA(FAST_WIN) ≥ MA(SLOW_WIN)
#   momentum  动量读数 vs 0（MOM_N 日累计收益）
#   macd      DIF > 0 且 DIF ≥ DEA（FAST_WIN/SLOW_WIN 当 EMA 周期）
KIND = "ma"
BUF = 0.0               # 均线类口径的确认缓冲（0 = 贴着线就翻转；0.02 = 要跌破 2% 才算跌破）
FAST_WIN = 50           # dual_ma / macd 的快线（EMA）
SLOW_WIN = 200          # dual_ma / macd 的慢线（EMA）
KAMA_N = 10             # 考夫曼自适应均线：效率比率窗口
KAMA_FAST = 2           # KAMA 快端 EMA 常数（2/(n+1) 里的 n）
KAMA_SLOW = 30          # KAMA 慢端
HULL_N = 100            # Hull 均线窗口（低滞后）
DON_N = 55              # 唐奇安通道回看
MOM_N = 126             # 动量口径回看（约 6 个月）
REF_HI = 250            # 「距高点回撤」的参照窗口，固定不随口径变（否则滞后统计不可比）
REDUCE_DAYS = 5         # 跌破年线满 5 个交易日 → 减至半仓
CLEAR_DAYS = 20         # 满 20 个交易日未收复 → 清至观察仓
ACTION_GAP = 5          # 同一腿两次调仓的最小间隔（频率帽第二层）

FULL, HALF, OBSERVE = 1.0, 0.5, 0.2      # 满带 / 半仓 / 观察仓（带的占比）
LEVEL_TXT = {1.0: "满带", 0.5: "半仓", 0.2: "观察仓", 0.0: "空仓"}    # 档位中文名（图与每日共用）

# ============ 波动层（第三层风险预算，默认开启；实测见 research/_vol_overlay.md） ============
# 关掉即退化成纯趋势版（v5.0 口径）：`VOL_USE=False`。
VOL_USE = True
VOL_APPLY = "full_only"   # "full_only"=只压满带（半仓/观察仓原样，语义最干净）/ "mul"=乘全部档位 / "cap"=min
VOL_MODE = "adaptive"     # "adaptive"=自身波动分位的倍数（推荐）/ "fixed"=绝对目标 VOL_TARGET
VOL_REF_Q = 0.80          # adaptive 参考 = 该腿波动历史的 80 分位（只在最波动的 20% 日子压）
VOL_GAIN = 0.90           # target = gain × 参考；<1 表示门槛打九折（略保守）
VOL_TARGET = 0.15         # 仅 VOL_MODE="fixed" 生效（绝对年化波动）
VOL_WINDOW = 120          # 已实现波动窗口（交易日）
VOL_DEADBAND = 0.15       # 系数变化不足该幅度不动（防每天微调）
VOL_MIN_GAP = 10          # 两次缩放变化的最小间隔（交易日）
VOL_MIN_LEVEL = 0.20      # 缩放下限：不接受比「观察仓」还碎的仓位
VOL_MIN_HIST = 500        # 参考分位最少需要的样本；在此之前 scale 恒为 1（无前视）

# 作用域：默认套全部七条腿（2026-11-03 起按用户要求「标的池改为全部 7 条腿」）。
# `GATE_ALL=True` 时 `gated()` 对七腿全返回 True；`GATE_LEGS` 只作为「无现金流锚子集」的
# 分类常量（research 脚本按它取三腿口径、以及 GATE_ALL=False 时的回落集合）。历史默认只套这三条腿，
# 宽基因估值锚自带均值回归而不套；该取舍在收益效率上仍成立（七腿全套让渡约 2.4pp 年化、回撤无改善），
# 故 `GATE_LEGS` 保持三腿，三腿版回测/研究降为对照。
GATE_LEGS = ("kc50", "hkt", "gold")
GATE_ALL = True         # 默认 = 七腿全套闸门；设为 False 则回落到只套 GATE_LEGS（三腿）
START = "full"          # "full" = 带内满仓起步（实盘口径）/ "flat" = 空仓起步
MODE = "quad"           # "quad" 四象限（默认）/ "ma_only" 只看 close 与年线（对照）/
                        # "asym" 非对称：减仓用快线（DOWN_WIN + DOWN_SLOPE），加仓仍由 MA250+20 把门
DOWN_WIN = 120          # 非对称档的「快线」：跌破它满 REDUCE_DAYS 日就减半（只压不抬）
DOWN_SLOPE = 5          # 快线斜率回看（判定「快线下行」）

WARMUP = MA_WIN + SLOPE_N - 1     # 年线成形 + 斜率可算的第一个交易日
SLIP = 0.001            # 单边滑点 0.1%（与 v1~v6 同一口径）

CASH_ANNUAL = rule.CASH_ANNUAL
CASH_DAILY = (1.0 + CASH_ANNUAL) ** (1.0 / 252) - 1.0
LEGS = rule.LEG_ORDER
W = {k: float(rule.LEGS[k]["band"]) / float(rule.SLEEVE_PP) for k in LEGS}   # 带权归一化(Σ=1)

# 场外申赎成本（(买入, 卖出) 单边费率），与 v1/v2/v3/v6 同一张表：
# A 类（270042 / 017641 / 000216）申购 0.10% + 赎回 0.50%（持有 >7 天）；
# C 类（023299 / 011609 / 012805）申赎免费，改收约 0.2%/年销售服务费 —— 后者**未计入**，
# 故 C 类腿的扣费后数字偏乐观。滑点 `SLIP` 在 `fee_of()` 里逐边追加。
FEE = {
    "a500": (0.0, 0.0),
    # v1.4 第七腿: 000051 是 A 类(申购 0.10% / 赎回 0.50%), 与美股腿同一档
    "hs300": (0.001, 0.005),
    "ndx": (0.001, 0.005),
    "spx": (0.001, 0.005),
    "kc50": (0.0, 0.0),
    "hkt": (0.0, 0.0),
    "gold": (0.001, 0.005),
}

QUADS = ("上涨趋势", "反弹", "高位回调", "下跌趋势")

# 各腿「判趋势用哪种读数」—— 逐腿实测的结论（每日报告的「板块2」直接引用这张表）。
# 数字都是该腿**自己样本**上的回测：2026-11-03 起默认七腿全套装闸门（GATE_ALL=True），
# 下表每一行都是该腿当前的真实读数。样本：A500 16.2 年、纳指 13.0 年、标普 13.2 年、黄金 12.0 年、
# 科创50 4.4 年、恒生科技 4.1 年。研究过程见 research/_a500_trend.md 与 research/_v5_trend_*.png。
# v1.4 新增的沪深300 没有单独立项（它与 A500 的场外序列 2025-03 前完全相同），沿用 A500 的结论。
TREND_READ = {
    "a500": dict(
        label="双均线 50/150 + 10 日斜率",
        stats="套闸门 +5.10% / -31.45% / 0.29 · 4.5 次/年（年线口径只有 +2.74% / -42.23% / 0.08 · 2.8 次/年）",
        why="16.2 年里唯一「年化与回撤同时改善」的口径，滞后也短一档（下侧 55%、上侧 74%；年线 66%、86%）。"
            "该腿同样套闸门（默认七腿全套），换口径只改「读数」，不影响档位。"),
    "hs300": dict(
        label="年线 250 + 20 日斜率（未单独立项）",
        stats="沿用中证A500 的结论（同族宽基，两腿成分重叠约八成）",
        why="2026-11-02 新增的第七腿，未单独跑过口径对比：场外序列在 2025-03-27 之前与中证A500 "
            "**完全相同**（同一只 000051 联接），单独再跑一遍只会得到同一张图。现与 A500 同套闸门，"
            "读数沿用其结论。"),
    "ndx": dict(
        label="年线 250 + 20 日斜率",
        stats="套闸门 +14.06% / -26.49% / 0.72 · 1.5 次/年（双均线 +7.13% / -19.40% / 0.41 · 5.8 次/年）",
        why="美股慢牛急跌，快口径一年翻 20 次、被洗掉一半收益；年线几乎不用动就能待在里面。"),
    "spx": dict(
        label="年线 250 + 20 日斜率",
        stats="套闸门 +10.20% / -29.53% / 0.62 · 1.4 次/年（双均线 +4.04% / -20.49% / 0.25 · 5.5 次/年）",
        why="同纳指：换快口径年化腰斩，只为少 9pp 回撤，不划算。"),
    "kc50": dict(
        label="年线 250 + 20 日斜率",
        stats="+5.49% / -40.51% / 0.17 · 2.0 次/年（双均线 -0.72% / -29.52% / -0.10 · 4.1 次/年）",
        why="4.4 年高波动样本，象限翻转从 2.9 次/年涨到 11.1 次/年，快口径把收益直接打没。"),
    "hkt": dict(
        label="年线 250 + 20 日斜率",
        stats="-3.50% / -26.79% / -0.25 · 3.7 次/年（双均线 -5.24% / -30.26% / -0.35 · 4.6 次/年）",
        why="4.1 年震荡样本两口径都亏，年线少亏又少交易；这条腿闸门的价值在压回撤（恒持 -44.74% → -26.79%），不在抓涨。"),
    "gold": dict(
        label="年线 250 + 20 日斜率（双均线可替补）",
        stats="+7.90% / -29.87% / 0.49 · 2.3 次/年（双均线 +7.94% / -25.07% / 0.54 · 4.1 次/年）",
        why="12.0 年收益打平、回撤浅 4.8pp，但调仓几乎翻倍，属噪声级改善，默认不换。"),
}


def params(**kw):
    """参数打包：回测敏感性扫描时用 `**{...}` 覆盖，不必改模块常量。"""
    p = dict(MA_WIN=MA_WIN, SLOPE_N=SLOPE_N, REDUCE_DAYS=REDUCE_DAYS,
             CLEAR_DAYS=CLEAR_DAYS, ACTION_GAP=ACTION_GAP,
             FULL=FULL, HALF=HALF, OBSERVE=OBSERVE,
             GATE_LEGS=GATE_LEGS, GATE_ALL=GATE_ALL, START=START, MODE=MODE,
             DOWN_WIN=DOWN_WIN, DOWN_SLOPE=DOWN_SLOPE,
             KIND=KIND, BUF=BUF, FAST_WIN=FAST_WIN, SLOW_WIN=SLOW_WIN, DON_N=DON_N, MOM_N=MOM_N,
             KAMA_N=KAMA_N, KAMA_FAST=KAMA_FAST, KAMA_SLOW=KAMA_SLOW, HULL_N=HULL_N,
             VOL_USE=VOL_USE, VOL_APPLY=VOL_APPLY, VOL_MODE=VOL_MODE, VOL_REF_Q=VOL_REF_Q,
             VOL_GAIN=VOL_GAIN, VOL_TARGET=VOL_TARGET, VOL_WINDOW=VOL_WINDOW,
             VOL_DEADBAND=VOL_DEADBAND, VOL_MIN_GAP=VOL_MIN_GAP, VOL_MIN_LEVEL=VOL_MIN_LEVEL,
             VOL_MIN_HIST=VOL_MIN_HIST)
    p.update(kw)
    return p


def gated(leg, p=None):
    """该腿是否套闸门（默认 GATE_ALL=True，七腿全套装）。"""
    p = p or params()
    if p["GATE_ALL"]:
        return True
    return leg in tuple(p["GATE_LEGS"])


def load(leg, mode=None):
    """该腿价格序列（默认场外复权净值）；缺失/刷新失败直接抛错，不静默补位。"""
    return vdata.series_for_leg(leg, mode=mode).dropna()


def fee_of(leg, slip=None):
    """-> (买入费率, 卖出费率)，含滑点。"""
    s = SLIP if slip is None else float(slip)
    b, sl = FEE[leg]
    return float(b) + s, float(sl) + s


# ---------------------------------------------------------------- 2. 指标与象限
def _streak(cond):
    """cond 连续为 True 的计数（遇 False 归零）。"""
    grp = (~cond.fillna(False)).cumsum()
    return cond.fillna(False).astype(int).groupby(grp).cumsum()


def _wma(s, n):
    """加权移动平均（近端权重高）—— Hull 均线的零件。"""
    w = np.arange(1, n + 1, dtype=float)
    return s.rolling(n, min_periods=n).apply(lambda x: float(np.dot(x, w)) / w.sum(), raw=True)


def _kama(c, n, fast, slow):
    """考夫曼自适应均线：平滑系数随「效率比率」在 fast/slow 之间自适应。"""
    arr = c.to_numpy(dtype=float)
    er = (c.diff(n).abs() / c.diff().abs().rolling(n).sum()).to_numpy(dtype=float)
    sc = (np.nan_to_num(er) * (2.0 / (fast + 1) - 2.0 / (slow + 1))
          + 2.0 / (slow + 1)) ** 2
    out = np.full(len(arr), np.nan)
    if len(arr) <= n:
        return pd.Series(out, index=c.index)
    out[n] = arr[n]
    for i in range(n + 1, len(arr)):
        out[i] = out[i - 1] + sc[i] * (arr[i] - out[i - 1])
    return pd.Series(out, index=c.index)


def readout(c, p):
    """按 `KIND` 算该口径的「趋势读数」。-> (base, above, ok)

    `base` 是读数序列（均线 / 中轨 / 快线 / 动量 / DIF），下游统一用它算斜率；
    `above` 是「处于上方态」的布尔序列；`ok` 是该读数已成形。

    这样五种口径都能压成同一组 (above, up)，档位阶梯与回测代码不用改 —— 换口径只换读数，
    换的不是规则。
    """
    kind = p["KIND"]
    if kind == "ma":
        base = c.rolling(p["MA_WIN"], min_periods=p["MA_WIN"]).mean()
        return base, c >= base * (1.0 - p["BUF"]), base.notna()
    if kind == "donchian":
        n = p["DON_N"]
        mid = (c.rolling(n, min_periods=n).max() + c.rolling(n, min_periods=n).min()) / 2.0
        return mid, c >= mid * (1.0 - p["BUF"]), mid.notna()
    if kind == "dual_ma":
        f = c.rolling(p["FAST_WIN"], min_periods=p["FAST_WIN"]).mean()
        s = c.rolling(p["SLOW_WIN"], min_periods=p["SLOW_WIN"]).mean()
        return f, (c >= f * (1.0 - p["BUF"])) & (f >= s), f.notna() & s.notna()
    if kind == "momentum":
        mom = c / c.shift(p["MOM_N"]) - 1.0
        return mom, mom > 0.0, mom.notna()
    if kind == "macd":
        dif = (c.ewm(span=p["FAST_WIN"], adjust=False).mean()
               - c.ewm(span=p["SLOW_WIN"], adjust=False).mean())
        dea = dif.ewm(span=9, adjust=False).mean()
        return dif, (dif > 0.0) & (dif >= dea), dif.notna()
    if kind == "kama":
        # 考夫曼自适应均线：趋势明确（效率比率高）时几乎贴价格跑，震荡时自动变慢 ——
        # 「又快又稳」的教科书答案，代价是它有自相关、不好解释
        base = _kama(c, p["KAMA_N"], p["KAMA_FAST"], p["KAMA_SLOW"])
        return base, c >= base * (1.0 - p["BUF"]), base.notna()
    if kind == "hull":
        # Hull 均线：WMA(2*WMA(n/2) - WMA(n), sqrt(n))，用加权压低滞后
        n = p["HULL_N"]
        base = _wma(2.0 * _wma(c, max(n // 2, 2)) - _wma(c, n), max(int(np.sqrt(n)), 2))
        return base, c >= base * (1.0 - p["BUF"]), base.notna()
    raise ValueError(
        f"未知 KIND: {kind}（可选 ma / donchian / dual_ma / momentum / macd / kama / hull）")


def indicators(px, **kw):
    """收盘价 -> 指标表。只用到当日及之前的数据。

    列：close / ma / slope / above / below_days / quad / dev / dd_hi / ready
        + 诊断列 mom6 / mom12（动量法口径，只用于「三口径分歧」统计，不参与交易）

    `ma` 这一列名沿用均线口径的叫法，实际存的是 `KIND` 对应口径的读数（均线 / 中轨 / 快线 /
    动量 / DIF）——下游只把它当「一条要比较斜率的主线」，不假设它是均线。
    读数类口径（momentum / macd）没有「价格乖离」，`dev` 记 NaN。
    """
    p = params(**kw)
    c = px.astype("float64")
    out = pd.DataFrame(index=c.index)
    out["close"] = c
    base, above, ok = readout(c, p)
    out["ma"] = base
    out["slope"] = base - base.shift(p["SLOPE_N"])
    out["ready"] = ok & out["slope"].notna()
    out["above"] = above
    # 连续不在上方态只从「指标成形」那天起算 —— 预热期的 NaN 不参与计数，
    # 否则第一条 ready 的记录会显示「已跌破 270 日」（实际上是预热期被算了进去）
    out["below_days"] = _streak(~out["above"] & out["ready"]).where(out["ready"], 0)
    if p["KIND"] in ("ma", "donchian", "dual_ma", "kama", "hull"):
        out["dev"] = c / base - 1.0                       # 价格乖离
    else:
        out["dev"] = np.nan                               # 读数类口径无乖离概念
    out["dd_hi"] = c / c.rolling(REF_HI, min_periods=1).max() - 1.0
    up = out["slope"] > 0.0
    quad = np.where(out["above"].to_numpy(dtype=bool),
                    np.where(up.to_numpy(dtype=bool), QUADS[0], QUADS[1]),
                    np.where(up.to_numpy(dtype=bool), QUADS[2], QUADS[3]))
    out["quad"] = np.where(out["ready"].to_numpy(dtype=bool), quad, "")
    out["mom6"] = c / c.shift(126) - 1.0
    out["mom12"] = c / c.shift(252) - 1.0
    return out


def target_of(quad, below_days, cur, p=None):
    """象限 + 连续在年线下的天数 -> (目标带内占比, 理由)。纯函数，回测与每日共用。"""
    p = p or params()
    full, half, obs = p["FULL"], p["HALF"], p["OBSERVE"]
    if p.get("MODE") == "ma_only":              # 对照档：只看 close 在年线上/下
        return (full, "年线上方：满带") if quad in (QUADS[0], QUADS[1]) \
            else (obs, "年线下方：清至观察仓")
    if quad == QUADS[0]:                        # 上涨趋势：两个都朝上
        return full, "年线上方且年线上行：加至满带"
    if quad == QUADS[1]:                        # 反弹：价格回到年线上，均线还在下
        return cur, "年线上方但年线下行：只持有，不给加仓许可"
    if quad == QUADS[2]:                        # 高位回调：均线还朝上
        if below_days >= p["REDUCE_DAYS"] and cur > half:
            return half, f"年线上行但已跌破 {below_days} 日：降一档"
        return cur, "年线下方：不新增"
    # 下跌趋势：两个都朝下
    if below_days >= p["CLEAR_DAYS"] and cur > obs:
        return obs, f"年线下行且已跌破 {below_days} 日：清至观察仓，等右侧"
    if below_days >= p["REDUCE_DAYS"] and cur > half:
        return half, f"年线下行且已跌破 {below_days} 日：减半"
    return cur, "年线下方：不新增"


def vol_scale(px, **kw):
    """-> 波动系数 Series（>0，≤1）。`scale[t]` 只用 t-1 及之前的已实现波动，无前视。

    `VOL_USE=False` 时恒为 1.0（退化成纯趋势版）。
    参考值 `target` 的两种取法见模块 docstring §3.9：adaptive（自身分位）/ fixed（绝对数）。
    系数变化受 `VOL_DEADBAND` 与 `VOL_MIN_GAP` 双重限速；下限 `VOL_MIN_LEVEL` 防止碎仓。
    """
    p = params(**kw)
    idx = px.index
    if not p["VOL_USE"]:
        return pd.Series(1.0, index=idx)
    ret = px.astype("float64").pct_change().fillna(0.0)
    vol = ret.rolling(int(p["VOL_WINDOW"]), min_periods=int(p["VOL_WINDOW"])).std() * math.sqrt(252.0)
    if p["VOL_MODE"] == "fixed":
        tgt = pd.Series(float(p["VOL_TARGET"]), index=idx)
    elif p["VOL_MODE"] == "adaptive":
        tgt = (vol.expanding(min_periods=int(p["VOL_MIN_HIST"])).quantile(float(p["VOL_REF_Q"]))
               * float(p["VOL_GAIN"]))
    else:
        raise ValueError(f"未知 VOL_MODE: {p['VOL_MODE']}（可选 adaptive / fixed）")
    raw = (tgt / vol).clip(lower=float(p["VOL_MIN_LEVEL"]), upper=1.0).shift(1)
    out = np.empty(len(idx))
    cur, last = 1.0, None
    for i, v in enumerate(raw.to_numpy(dtype=float)):
        if not np.isnan(v):
            far = last is None or (i - last) >= int(p["VOL_MIN_GAP"])
            if far and abs(v - cur) >= float(p["VOL_DEADBAND"]) - 1e-12:
                cur, last = float(v), i
        out[i] = cur
    return pd.Series(out, index=idx)


def apply_vol(trend, scale, mode="full_only"):
    """趋势档位 + 波动系数 -> 实际档位。三种作用形态见 `VOL_APPLY`。"""
    t = np.asarray(trend, dtype=float)
    sc = np.asarray(scale, dtype=float)
    if mode == "mul":
        eff = t * sc
    elif mode == "full_only":          # 只压满带：半仓 / 观察仓原样
        eff = np.where(t >= 1.0 - 1e-9, np.minimum(t, sc), t)
    elif mode == "cap":
        eff = np.minimum(t, sc)
    else:
        raise ValueError(f"未知 VOL_APPLY: {mode}（可选 full_only / mul / cap）")
    return pd.Series(np.clip(eff, 0.0, 1.0), index=trend.index)


def state_machine(px, leg, **kw):
    """-> DataFrame(index=净值日)。`state[t]` = t 日收盘成交后的带内占比。

    `decide[t]` ∈ {buy, sell, ""} 是 t 日收盘做出、t+1 日成交的决策；`done[t]` 是 t 日
    成交后的说明。不套闸门的腿恒为满带（`state=1.0`，decide 恒空）。
    """
    p = params(**kw)
    I = indicators(px, **kw)
    n = len(px)
    state = np.zeros(n)
    decide = np.empty(n, dtype=object)
    decide[:] = ""
    done = np.empty(n, dtype=object)
    done[:] = ""
    tgt_v = np.full(n, np.nan)

    if not gated(leg, p):
        state[:] = 1.0
        flat = pd.Series(1.0, index=px.index)
        out = pd.DataFrame(dict(state=flat, state_trend=flat,
                                vol_scale=pd.Series(1.0, index=px.index),
                                decide=decide, done=done, target=1.0), index=px.index)
        return out.join(I)

    c = I["close"].to_numpy(dtype=float)
    quad = I["quad"].to_numpy(dtype=object)
    ready = I["ready"].to_numpy(dtype=bool)
    below = I["below_days"].to_numpy(dtype=int)

    # 非对称档（MODE="asym"）：另算一套「快」读数，只允许把档位压得更低（min），
    # 加仓许可仍由慢口径（主读数）把着 —— 快线下车、慢线上车。
    d_quad = d_below = d_ready = None
    if p["MODE"] == "asym":
        D = indicators(px, KIND="ma", MA_WIN=p["DOWN_WIN"], SLOPE_N=p["DOWN_SLOPE"])
        d_quad = D["quad"].to_numpy(dtype=object)
        d_below = D["below_days"].to_numpy(dtype=int)
        d_ready = D["ready"].to_numpy(dtype=bool)

    hold = 1.0 if p["START"] == "full" else 0.0
    last_act, pend, pend_note = -10 ** 9, None, ""
    for t in range(n):
        if pend is not None:                     # 执行上一根的决策（T+1 收盘成交）
            hold = float(pend)
            last_act = t
            done[t] = pend_note
            pend = None
        state[t] = hold
        if not ready[t]:
            continue
        tgt, why = target_of(quad[t], int(below[t]), hold, p)
        if d_ready is not None and d_ready[t]:
            tgt_d, why_d = target_of(d_quad[t], int(d_below[t]), hold, p)
            if tgt_d < tgt - 1e-9:
                tgt, why = tgt_d, why_d + "（快线下车）"
        tgt_v[t] = tgt
        if abs(tgt - hold) > 1e-9 and t - last_act >= p["ACTION_GAP"]:
            pend, pend_note = tgt, why
            decide[t] = "buy" if tgt > hold else "sell"

    # ---- 第三层：波动系数（趋势档位 × 缩放 = 实际档位）
    trend = pd.Series(state, index=px.index)
    sc = vol_scale(px, **kw)
    eff = apply_vol(trend, sc, p["VOL_APPLY"]) if p["VOL_USE"] else trend
    arr_sc = sc.to_numpy(dtype=float)
    for i in range(1, n):                      # 纯缩放日（无趋势换档）补一句说明
        if abs(arr_sc[i] - arr_sc[i - 1]) > 1e-9 and not done[i]:
            done[i] = f"波动层缩放 → {arr_sc[i]:.0%}"

    out = pd.DataFrame(dict(state=eff, state_trend=trend, vol_scale=sc,
                            decide=decide, done=done, target=tgt_v), index=px.index)
    return out.join(I)


# ---------------------------------------------------------------- 3. 回测
def run_leg(state, px, fee=(0.0, 0.0)):
    """单腿净值（起点 = 1）。持仓按 `state.shift(1)` 赚当日收益，成交日扣成本。"""
    ret = px.pct_change().fillna(0.0)
    hold = state.shift(1).fillna(0.0).astype(float)
    r = hold * ret + (1.0 - hold) * CASH_DAILY
    d = state.diff().fillna(state).astype(float)
    r = r - (d > 0) * float(fee[0]) - (d < 0) * float(fee[1])
    return (1.0 + r).cumprod()


def actions(state):
    """按 state 的变化折出调仓动作（成交日 = 决策次日）。-> [{date, frm, to, kind, gap}]"""
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
    """打脸统计：减仓后 `window` 个交易日内又加回的次数，以及期间让掉的价格变动。"""
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
            miss.append(val[j] / val[i] - 1.0)      # 卖在地板、买回来的代价
    return dict(n=n, avg_miss=(float(np.mean(miss)) if miss else 0.0),
                total_miss=(float(np.sum(miss)) if miss else 0.0))


def perf(nav):
    """绩效指标（复用主策略 engine.metrics，无风险利率 = 货基 CASH_ANNUAL）。"""
    return engine.metrics(nav)


def exposure(state):
    """带内平均曝光（0–1）。"""
    return float(state.mean()) if len(state) else 0.0


def leg_slice(leg, **kw):
    """-> (px, sm)。样本自年线 + 斜率都成形的第一个交易日起算。"""
    px_full = load(leg)
    sm = state_machine(px_full, leg, **kw)
    start = px_full.index[min(WARMUP, max(len(px_full) - 2, 0))]
    return px_full.loc[start:], sm.loc[start:]


def common_frame():
    """七腿净值的共同窗口（各自年线 + 斜率成形之后再取交集）。"""
    cols = {}
    for k in LEGS:
        px = load(k)
        if len(px) > WARMUP:
            px = px.iloc[WARMUP:]
        cols[k] = px
    return pd.DataFrame(cols).dropna(how="any").sort_index()


def portfolio(frame, states=None, weight=None, fee=False, slip=None, detail=False):
    """组合层净值：Σ w·[state·ret + (1−state)·货基] − 调仓成本。

    `state` 是**带内占比**（0.2/0.5/1.0），不是 pp；`detail=True` 时返回 (nav, 平均带内曝光)。
    """
    weight = weight or W
    ret = frame.pct_change().fillna(0.0)
    cols = list(frame.columns)
    idx = frame.index
    if states is None:
        hold = {k: pd.Series(1.0, index=idx) for k in cols}
    else:
        hold = {}
        for k in cols:
            s = states[k].reindex(idx)
            hold[k] = s.ffill().bfill().fillna(0.0).astype(float).shift(1).fillna(0.0)
    prev = {k: float(hold[k].iloc[0]) for k in cols} if len(idx) else {k: 0.0 for k in cols}
    nav, out, inv = 1.0, [], []
    for i, _d in enumerate(idx):
        if i == 0:
            out.append(nav)
            inv.append(0.0)
            continue
        r, cost, e_sum = 0.0, 0.0, 0.0
        for k in cols:
            e = float(hold[k].iloc[i])
            e_sum += weight[k] * e
            r += weight[k] * (e * float(ret[k].iloc[i]) + (1.0 - e) * CASH_DAILY)
            if fee:
                de = e - prev[k]
                rate = fee_of(k, slip)[0] if de > 0 else fee_of(k, slip)[1]
                cost += weight[k] * abs(de) * rate
            prev[k] = e
        r -= cost
        nav *= (1.0 + r)
        out.append(nav)
        inv.append(e_sum)
    nav_s = pd.Series(out, index=idx, name="nav")
    return (nav_s, float(np.mean(inv))) if detail else nav_s


def yearly(nav):
    """按自然年切分的年度收益（首尾为不完整年度）。"""
    out, seg = {}, {}
    vals = nav.to_numpy(dtype=float)
    for i, d in enumerate(nav.index):
        y = str(d.year)
        st = 1.0 if i == 0 else vals[i - 1]
        if y not in seg:
            seg[y] = [st, vals[i]]
        seg[y][1] = vals[i]
    for y, (a, b) in seg.items():
        out[y] = b / a - 1.0
    return out


def seg_perf(nav, d0, d1):
    """区间绩效（区间首日重新归 1）；区间无数据返回 None。"""
    sub = nav.loc[pd.Timestamp(d0):pd.Timestamp(d1)]
    if len(sub) < 3:
        return None
    return perf(sub / sub.iloc[0])


def fmt_pct(x, nd=2):
    return "—" if x is None else f"{x * 100:+.{nd}f}%"


def fmt_x(x, nd=2):
    if x is None:
        return "—"
    if math.isinf(x):
        return "∞"
    return f"{x:.{nd}f}"


def _self_check():
    """快速自检：七腿可读、象限四格齐全、趋势档位只落在 {0,.2,.5,1}、实际档位 ∈[0,1]、无前视。"""
    for k in LEGS:
        px, sm = leg_slice(k)
        st, tr, sc = sm["state"], sm["state_trend"], sm["vol_scale"]
        assert len(st) == len(px) == len(sc)
        assert set(np.round(tr.unique(), 6)) <= {0.0, 0.2, 0.5, 1.0}, tr.unique()
        assert float(st.min()) >= -1e-9 and float(st.max()) <= 1.0 + 1e-9
        assert float(sc.min()) >= VOL_MIN_LEVEL - 1e-9 and float(sc.max()) <= 1.0 + 1e-9
        assert bool((st <= tr + 1e-9).all())          # 波动层只压不抬
        q = sm.loc[sm["quad"] != "", "quad"]
        cnt = q.value_counts()
        print(f"{k:6s} {rule.LEGS[k]['name']:8s} {px.index[0].date()} ~ {px.index[-1].date()} "
              f"n={len(px):5d} 闸门={'是' if gated(k) else '否'} "
              f"档位={tr.iloc[-1]:.0%}×{sc.iloc[-1]:.0%}={st.iloc[-1]:.0%} "
              f"均档位={exposure(tr):.0%} 均仓位={exposure(st):.0%} 象限日数="
              + " ".join(f"{name}{int(cnt.get(name, 0))}" for name in QUADS))
    print(f"年线 MA{MA_WIN} · 斜率 {SLOPE_N} 日 · 阶梯 {REDUCE_DAYS}/{CLEAR_DAYS} 日 · "
          f"间隔帽 {ACTION_GAP} 日 · 档位 {FULL:g}/{HALF:g}/{OBSERVE:g} · "
          f"作用域 {'全腿' if GATE_ALL else '/'.join(GATE_LEGS)}")
    if VOL_USE:
        if VOL_MODE == "adaptive":
            vol_txt = (f"自适应 q{VOL_REF_Q * 100:.0f} × gain {VOL_GAIN:g}"
                       f"（参考=自身波动历史分位，最少 {VOL_MIN_HIST} 日）")
        else:
            vol_txt = f"固定目标 {VOL_TARGET:.0%}"
        print(f"波动层 开 · {vol_txt} · 窗口 {VOL_WINDOW} 日 · 死区 {VOL_DEADBAND:g} · "
              f"限速 {VOL_MIN_GAP} 日 · 下限 {VOL_MIN_LEVEL:g} · 作用 {VOL_APPLY}")
    else:
        print("波动层 关（纯趋势版）")


if __name__ == "__main__":
    _self_check()
