# -*- coding: utf-8 -*-
"""波动资产操作规则 v1.3 · 规则定义 —— 唯一权威源。

战略权重(v1.3, pp of 总资产): 核心腿 A股 5(中证A500) + 美股 20(纳指100 10 / 标普500 10);
进攻腿 5(科创50 2.5 / 恒生科技 2.5); 避险腿 10(黄金) —— 管辖额度 40pp。
其余 60pp: 红利 20(另见 strategies/dividend) / 国债 30 / 货币基金 10, 不在本策略内。
即大类仍是 权益 50 / 债 30 / 金 10 / 现金 10(文档 §0 的总目标未变, 变的是权益内部 A股/美股 的配比)。

规则文档: 《波动资产操作规则.md》v1.1(2026-10-28)。文档 §0 写的是「A500 10% + 纳指 5% +
标普 5%」(旧权重), v1.3 按用户确认的目标带改为 A500 5% / 纳指 10% / 标普 10% / 科创50 2.5% /
恒生科技 2.5% / 黄金 10% —— **份数按同一「满梯≈带」比例重标**, 见下面 ①。

v1.2 只改口径不改规则: 信号 / 记账 / 回测 / 执行统一到**场外基金复权净值**
(`SIGNAL_MODE = "otc"`, 默认)。v1.1 的场内 ETF 收盘 + 美元指数口径保留为
`SIGNAL_MODE = "exchange"`, 回测里两套口径直接对照。

## 本文件负责什么
1. `CFG` —— 文档 §9 参数表的镜像 + v1.3 权重重标后的份数(`CFG["unit_pp"]`), 改阈值只改这里。
2. `add_indicators` —— close 口径的四个派生量: ma250 / high52w(近250交易日最高收盘) /
   dd(回撤) / rsi14(Wilder)。全部由收盘价自算, 不用估算值。
3. 六条腿的**状态机**(`IndexLadderPolicy` / `TechPolicy` / `GoldPolicy`) —— 文档 §2~§6 的
   「闸门 → 阶梯 → 扳机 → 频率帽」逐条落地。策略只在此处做决策, engine.py 只负责记账。

## 五处文档口径与取法(必须知道, 否则数字对不上)
### ① 「1 份 = 权益账户 2%」的分母
文档 §2 自检写「满梯 9% ≈ 填满 10% 带」: 阶梯份数 1+1.5+2=4.5 份 → 4.5×2%=9%。
v1.3 的权重表让这句话**字面成立**: 权益账户 = 总资产的 50%, 2% 权益 = **1pp 总资产**,
A500 带 5pp, 满梯 4.5 份 = 4.5pp ≈ 5pp 带。于是 `unit_pp` 取「带的 20%」:
  a500 1.0pp / ndx 2.0 / spx 2.0 / gold 2.0(各 4.5 份 = 4.5pp / 9pp, 都是带的 90%)。
旧权重(A500 10pp、美股各 5pp)下同一比例给出 2pp / 1pp / 1pp —— 与文档 §9 的
`unit_pct_equity`(全局 2.0, 美股 1.0)一致, 所以这是同一个设计不变量的重标, 不是新规则。
美股/黄金的 1 份 = 权益账户 4%(2pp 总资产)。份数统一用 `unit_pp(leg)` 取。

### ② 「仓位带是主锚」vs「阶梯填满带」
文档设计原则说「仓位带是主锚」, 但 §2/§5/§6 的份数设计只能解释为「**空仓起步、由阶梯填满带**」
(否则阶梯加仓会顶穿带)。本策略取后者: 各腿初始仓位 0, 由规则逐档建仓,
未投部分留在货币基金(按 `CASH_ANNUAL` 计息)。`engine.run(init=...)` 可注入存量持仓
(份数自动折算), 供已持有底仓的人校准。

### ③ 「数据不足输出 null」的语义
文档 §1: 数据不足时信号输出 null, 「对应规则不执行」。本策略把 null 解释为
**「该闸门不生效(不否决)」**, 而非「视为否决」。例: A500 的 ERP 分位当前为 null
(中证A500 指数 2024-09 才有 PE 历史, 差 3 年门槛) → 启动闸门不执行 → 回撤阶梯照常工作;
科创50 的 PB 分位 null → 「pb<20 禁新增」不生效。所有 null 都在每日报告中逐条列出原因,
不以估算值补位。若你想把 null 当否决(更保守), 把 `NULL_GATE_BLOCKS` 设为 True。

### ④ 美股腿(§6)的三条特殊口径
- **信号源随 `SIGNAL_MODE` 走**: `"otc"`(默认, v1.2) 用该腿场外 QDII 复权净值(270042/017641,
  即账户真实收益); `"exchange"`(v1.1) 用美元指数 `.NDX` / `.INX`, 只用人民币产品净值会掺入汇率因子。
- **ERP 闸门**需 PE(TTM) 月度历史 + 10Y 美债(本地有 `data/_bt_caches/ust10y_m.csv`,
  但**没有 NDX/SPX 的 TTM PE 月度库**) → 分位取自 `data/_val_inputs.json` 人工月度复核
  (`ndx_erp_pct` / `spx_erp_pct`)。文档 §6「当前状态读数」= erp 为负 → 分位 <30% → 暂停新增,
  已按该读数预填, 即**美股腿当前只减不加**。
- **QDII 通道纪律**: 只走场外 A 类; 额度关闭/限大额时该档**顺延**(`_val_inputs.json` 的
  `qdii_limit_closed` 列出腿名即生效), 禁止换场内追溢价、禁止换产品顶替。
"""
import math
import os
import sys

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- 1. 参数(§9 镜像)
CFG = {
    # 1 份 = 该腿多少 pp of 总资产。取「带的 20%」, 使 4.5 份满梯 ≈ 带的 90%
    # (与文档 §2/§5/§6 自检「满梯 9% ≈ 填满 10 带」「满梯 4.5% ≈ 5 带」同比例)。
    # 文档 §9 的 unit_pct_equity(全局 2.0 / 美股 1.0)是按旧权重 A500 10pp、美股各 5pp 标定的;
    # v1.3 权重改成 A500 5pp、美股各 10pp 后按同一比例重标: 5pp 带 → 1pp, 10pp 带 → 2pp。
    "unit_pp": {"a500": 1.0, "ndx": 2.0, "spx": 2.0, "gold": 2.0},
    "a500": {"erp_gate_on": 70, "erp_gate_off": 30, "ladder_dd": [8, 16, 24],
             "ladder_units": [1, 1.5, 2], "max_tranche_per_month": 1},
    "kc50": {"ma_gate": 250, "half_in_days": 5, "clear_after_days": 20,
             "pb_panic": 90, "pb_add_min": 80, "pb_no_add_below": 20},
    "hkt": {"ma_gate": 250, "tips_pause_months": 3, "erp_gate_on": 70,
            "erp_gate_off": 20, "fomc_blackout_days": 5},
    "gold": {"ma_gate": 250, "degrade_after_days": 60, "ladder_dd": [10, 15, 20],
             "ladder_units": [1, 1.5, 2], "band_normal": 10, "band_degraded": 5,
             "wgrc_fuse_quarters": 2},
    # v1.1 §6: 美股腿。阶梯间距纳指加宽(10/20/30)以匹配其波动; 标普沿用 A500 的 8/16/24。
    "ndx": {"erp_gate_on": 70, "erp_gate_off": 30, "ladder_dd": [10, 20, 30],
            "ladder_units": [1, 1.5, 2],
            "max_tranche_per_month": 1, "max_premium_pct": 2,
            "channel": "offshore_a_only", "fomc_blackout": True},
    "spx": {"erp_gate_on": 70, "erp_gate_off": 30, "ladder_dd": [8, 16, 24],
            "ladder_units": [1, 1.5, 2],
            "max_tranche_per_month": 1, "max_premium_pct": 2,
            "channel": "offshore_a_only", "fomc_blackout": True},
    # 季度再平衡触发阈值 = min(band_pp, 目标带 × band_frac)。文档 §7 原文是绝对 ±5pp,
    # 但带宽减半后 5pp 对 A500 等于「可以漂到 2 倍带才减」, 故叠加 50% 比例上限。
    "rebalance": {"freq": "quarterly", "band_pp": 5, "band_frac": 0.5},
    "rsi": {"period": 14, "trigger_below": 40},
    "hard_filter": {"min_size_yi": 2, "max_premium_pct": 2, "min_turnover_wan": 1000,
                    "max_tracking_err_pct": 2},
}


def unit_pp(leg):
    """该腿 1 份 = 多少 pp 总资产。唯一来源 `CFG["unit_pp"]`, 缺省按 带宽/5。

    换算: 权益账户 = 总资产的 50%。A500 1 份 = 1pp 总资产 = **权益账户 2%**, 即文档 §2
    的原话; 美股/黄金 1 份 = 2pp 总资产 = 权益账户 4%。若你按权益账户记账(权益 50 视为 100),
    把报告里所有 pp 数字 ×2 即得 权益账户口径。
    """
    u = CFG["unit_pp"].get(leg)
    return float(u) if u is not None else float(LEGS[leg]["band"]) / 5.0


UNIT_PCT = unit_pp("a500")
UNIT = UNIT_PCT          # 默认 1 份 = 1.0pp(A500) / 2.0pp(美股、黄金)

NULL_GATE_BLOCKS = False   # null 闸门是否视为否决(默认否, 见模块 docstring ③)

# §6 季度再平衡的方向口径: True = 双向回到目标位; False(默认) = **只做减持**
# 理由: 文档 §5 明确「年线下只停加仓」、§2 的阶梯本身是建仓路径, 若再平衡可以无条件买回,
# 这两条规则就同时失效(把空仓的腿在季度末直接拉到带位)。而 §5 对再平衡的定位是
# 「兑现来源」—— 卖超配。回补交给阶梯/趋势闸门, 报告里照实列出缺口。
REBAL_BUY_BACK = False

MA_WIN = 250
RSI_N = int(CFG["rsi"]["period"])
RSI_TRIGGER = float(CFG["rsi"]["trigger_below"])
BAND_PP = float(CFG["rebalance"]["band_pp"])
BAND_FRAC = float(CFG["rebalance"]["band_frac"])
QUARTERLY = CFG["rebalance"]["freq"] == "quarterly"

# 现金/未投部分收益(货币基金): 2026-10 全市场 7 日年化约 1.3-1.5%, 取 1.40%
CASH_ANNUAL = 0.014

# 申赎成本(场外 A 类): 申购费打折后约 0.10%; 赎回费持有 <7 天 1.5%、7 天-1 年 0.5%。
FEE_BUY = 0.0010
FEE_SELL = 0.0050
FEE_SELL_7D = 0.0150
MIN_HOLD_FOR_LOW_FEE = 7

# ---------------------------------------------------------------- 2. 标的与仓位带
# band = 目标带(pp of 总资产)。
# SIGNAL_MODE 决定「信号 & 回测用哪条价格序列」(两种口径都已实现, 回测里直接对比):
#   "otc"      : **全部用场外基金净值**(默认) —— 信号、回测收益、执行三件套同一口径,
#                最贴近实际账户(含汇率、含跟踪误差、含申赎时滞), 代价是 A500/标普场外
#                成立晚, 需用同类老基金「桥接」补齐 250 日窗口(otc_proxy, 报告会标注)。
#   "exchange" : A500/科创50/恒生科技/黄金用场内 ETF 收盘, 美股用美元指数(NDX/SPX) ——
#                即文档 §6 的原口径(信号纯净、不含汇率与溢价, 但与你实际持有的净值有偏离)。
SIGNAL_MODE = "otc"

# exec = 场外执行代码(唯一执行通道, A 类优先); otc = 该代码即场外净值序列;
# otc_proxy = 场外长历史同类基金, 仅用于桥接 otc 成立前的窗口(按拼接日净值缩放)。
# band = 目标带(pp of 总资产)。v1.3 战略权重: A股核心 5(A500) + 美股核心 20(纳指10/标普10)
#   + 进攻 5(科创50 2.5 / 恒生科技 2.5) + 避险 10(黄金) = 40pp。
#   其余 60pp(红利 20 / 国债 30 / 货币 10)不在本策略内 —— 权益合计 50 / 债 30 / 金 10 / 现金 10。
LEGS = {
    "a500": dict(name="中证A500", role="核心腿", band=5.0,
                 signal="563360", signal_name="A500ETF华泰柏瑞",
                 exec="023299", exec_name="汇添富中证A500指数增强C",
                 otc="023299", otc_start="2025-03-27",
                 otc_proxy="000051", otc_proxy_name="华夏沪深300ETF联接A"),
    # v1.1 §6: 美股腿的 exchange 口径信号是**美元指数**(Xueqiu 代码, data.py 会拉取并缓存到
    # data/_us_index_klines.json); proxy 仅在指数源不可用时作降级(CNY 口径, 报告会标注)。
    "ndx": dict(name="纳指100", role="核心腿", band=10.0,
                signal=".NDX", signal_name="纳斯达克100指数(美元)",
                otc="270042", otc_qdii=True,
                proxy="513100", proxy_name="纳指ETF国泰(CNY代理)",
                exec="270042", exec_name="广发纳斯达克100ETF联接(QDII)A"),
    "spx": dict(name="标普500", role="核心腿", band=10.0,
                signal=".INX", signal_name="标普500指数(美元)",
                otc="017641", otc_start="2023-04-06", otc_qdii=True,
                otc_proxy="050025", otc_proxy_name="博时标普500ETF联接A",
                proxy="513500", proxy_name="标普500ETF博时(CNY代理)",
                exec="017641", exec_name="摩根标普500指数(QDII)人民币A"),
    "kc50": dict(name="科创50", role="进攻腿", band=2.5,
                 signal="588000", signal_name="科创50ETF华夏",
                 exec="011609", exec_name="易方达上证科创板50ETF联接C",
                 otc="011609", otc_start="2021-03-01"),
    "hkt": dict(name="恒生科技", role="进攻腿", band=2.5,
                signal="513180", signal_name="恒生科技ETF华夏",
                exec="012805", exec_name="广发恒生科技ETF联接QDII-C",
                otc="012805", otc_start="2021-08-11", otc_qdii=True),
    "gold": dict(name="黄金", role="避险腿", band=10.0,
                 signal="518880", signal_name="黄金ETF华安",
                 exec="000216", exec_name="华安黄金ETF联接A",
                 otc="000216", otc_start="2013-08-22"),
}
LEG_ORDER = ["a500", "ndx", "spx", "kc50", "hkt", "gold"]
SLEEVE_PP = sum(LEGS[k]["band"] for k in LEG_ORDER)      # 40pp: 本策略管辖的额度


def rebalance_pp(leg):
    """该腿的季度再平衡触发阈值 = min(绝对 5pp, 目标带 × 50%)。

    纯绝对阈值在小带宽腿上等于失效: A500 带 5pp 时可以漂到 10pp(2 倍带)才减持;
    叠加比例上限后 → A500 2.5pp、科创50/恒生科技 1.25pp、美股与黄金 5pp。
    """
    return min(BAND_PP, float(LEGS[leg]["band"]) * BAND_FRAC)

# 进攻腿的观察仓: 带的 20%(v1.3 带 2.5pp → 0.5pp), 与「减半 1.25pp」拉开一档,
# 同时满足文档 §3「清至观察仓 ≤1%」。旧权重(5pp 带)时是 1.0pp, 同比例。
OBSERVE_PP = 0.5

# 仅 exchange 口径有意义: A500 场内 ETF(563360 上市 2024-10)历史不足 —— 用沪深300ETF(510300)
# 链接补齐 52 周窗口与回撤阶梯。只影响**信号序列**的早期段, 拼接日之后全是真实 A500。
# 设 False 则场内口径的 A500 从 2025-10 才「就绪」。otc 口径的桥接见 OTC_USE_PROXY_HISTORY。
A500_PROXY_CODE = "510300"
A500_USE_PROXY_HISTORY = True

# 场外口径(SIGNAL_MODE="otc")的桥接开关: 023299 成立于 2025-03、017641 于 2023-04,
# 不足 250 日窗口 -> 前段用同类场外老基金(000051 / 050025)按拼接日净值缩放补齐。
# 只影响窗口早期段; 拼接日之后是真实执行标的的净值。设 False 则组合层样本从 2026-04 才起算。
OTC_USE_PROXY_HISTORY = True

# 硬过滤(§8): 当前池内标的的实测值(来源 data/_universe.md, 2026-09 维护)
HARD_FILTER_CHECK = {
    "a500": dict(size_yi=16.3, turnover_wan=None, premium_pct=None, track_err_pct=None),
    # 美股两条腿的规模取自 data/_universe.md 的场外基金规模(270042=122.2亿 / 017641=53.0亿);
    # QDII 另有「额度」风险, 不是规模问题, 见 §6 通道纪律。
    "ndx": dict(size_yi=122.2, turnover_wan=None, premium_pct=None, track_err_pct=None),
    "spx": dict(size_yi=53.0, turnover_wan=None, premium_pct=None, track_err_pct=None),
    "kc50": dict(size_yi=140.6, turnover_wan=None, premium_pct=None, track_err_pct=None),
    "hkt": dict(size_yi=56.0, turnover_wan=None, premium_pct=None, track_err_pct=None),
    "gold": dict(size_yi=97.2, turnover_wan=None, premium_pct=None, track_err_pct=None),
}

# 需要人工维护/外部回填的估值输入(空缺即 null, 不用估算值补位)
VAL_INPUTS = os.path.join(_DIR, "data", "_val_inputs.json")


# ---------------------------------------------------------------- 3. 指标
def rsi_wilder(close: pd.Series, n: int = RSI_N) -> pd.Series:
    """Wilder RSI(n)。下跌段均值为 0 时 RSI=100(与 Wilder 定义一致, 不用 NaN 糊过去)。"""
    d = close.diff()
    up = d.clip(lower=0.0)
    dn = (-d).clip(lower=0.0)
    ru = up.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    rd = dn.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    rsi = 100.0 - 100.0 / (1.0 + ru / rd)
    rsi = rsi.where(rd > 1e-12, 100.0)
    rsi[ru.isna() | rd.isna()] = float("nan")
    return rsi


def _streak(cond: pd.Series) -> pd.Series:
    """cond 为 True 的连续计数(遇 False 归零)。"""
    grp = (~cond.fillna(False)).cumsum()
    return cond.fillna(False).astype(int).groupby(grp).cumsum()


def add_indicators(close: pd.Series) -> pd.DataFrame:
    """收盘价 -> 指标表。列: close/ma/high52w/dd/rsi/above/below_days/above_days/
    new_high/ready。

    ready = 近 MA_WIN 个交易日满窗口(文档 §1: 数据不足该信号为 null, 本例整腿不动)。
    """
    c = close.astype("float64")
    out = pd.DataFrame(index=c.index)
    out["close"] = c
    out["ma"] = c.rolling(MA_WIN, min_periods=MA_WIN).mean()
    out["high52w"] = c.rolling(MA_WIN, min_periods=MA_WIN).max()
    out["dd"] = 1.0 - c / out["high52w"]
    out["rsi"] = rsi_wilder(c)
    out["ready"] = (out["ma"].notna() & out["high52w"].notna() & out["rsi"].notna())
    out["above"] = (c >= out["ma"]) & out["ready"]
    out["below_days"] = _streak(~out["above"]).where(out["ready"], 0)
    out["above_days"] = _streak(out["above"]).where(out["ready"], 0)
    out["new_high"] = (c >= out["high52w"] - 1e-12) & out["ready"]
    out["ma_dev"] = c / out["ma"] - 1.0
    return out


def row_of(ind: pd.DataFrame, date) -> dict:
    """指标表某日 -> 纯 dict(供 policy 使用, 避免 pandas 标量泄漏)。"""
    r = ind.loc[date]
    d = {}
    for k in ("close", "ma", "high52w", "dd", "rsi", "below_days", "above_days",
              "ma_dev"):
        v = r.get(k)
        d[k] = None if v is None or (isinstance(v, float) and v != v) or pd.isna(v) else float(v)
    for k in ("ready", "above", "new_high"):
        v = r.get(k)
        d[k] = bool(v) if v is not None and not pd.isna(v) else False
    return d


# ---------------------------------------------------------------- 4. 决策动作
def _op(leg, kind, rule, detail, delta_pp=None, target_pp=None, units=None):
    return dict(leg=leg, kind=kind, rule=rule, detail=detail,
                delta_pp=delta_pp, target_pp=target_pp, units=units)


class IndexLadderPolicy:
    """§2 A500 / §6 纳指100 / §6 标普500: 启动闸门(ERP 分位) → 回撤阶梯 → RSI 扳机 → 月度帽。

    三条腿结构相同, 差异集中在参数(§9 + v1.3 权重):
      a500 : dd 8/16/24%, 带 5pp, 1 份 = 1pp, 无 FOMC 窗口(在岸资产)
      ndx  : dd 10/20/30%(波动更大, 间距加宽), 带 10pp, 1 份 = 2pp, FOMC 窗口禁新仓, QDII 额度可顺延
      spx  : dd 8/16/24%, 带 10pp, 1 份 = 2pp, 同 ndx
    阶梯份数 1/1.5/2 为**累加**(A500 满梯 4.5 份 = 4.5pp; 美股/黄金 4.5 份 = 9pp)。
    close 创 52 周新高 → 阶梯计数归零(已建份数不动); 累计仓位封顶在仓位带以内。
    """

    def __init__(self, key, units=0.0):
        self.key = key
        self.cfg = CFG[key]
        self.band = LEGS[key]["band"]
        self.unit = unit_pp(key)
        self.gate_key = f"{key}_erp"
        self.label = LEGS[key]["name"]
        self.tiers_done = [False] * len(self.cfg["ladder_dd"])
        self.armed_tier = None          # 已触发、等 RSI 扳机的档位
        self.units = float(units)       # 已建份数
        self.month = None
        self.month_tranche = 0
        self.prev_rsi = None
        self.notes = []

    # ---- 供 engine / 报告
    def built_pp(self):
        return min(self.band, self.units * self.unit)

    def target_pp(self):
        return self.built_pp()

    def band_target(self):
        """§6 再平衡的「目标位」= 战略目标带(不是已建份数)。"""
        return self.band

    def ladder_state(self):
        return dict(units=self.units, built_pp=self.built_pp(),
                    tiers_done=list(self.tiers_done), armed_tier=self.armed_tier,
                    month_tranche=self.month_tranche, prev_rsi=self.prev_rsi)

    def gate_text(self, gates):
        g = gates.get(self.gate_key)
        on = self.cfg["erp_gate_on"]
        off = self.cfg["erp_gate_off"]
        return {"on": f"on(分位 ≥{on}%, 激活阶梯)",
                "off": f"off(分位 <{off}%, 暂停新增)",
                None: "null(数据不足 → §1 规定该规则不执行, 不否决)"}[g]

    # ---- 每日决策
    def step(self, row, gates, date, cur_pp=0.0):
        ops = []
        if not row["ready"]:
            self.prev_rsi = None
            return ops

        mk = (date.year, date.month)
        if mk != self.month:
            self.month, self.month_tranche = mk, 0

        # 1) 52 周新高 -> 阶梯计数归零(已建份数不动)
        if row["new_high"] and any(self.tiers_done):
            self.tiers_done = [False] * len(self.tiers_done)
            self.armed_tier = None
            self.notes.append((date, "阶梯计数归零(close 创 52 周新高)"))

        # 2) 档位判定 —— 只认**最低的未填档位**: dd 一次跳到 17% 时先补第1档,
        #    第2档留到下个月(受频率帽约束), 与「阶梯逐级向下」的语义一致。
        dd_pct = (row["dd"] or 0.0) * 100.0
        tier = None
        for i, th in enumerate(self.cfg["ladder_dd"]):
            if dd_pct >= th and not self.tiers_done[i]:
                tier = i
                break
        gate = gates.get(self.gate_key)          # "on" / "off" / None
        blocked = (gate == "off") or (gate is None and NULL_GATE_BLOCKS)
        if tier is not None and not blocked:
            if self.armed_tier != tier:
                self.armed_tier = tier
                self.notes.append((date, f"触及第{tier + 1}档(dd {dd_pct:.1f}% ≥ "
                                         f"{self.cfg['ladder_dd'][tier]}%), 等 RSI 扳机"))

        # 3) RSI 扳机: rsi<40 或 从 <40 回升的首日
        if self.armed_tier is not None:
            r, pr = row["rsi"], self.prev_rsi
            trig = (r is not None and r < RSI_TRIGGER) or (
                r is not None and pr is not None and pr < RSI_TRIGGER <= r)
            if trig:
                # §6 事件窗口 / QDII 额度: 都属「顺延」而非「取消」—— 档位不消耗, 恢复后补执行
                if self.cfg.get("fomc_blackout") and gates.get("fomc_blackout"):
                    self.notes.append((date, "FOMC 禁新仓窗口, 该档顺延"))
                    return ops
                if self.key in (gates.get("qdii_limit_closed") or []):
                    self.notes.append((date, "QDII 额度关闭/限大额, 该档顺延(禁止换场内追溢价)"))
                    return ops
                if self.month_tranche >= self.cfg["max_tranche_per_month"]:
                    self.notes.append((date, "本月已执行 1 档, 顺延(频率帽)"))
                else:
                    i = self.armed_tier
                    want = self.cfg["ladder_units"][i] * self.unit
                    room = self.band - max(cur_pp, 0.0)
                    if r < RSI_TRIGGER:
                        trig_txt = f"RSI14 {r:.1f} < {RSI_TRIGGER:.0f}"
                    else:
                        trig_txt = f"RSI14 由 {pr:.1f} 回升至 {r:.1f}(从<{RSI_TRIGGER:.0f} 回升的首日)"
                    if room >= 0.2 * self.unit:
                        delta = min(want, room)
                        ops.append(_op(
                            self.key, "buy",
                            f"{self.label} 回撤阶梯第{i + 1}档(dd≥{self.cfg['ladder_dd'][i]}%)",
                            f"{trig_txt} 扳机 · dd {dd_pct:.1f}% · "
                            f"阶梯第{i + 1}档 {self.cfg['ladder_units'][i]}份",
                            delta_pp=delta, units=delta / self.unit))
                        self.units += delta / self.unit
                    else:
                        self.notes.append((date, "仓位带已满(剩余额度 <0.2 份), 该档不执行"))
                    self.tiers_done[i] = True
                    self.month_tranche += 1
                    self.armed_tier = None

        self.prev_rsi = row["rsi"]
        return ops


class TechPolicy:
    """§3/§4 进攻腿(科创50 / 恒生科技): 趋势闸门为主, 估值/事件/宏观只作「加仓许可」。

    状态 = 目标仓位(pp): 带上限 5 / 半仓 2.5 / 观察仓 1 / 空仓 0。
      close ≥ ma250          -> 持有区; 加仓许可成立则加至带上限
      close < ma250 满 5 日   -> 减至半仓
      close < ma250 满 20 日  -> 清至观察仓(OBSERVE_PP = 0.5pp)
      科创50 唯一例外: 年线下且 pb 分位 ≥90% -> 允许 1/4 仓(0.625pp)试错一次
    """

    band = 5.0

    def __init__(self, key, target_pp=0.0):
        self.key = key
        # 文档 §4: 恒生科技的趋势闸门「同科创50 第三节」—— 半仓/清仓天数继承 kc50,
        # 各自的估值与事件参数覆盖之(erp 门槛、TIPS 保险丝、FOMC 窗口)。
        self.cfg = dict(CFG["kc50"])
        self.cfg.update(CFG.get(key, {}))
        self.band = LEGS[key]["band"]
        self.target = float(target_pp)
        self.panic_used = False
        self.notes = []

    def target_pp(self):
        return self.target

    def band_target(self):
        return self.target          # 进攻腿的目标就是趋势闸门给出的档位(5/2.5/1/0)

    def _add_allowed(self, gates):
        """-> (是否允许加仓, 理由)。None 闸门按文档「不执行」处理(默认放行)。"""
        if self.key == "hkt":
            if gates.get("fomc_blackout"):
                return False, "FOMC 禁新仓窗口"
            if gates.get("tips_fuse"):
                return False, "10Y TIPS 实际利率连续 3 月上行(宏观保险丝)"
            erp = gates.get("hkt_erp_pct")
            if erp is not None and erp * 100.0 < self.cfg["erp_gate_off"]:
                return False, f"恒生科技 ERP 分位 {erp * 100:.0f}% < {self.cfg['erp_gate_off']}(禁新增)"
            return True, "趋势闸门放行"
        pb = gates.get("kc50_pb_pct")
        if pb is not None and pb * 100.0 < self.cfg["pb_no_add_below"]:
            return False, f"PB 分位 {pb * 100:.0f}% < {self.cfg['pb_no_add_below']}(禁新增)"
        return True, "趋势闸门放行"

    def step(self, row, gates, date, cur_pp=0.0):
        if not row["ready"]:
            return []
        band, half = self.band, self.band / 2.0
        below = int(row["below_days"] or 0)
        eps = 0.05      # 0.05pp 内不再动手, 避免把价格漂移当成信号反复微调

        # 极端恐慌试错(仅科创50, 需 PB 分位; 未维护时该分支不执行)
        pb = gates.get("kc50_pb_pct") if self.key == "kc50" else None
        if (not row["above"] and pb is not None and pb * 100.0 >= self.cfg.get("pb_panic", 90)
                and not self.panic_used):
            tgt = band / 4.0
            self.panic_used = True
            self.target = tgt
            self.notes.append((date, f"极端恐慌试错 1/4 仓(PB 分位 {pb * 100:.0f}%)"))
            return [_op(self.key, "buy", "科创50 极端恐慌试错(1/4 仓)",
                        f"年线下方 + PB 分位 {pb * 100:.0f}% ≥90%", target_pp=tgt)]

        if row["above"]:
            ok, why = self._add_allowed(gates)
            if ok and self.target < band - 1e-9:
                self.target = band
                self.notes.append((date, f"站上年线, 加至带顶({why})"))
                if cur_pp < band - eps:
                    return [_op(self.key, "buy", "趋势闸门恢复(close ≥ MA250)",
                                f"站上年线, 加至带顶 {band:.1f}pp · {why}",
                                target_pp=band)]
                return []
            if not ok and self.target < band - 1e-9 and self.target > 0:
                self.notes.append((date, f"站上年线但加仓许可不成立: {why}"))
            return []

        # 年线下方: 减仓是「上限」语义 —— 只有当前仓位高于目标才卖, 不因为空仓去建观察仓
        if below >= self.cfg["clear_after_days"]:
            if self.target > OBSERVE_PP + 1e-9:
                self.target = OBSERVE_PP
                self.notes.append((date, f"跌破年线满 {below} 日, 清至观察仓"))
                if cur_pp > OBSERVE_PP + eps:
                    return [_op(self.key, "sell",
                                f"趋势闸门清仓(跌破 MA250 满 {self.cfg['clear_after_days']} 日)",
                                f"已连续 {below} 个交易日在年线下方",
                                target_pp=OBSERVE_PP)]
                return []
        elif below >= self.cfg["half_in_days"]:
            if self.target > half + 1e-9:
                self.target = half
                self.notes.append((date, f"跌破年线满 {below} 日, 减至半仓"))
                if cur_pp > half + eps:
                    return [_op(self.key, "sell",
                                f"趋势闸门减半(跌破 MA250 满 {self.cfg['half_in_days']} 日)",
                                f"已连续 {below} 个交易日在年线下方", target_pp=half)]
                return []
        return []


class GoldPolicy:
    """§5 黄金避险腿: 宽松版趋势闸门(只停加仓不清仓) + 回撤阶梯 + 季度再平衡。

      close < ma250          -> 只停加仓(不清仓)
      close < ma250 连续 60 日 -> 目标带 10 -> 5(由再平衡降档执行)
      dd ≥10/15/20% 且 close ≥ ma250 -> 1/1.5/2 份(1 份 = UNIT pp)
      创 52 周新高 -> 阶梯归零; FOMC 窗口 -> 不加仓
    """

    key = "gold"
    band = LEGS["gold"]["band"]

    def __init__(self, units=0.0):
        self.tiers_done = [False] * len(CFG["gold"]["ladder_dd"])
        self.units = float(units)
        self.unit = unit_pp("gold")
        self.degraded = False
        self.notes = []

    def built_pp(self):
        return min(self.band, self.units * self.unit)

    def target_pp(self):
        return CFG["gold"]["band_degraded"] if self.degraded else self.built_pp()

    def band_target(self):
        """§6 再平衡目标位; 降档保险丝生效时为 5pp。"""
        return CFG["gold"]["band_degraded"] if self.degraded else self.band

    def step(self, row, gates, date, cur_pp=0.0):
        ops = []
        if not row["ready"]:
            return ops
        cfg = CFG["gold"]
        below = int(row["below_days"] or 0)

        # 降档保险丝: 连续 60 日在年线下 -> 目标带降到 5pp(超出的部分由再平衡卖出)
        if (not row["above"]) and below >= cfg["degrade_after_days"] and not self.degraded:
            self.degraded = True
            self.notes.append((date, f"连续 {below} 日在年线下, 目标带 10 → 5"))
            tgt = min(self.built_pp(), cfg["band_degraded"])
            if cur_pp > tgt + 0.05:
                ops.append(_op(self.key, "sell", "黄金长期保险丝(年线下满 60 日)",
                               f"目标带 10% → 5%, 已连续 {below} 日在年线下",
                               target_pp=tgt))
        # 收复年线 -> 恢复目标带(不强制买, 由阶梯/再平衡执行)
        if row["above"] and self.degraded:
            self.degraded = False
            self.notes.append((date, "收复年线, 目标带恢复"))

        if row["new_high"] and any(self.tiers_done):
            self.tiers_done = [False] * len(self.tiers_done)
            self.notes.append((date, "阶梯计数归零(close 创 52 周新高)"))

        # 回撤阶梯: 仅当 close ≥ ma250; 每次只补**最低的未填档位**
        if row["above"] and not gates.get("fomc_blackout") and not self.degraded:
            dd_pct = (row["dd"] or 0.0) * 100.0
            for i, th in enumerate(cfg["ladder_dd"]):
                if dd_pct >= th and not self.tiers_done[i]:
                    want = cfg["ladder_units"][i] * self.unit
                    room = self.band - max(cur_pp, 0.0)
                    if room >= 0.2 * self.unit:
                        delta = min(want, room)
                        ops.append(_op(
                            self.key, "buy",
                            f"黄金回撤阶梯第{i + 1}档(dd≥{th}%)",
                            f"dd {dd_pct:.1f}% · 年线上方 · 阶梯第{i + 1}档 "
                            f"{cfg['ladder_units'][i]}份",
                            delta_pp=delta, units=delta / self.unit))
                        self.units += delta / self.unit
                    else:
                        self.notes.append((date, "仓位带已满(剩余额度 <0.2 份), 该档不执行"))
                    self.tiers_done[i] = True
                    break
        return ops


def make_policies(init_pp=None):
    """-> {leg: policy}。init_pp 给存量持仓(pp of 总资产)时按份数折算起步状态。"""
    init_pp = init_pp or {}
    out = {}
    for key in ("a500", "ndx", "spx"):
        out[key] = IndexLadderPolicy(key, units=float(init_pp.get(key, 0.0)) / unit_pp(key))
    for key in ("kc50", "hkt"):
        out[key] = TechPolicy(key, target_pp=float(init_pp.get(key, 0.0)))
    out["gold"] = GoldPolicy(units=float(init_pp.get("gold", 0.0)) / unit_pp("gold"))
    return out


# ---------------------------------------------------------------- 5. 报告辅助
def ladder_text(key, pol):
    """阶梯状态的一句话说明(每日报告用)。"""
    cfg = CFG[key]
    th, un = cfg["ladder_dd"], cfg["ladder_units"]
    parts = []
    for i, (t, u) in enumerate(zip(th, un)):
        mark = "✓" if pol.tiers_done[i] else "·"
        parts.append(f"{mark}档{i + 1}(dd≥{t}%:{u}份)")
    return " ".join(parts)


def hard_filter_rows():
    """§7 硬过滤一票否决项的当前核对结果 -> [(名称, 判据, 实测, 结论)]。"""
    hf = CFG["hard_filter"]
    rows = []
    for leg in LEG_ORDER:
        c = HARD_FILTER_CHECK[leg]
        size = c.get("size_yi")
        ok = size is None or size >= hf["min_size_yi"]
        rows.append((LEGS[leg]["name"], f"规模 ≥{hf['min_size_yi']}亿",
                     f"{size:.1f} 亿" if size is not None else "—",
                     "过" if ok else "否决"))
    return rows


def _self_check():
    assert abs(SLEEVE_PP - 40.0) < 1e-9, SLEEVE_PP
    print(f"管辖额度 {SLEEVE_PP:.0f}pp = " + " + ".join(
        f"{LEGS[k]['name']}{LEGS[k]['band']:g}pp" for k in LEG_ORDER))
    print("其余 60pp 不在本策略: 红利 20 / 国债 30 / 货币基金 10")
    for key in ("a500", "ndx", "spx", "gold"):
        u = unit_pp(key)
        tot = sum(CFG[key]["ladder_units"]) * u
        print(f"  {LEGS[key]['name']:8s} 1 份 = {u:.2f}pp · 满梯 "
              f"{sum(CFG[key]['ladder_units'])} 份 = {tot:.1f}pp(带 {LEGS[key]['band']:g}pp, "
              f"占带 {tot / LEGS[key]['band']:.0%}) · 阶梯 {CFG[key]['ladder_dd']}")
    print(f"进攻腿: 带 {LEGS['kc50']['band']:g}pp → 半仓 {LEGS['kc50']['band'] / 2:g}pp / "
          f"观察仓 {OBSERVE_PP:g}pp / 试错 {LEGS['kc50']['band'] / 4:g}pp")
    print(f"再平衡阈值 min({BAND_PP:g}pp, 带×{BAND_FRAC:.0%}): " + " / ".join(
        f"{LEGS[k]['name']}{rebalance_pp(k):g}pp" for k in LEG_ORDER))


if __name__ == "__main__":
    _self_check()
