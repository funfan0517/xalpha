# -*- coding: utf-8 -*-
"""波动资产 · V2 变种：资金渗透率（份额拥挤度）闸门 —— 规则与状态机（唯一权威源）。

## 为什么要看「份额渗透率」
`head_fund_ratio_rules.md` §1 的核心恒等式：
`占比 ≈ Σ份额 ÷ (流通市值/价格系数)` —— ETF 价格与指数价格同向，价格项在比值里基本抵消，
所以那条线**本质是「份额渗透率」而不是资金流**。它自身单调慢涨（A500 两年从 0.18% 升到 0.22%），
水平没有择时意义，**有信息的是它的斜率**：斜率 = 头部 ETF 的**净申购率**（真金白银的进出）。

本变种就是把斜率抽出来做闸门：
`g(t) = Σ w_i · (s_i(t)/s_i(t−63) − 1)`，w 按各头部基金的当前份额归一化 —— 即
「头部 ETF 合计份额过去一个季度的增长率」，等价于渗透率的季度变化率（价格项抵消后）。

## 规则（三档 + 死区，只在**带内做减法**）
| 拥挤度 c(t) = g 在近 750 交易日的分位 | 目标仓位 |
|---|---|
| `c ≥ 0.90`（资金涌入速度处于 3 年最高 10%） | **半带**（0.5 × 目标带） |
| `c ≤ 0.30`（净申购率处于 3 年最低 30%，筹码出清） | **满带**（1.0 × 目标带） |
| 中间 | **维持**原档位（死区，不因噪声来回切换） |

- 起点 = **满带**（与主策略「带内满仓起步」一致，因为 v2 只做减法，不做建仓路径）。
- 拥挤度用**分位**而不是 z-score：份额序列是季报阶跃 + 线性内插，分布厚尾，分位对极端值稳健，
  且天然可跨腿比较（黄金的年均份额增长 20% 与科创50 的 50% 不在同一量纲上）。
- 拥挤度需要至少 `PCT_MIN=250` 个观测；不足 → `c=None` → **该腿不执行**（规则 §1 null 条款），
  报告里逐条标注原因，**不用估算值补位**。

## 定位（与主策略、V1 的边界）
- 主策略（`../rule.py`）回答「**组合层仓位带怎么摆**」（阶梯 / 年线闸门 / 再平衡）；
- V1（`../v1_ma20/`）回答「**每条腿什么时候下车**」（价格 vs MA20）；
- V2（本变种）回答「**这条腿的资金是不是挤在门口**」—— 只看份额，不看价格，
  因此与 V1 正交、与主策略可叠加：主策略给「该持多少」，v2 给「是否临时打对折」。

## 已知口径偏差（写进产物说明，不隐藏）
- **M-01 份额起点**：锚点之前不产出（规则 R-05 原文是常数钳制，但钳制会伪造「0 增长」，
  这里按 R-09「全头部可得才产出」处理），代价是 A500 只有 2025-04 起的信号。
- **M-02 加权口径**：头部名单会变（新 ETF 上市、规模换位），用「份额加权平均增速」而非
  「Σ份额的增长率」，避免名单切换时出现水平跳变；名单稳定时两者等价（`backtest.py` 的 V-04 自检）。
- **M-03 无实时锚**：默认 `flowdata.LIVE_ANCHOR=False`，渗透率止于最近季报（2026-09-30），
  季内真实份额变化要等下一期季报才进信号。开启后与外部快照（含实时锚）会有 10~30% 的差异。
- **M-04 分母缺失**：美股 / 港股 / 黄金三条腿没有「标的指数流通市值」口径（NDX/SPX/HSTECH
  不在东财指数接口里，黄金也没有），只算分子（合计份额）。由核心恒等式，斜率不受影响。
"""
import os
import sys

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_VL = os.path.dirname(_HERE)
if _VL not in sys.path:
    sys.path.insert(0, _VL)

import rule as vrule        # noqa: E402
import flowdata as fdata    # noqa: E402

# ---------------------------------------------------------------- 1. 参数（改阈值只改这里）
CFG = {
    "head_n": fdata.HEAD_N,       # R-01 头部只数（per-leg 可被 flowdata.PROFILES 覆盖）
    "flow_win": 63,               # 净申购率窗口（≈ 一个季度交易日）
    "pct_win": 750,               # 拥挤度分位的回看窗口（≈ 3 年）
    "pct_min": 250,               # 分位最少观测数（不足 -> null，不执行）
    "crowd_hi": 0.90,             # ≥ 此分位 -> 半带（拥挤）
    "crowd_lo": 0.30,             # ≤ 此分位 -> 满带（冷清）
    "pos_full": 1.0,              # 满带系数
    "pos_crowd": 0.5,             # 拥挤时的仓位系数
    "start_pos": 1.0,             # 起点档位（带内满仓起步）
}

LABEL = "V2 资金渗透率闸门"
LEGS = list(vrule.LEG_ORDER)
CASH_ANNUAL = vrule.CASH_ANNUAL
CASH_DAILY = (1.0 + CASH_ANNUAL) ** (1.0 / 252) - 1.0

# 场外申赎费（买/卖单边），按该腿**实际执行代码**的份额类别取：
# A 类（270042 / 017641 / 000216）申购 0.10% + 赎回 0.50%（持有 >7 天）；
# C 类（023299 / 011609 / 012805）申赎免费，但收约 0.2%/年销售服务费 —— 后者未计入，偏乐观。
FEE = {
    "a500": (0.0, 0.0),
    "ndx": (0.001, 0.005),
    "spx": (0.001, 0.005),
    "kc50": (0.0, 0.0),
    "hkt": (0.0, 0.0),
    "gold": (0.001, 0.005),
}


def unit_pp(leg):
    """该腿 1 份 = 多少 pp（沿用主策略 v1.3 的 unit_pp / 目标带）。"""
    return float(vrule.LEGS[leg]["band"])


def band_pp(leg):
    return float(vrule.LEGS[leg]["band"])


# ---------------------------------------------------------------- 2. 指标
def flow_rate(df, win=None):
    """头部 ETF 份额的加权净申购率 g(t)（口径 M-02）。

    g(t) = Σ_i w_i(t) · (s_i(t)/s_i(t−win) − 1)，w_i(t) = s_i(t) / Σ_j s_j(t)。
    只用「t 与 t−win 都有锚点」的基金；权重按当前份额归一化，因此头部名单变动不会造成水平跳变。
    """
    win = int(CFG["flow_win"] if win is None else win)
    if df is None or df.empty:
        return pd.Series(dtype="float64")
    growth = df / df.shift(win) - 1.0
    weight = df.div(df.sum(axis=1), axis=0)
    return (growth * weight).sum(axis=1, min_count=1)


def crowd_pct(g, win=None, min_obs=None):
    """拥挤度 c(t) = g(t) 在近 win 个观测中的分位（0-1）。观测不足 -> NaN（不执行）。"""
    win = int(CFG["pct_win"] if win is None else win)
    min_obs = int(CFG["pct_min"] if min_obs is None else min_obs)
    if g is None or g.dropna().empty:
        return pd.Series(dtype="float64")
    return g.rolling(win, min_periods=min_obs).apply(lambda a: (a <= a[-1]).mean(), raw=True)


def main_flow(sigma):
    """合计份额（亿份）与其季度增长率 —— 头部名单稳定时用于 V-04 交叉校验。"""
    total = sigma.sum(axis=1, min_count=len(sigma.columns))
    return total, total / total.shift(int(CFG["flow_win"])) - 1.0


# ---------------------------------------------------------------- 3. 状态机
class CrowdGate:
    """单腿「拥挤度三档」状态机：只记录档位，不记账（记账在 backtest / scan）。"""

    def __init__(self, leg, pos=None):
        self.leg = leg
        self.pos = float(CFG["start_pos"] if pos is None else pos)
        self.notes = []

    def step(self, crowd, date):
        """-> (新档位, 动作, 说明)。动作 ∈ {满带, 减半, 恢复, 维持, 不执行}。"""
        if crowd is None or crowd != crowd:            # NaN -> null 条款
            return self.pos, "不执行", "拥挤度未成形（观测 < %d）" % CFG["pct_min"]
        if crowd >= CFG["crowd_hi"] and self.pos > CFG["pos_crowd"] + 1e-12:
            self.pos = float(CFG["pos_crowd"])
            self.notes.append((date, f"拥挤度 {crowd:.0%} ≥ {CFG['crowd_hi']:.0%} → 降至半带"))
            return self.pos, "减半", f"份额净申购处于 3 年 {crowd:.0%} 分位（拥挤）"
        if crowd <= CFG["crowd_lo"] and self.pos < CFG["pos_full"] - 1e-12:
            self.pos = float(CFG["pos_full"])
            self.notes.append((date, f"拥挤度 {crowd:.0%} ≤ {CFG['crowd_lo']:.0%} → 恢复满带"))
            return self.pos, "恢复", f"份额净申购处于 3 年 {crowd:.0%} 分位（冷清/出清）"
        return self.pos, "维持", "拥挤度在死区内"


def positions(g):
    """把拥挤度序列整段过一遍状态机 -> (position Series, action Series)。"""
    gate = CrowdGate("multi")
    c = crowd_pct(g)
    pos, act = [], []
    for date, v in c.items():
        p, a, _ = gate.step(v, date)
        pos.append(p)
        act.append(a)
    return (pd.Series(pos, index=c.index, dtype="float64"),
            pd.Series(act, index=c.index, dtype="object"))


# ---------------------------------------------------------------- 4. 单腿面板（回测/日报共用）
def leg_panel(leg, refresh=False):
    """-> dict(leg, name, role, band, exec, shares, g, crowd, pos, action, ratio, meta)

    `shares` = 头部 ETF 份额矩阵（亿元/亿份口径：单位亿份）；`g` = 加权净申购率；
    `crowd` = 拥挤度分位；`pos` = 目标仓位系数（0.5 / 1.0）；`ratio` = 严格渗透率（仅 A 股腿）。
    """
    axis = fdata.leg_axis(leg)
    shares = fdata.shares_frame(leg, axis=axis)
    g = flow_rate(shares)
    pos, act = positions(g)
    ratio = fdata.ratio_series(leg, axis=axis)
    meta = fdata.leg_meta(leg)
    return dict(leg=leg, name=meta["name"], role=meta["role"], band=meta["band"],
                exec=meta["exec_code"], exec_name=meta["exec_name"],
                head=meta["head"], head_names=meta["head_names"],
                last_anchor=meta["last_anchor"], axis=axis, shares=shares,
                g=g, crowd=crowd_pct(g), pos=pos, action=act, ratio=ratio)


def panels(legs=None, refresh=False):
    return {leg: leg_panel(leg, refresh=refresh) for leg in (legs or LEGS)}


# ---------------------------------------------------------------- 5. 自检
def _self_check():
    print(f"{LABEL} · 参数")
    print(f"  净申购率窗口 {CFG['flow_win']} 交易日 · 拥挤度窗口 {CFG['pct_win']} 日"
          f"（最少 {CFG['pct_min']}）")
    print(f"  拥挤 c≥{CFG['crowd_hi']:.0%} → 仓位 {CFG['pos_crowd']:g} · "
          f"冷清 c≤{CFG['crowd_lo']:.0%} → 仓位 {CFG['pos_full']:g} · 起点 {CFG['start_pos']:g}")
    print(f"  空仓/半仓部分按货基 {CASH_ANNUAL:.2%} 计息 · 成交 T+1")
    tot = sum(band_pp(k) for k in LEGS)
    print(f"  六腿目标带合计 {tot:g}pp（沿用主策略 v1.3，不改带宽）")
    for leg in LEGS:
        p = leg_panel(leg)
        c = p["crowd"].iloc[-1] if len(p["crowd"]) else float("nan")
        print(f"  {p['name']:8s} {p['band']:4g}pp 头部 {p['head']} · "
              f"g63 {p['g'].iloc[-1] * 100:+7.2f}% · 拥挤度 "
              f"{('%.0f%%' % (c * 100)) if c == c else '  n/a'}")


if __name__ == "__main__":
    _self_check()
