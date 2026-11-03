# -*- coding: utf-8 -*-
"""波动资产 · V6 变种：**估值分位仓位带**（PE 分位定带 + 回撤阶梯 + RSI 扳机）。

来源：`E:/qianwen/jijin/中证A500操作规则.md` v1.0（2026-10-30 定稿）。
一句话：**PE 自身分位决定「最多能买多重」，回撤阶梯决定「这一次进多少」，RSI 决定「哪一天进」；
股债利差只有一票否决权，没有加分权。**

规则本身**与标的无关**（只要指数有滚动 PE 历史），本模块接两个标的：

| `UNIVERSE` | 价格 | 滚动 PE 起点 | PE 窗口状态 |
|---|---|---|---|
| `"a500"`（文档原型） | 中证A500 000510，2005 起含回溯 | **2024-09-02** | 10 年窗口不可能成立（`window_short=True`） |
| `"hs300"`（一等样本） | 沪深300 全收益 H00300 | 2011-06 | 2012-10 起 10 年窗口成立，**14 年可回测** |

> 2026-11-03 按用户要求：①从 `v6_a500_band` 改名 `v6_pe_band`（不再绑标的）；
> ②**定档频率默认改为 `weekly`（每周最后一个交易日）**，`monthly` / `quarterly`（文档原口径）保留作对照；
> ③沪深300 从「代理口径」升为一等样本，与 A500 并列跑同一套规则。

## 1. 它管哪一层

本变种管「**投给该指数的这笔钱（0–100%）内部怎么分配**」，不假设它占总资产多少
（总仓位由再平衡层 / 主策略 `../rule.py` 的 2.5pp 带决定）。所以它的净值是**这笔钱的
份额化净值**（0–100% 仓位 + 空仓吃货基利息），基准是「同区间买入持有该指数」。

| 谁 | 管什么 | 信号 |
|---|---|---|
| 主策略 `../` | 各腿 2.5pp 带内的**回撤阶梯**（跌 8/16/24% 各补几档） | ERP 分位闸门 + dd + RSI |
| **本变种 v6** | **这笔钱最多能投几成**（PE 分位仓位带）+ 阶梯 + RSI 扳机 | **PE 自身分位**（文档 §0 唯一闸门） |
| `../v4_erp_band/` | 沪深300 与国债之间摆多少（ERP 通道 + 两带滞回 0/100） | ERP 的 MA±2σ |
| `../v1_ma20/`、`../v2_boll_breakout/`、`../v3_macd_boll/`、`../v5_trend_gate/` | 逐腿进出时点 / 带内档位 | 价格趋势 |

四者抢同一份额度，**叠加前先定谁优先**。本变种与主策略 §2 的差别是闸门：主策略用
ERP 分位（大概率 null），本变种用 **PE 自身分位**（文档 §0 的理由：510300 2012–2026 回测
里 PE 五年分位 6.45%/18.24%，ERP 分位 6.59%/21.02%，两者相关系数 −0.946、信息几乎完全重叠）。

## 2. 规则卡（逐条 → 代码）

| 编号 | 文档原文 | 代码 |
|---|---|---|
| R-A1 | 闸门 = 该指数 PE-TTM 在近 **10 年**自身历史中的百分位，窗口**写死** | `pe_percentile()`（`WINDOW_DAYS=2520`） |
| R-A2 | 仓位带 ≤20%→100 / 20–40→70 / 40–60→50 / 60–80→30 / ≥80→10 | `band_weight()` + `CFG["BAND_EDGES/WEIGHTS"]` |
| R-A3 | 买卖用同一张表；减仓时先减分位最高档那一份 | `simulate()`：band 下调即在定档日卖到目标 |
| R-A4 | **越带才动，到期核对**（原文「季末」；`REBAL_FREQ` 可 weekly / monthly / quarterly） | `decision_mask()` + `CFG["REBAL_FREQ"]` |
| R-A4′ | 6/12 月调样当月信号顺延至下一个季末（**仅 `quarterly` 生效**，见 §3-⑤） | `CFG["BLACKOUT_MONTHS"]` + `CFG["BLACKOUT_FREQ"]` |
| R-A5 | 分批 + 扳机：向目标带推进分 5 份，1 份 = 本策略资金 20% | `CFG["UNIT_PCT_POOL"]=20` |
| R-A6 | 回撤阶梯 dd ≥8/16/24% → 1/1.5/2 份（dd = 1 − close/近250日最高） | `CFG["LADDER_DD/UNITS"]` + `drawdown()` |
| R-A7 | 档位触发后，等 `RSI14<40` 或从 `<40` 回升的首个交易日才下单 | `rsi()` + `rsi_trigger()` |
| R-A8 | 同一自然月最多执行 1 档；close 创 **52 周新高**则阶梯计数归零 | `simulate()` |
| R-A9 | 满梯 4.5 份 ≈ 90%，与 100% 带吻合 | `CFG["LADDER_UNITS"]` 合计 4.5 |
| R-A10 | 利差**只做否决**：分位没降但利差被 10Y 下行撑高 → 按低一档执行，且需连续 2 个月成立 | `monthly_signals()` 的 `veto` |
| R-A11 | ≥80% 分位**连续 2 个月**成立才降到 10% 观察仓 | `sell_confirmed()` |
| R-A12 | 载体：规模最大的该指数 ETF / A 类联接；**不做指数增强**；四条一票否决 | 人工执行（`daily/` §5 清单） |

## 3. 五处「没写死、不定就跑不下去」的口径

### ① 边界归属
文档表里「≤20% → 100」与「20–40% → 70」在 20% 处重叠、「≥80% → 10」与「60–80% → 30」在
80% 处重叠。本实现的取法是：**下界闭、上界开，只有第一档的左端闭**：
`pct ≤ 0.20 → 1.0`、`(0.20, 0.40] → 0.7`、`(0.40, 0.60] → 0.5`、`[0.60, 0.80) → 0.3`、`pct ≥ 0.80 → 0.1`。
好处是 0.80 一旦出现就落到观察仓（与「贵是降带的理由」一致），且不产生空档。

### ② 「两个结论相反时取低一档」（文档 §6-1 交叉校验）
A500 的 PE 只有 **2024-09-02 起**（指数本身没有更早的估值历史，**不是取数没取全**），
所以 A500 档把 **沪深300 的真 10 年 PE 分位**作为第二把尺子：

- 两把尺子给出**同一档** → 用该档；
- 给出**不同档** → **取较低的那一档仓位**（`CFG["CROSS_RULE"]="take_lower"`）。
  文档原话是「取低一档」；在只有两把尺子时，「较低的结论」与「降一档」差一档，
  本实现取更保守者对（`take_lower`），并在报告里同时列出两把尺子的原始档位。
  想切回逐字读法：`CROSS_RULE="one_step_down"`。
- **沪深300 是一等样本时没有第二把尺子**（本地没有第二个同级长历史估值源）→ `pct_h` 为空，
  交叉校验自动关闭。这是结构限制，不是开关。

### ③ 全收益口径
- 沪深300 用 **H00300 全收益**（原生）；
- A500 的全收益指数 `H00510` 本机不可达（已删除的旧 v4 `a500/` 子变种也是同一结论），
  故用 **价格指数 + 股息率按日计提** 合成：`ret_tr = ret_px + dy/252`，`dy` 默认 2.5%。
  **只影响回测收益，不影响任何信号**（信号只用 PE / close / dd / rsi）。

### ④ 起手仓位
文档说「这笔钱」已经存在，但没说第一天就摆到带位还是从空仓爬梯。两种都实现：

- `init="band"`（**默认 / 实盘口径**）：第一个有分位的交易日直接摆到当时的带位；
- `init="zero"`：空仓起步，只能靠阶梯填（回测里作对照，用来量化「等跌下来再买」的代价）。

### ⑤ 定档频率与调样月
`REBAL_FREQ`：
- `"weekly"`（**2026-11-03 起默认**）：每个自然周最后一个交易日核对分位是否跨档；
- `"monthly"`：每月最后一个交易日；
- `"quarterly"`（文档原口径）：3/6/9/12 月最后一个交易日。

**6/12 月顺延只在 `quarterly` 下生效**（`CFG["BLACKOUT_FREQ"]="quarterly"`）。理由是原文那句
「调样当月信号顺延至下一个季末」是给季末定档写的规则；weekly 下把 6/12 整整两个月按下不动，
等于凭空多出两个月的空窗，比调样噪音更贵。想恢复：`BLACKOUT_FREQ="all"`。

## 4. 数据（跨策略数据类，本变种**只读**）

| 用途 | 文件 | 维护方 |
|---|---|---|
| A500 收盘（2005 起含回溯）+ 滚动 PE（2024-09 起） | `data/_bt_caches/csi_000510.csv`（`date,close,peg`） | 已冻结（原 `v4_erp_band/a500/` 随旧 v4 删除） |
| 沪深300 全收益 + 滚动 PE（2011-06 起） | `data/_bt_caches/csi_H00300.csv`、`csi_000300.csv` | `v4_erp_band/erp.py --refresh` |
| 10Y 中债（利差否决器） | `data/_bt_caches/bond10y_m.csv` | 股息策略生产（只读） |
| A500 PE 兜底（同源、无 close） | `data/_a500_pe_hist.csv` | `vol_legs/data.py` |

本模块**不写**这些文件（AGENTS §8.3：跨策略共享数据只读）。无网络请求 → 每日脚本可离线跑。

## 5. 已知失效点（文档 §6 + 实测）

1. **回溯偏差**：A500 的 10 年 PE 序列不存在，2 年分位在统计上不是「10 年分位」
   → 用沪深300 交叉校验兜底（§3-②）；沪深300 档没有这个问题（真 10 年）。
2. **便宜不等于见底**：分位 0–20% 之后 1 年内最坏路径仍可回撤 40%+ → 阶梯与月频帽不能省。
3. **窗口决定结论**：分位随窗口变化极大，`WINDOW_DAYS` 写死，不许中途换。
4. **政策脉冲**（2024-09 两周利差从 7.1 砸到 4.9）：任何分位信号瞬间失效，靠「3 年不用的钱」扛。
5. **weekly 定档的代价**：分位在档位边界附近会来回抖，换手比季度高一个量级；
   实跑数字见 `backtest/_v6_pe_band_bt.md` §1（weekly vs monthly vs quarterly 三档对照）。

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

_HERE = os.path.dirname(os.path.abspath(__file__))      # .../vol_legs/v6_pe_band
_VL = os.path.dirname(_HERE)                            # strategies/vol_legs
_ROOT = os.path.dirname(os.path.dirname(_VL))           # 仓库根

if _VL not in sys.path:
    sys.path.insert(0, _VL)

import engine  # noqa: E402  —— 绩效口径复用主策略
import rule    # noqa: E402  —— 现金收益 / 申赎费 / A500 战略带（只读，不改）

LABEL = "V6 估值分位仓位带"

# 标的注册表：规则与标的无关，这两个只是「本地有足够 PE 历史」的落地样本。
# `cross` = 交叉校验用哪把尺子（None = 没有第二把，交叉校验自动关闭）。
UNIVERSES = {
    "a500": dict(name="中证A500", code="000510", cross="hs300"),
    "hs300": dict(name="沪深300", code="000300", cross=None),
}


def universe_info(key=None, p=None):
    key = key or (p or CFG)["UNIVERSE"]
    if key not in UNIVERSES:
        raise ValueError(f"未知标的 {key}；可选 {sorted(UNIVERSES)}")
    return dict(UNIVERSES[key], key=key)


def label(key=None, p=None):
    u = universe_info(key, p)
    return f"{LABEL}（{u['name']}）"

_CACHE = os.path.join(_ROOT, "data", "_bt_caches", "csi_000510.csv")
_PE_ALT = os.path.join(_ROOT, "data", "_a500_pe_hist.csv")
_HS300_TR = os.path.join(_ROOT, "data", "_bt_caches", "csi_H00300.csv")
_HS300_PE = os.path.join(_ROOT, "data", "_bt_caches", "csi_000300.csv")
_BOND10Y = os.path.join(_ROOT, "data", "_bt_caches", "bond10y_m.csv")

DIV_YIELD = 0.025          # A500 股息率（文档 §5：2026-09-30 滚动 2.44%；取 2.5%）

SOURCE = {}
WARN = []


def _warn(msg):
    WARN.append(msg)


# ---------------------------------------------------------------- 1. 参数（唯一权威源）
# 文档 §7 的 JSON 原样镜像（键名改成大写，语义不动）+ 实现必需的口径常量。
CFG = {
    # ---- 标的与频率（2026-11-03 通用化：改名 v6_pe_band、定档默认每周）
    "UNIVERSE": "a500",            # a500（文档原型，PE 仅 2024-09 起）/ hs300（PE 2011-06 起）
    # ---- §7 原表
    "GATE": "pe_ttm_percentile_self",
    "WINDOW_YEARS": 10,
    "DATA_SOURCE": "csindex_monthly",
    "BAND_EDGES": [0.20, 0.40, 0.60, 0.80],
    "BAND_WEIGHTS": [1.00, 0.70, 0.50, 0.30, 0.10],
    "SELL_CONFIRM_MONTHS": 2,
    "LADDER_DD": [8.0, 16.0, 24.0],
    "LADDER_UNITS": [1.0, 1.5, 2.0],
    "UNIT_PCT_POOL": 20.0,
    "MAX_TRANCHE_PER_MONTH": 1,
    "RSI_PERIOD": 14,
    "RSI_BELOW": 40.0,
    "REBAL_FREQ": "weekly",        # weekly（默认）/ monthly / quarterly（文档原口径）
    "BLACKOUT_MONTHS": [6, 12],
    "BLACKOUT_FREQ": "quarterly",  # 6/12 月顺延只在 quarterly 生效；"all" = 所有频率都顺延
    "SPREAD_ROLE": "veto_only",
    "CHANNEL": "largest_etf_or_class_A_feeder",
    "ENHANCED_INDEX_ALLOWED": False,
    # ---- 实现口径（文档没写死，见模块 docstring §3）
    "WINDOW_DAYS": 2520,       # = WINDOW_YEARS × 252，写死
    "MIN_OBS": 250,            # 分位最小样本（不足 → null，不决策）
    "PULLBACK_WIN": 250,       # dd 分母窗口
    "NEWHIGH_WIN": 252,        # 52 周新高窗口
    "CROSS_CHECK": "hs300",    # 交叉校验用哪把尺子；None = 关闭
    "CROSS_RULE": "take_lower",  # take_lower（默认）/ one_step_down / ignore
    # 利差否决器（文档 §2 纪律三的一句话 → 三种可执行读法，见 monthly_signals 文档串）
    "VETO_MODE": "level_gap",  # level_gap（默认，可触发）/ diff_literal（原文差分读法，实测 0 次）/ off
    "VETO_GAP": 0.10,          # level_gap: ERP 分位 − EY 分位 ≥ 该值（0.10 ≈ 16% 的月份）
    "VETO_CONFIRM_M": 2,       # 「连续 N 个月读数成立」的 N
    "VETO_MIN_M": 24,          # 扩张分位的最小样本（月）
    "VETO_LOOKBACK_M": 2,      # 仅 diff_literal 用：差分的回看月数
    "DIV_YIELD": DIV_YIELD,    # 全收益近似的年化股息率
    "RF": rule.CASH_ANNUAL,    # 空仓收益（货基 1.40%，与主策略同源）
    "FEE_BUY": rule.FEE_BUY,
    "FEE_SELL": rule.FEE_SELL,
    "START": None,             # None = 第一个有分位的交易日
}


def params(**kw):
    """参数打包：回测 / 敏感性扫描用 `**{...}` 覆盖。"""
    p = dict(CFG)
    p.update(kw)
    return p


# ---------------------------------------------------------------- 2. 数据
def _read_csv(path, what):
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"缺 {os.path.relpath(path, _ROOT)}（{what}）→ 沪深300 侧跑 "
            f"python strategies/vol_legs/v4_erp_band/erp.py --refresh；"
            f"A500 缓存已冻结（原 a500 子变种随旧 v4 删除）")
    df = pd.read_csv(path, parse_dates=["date"]).dropna(subset=["close"])
    df = df.sort_values("date").drop_duplicates("date", keep="last")
    return df.set_index("date")


def load_y10():
    """10Y 中债月度（% 数值）。缺文件 → None（否决器不生效，不估算补位）。"""
    if not os.path.exists(_BOND10Y):
        _warn(f"缺 {os.path.basename(_BOND10Y)} → 利差否决器不生效")
        return None
    df = pd.read_csv(_BOND10Y)
    df.columns = [str(c).strip().lstrip("\ufeff") for c in df.columns]
    dcol = df.columns[0]
    df[dcol] = pd.to_datetime(df[dcol])
    return df.set_index(dcol)["close"].astype("float64").sort_index()


def load_frame(mode="a500", start=None):
    """-> DataFrame(index=交易日, cols=close/pe/tr)。

    mode="a500" : A500 价格指数（2005 起含回溯）+ 滚动 PE（2024-09 起）+ 股息计提的全收益近似
    mode="hs300": 沪深300 全收益 H00300 + 000300 滚动 PE（两列 peg 交叉校验）
    """
    if mode == "hs300":
        tr = _read_csv(_HS300_TR, "沪深300 全收益 H00300")
        pe_df = _read_csv(_HS300_PE, "沪深300 价格指数 000300（PE 源）")
        both = pd.concat([tr["peg"].rename("a"), pe_df["peg"].rename("b")],
                         axis=1, join="inner").dropna()
        if not len(both):
            raise ValueError("H00300 与 000300 的 peg 无重叠日期")
        diff = float((both["a"] - both["b"]).abs().max())
        if diff > 1e-6:
            raise ValueError(f"H00300 与 000300 的 peg 不一致（max|Δ|={diff:.4g}）")
        df = pd.DataFrame({"close": tr["close"].astype("float64"),
                           "pe": pe_df["peg"].astype("float64")}).sort_index()
        # 缓存里夹着个别非交易日的空行（如 2014-01-01 有日期无收盘）→ 直接剔除，
        # 否则收益率会出现 NaN 并沿着净值一路传播（2014 年后的逐年全部失效）。
        df = df[df["close"].notna() & (df["close"] > 0)]
        df["tr"] = df["close"]
        SOURCE["hs300_px"] = (f"中证官网 H00300 全收益 · {df.index[0]:%Y-%m-%d} ~ "
                              f"{df.index[-1]:%Y-%m-%d} · {len(df)} 根")
        SOURCE["hs300_pe"] = (f"中证官网 000300 滚动 PE · {both.index[0]:%Y-%m-%d} ~ "
                              f"{both.index[-1]:%Y-%m-%d}")
    else:
        df = _read_csv(_CACHE, "中证A500 000510 收盘 + 滚动 PE")
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df["pe"] = pd.to_numeric(df.get("peg"), errors="coerce")
        if df["pe"].notna().sum() == 0 and os.path.exists(_PE_ALT):
            alt = pd.read_csv(_PE_ALT, parse_dates=["date"])
            df = df.drop(columns=["pe"]).merge(alt[["date", "peg"]].rename(
                columns={"peg": "pe"}), on="date", how="left")
            _warn("csi_000510.csv 无 PE → 暂用 data/_a500_pe_hist.csv 兜底")
        df = df[["close", "pe"]]
        df = df[df["close"].notna() & (df["close"] > 0)]
        ret = df["close"].pct_change().fillna(0.0)
        df["tr"] = df["close"].iloc[0] * (1.0 + ret + float(CFG["DIV_YIELD"]) / 252.0).cumprod()
        SOURCE["a500_px"] = (f"新浪 sh{UNIVERSES['a500']['code']} 收盘（2005 起含回溯）· "
                             f"{df.index[0]:%Y-%m-%d} ~ {df.index[-1]:%Y-%m-%d} · {len(df)} 根")
        pe_ok = df["pe"].dropna()
        SOURCE["a500_pe"] = (f"中证官网 indexCsiDsPe 滚动 PE · {pe_ok.index[0]:%Y-%m-%d} ~ "
                             f"{pe_ok.index[-1]:%Y-%m-%d} · {len(pe_ok)} 个交易日"
                             if len(pe_ok) else "PE 全空")
    if start is not None:
        df = df.loc[pd.Timestamp(start):]
    return df.sort_index()


def data_asof(frame=None):
    df = load_frame() if frame is None else frame
    pe = df["pe"].dropna()
    return df.index[-1], (pe.index[-1] if len(pe) else None)


# ---------------------------------------------------------------- 3. 指标
def pe_percentile(pe, W=None, MIN_OBS=None):
    """PE 序列 -> DataFrame(pct, n_eff, window_short)。`pct = count(win <= PE_t)/n_eff`。

    `t` 是 **PE 序列里的位置**（不是收盘价序列）。`n_eff = min(W, t+1)`：
    历史不足 10 年时自适应取全部可用历史，并把 `window_short` 标 True
    （A500 的 PE 只有 2024-09 起，所以当前**必然** window_short —— 这是文档 §6-1 的已知失效点，
    不是我们的 bug；用沪深300 交叉校验兜底，见 `cross_band()`）。
    """
    p = params()
    if W is not None:
        p["WINDOW_DAYS"] = int(W)
    if MIN_OBS is not None:
        p["MIN_OBS"] = int(MIN_OBS)
    w, mn = int(p["WINDOW_DAYS"]), int(p["MIN_OBS"])
    s = pe.dropna().astype("float64")
    arr = s.to_numpy()
    n = len(arr)
    pct = [None] * n
    eff = [0] * n
    for i in range(n):
        n_eff = min(w, i + 1)
        eff[i] = n_eff
        if n_eff < mn:
            continue
        pct[i] = float((arr[i - n_eff + 1:i + 1] <= arr[i]).sum()) / float(n_eff)
    out = pd.DataFrame({"pct": pct, "n_eff": eff}, index=s.index)
    out["window_short"] = out["n_eff"] < w
    out["pe"] = s
    return out


def band_index(pct, p=None):
    """分位 -> 档位序号 0..4（0 = 最便宜）。

    边界：`pct ≤ 0.20 → 0`、`(0.20, 0.40] → 1`、`(0.40, 0.60] → 2`、
    `[0.60, 0.80) → 3`、`pct ≥ 0.80 → 4`（见模块 docstring §3-①）。
    """
    p = p or params()
    if pct is None or pct != pct:
        return None
    e = p["BAND_EDGES"]
    for i, edge in enumerate(e[:-1]):
        if pct <= edge:
            return i
    return len(e) - 1 if pct >= e[-1] else len(e) - 2


def band_weight(pct, p=None):
    """分位 -> 目标仓位（占本策略资金）。"""
    p = p or params()
    i = band_index(pct, p)
    return None if i is None else float(p["BAND_WEIGHTS"][i])


def one_step_down(w, p=None):
    """仓位降一档（1.0→0.7→0.5→0.3→0.1）。"""
    p = p or params()
    ws = [float(x) for x in p["BAND_WEIGHTS"]]
    for i, x in enumerate(ws):
        if abs(w - x) < 1e-9:
            return ws[i + 1] if i + 1 < len(ws) else ws[-1]
    lower = [x for x in ws if x < w - 1e-9]
    return max(lower) if lower else ws[-1]


def rsi(close, n=None):
    """Wilder RSI（`ewm(alpha=1/n, adjust=False)` 与 Wilder 平滑等价）。"""
    n = int(params()["RSI_PERIOD"] if n is None else n)
    d = close.diff()
    up = d.clip(lower=0.0)
    dn = (-d).clip(lower=0.0)
    au = up.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    ad = dn.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    out = 100.0 - 100.0 / (1.0 + au / ad.replace(0.0, np.nan))
    out = out.where(ad > 0, 100.0).where(au > 0, 0.0)
    return out


def drawdown(close, win=None):
    """dd = 1 − close / 近 250 交易日最高 close（正值 = 跌了百分之多少）。"""
    win = int(params()["PULLBACK_WIN"] if win is None else win)
    hi = close.rolling(win, min_periods=win).max()
    return 1.0 - close / hi


def new_high(close, win=None):
    """close 是否创 52 周新高（含当日）。"""
    win = int(params()["NEWHIGH_WIN"] if win is None else win)
    hi = close.rolling(win, min_periods=win).max()
    return (close >= hi - 1e-12) & hi.notna()


def rsi_trigger(rsi_s, p=None):
    """RSI14 < 40，或从 <40 回升的首个交易日。"""
    p = p or params()
    th = float(p["RSI_BELOW"])
    below = rsi_s < th
    rise = (rsi_s >= th) & below.shift(1, fill_value=False)
    return (below | rise).fillna(False)


def indicators(frame, p=None, universe=None):
    """收盘序列 -> 指标表（close/tr/pe/dd/rsi14/rsi_ok/high52w/pct/pct_h/n_eff/y10）。"""
    p = p or params()
    u = universe_info(universe, p)
    out = pd.DataFrame(index=frame.index)
    out["close"] = frame["close"].astype("float64")
    out["tr"] = frame["tr"].astype("float64") if "tr" in frame else out["close"]
    out["pe"] = frame["pe"].astype("float64")
    out["dd"] = drawdown(out["close"], p["PULLBACK_WIN"]) * 100.0
    out["rsi14"] = rsi(out["close"], p["RSI_PERIOD"])
    out["rsi_ok"] = rsi_trigger(out["rsi14"], p)
    out["high52w"] = new_high(out["close"], p["NEWHIGH_WIN"])
    pc = pe_percentile(out["pe"], p["WINDOW_DAYS"], p["MIN_OBS"])
    out["pct"] = pc["pct"].reindex(out.index)
    out["n_eff"] = pc["n_eff"].reindex(out.index)
    out["window_short"] = pc["window_short"].reindex(out.index)
    # 交叉尺子：只有「本地存在第二个同级长历史估值源」时才有（见 docstring §3-②）
    cross = u.get("cross") if p.get("CROSS_CHECK") else None
    if cross and cross != u["key"]:
        try:
            other = load_frame(cross)
            pch = pe_percentile(other["pe"], p["WINDOW_DAYS"], p["MIN_OBS"])
            out["pct_h"] = pch["pct"].reindex(out.index)
        except (OSError, ValueError, KeyError) as exc:
            _warn(f"{cross} 交叉校验不可用（{type(exc).__name__}: {exc}）")
            out["pct_h"] = np.nan
    else:
        out["pct_h"] = np.nan
    # 利差否决器
    y10 = load_y10()
    if y10 is None or p.get("SPREAD_ROLE") != "veto_only":
        out["y10"] = np.nan
    else:
        out["y10"] = y10.reindex(out.index, method="ffill").to_numpy()
    return out


# ---------------------------------------------------------------- 4. 月度信号（利差否决 / 降档确认）
def _month_end(df):
    for freq in ("ME", "M"):
        try:
            return df.resample(freq).last()
        except ValueError:
            continue
    raise RuntimeError("本机 pandas 不支持月末重采样")


def _expanding_pct(s, min_periods):
    """扩张窗口分位（含当期、`<=` 比较，无前视）。月频用，避免滚动窗口早期的空窗。

    先 `dropna` 再算，否则窗口里的 NaN 会让 `a <= a[-1]` 判 False，把分位系统性压低。
    """
    v = s.dropna()
    out = v.expanding(int(min_periods)).apply(lambda a: float((a <= a[-1]).mean()), raw=True)
    return out.reindex(s.index)


def monthly_signals(ind, p=None):
    """月末序列 + 利差否决标记（文档 §2 纪律三：股债利差**只做否决**，没有加分权）。

    文档原文一句话：「分位没降、但利差被 10Y 下行撑高 → 这一档便宜是利率借来的，按低一档执行，
    且需连续 2 个月读数成立」。这句话**没有可执行定义**，本模块给出三种读法（`VETO_MODE`）：

    | 模式 | 判据 | 14 年实测（沪深300） |
    |---|---|---|
    | `diff_literal` | 月度差分：`ERP 升` 且 `PE 分位不降` 且 `10Y 降`，连续 2 月 | 单月 4 次、**连续 2 月 0 次 → 永不触发** |
    | `level_gap`（**默认**） | 水平缺口：`ERP 分位 − EY 分位 ≥ VETO_GAP`，连续 2 月 | 单月 18 次（16%）、连续 2 月 14 次（12%） |
    | `off` | 关闭 | — |

    **为什么不用差分读法**：`ERP = 1/PE − y10`，而 `1/PE` 的月度波动（0.1~1pp）比 10Y 的月度波动
    （0.02~0.05pp）大一个量级，所以「ERP 上升」几乎等价于「PE 下跌」，与「分位不降」在数学上
    接近互斥 —— 14 年里三个条件同月只成立过 4 次，再叠加「连续 2 个月」就归零，这个否决器
    实际上是个装饰品（`diff_literal` 保留仅为可复现）。

    **水平缺口读法为什么对**：`ERP 分位` 高说明利差大，`EY 分位`（= 1/PE 的分位）高说明估值真便宜。
    如果利差看着便宜、但盈利收益率并不便宜，那这个缺口就是 **10Y 下行贡献的**，正是原文
    「利率借来的便宜」。数学上：`ERP 分位 − EY 分位` 的中位数 ≈ 0（无系统性偏移），
    只有利率显著走低时才转正，阈值 `VETO_GAP=0.10` 对应 16% 的月份。

    任一月末缺 PE 或 10Y → 该月条件为 False（不否决、不估算补位）。
    """
    p = p or params()
    cols = ["pct", "pct_h", "pe", "y10", "close", "dd", "rsi14"]
    me = _month_end(ind[[c for c in cols if c in ind.columns]])
    me["ey"] = 100.0 / me["pe"]
    me["erp"] = me["ey"] - me["y10"]
    mode = str(p.get("VETO_MODE", "level_gap"))
    no_data = (p.get("SPREAD_ROLE") != "veto_only" or mode == "off"
               or me["y10"].isna().all() or me["pe"].isna().all())
    if no_data:
        me["ey_pct"] = np.nan
        me["erp_pct"] = np.nan
        me["veto_gap"] = np.nan
        me["veto"] = False
        return me

    confirm = max(1, int(p["VETO_CONFIRM_M"]))
    # 两个分位必须**同起点同长度**：EY 从 2011 起、ERP 要等 10Y 从 2015-06 起，
    # 若各算各的，EY 的历史里混进 2011-2014 的高 EY 段，会把 ey_pct 系统性压低、
    # 让 gap 凭空 +0.2（这不是「利率借来的便宜」，是样本起点错位）。
    mm = int(p["VETO_MIN_M"])
    mask = me["erp"].notna() & me["ey"].notna()
    sub = me.loc[mask]
    me["erp_pct"] = _expanding_pct(sub["erp"], mm).reindex(me.index)
    me["ey_pct"] = _expanding_pct(sub["ey"], mm).reindex(me.index)
    me["veto_gap"] = me["erp_pct"] - me["ey_pct"]

    if mode == "diff_literal":
        k = int(p["VETO_LOOKBACK_M"])
        d = me.diff(k)
        cond = (d["erp"] > 0) & (d["pct"] >= 0) & (d["y10"] < 0)
    elif mode == "level_gap":
        cond = me["veto_gap"] >= float(p["VETO_GAP"])
    else:
        raise ValueError(f"VETO_MODE 只支持 level_gap / diff_literal / off，收到 {mode!r}")

    cond = cond.fillna(False).astype(bool)
    if confirm > 1:      # 「连续 N 个月读数成立」—— 当期及前 N-1 个月都成立
        me["veto"] = (cond.astype("float64").rolling(confirm, min_periods=confirm).sum()
                      >= confirm).fillna(False).astype(bool)
    else:
        me["veto"] = cond
    return me


def sell_confirmed(me, date, p=None):
    """文档 §3：分位 ≥80% 且**连续 2 个月**成立 → 才降到 10% 观察仓。

    `date` 是季度末当天（= 当月月末，无前视）；取当月与上月末两个读数。
    """
    p = p or params()
    n = int(p["SELL_CONFIRM_MONTHS"])
    if n <= 1:
        return True
    series = me["pct"].dropna()
    if not len(series):
        return False
    key = (date.year, date.month)
    pos = [(y, m) for y, m in zip(series.index.year, series.index.month)]
    if key not in pos:
        return False
    i = pos.index(key)
    if i + 1 < n:
        return False
    window = series.to_numpy()[i - n + 1:i + 1]
    return bool((window >= float(p["BAND_EDGES"][-1])).all())


FREQS = ("weekly", "monthly", "quarterly")


def _period_key(idx, freq):
    """把交易日映射到所属周期标签（周 = 周一~周日）。"""
    if freq == "weekly":
        return idx.to_period("W")
    if freq == "monthly":
        return idx.to_period("M")
    if freq == "quarterly":
        return idx.to_period("Q")
    raise ValueError(f"REBAL_FREQ 只支持 {FREQS}，收到 {freq!r}")


def _is_period_final(day, freq):
    """`day` 是否是该周期的最后一个交易日（工作日近似，节假日以交易所日历为准）。"""
    d = pd.Timestamp(day).normalize()
    nxt = d + pd.offsets.BDay(1)
    if freq == "weekly":
        return d.weekday() == 4 or nxt.to_period("W") != d.to_period("W")
    if freq == "monthly":
        return (nxt.month != d.month) or (nxt.year != d.year)
    if freq == "quarterly":
        return (d.month in (3, 6, 9, 12)) and ((nxt.month != d.month) or (nxt.year != d.year))
    raise ValueError(f"REBAL_FREQ 只支持 {FREQS}，收到 {freq!r}")


def _use_blackout(p, freq):
    """6/12 月顺延是否生效：`BLACKOUT_FREQ="all"` 或频率本身是 quarterly。"""
    return str(p.get("BLACKOUT_FREQ", "quarterly")) == "all" or freq == "quarterly"


def decision_mask(index, freq=None, p=None, finalize=None):
    """定档日掩码：每个周期（周 / 月 / 季）的**最后一个交易日**。

    `finalize` 控制最后一行（当前周期尚未走完）算不算定档日：`None` = 按工作日历判断
    （周看是否周五、月/季看是否月末），回测与每日脚本都安全，不需要额外传参。
    """
    p = p or params()
    freq = freq or p["REBAL_FREQ"]
    idx = pd.DatetimeIndex(index)
    keys = _period_key(idx, freq)
    out = np.zeros(len(idx), dtype=bool)
    for i in range(len(idx) - 1):
        out[i] = keys[i] != keys[i + 1]
    if len(idx):
        out[-1] = bool(_is_period_final(idx[-1], freq)) if finalize is None else bool(finalize)
    return pd.Series(out, index=idx, dtype=bool)


def is_decision_today(day=None, freq=None, p=None):
    """daily 脚本用：今天是否定档日。"""
    p = p or params()
    freq = freq or p["REBAL_FREQ"]
    d = pd.Timestamp.today().normalize() if day is None else pd.Timestamp(day)
    return bool(_is_period_final(d, freq))


def next_decision_day(day=None, freq=None, p=None):
    """下一个**实际生效**的定档日（已跳过 6/12 月调样顺延）。"""
    p = p or params()
    freq = freq or p["REBAL_FREQ"]
    black = set(int(m) for m in p["BLACKOUT_MONTHS"])
    use_black = _use_blackout(p, freq)
    cur = pd.Timestamp.today().normalize() if day is None else pd.Timestamp(day)
    for _ in range(500):
        cur = cur + pd.offsets.BDay(1)
        if not _is_period_final(cur, freq):
            continue
        if use_black and int(cur.month) in black:
            continue
        return cur
    return None


def band_series(ind, me, p=None):
    """逐日目标带（只在定档日更新；`REBAL_FREQ` 决定周/月/季，6/12 顺延见 `_use_blackout`）。

    列：`dec`（是否定档日）/ `target`（定档后的目标仓位）/ `w_a`/`w_h`（两把尺子各自的档位）
    """
    p = p or params()
    freq = str(p["REBAL_FREQ"])
    dec = decision_mask(ind.index, freq, p)
    blackout = set(int(m) for m in p["BLACKOUT_MONTHS"]) if _use_blackout(p, freq) else set()
    w_a = ind["pct"].map(lambda x: band_weight(x, p))
    w_h = (ind["pct_h"].map(lambda x: band_weight(x, p)) if ind["pct_h"].notna().any()
           else pd.Series(np.nan, index=ind.index))
    target = [None] * len(ind)
    rows = []
    cur = None
    for i, d in enumerate(ind.index):
        target[i] = cur
        if not bool(dec.iloc[i]):
            continue
        if int(d.month) in blackout:          # 调样当月不做决定
            rows.append(dict(date=d, action="顺延", month=d.month, w_a=w_a.iloc[i],
                             w_h=w_h.iloc[i], w=None, veto=False, note="调样月顺延"))
            continue
        wa, wh = w_a.iloc[i], w_h.iloc[i]
        if wa is None or (isinstance(wa, float) and wa != wa):
            rows.append(dict(date=d, action="无分位", month=d.month, w_a=wa, w_h=wh, w=None,
                             veto=False, note="PE 样本不足"))
            continue
        rule_ = str(p["CROSS_RULE"])
        w = float(wa)
        cross = ""
        if wh is not None and isinstance(wh, float) and wh == wh and rule_ != "ignore":
            if abs(wh - wa) > 1e-9:
                u = universe_info(None, p)
                cross = f"交叉不一致({u['name']} {wa:.0%} / {UNIVERSES[u['cross']]['name']} {wh:.0%})"
                w = min(wa, wh) if rule_ == "take_lower" else one_step_down(wa, p)
        key = (d.year, d.month)
        vrow = [r for r in me.index if (r.year, r.month) == key]
        veto = bool(me.loc[vrow[0], "veto"]) if vrow else False
        if veto:
            before = w
            w = one_step_down(w, p)
            cross = (cross + " · " if cross else "") + f"利差否决 {before:.0%}→{w:.0%}"
        if abs(w - float(p["BAND_WEIGHTS"][-1])) < 1e-9 and not sell_confirmed(me, d, p):
            w = float(p["BAND_WEIGHTS"][-2])       # 未连续 2 个月 → 停在 30% 档
            cross = (cross + " · " if cross else "") + "高估未确认(>2 月)"
        cur = w
        target[i] = cur
        rows.append(dict(date=d, action="定档", month=d.month, w_a=wa, w_h=wh, w=w, veto=veto,
                         note=cross or "同档"))
    out = pd.DataFrame({"dec": dec, "target": target, "w_a": w_a, "w_h": w_h})
    return out, pd.DataFrame(rows)


# ---------------------------------------------------------------- 5. 账户模拟
def simulate(ind, bands, p=None, init="band"):
    """逐日复算 0–100% 仓位 -> dict。

    执行纪律（与主策略一致）：**T 日收盘决策 → T+1 收盘成交**，无前视。
    加仓只来自「阶梯 + RSI 扳机」（且不超过目标带）；减仓只在季度决策日卖到目标带。
    """
    p = p or params()
    idx = ind.index
    n = len(idx)
    close = ind["tr"].to_numpy(dtype="float64")
    dd = ind["dd"].to_numpy(dtype="float64")
    rsi_ok = ind["rsi_ok"].to_numpy(dtype=bool)
    high = ind["high52w"].to_numpy(dtype=bool)
    tgt = bands["target"].to_numpy(dtype="object")
    dec = bands["dec"].to_numpy(dtype=bool)
    rf_d = (1.0 + float(p["RF"])) ** (1.0 / 252.0) - 1.0
    fee_b, fee_s = float(p["FEE_BUY"]), float(p["FEE_SELL"])
    unit = float(p["UNIT_PCT_POOL"]) / 100.0
    dd_lev = [float(x) for x in p["LADDER_DD"]]
    dd_unit = [float(x) for x in p["LADDER_UNITS"]]
    max_per_month = int(p["MAX_TRANCHE_PER_MONTH"])

    w = 0.0                       # 当前仓位（占本策略资金）
    pending = None                # dict(w=目标仓位, reason=str, sig=i)
    nav = [1.0] * n
    weights = [0.0] * n
    triggered = [False] * len(dd_lev)
    month_cnt, month_key = 0, None
    trades, events = [], []
    target_now = None
    started = False
    last_q = None

    def _has_t(v):
        """目标带是否可用（`target` 是 object 数组，空值可能是 None 也可能是 NaN）。"""
        return v is not None and (v == v)

    for i in range(n):
        d = idx[i]
        # ---- 1) 执行昨日挂单（T+1 收盘）
        fee = 0.0
        if pending is not None:
            new_w = float(pending["w"])
            dw = new_w - w
            if abs(dw) > 1e-9:
                fee = abs(dw) * (fee_b if dw > 0 else fee_s)
                trades.append(dict(signal_date=idx[pending["sig"]], exec_date=d,
                                   side="buy" if dw > 0 else "sell", w_from=w, w_to=new_w,
                                   dw=dw, price=float(close[i]), reason=pending["reason"]))
                w = new_w
            pending = None

        # ---- 2) 净值（当日涨跌用**成交前**的仓位，费用在成交后扣）
        w_prev = weights[i - 1] if i else 0.0
        r = (close[i] / close[i - 1] - 1.0) if i else 0.0
        nav[i] = nav[i - 1] * (1.0 + w_prev * r + (1.0 - w_prev) * rf_d) * (1.0 - fee)
        weights[i] = w

        if i >= n - 1:
            break

        # ---- 3) 52 周新高 → 阶梯计数归零（文档 §2 纪律二）
        if bool(high[i]) and any(triggered):
            triggered = [False] * len(dd_lev)
            events.append(dict(date=d, kind="reset", note="close 创 52 周新高，阶梯归零"))

        # ---- 4) 起手：第一个有目标带的交易日
        if not started and _has_t(tgt[i]):
            started = True
            target_now = float(tgt[i])
            if init == "band":                 # 实盘口径：这笔钱已经在场，直接摆到带位
                if target_now > w + 1e-9:
                    pending = dict(w=target_now, reason="起手摆到带位", sig=i)
                events.append(dict(date=d, kind="init", note=f"起手 {target_now:.0%}"))
                continue
            events.append(dict(date=d, kind="init", note=f"空仓起步，目标带 {target_now:.0%}"))

        # ---- 5) 季度决策：更新目标带；只减持、不主动加（加仓归阶梯）
        if bool(dec[i]) and _has_t(tgt[i]):
            target_now = float(tgt[i])
            if target_now < w - 1e-9:
                pending = dict(w=target_now, reason=f"季末降带 {w:.0%}→{target_now:.0%}", sig=i)
                events.append(dict(date=d, kind="band_down", note=f"降到 {target_now:.0%}"))
                continue
            events.append(dict(date=d, kind="band_hold",
                               note=f"目标带 {target_now:.0%}（现仓 {w:.0%}）"))

        # ---- 6) 阶梯加仓（dd 阈值 + RSI 扳机 + 目标带上限 + 月度帽）
        if target_now is None or w >= target_now - 1e-9:
            continue
        key = (d.year, d.month)
        if key != month_key:
            month_key, month_cnt = key, 0
        if month_cnt >= max_per_month:
            continue
        hit = None
        for j, lev in enumerate(dd_lev):
            if not triggered[j] and dd[i] >= lev:
                hit = j
        if hit is None or not bool(rsi_ok[i]):
            continue
        want = min(dd_unit[hit] * unit, target_now - w)
        if want <= 1e-9:
            continue
        triggered[hit] = True
        month_cnt += 1
        pending = dict(w=w + want, reason=f"阶梯 dd≥{dd_lev[hit]:.0f}% 加 {dd_unit[hit]:g} 份", sig=i)
        events.append(dict(date=d, kind="ladder",
                           note=f"dd {dd[i]:.1f}% ≥ {dd_lev[hit]:.0f}% · RSI {ind['rsi14'].iloc[i]:.1f}"
                                f" → {w + want:.0%}"))

    nav_s = pd.Series(nav, index=idx, dtype="float64", name="nav")
    w_s = pd.Series(weights, index=idx, dtype="float64", name="w")
    bh = pd.Series(close / close[0], index=idx, dtype="float64", name="bh")
    dw = w_s.diff().fillna(w_s.iloc[0]).abs()
    return dict(nav=nav_s, w=w_s, bh=bh, trades=trades, events=events,
                exposure=float(w_s.mean()) if n else 0.0,
                turnover=float(dw.sum()), n_trades=len(trades),
                n_round=sum(1 for t in trades if t["side"] == "sell"),
                target=tgt, dec=dec, init=init)


# ---------------------------------------------------------------- 6. 绩效
def perf(nav, rf=None):
    p = params()
    m = engine.metrics(nav, rf=float(p["RF"] if rf is None else rf))
    m["calmar"] = (m["ann"] / abs(m["mdd"])) if m["mdd"] else None
    return m


def evaluate(ind, p=None, init="band"):
    p = p or params()
    me = monthly_signals(ind, p)
    bands, rows = band_series(ind, me, p)
    sim = simulate(ind, bands, p, init=init)
    sim["bands"] = bands
    sim["me"] = me
    sim["band_rows"] = rows.to_dict("records")
    sim["m"] = perf(sim["nav"])
    sim["mbh"] = perf(sim["bh"])
    sim["excess"] = sim["m"]["ann"] - sim["mbh"]["ann"]
    return sim


def yearly(sim):
    rows, prev_n, prev_b = [], None, None
    for y in sorted({d.year for d in sim["nav"].index}):
        nv, bh, wt = (sim["nav"].loc[str(y)], sim["bh"].loc[str(y)], sim["w"].loc[str(y)])
        if len(nv) < 20:
            continue
        n0 = prev_n if prev_n else float(nv.iloc[0])
        b0 = prev_b if prev_b else float(bh.iloc[0])
        rows.append(dict(year=y, ret=float(nv.iloc[-1]) / n0 - 1.0,
                         ret_bh=float(bh.iloc[-1]) / b0 - 1.0,
                         exp=float(wt.mean()), n=int((wt.diff().fillna(0) != 0).sum())))
        prev_n, prev_b = float(nv.iloc[-1]), float(bh.iloc[-1])
    return rows


def latest(frame=None, p=None, universe=None):
    """当前读数（每日脚本 / 自检共用）。"""
    p = p or params()
    u = universe_info(universe, p)
    p = params(**p)
    p["UNIVERSE"] = u["key"]
    df = load_frame(u["key"]) if frame is None else frame
    ind = indicators(df, p, u["key"])
    me = monthly_signals(ind, p)
    bands, rows = band_series(ind, me, p)
    last = ind.iloc[-1]
    tgt = bands["target"].iloc[-1]
    # PE / 分位取「最后一个有 PE 的交易日」（指数收盘可能比 PE 快一天）
    pe_ok = ind["pe"].dropna()
    pe_row = ind.loc[pe_ok.index[-1]] if len(pe_ok) else last
    pct = float(pe_row["pct"]) if pe_row["pct"] == pe_row["pct"] else None
    pct_h = float(pe_row["pct_h"]) if pe_row["pct_h"] == pe_row["pct_h"] else None
    w_a = band_weight(pct, p)
    w_h = band_weight(pct_h, p)
    return dict(date=ind.index[-1], pe_date=(pe_ok.index[-1] if len(pe_ok) else ind.index[-1]),
                universe=u, freq=str(p["REBAL_FREQ"]),
                ind=ind, me=me, bands=bands, rows=rows.to_dict("records"),
                close=float(last["close"]), pe=float(pe_row["pe"]) if len(pe_ok) else None,
                pct=pct, pct_h=pct_h,
                n_eff=int(pe_row["n_eff"]) if pe_row["n_eff"] == pe_row["n_eff"] else 0,
                window_short=(True if pe_row["window_short"] != pe_row["window_short"]
                              else bool(pe_row["window_short"])),
                dd=float(last["dd"]) if last["dd"] == last["dd"] else None,
                rsi14=float(last["rsi14"]) if last["rsi14"] == last["rsi14"] else None,
                rsi_ok=bool(last["rsi_ok"]), high52w=bool(last["high52w"]),
                y10=float(last["y10"]) if last["y10"] == last["y10"] else None,
                w_a=w_a, w_h=w_h, target=tgt,
                source={k[len(u["key"]) + 1:]: v for k, v in SOURCE.items()
                        if k.startswith(u["key"] + "_")},
                warn=list(WARN))


# ---------------------------------------------------------------- 7. 工具
def fmt_pct(x, nd=2):
    return "—" if x is None or (isinstance(x, float) and x != x) else f"{x * 100:+.{nd}f}%"


def fmt_x(x, nd=2):
    if x is None or (isinstance(x, float) and x != x):
        return "—"
    if isinstance(x, float) and math.isinf(x):
        return "∞"
    return f"{x:.{nd}f}"


def fmt_w(x):
    return "—" if x is None or (isinstance(x, float) and x != x) else f"{x * 100:.0f}%"


# ---------------------------------------------------------------- 8. 自检
def _self_check_one(key, p):
    r = latest(None, p, key)
    u = r["universe"]
    ok = r["ind"]["pct"].dropna()
    print(f"\n[{u['name']}({u['code']})] {label(key, p)} · 定档 {r['freq']}")
    print(f"  收盘 {r['ind'].index[0]:%Y-%m-%d} ~ {r['date']:%Y-%m-%d}（{len(r['ind'])} 根）")
    if len(ok):
        print(f"  首个有效 PE 分位 {ok.index[0]:%Y-%m-%d}"
              f"（n_eff={int(r['ind'].loc[ok.index[0], 'n_eff'])}）")
    pe_txt = "—" if r["pe"] is None else f"{r['pe']:.2f}"
    pct_txt = "—" if r["pct"] is None else f"{r['pct']:.1%}"
    pcth_txt = ("—（无第二把尺子）" if u.get("cross") is None
                else ("—" if r["pct_h"] is None else f"{r['pct_h']:.1%}"))
    print(f"  最新 {r['date']:%Y-%m-%d}: 收盘 {r['close']:.0f} · PE {pe_txt}"
          f"（{r['pe_date']:%Y-%m-%d}）· 分位 {pct_txt}"
          f"（n_eff={r['n_eff']}{'·window_short' if r['window_short'] else ''}）")
    print(f"  交叉尺子分位 {pcth_txt} · dd {r['dd']:.1f}% · RSI14 {r['rsi14']:.1f}")
    print(f"  在效目标带 {fmt_w(r['target'])}（今日读数 {fmt_w(r['w_a'])}"
          f"{'' if u.get('cross') is None else ' / 交叉 ' + fmt_w(r['w_h'])}）· "
          f"1 份 = {p['UNIT_PCT_POOL']:g}% · 阶梯 {p['LADDER_DD']}→{p['LADDER_UNITS']}")
    print(f"  今日定档日: {is_decision_today(None, r['freq'], p)} · "
          f"下一个定档日: {next_decision_day(None, r['freq'], p):%Y-%m-%d}")
    if r["warn"]:
        for x in r["warn"][:3]:
            print("   告警:", x)
    print(f"  来源: {r['source'].get('px')} | {r['source'].get('pe')}")

def _self_check():
    p = params()
    print(f"{LABEL} · 定档 {p['REBAL_FREQ']}（可选 {list(FREQS)}）· 标的 {sorted(UNIVERSES)}")
    for key in UNIVERSES:
        _self_check_one(key, p)


if __name__ == "__main__":
    _self_check()
