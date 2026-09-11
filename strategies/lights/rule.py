# -*- coding: utf-8 -*-
r"""亮灯策略（Lights）—— 逐标的独立状态机的场内 ETF 择时策略。

设计原则: **只有一个策略, 所有差异收敛为参数。**

----------------------------------------------------------------------
模型（五层，各层都由参数驱动）
----------------------------------------------------------------------
L1 门槛层 gates      : 一组布尔硬条件（AND）。全过才允许建仓; 可配置是否「不过即清仓」。
L2 灯层 lights       : 每个维度取一盏灯（0~2 分），按权重加权求和得到 score。
                       维度固定为 7 个键: trend / momentum / capital / sustain /
                       heat / emotion / divergence —— 每个键可有多种「定义」可选。
L3 决策层 decision   : enter = 门槛全过 且 score >= enter_min 且 各 required 灯达标;
                       exit  = (门槛不过 且 exit_on_gate_fail) 或 score <= exit_max
                               或 命中 exit_events 或 exit_all_zero 的灯全为 0。
L4 仓位层 sizing     : weight_rules 按序匹配（支持按 score 区间 / 按某灯取值分档）。
L5 执行层 execution  : rebal 调仓节律 + trail_stop 回撤止损 + max_hold 持仓上限
                       + fee/slip_k_bp 成本。

----------------------------------------------------------------------
生效配置
----------------------------------------------------------------------
`ACTIVE = active_config()` 是**唯一的参数权威源**（label = "c7"）。它由参数搜索
（目标 = 策略年化 × 择时边际）选出、并经分段 walk-forward 交叉确认后采纳，
证据见 strategies/_tune_report.md 与 strategies/_walkforward_report.md。
改参数只改这里，然后跑 `strategies/lights/_sync_meta.py` 同步 pipeline/strategies.json 的镜像块。

----------------------------------------------------------------------
历史口径的三处修正（当前实现均已修正, 留档备查）
----------------------------------------------------------------------
  fix#1 周线 MACD 周序: 历史实现用字符串 "年-周" 作分组键, 字典序把 2016-1 排在 2016-10
        之前, 全部标的的周序都不是时间序, 约 1/4 交易日的周线多头判断是错的。
        修正 = WEEKLY_ORDER_DEFAULT（"chrono"）。
  fix#2 停牌日污染: 历史实现走「全池并集日期 + ffill」面板, 全部标的都混入了合计
        64 个被补齐的停牌日（volume=0 -> 量比被读成"缩量"）。
        修正 = 一律用「标的自身交易日」（不 ffill）。
  fix#3 指标 warm-up: 历史实现先把序列截到样本起点再算指标, 导致样本前 ~34 个交易日指标
        不完整。修正 = warm-up 完整保留。
"""
import os
import sys

import numpy as np
import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
_STRAT = os.path.dirname(_DIR)
for _p in (_STRAT, _DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from lights import data as _data    # noqa: E402
from lights import engine as _eng   # noqa: E402
from lights import factors as fx    # noqa: E402

POOL = _data.POOL
CATS = _data.CATS
NAMES = _data.NAMES
THEMES = _data.THEMES
CODES = _data.CODES
START = _data.START
SAMPLE_FROM = _data.SAMPLE_FROM

WEEKLY_ORDER_DEFAULT = fx.WK_CHRONO      # fix#1：默认为修正后的时间序

# 灯的维度键（顺序即报告展示顺序）
LIGHT_KEYS = ("trend", "momentum", "capital", "sustain", "heat", "emotion", "divergence")
LIGHT_NAMES = {
    "trend": "趋势灯", "momentum": "动量灯", "capital": "资金灯", "sustain": "持续力灯",
    "heat": "热度灯", "emotion": "情绪灯", "divergence": "分化度灯",
}


# ======================================================================
# 灯定义注册表：每个维度若干可选定义, 均为 (factors) -> Series(0~2) 或 (0~1)
# ======================================================================
def light_trend_ma_short_adx(f):
    """趋势灯 (短均线 + ADX): MA5>MA10>MA20 & MA20↑(1日) & 收>MA20 & ADX>25 -> 2; MA5>MA10 & 收>MA20 -> 1。"""
    cond2 = ((f["ma5"] > f["ma10"]) & (f["ma10"] > f["ma20"])
             & (f["ma20"] > f["ma20"].shift(1)) & (f["c"] > f["ma20"]) & (f["adx"] > 25))
    cond1 = (f["ma5"] > f["ma10"]) & (f["c"] > f["ma20"])
    out = np.zeros(len(f["c"]), dtype=float)
    out[cond2.fillna(False).to_numpy()] = 2
    out[(cond1 & ~cond2).fillna(False).to_numpy()] = 1
    out[f["adx"].isna().to_numpy() | f["ma20"].isna().to_numpy()] = 0
    return out


def light_trend_ma_long_mom(f):
    """趋势门 (长均线 + 绝对动量): MA20>MA60 & 收>MA60 & MA60↑(10日) & 绝对动量(60日)>0 -> 2; 两个宽松组合 -> 1。"""
    dualm = (f["ma20"] > f["ma60"])
    above60 = f["c"] > f["ma60"]
    ma60_rise = f["ma60"] > f["ma60"].shift(10)
    abs_mom = f["c"] > f["c"].shift(60)
    strong = dualm & above60 & ma60_rise & abs_mom
    mild = ((above60 & abs_mom) | (dualm & ma60_rise)) & ~strong
    out = np.where(strong.fillna(False).to_numpy(), 2,
                   np.where(mild.fillna(False).to_numpy(), 1, 0)).astype(float)
    out[f["c"].isna().to_numpy() | f["ma60"].isna().to_numpy()] = 0
    return out


def light_momentum_ret5(f):
    """近端动量: ret5>=2% 且 ret3>0 -> 2; ret5>0 -> 1。"""
    m5, m3 = f["ret5"], f["ret3"]
    out = np.zeros(len(m5), dtype=float)
    out[(m5 > 0).fillna(False).to_numpy()] = 1
    out[((m5 >= 0.02) & (m3 > 0)).fillna(False).to_numpy()] = 2
    return out


def light_momentum_ret3_band(f):
    """价格动量灯: 0 < ret3 <= 12% -> 1。"""
    return ((f["ret3"] > 0) & (f["ret3"] <= 0.12)).fillna(False).to_numpy().astype(float)


def light_capital_vr5_ge07(f):
    """主力灯 (量价代理): 放量(量比>=1.5)&收阳 -> 2; (收阳&0.7<=量比<1.5)|(量比>=1.5&收阴) -> 1。"""
    vr, yang = f["vr5"], f["yang"]
    out = np.zeros(len(f["c"]), dtype=float)
    out[(yang & (vr >= 1.5)).fillna(False).to_numpy()] = 2
    out[((yang & (vr < 1.5) & (vr >= 0.7)) | ((vr >= 1.5) & ~yang)).fillna(False).to_numpy()] = 1
    return out


def light_capital_vr5_any(f):
    """主力灯 (加分项): 收阳&量比>=1.5 -> 2; (收阳&量比<1.5)|(量比>=1.5&收阴) -> 1。"""
    vr, yang = f["vr5"], f["yang"]
    out = np.zeros(len(f["c"]), dtype=float)
    out[(yang & (vr >= 1.5)).fillna(False).to_numpy()] = 2
    out[((yang & (vr < 1.5)) | ((~yang) & (vr >= 1.5))).fillna(False).to_numpy()] = 1
    return out


def light_capital_cap3(f):
    """资金灯: cap3 >= 1（资金净流入）-> 1。

    上界不再由本灯表达 —— 「极端脉冲」的过滤交给门槛 `cap_abs_max`（双侧 |cap3|<=6）。
    原先灯写 [1,6]、门槛写 <=5, 两处都在管上界且互相重叠, 导致灯的 `<=6` 永远生效不了
    （cap3 落在 (5,6] 时灯亮但门槛已挡）。现在职责分离: 门槛管「极端值」, 灯管「流入方向」。
    """
    return (f["cap3"] >= 1).fillna(False).to_numpy().astype(float)


def light_capital_vr_cmf(f):
    """主力灯（文档灯2 的完整还原 = AND）: 放量收阳 **且** 资金净流入 -> 2;
    只满足其中一边 -> 1; 两边都不满足 -> 0。

    文档灯2 的亮灯条件是三条同时成立（量能放大 且 量价配合 且 资金净流入）。
    此前的 vr5_* 只还原量价、cap3 只还原资金, 且互为候选（一个维度只能选一个）,
    所以无论选哪个都是残缺还原。本定义把两者合回一个 AND。
    """
    vr, yang, cap = f["vr5"], f["yang"], f["cap3"]
    strong = yang & (vr >= 1.5) & (cap >= 1)
    mild = (yang & (vr >= 1.5)) | (yang & (cap >= 1))
    out = np.zeros(len(f["c"]), dtype=float)
    out[mild.fillna(False).to_numpy()] = 1
    out[strong.fillna(False).to_numpy()] = 2
    return out


def light_sustain_macd_weekly(f):
    """持续力灯 (含周线): DIF>DEA & DIF>0 & ret3>3% & 周线MACD多 -> 2; DIF>DEA & DIF>0 -> 1。"""
    dgt = (f["dif"] > f["dea"]) & (f["dif"] > 0)
    cond2 = (dgt & (f["ret3"] > 0.03) & f["wk_bull"]).fillna(False).to_numpy()
    out = np.zeros(len(f["c"]), dtype=float)
    out[dgt.fillna(False).to_numpy()] = 1
    out[cond2] = 2
    return out


def light_sustain_macd(f):
    """持续力灯（去周线, fix 候选）: DIF>DEA & DIF>0 & ret3>3% -> 2; DIF>DEA & DIF>0 -> 1。"""
    dgt = (f["dif"] > f["dea"]) & (f["dif"] > 0)
    out = np.zeros(len(f["c"]), dtype=float)
    out[dgt.fillna(False).to_numpy()] = 1
    out[(dgt & (f["ret3"] > 0.03)).fillna(False).to_numpy()] = 2
    return out


def light_sustain_macd_binary(f):
    """持续力灯 (0/1): DIF>DEA & DIF>0 & ret3>3% & 周线多 -> 1。"""
    return ((f["dif"] > f["dea"]) & (f["dif"] > 0) & (f["ret3"] > 0.03)
            & f["wk_bull"]).fillna(False).to_numpy().astype(float)


def light_heat_ret5_vr5(f):
    """热度灯 (涨幅带 + 量比): 5%<=ret5<=20% 且量比>=1.3 -> 2; (同区间且量比<1.3)|(0<ret5<5%) -> 1。"""
    w5, vr = f["ret5"], f["vr5"]
    out = np.zeros(len(f["c"]), dtype=float)
    out[((w5 >= 0.05) & (w5 <= 0.20) & (vr >= 1.3)).fillna(False).to_numpy()] = 2
    out[(((w5 >= 0.05) & (w5 <= 0.20) & (vr < 1.3))
         | ((w5 > 0) & (w5 < 0.05))).fillna(False).to_numpy()] = 1
    return out


def light_heat_vr60_band(f):
    """换手合理带: 0.7<=vr60<=1.4 且收阳 -> 2; 0.5<vr60<1.6 且非放量收阴 -> 1; 爆量/萎缩/放量收阴 -> 0。"""
    vr60, yang = f["vr60"], f["yang"]
    v = vr60.notna()
    overflow = v & (vr60 >= 1.6)
    shrunk = v & (vr60 <= 0.5)
    dump = v & (~yang) & (vr60 >= 1.2)
    h2 = v & (vr60 >= 0.7) & (vr60 <= 1.4) & yang
    h1 = v & (vr60 > 0.5) & (vr60 < 1.6) & ~dump & ~h2
    out = np.zeros(len(f["c"]), dtype=float)
    out[h1.fillna(False).to_numpy()] = 1
    out[h2.fillna(False).to_numpy()] = 2
    out[(overflow | shrunk | dump).fillna(False).to_numpy()] = 0
    return out


def light_heat_ret5_band(f):
    """纯动量热度（对照用）: 5%<=ret5<=20% -> 1。"""
    w5 = f["ret5"]
    return ((w5 >= 0.05) & (w5 <= 0.20)).fillna(False).to_numpy().astype(float)


def light_emotion_turn_pct(f):
    """情绪灯: 换手分位落在该类别安全带内 -> 1（带由 cfg.band_wide/band_sector 决定）。"""
    lo, hi = f["band"]
    t = f["turn_pct"]
    return ((t >= lo) & (t <= hi)).fillna(False).to_numpy().astype(float)


def light_divergence_div(f):
    """持续性灯: 分化度 <= 阈值 -> 1。"""
    return (f["div"] <= f["div_max"]).fillna(False).to_numpy().astype(float)


LIGHT_REGISTRY = {
    "trend": {
        "ma_short_adx": (light_trend_ma_short_adx, "MA5>MA10>MA20 & MA20↑1 & 收>MA20 & ADX>25 (2分)"),
        "ma_long_mom": (light_trend_ma_long_mom, "MA20>MA60 & 收>MA60 & MA60↑10 & 60日动量>0 (2分)"),
    },
    "momentum": {
        "ret5": (light_momentum_ret5, "ret5>0 (1分) / ret5>=2%且ret3>0 (2分)"),
        "ret3_band": (light_momentum_ret3_band, "0<ret3<=12% (1分)"),
    },
    "capital": {
        "vr5_ge07": (light_capital_vr5_ge07, "放量收阳=2 / 单边=1（缩量收阳不计量能）"),
        "vr5_any": (light_capital_vr5_any, "放量收阳=2 / 单边=1（不设量能下限）"),
        "cap3": (light_capital_cap3, "cap3>=1 资金净流入 (1分; 上界由门槛 cap_abs_max 管)"),
        "vr_cmf": (light_capital_vr_cmf, "放量收阳 & 资金净流入 = 2分 / 单边 = 1分（文档灯2 的 AND 还原）"),
    },
    "sustain": {
        "macd_weekly": (light_sustain_macd_weekly, "日线MACD多头 (+ret3>3% & 周线多 => 2分)"),
        "macd": (light_sustain_macd, "日线MACD多头 (+ret3>3% => 2分, 去周线)"),
        "macd_binary": (light_sustain_macd_binary, "日线MACD多头 & ret3>3% & 周线多 (1分)"),
    },
    "heat": {
        "ret5_vr5": (light_heat_ret5_vr5, "ret5∈[5%,20%] & 量比>=1.3 (2分)"),
        "vr60_band": (light_heat_vr60_band, "vr60∈[0.7,1.4]&收阳 (2分)"),
        "ret5_band": (light_heat_ret5_band, "ret5∈[5%,20%] (1分)"),
    },
    "emotion": {
        "turn_pct": (light_emotion_turn_pct, "换手分位落在安全带 (1分)"),
    },
    "divergence": {
        "div": (light_divergence_div, "分化度 <= 阈值 (1分)"),
    },
}


# ======================================================================
# 门槛注册表：(factors, cfg) -> bool Series；None 表示该门槛未启用
# ======================================================================
def gate_liq(f, cfg):
    return (f["amt20"] >= cfg.liq_amt_min).fillna(False)


def gate_age(f, cfg):
    return (f["bars"] >= cfg.list_min_bars)


def gate_ma_trend(f, cfg):
    return (f["c"] > f["ma20"]).fillna(False)


def gate_ret3_max(f, cfg):
    return (f["ret3"] <= cfg.ret3_max).fillna(False)


def gate_ret5_max(f, cfg):
    return (f["ret5"] <= cfg.ret5_max).fillna(False)


def gate_cap_abs_max(f, cfg):
    return (f["cap3"].abs() <= cfg.cap_abs_max).fillna(False)


def gate_entry_ma10(f, cfg):
    return (f["c"] > f["ma10"]).fillna(False)


def gate_indicators_ready(f, cfg):
    """指标 warm-up 完成（指标 warm-up 门槛）。必填指标由 cfg.ready_fields 指定。"""
    ok = pd.Series(True, index=f["c"].index)
    for k in cfg.ready_fields:
        ok &= f[k].notna()
    return ok


GATE_REGISTRY = {
    "liq": gate_liq, "age": gate_age, "ma_trend": gate_ma_trend,
    "ret3_max": gate_ret3_max, "ret5_max": gate_ret5_max,
    "cap_abs_max": gate_cap_abs_max, "entry_ma10": gate_entry_ma10,
    "indicators_ready": gate_indicators_ready,
}


# ======================================================================
# 事件型离场注册表
# ======================================================================
def event_death_cross(f, cfg):
    """MA5 下穿 MA10（事件）。"""
    d = f["ma5"] - f["ma10"]
    return ((d < 0) & (d.shift(1) >= 0)).fillna(False)


def event_mom_neg_ma10(f, cfg):
    """近 5 日动量转负 且 收盘跌破 MA10（过滤单日噪音）。"""
    return ((f["ret5"] < 0) & (f["c"] < f["ma10"])).fillna(False)


def event_mom_neg(f, cfg):
    """近 5 日动量转负（不做确认）。"""
    return (f["ret5"] < 0).fillna(False)


EVENT_REGISTRY = {
    "death_cross": event_death_cross,
    "mom_neg_ma10": event_mom_neg_ma10,
    "mom_neg": event_mom_neg,
}


# ======================================================================
# 配置
# ======================================================================
class Config(dict):
    """参数容器（dict 子类, 便于 .copy() 后逐项调参）。"""

    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as e:
            raise AttributeError(k) from e

    def with_(self, **kw):
        c = Config(self)
        c.update(kw)
        return c


def base_config(**kw):
    c = Config(
        # ---- 灯层: {维度: (定义名, 权重或 None=不启用)} ----
        # ⚠ 灯的分数**只作用于离场**（`exit_max`），不作用于买点 —— 实测把分数
        #    均值从 2.08 改到 4.08，enter 序列逐位不变（见 strategies/_ablate_report.md）。
        #    买点实际由 `gate_pass & enter_required` 决定。
        lights={
            "trend": ("ma_short_adx", 1),
            "momentum": ("ret5", 1),
            # capital 2026-09-11 停用: 消融实测目标增量 -0.004（关掉后目标 0.976 >
            #   基线 0.972、超额 +2.6% -> +2.7%），零贡献且方向略负。
            #   注意 cap3 **因子**仍被 cap_abs_max 门槛与 indicators_ready 使用, 不要删因子。
            "capital": None,
            "sustain": ("macd", 2),
            "heat": ("ret5_vr5", 1),
            "emotion": None,
            "divergence": None,
        },
        # ---- 门槛层（全过才允许建仓；exit_on_gate_fail=True 时任一不过即清仓）----
        gates=("liq", "age", "ma_trend", "ret3_max", "cap_abs_max", "indicators_ready"),
        exit_on_gate_fail=True,           # 目标制: 任一门槛不过即清仓
        exit_gates=(),                    # 额外「也门控离场」的门槛（默认无）
        # 门槛参数
        liq_amt_min=5e7,
        list_min_bars=250,
        ret3_max=0.15,
        ret5_max=0.25,
        cap_abs_max=6.0,                  # 双侧 |cap3|<=6: 唯一的「资金极端脉冲」过滤点
        cap_proxy_scale=0.146,
        cap_win=3,                        # 资金强度代理的 CMF 窗口: 3=三日累计, 1=单日
        ready_fields=("adx", "ma20", "amt20", "cap3"),
        # 因子参数
        macd=(12, 26, 9),
        turn_win=60,
        div_win=5,
        div_max=0.7,
        band_wide=(0.30, 0.75),
        band_sector=(0.20, 0.80),
        vol_fast=5,
        vol_hist=60,
        weekly_order=WEEKLY_ORDER_DEFAULT,
        # ---- 决策层 ----
        # enter_min 刻意取**非约束值**: 实测 enter_min=1/2/3 的入场序列逐位相同
        #   （入场率都是 15.1%），要 >=4 才开始起作用（见 strategies/_probe.py）。
        #   生效的买点条件是 `gate_pass & enter_required` 本身。
        #   写成 0.0 是为了让「它不设约束」显式可见, 而不是留一个看似有意义的 3.0,
        #   让人调 3->1 得到「毫无变化」后误判这个旋钮坏了。
        enter_min=0.0,
        enter_required={"trend": 1, "momentum": 1},   # {灯维度: 最低分} 建仓时额外要求
        exit_max=2.0,                     # score 清仓门槛（None = 不启用）
        exit_events=(),                   # 事件型离场
        exit_all_zero=(),                 # 这些灯全为 0 -> 清仓
        # ---- 仓位层: 按序匹配, 首个命中生效 ----
        weight_rules=({"any": True, "w": 1.0},),
        # ---- 执行层 ----
        rebal=1,
        trail_stop=None,
        max_hold=None,
        take_profit=None,                 # 限价止盈（如 0.02 = 涨 2% 即挂单卖出）
        fee=0.0003,
        # 滑点（单边, bp）: 额外成本 = slip_k_bp / sqrt(20日均额 / 1亿)。
        # 0 = 只收佣金（合并前的口径, 也是所有已公布回测数字的口径）。
        # 用来回答「放宽流动性门槛到底能不能赚钱」—— 不建模冲击成本时它是不可信的。
        slip_k_bp=0.0,
        slip_cap_bp=200.0,                # 滑点上限（防止极端低流动性时爆炸）
        sample_from=SAMPLE_FROM,
        sample_to=None,                   # 样本窗口终点（None=到最新）; 用于样本外确认
        min_bars=120,
        label="base",
    )
    c.update(kw)
    return c


# ======================================================================
# 命名预设与生效配置
# ======================================================================
# 命名预设: 当前只有一个（生效配置本体）; 保留 dict 结构, 便于日后横向比较不同配置。
PRESETS = {}


def active_config(**overrides):
    """★ 生效配置（label = "c7"）。

    `base_config()` 的默认值**就是**本配置 —— 由参数搜索（目标 = 策略年化 × 择时边际,
    见 strategies/_tune_report.md）选出、并经分段 walk-forward（_walkforward_report.md）
    交叉确认后采纳。所以本函数只负责打标签; 要试别的参数直接
    `active_config(enter_min=4)` 或 `backtest.py --set enter_min=4`。

    采纳过程中相对初始取值调整了四处:
      1. `enter_required` 由 {trend>=1} 加严为 {trend>=1, momentum>=1}
         —— 只砍 0.5pp 暴露换 +2.5bp 边际, 年化不变
      2. `sustain` 权重 1 -> 2 —— 日线 MACD 是五盏灯里唯一「关掉就有明显损失」的软灯
         （消融实测目标增量 +0.346、年化 +1.20pp, 见 strategies/_ablate_report.md）
      3. `heat` 由纯涨幅带 `ret5_band` 改为量价灯 `ret5_vr5` —— 实测更优
      4. `ret3_max` 0.12 -> 0.15、`cap_abs_max` 6 -> 5 —— 放宽短端上限、收紧资金极端脉冲

    ⚠ 旧注释曾写「日线 MACD 是单条件信息量最高的一档（+10.30bp）」—— 那是**单条件
      进出场测试**的口径, 与这里「在完整配置里的增量」不是一回事, 并列会严重误导。
      在完整配置下 MACD 的净边际增量只有约 +2.6bp/日。

    --- 2026-09-11 消融驱动的三处改动（依据 strategies/_ablate_report.md）---
      A. **停用 `capital` 灯**: 目标增量 -0.004（关掉后 0.976 > 基线 0.972）, 零贡献。
      B. **`enter_min` 3.0 -> 0.0（显式非约束）**: 实测 1/2/3 入场序列逐位相同,
         要 >=4 才起作用 —— 生效买点是 `gate_pass & enter_required`, enter_min 是死参数。
      C. **灯的定位写清**: 分数只进离场判据（`exit_max`）, 对买点零影响。
         五灯分工: trend/momentum = 入场硬开关（在 enter_required 里）;
         sustain(MACD) = 主力离场滤网（真有效）; heat = 离场小贡献; capital = 已停用。

    实测(池子 = 23 只 A 股行业 ETF): 年化 +6.2% / 边际 +15.8bp / 目标 0.972;
          样本外(2023-01 起) 见 strategies/_walkforward_report.md。
    ⚠ 样本外也被用于挑选候选, 存在选择偏差; 缓解方式是取"成片的稳健区域"而非单点 argmax。
      另外把流动性门槛降到 0 目标更高但不可交易（滑点模型下转负）, 已排除。
    """
    return base_config(label="c7", **overrides)


ACTIVE = active_config()
PRESETS["c7"] = ACTIVE          # 亦可用 --preset c7 复现


# ======================================================================
# 因子计算（逐标的自身交易日, 不 ffill —— fix#2）
# ======================================================================
_FIELDS = ("open", "high", "low", "close", "volume")
_panels_raw = None


def load_panels_raw():
    """不带 ffill 的全池面板（只读缓存）。统一策略一律用它 —— fix#2。"""
    global _panels_raw
    if _panels_raw is None:
        _panels_raw = _data.load_panels(ffill=False)
    return _panels_raw


def trading_frame(panels, code, end=None):
    """从**不带 ffill** 的面板取单标的的自身交易日序列（丢掉 NaN 行）。

    等价于逐标的 `xa.get_daily + dropna`, 但走本地缓存。停牌日不会被补齐,
    因此不会出现 volume=0 的假交易日（fix#2）。
    """
    df = pd.DataFrame({f: panels[f][code] for f in _FIELDS})
    if end is not None:
        df = df.loc[:end]
    df = df[df["close"].notna()]
    return df


def cost_array(f, cfg):
    """逐日单边成本数组（元→比例）: 佣金 + 与流动性反向的滑点。

    滑点模型: extra_bp = slip_k_bp / sqrt(20日均额 / 1亿)，上限 slip_cap_bp。
    标定直觉（slip_k_bp=6）: 10亿 -> 1.9bp；1亿 -> 6bp；5000万 -> 8.5bp；
    2000万 -> 13.4bp；500万 -> 26.8bp 的**额外**成本，叠加 3bp 佣金。
    slip_k_bp=0 时直接返回标量佣金，与合并前口径完全一致（不影响任何既有数字）。
    """
    if not cfg.slip_k_bp:
        return cfg.fee
    amt = f["amt20"].to_numpy(float) / 1e8                 # 亿元
    ok = np.isfinite(amt) & (amt > 0)
    extra = np.full(len(amt), float(cfg.slip_cap_bp))
    extra[ok] = cfg.slip_k_bp / np.sqrt(amt[ok])
    return cfg.fee + np.clip(extra, 0.0, cfg.slip_cap_bp) / 1e4


def band_of(code_or_cat, cfg=None):
    """换手分位安全带: 宽基/另类 30%~75% / 行业主题 20%~80%（情绪灯口径）。

    入参可传标的代码（自动查 CATS）或类别名。
    """
    cfg = cfg or ACTIVE
    cat = CATS.get(code_or_cat, code_or_cat)
    return cfg.band_wide if cat == "宽基/另类" else cfg.band_sector


def compute_factors(df, cfg, code):
    """单标的 OHLCV -> 因子字典（全部走 lights.factors）。"""
    c = df["close"].astype(float)
    o = df["open"].astype(float)
    h = df["high"].astype(float)
    lo = df["low"].astype(float)
    v = df["volume"].astype(float)
    fast, slow, sig = cfg.macd
    f = dict(c=c, o=o, h=h, l=lo, v=v,
             amount=fx.amount(v, c), yang=(c > o),
             ma5=fx.sma(c, 5), ma10=fx.sma(c, 10),
             ma20=fx.sma(c, 20), ma60=fx.sma(c, 60),
             adx=fx.adx(h, lo, c, 14),
             ret1=fx.ret(c, 1), ret3=fx.ret(c, 3), ret5=fx.ret(c, 5),
             ret20=fx.ret(c, 20),
             vr5=fx.vr(v, cfg.vol_fast), vr60=fx.vr(v, cfg.vol_hist),
             amt20=None, turn_pct=None, cap3=None, div=None,
             bars=pd.Series(np.arange(1, len(df) + 1), index=df.index))
    f["dif"], f["dea"] = fx.macd(c, fast, slow, sig)
    # wk_bull: 周线 MACD 多头。**当前生效配置未使用**（ACTIVE 的 sustain 用去周线的
    # `macd`, 只有备用的 `macd_weekly` / `macd_binary` 定义需要它）。仍然照算的原因:
    #   因子结果按「因子参数组合」缓存并跨灯定义复用（见 _tune.py::fkey）, 若按配置
    #   按需计算会让缓存内容不一致; 而周线重采样在缓存下只跑一次, 省不下来多少。
    #   fix#1 的周序修正（weekly_order="chrono"）只服务于这两个备用定义。
    f["wk_bull"] = fx.weekly_macd_bull(c, fast, slow, sig, order=cfg.weekly_order)
    f["amt20"] = fx.sma(f["amount"], 20)
    f["turn_pct"] = fx.turnover_pct(f["amount"], cfg.turn_win)
    # 键名 cap3 是历史遗留（窗口由 cfg.cap_win 决定, 当前默认 3）。改名会牵动
    # 两个门槛、两个灯定义、日报取值处, 收益不大, 故保留名字并在此注明。
    f["cap3"] = fx.cmf_strength(h, lo, c, v, cfg.cap_win, cfg.cap_proxy_scale)
    f["div"] = fx.divergence(c, cfg.div_win)
    f["div_max"] = cfg.div_max
    f["band"] = band_of(code, cfg)
    return f


# ======================================================================
# 信号构建（模型的 L1~L4 层）
# ======================================================================
def build_signals(f, cfg):
    """因子字典 -> {gates, light_vals, score, enter, exit, weight}。"""
    n = len(f["c"])

    # ---- L1 门槛 ----
    gate_masks = []
    for name in cfg.gates:
        fn = GATE_REGISTRY.get(name)
        if fn is None:
            raise KeyError(f"未注册的门槛: {name}")
        gate_masks.append(pd.Series(np.asarray(fn(f, cfg), dtype=bool), index=f["c"].index))
    gate_pass = gate_masks[0].copy() if gate_masks else pd.Series(True, index=f["c"].index)
    for g in gate_masks[1:]:
        gate_pass &= g

    # ---- L2 灯层: 加权求和 ----
    light_vals = {}
    score = np.zeros(n, dtype=float)
    for key in LIGHT_KEYS:
        spec = cfg.lights.get(key)
        if not spec:
            light_vals[key] = np.zeros(n, dtype=float)
            continue
        defname, weight = spec
        entry = LIGHT_REGISTRY[key].get(defname)
        if entry is None:
            raise KeyError(f"未注册的灯定义: {key}.{defname}")
        fn = entry[0]                       # 注册表存 (函数, 说明)
        vals = np.asarray(fn(f), dtype=float)
        light_vals[key] = vals
        score += float(weight) * vals

    # ---- L3 决策层 ----
    enter = gate_pass.to_numpy() & (score >= cfg.enter_min)
    for key, minv in (cfg.enter_required or {}).items():
        enter &= light_vals[key] >= minv

    exit_ = np.zeros(n, dtype=bool)
    if cfg.exit_on_gate_fail:
        exit_ |= ~gate_pass.to_numpy()
    if cfg.exit_max is not None:
        exit_ |= score <= cfg.exit_max
    if cfg.exit_all_zero:
        # 语义: 这些灯**全部**为 0 才清仓（趋势灯与资金灯同时为 0）；不是任一个为 0。
        all_zero = np.ones(n, dtype=bool)
        for key in cfg.exit_all_zero:
            all_zero &= light_vals[key] <= 0
        exit_ |= all_zero
    for name in (cfg.exit_events or ()):
        fn = EVENT_REGISTRY.get(name)
        if fn is None:
            raise KeyError(f"未注册的离场事件: {name}")
        exit_ |= np.asarray(fn(f, cfg), dtype=bool)
    # exit_gates: 这些门槛同时门控离场（门槛不过时不产生离场动作）
    for name in (cfg.exit_gates or ()):
        fn = GATE_REGISTRY.get(name)
        if fn is None:
            raise KeyError(f"未注册的门槛: {name}")
        exit_ &= pd.Series(np.asarray(fn(f, cfg), dtype=bool),
                           index=f["c"].index).to_numpy()

    # ---- L4 仓位层 ----
    weight = np.zeros(n, dtype=float)
    for rule in cfg.weight_rules:
        hit = np.ones(n, dtype=bool)
        if "any" not in rule:
            if "score" in rule:
                lo_, hi_ = rule["score"]
                if lo_ is not None:
                    hit &= score >= lo_
                if hi_ is not None:
                    hit &= score <= hi_
            for key, rng in rule.items():
                if key in ("score", "w", "any"):
                    continue
                lo_, hi_ = rng
                if lo_ is not None:
                    hit &= light_vals[key] >= lo_
                if hi_ is not None:
                    hit &= light_vals[key] <= hi_
        take = hit & (weight == 0)
        weight[take] = float(rule["w"])
    weight = np.where(enter, weight, 0.0)

    return dict(gate_pass=gate_pass.to_numpy(), light_vals=light_vals, score=score,
                enter=enter, exit=exit_, weight=weight, cost=cost_array(f, cfg),
                high=f["h"].to_numpy(float))


def run_engine(open_px, close_px, sig, begin=0, cfg=None):
    """转发 lights.engine.run_engine（enter/exit/weight/风控/rebal 全部来自 cfg）。"""
    cfg = cfg or ACTIVE
    return _eng.run_engine(
        open_px, close_px, enter=sig["enter"], exit_=sig["exit"], begin=begin,
        weight=sig["weight"], rebal=cfg.rebal, trail_stop=cfg.trail_stop,
        max_hold=cfg.max_hold, block_same_day_reentry=True,
        fee=sig.get("cost", cfg.fee),          # 逐日成本（含滑点）; slip_k_bp=0 时即标量佣金
        high_px=sig.get("high"), take_profit=cfg.take_profit)
