# -*- coding: utf-8 -*-
"""波动资产 · V6 变种：估值分位智能定投（**沪深300 · 月度现金流 · 份额化净值**）。

来源：`G:/tradingagents/index_strategy_backtest/strategy_A_valuation_dca.md`（策略A 编码规格 v1.0）。
一句话：**用估值分位决定「这个月投多少钱」，不是决定「买不买」**。

## 1. 它和 v1~v5 不是同一层

v1~v5 都在**七腿**（`rule.LEG_ORDER`）内部做「逐腿进出时点 / 带内档位」；本变种不做横向选腿，
它回答的是**纵向的资金节奏**：沪深300 这一条 2.5pp 的带（`rule.LEGS["hs300"]`），钱**怎么按月铺进去**。

所以本变种的基准不是「静态带」，而是**定投的对照组**（§6）：普通等额定投、全额买入、等低点。

## 2. 规则卡（逐条 → 代码）

| 编号 | 规格 | 代码 |
|---|---|---|
| R-A01 | 每月**第 1 个交易日**为决策日；`day_15` 为备选（15 日，非交易日顺延） | `decision_mask()` + `params()["DCA_RULE"]` |
| R-A02 | 分位 = `count(PE窗口 ≤ PE_t)/n_eff`，窗口 `W=2520`（10 年）、`MIN_SAMP=500`、`n_eff=min(W,t+1)`；不足则 `pct=None` → 倍数 1.0 且标记 warmup | `pe_percentile()`（**必须 `<=`**，见 §3） |
| R-A03 | 五档 2.0 / 1.5 / 1.0 / 0.5 / 0.0，阈值 20/40/60/80，**左闭右开** | `tier_index()` + `TIER_MULT` |
| R-A04 | 止盈分支（默认**关闭**）：`pct≥0.90` 且浮盈 ≥30% → 卖 50%，冷却 20 日，`pct<0.60` 才解除 | `params()["SELL_*"]` |
| R-A05 | `BUDGET_M = 2B`，`invest = min(mult×B, BUDGET_M + cash_pool)` | `simulate()` |
| R-A06 | **月度现金流模型**：每月到账 `BUDGET_M`，未投留现金池，按 `rf` **日频**计息 | `simulate()`（禁止「期初一次性总预算」） |
| R-A07 | 现金池再投资（可选）：`pct<0.20` 时额外投 `min(cash_pool, 1.0×B)` | `params()["REINVEST"]` |
| R-A08 | 份额与成本记账：`avg_cost` 只用于 R-A04 的浮盈判断，**不用于收益计算** | `simulate()` |
| R-A09 | **三套收益口径同时报告**：①份额化净值 CAGR（主）②XIRR（辅）③简单收益率（仅展示） | `perf()` / `xirr()` / `simple_ret` |
| R-A10 | 资金利用率 `utilization = Σinvest/Σ到账` 必须披露 | `simulate()` 返回 |
| R-A11 | 成本优势 `cost_edge = 平均持仓成本/期间平均市价 - 1`（负值 = 买得比均价便宜） | `simulate()` 返回 |
| R-A12 | 分档命中统计（月份数 / 投入 / 分位均值 / 其后 12 个月收益）+ 信号有效性检验 | `tier_stats()` |
| §6 | 对照组 `G0 buy_hold_monthly` / `G1 equal_dca` / `G2 smart_dca` / `G3 smart_reinvest` / `G4 lump_low` | `backtest.py` |

## 3. 三个必须写进代码而不是注释的口径

### ① `pct` 必须用 `<=`（含当日）
最低分是 `1/n_eff` 而非 0，避免「历史最低点 = 0% 分位」的边界歧义；也让 R-A03 的「左闭右开」
在 `pct=0` 时归入第一档而不是落在区间外。

### ② 份额化净值（R-A09）是**唯一**能跨组比较的口径
定投是分批现金流，「期末资产 / 累计投入」再开方**不是**年化收益率（分子分母时点不匹配）。
正确做法是「基金单位」法：每次到账按当时净值买单位，`nav_unit` 就是这套规则自己的净值曲线，
**与到账金额、到账节奏无关**。

> **推论（本变种实测的一条硬结论）**：在份额化口径下，把基础额度 `B` 放大 k 倍**不改变净值曲线**
> —— 预算与现金池同比例缩放，`units` 也同比例缩放，`nav_unit` 恒等。所以 R-A10 建议的
> 「把 B 放大到 utilization≈100% 再跑一次」在份额化口径下是**恒等变换、零信息量**；
> 它只在「简单收益率」那个错误口径下才有意义。本变种因此**只报 utilization，不做那个对照**，
> 并把这个理由写进报告（`backtest.py` §6）。

### ③ 价格用**全收益**指数 H00300，不用 ETF 前复权、不用价格指数
- 规格 §1 明令禁止用 ETF 前复权价主价格（2015-01-19 有因子错误）；
- 用价格指数（000300）会漏掉约 2pp/年的分红，`CAGR` 被系统性低估，等于把「定投」这个
  长期持有的动作白打折扣。

## 4. 数据（跨策略数据类，本变种只读）
- `data/_bt_caches/csi_H00300.csv`（`date,close,peg`）→ **全收益**收盘 = 回测价格；
- `data/_bt_caches/csi_000300.csv`（`date,close,peg`）→ **PE(滚动/TTM)** = 分位输入。

两份都由 `v4_erp_band/erp.py` 维护（`--refresh` 走中证官网 `index-perf`），**本变种不写这两个文件**
（AGENTS §8.3：跨策略共享数据只读；要刷新跑 `python strategies/vol_legs/v4_erp_band/erp.py --refresh`）。
两个文件的 `peg` 列逐日相同，本模块启动时校验（不一致直接抛）。

## 5. 已知边界（报告里必须写）
PE 只有 2011-06-28 起、3660 个交易日，故：
- 第 500 个 PE 交易日 = **2013-10-31**（`MIN_SAMP` 后首个有效分位）→ 回测最早起点 ≈ 2013-11；
- 第 2520 个（10 年）= **2022-02-15** → 默认 `W=2520` 才真正成立；
- 2013-11~2022-02 是 **warmup 段**（`n_eff` 从 500 扩到 2520，分位含义逐月漂移），
  该段绩效**单列且不得用于调参**（R-A13）。

## 6. 与其它变种的边界
- 主策略（`../`）管沪深300 这 2.5pp 带的**回撤阶梯**（跌 8/16/24% 各补几档）；
- `../v4_erp_band/` 管「沪深300 vs 国债」的 **80/50/20 大类比例**；
- `../v4_erp_band/hs300_ladder/` 管「沪深300 单腿 **0~100%** 爬梯」（信号 = ERP 通道位置）；
- **本变种管「每月往这条腿铺多少钱」**，信号 = PE 历史分位，**不看价格、不看趋势**。
四者抢同一份额度，叠加前先定谁优先（README §7）。

模拟结果，非投资建议。
"""
import json
import math
import os
import sys
from datetime import date

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_VL = os.path.dirname(_HERE)                          # strategies/vol_legs
_ROOT = os.path.dirname(os.path.dirname(_VL))         # 仓库根

if _VL not in sys.path:
    sys.path.insert(0, _VL)

import engine  # noqa: E402  —— 绩效口径复用主策略（rf 可覆盖）

LABEL = "V6 估值分位智能定投"
IDX_NAME = "沪深300"

_HS300_TR = os.path.join(_ROOT, "data", "_bt_caches", "csi_H00300.csv")
_HS300_PE = os.path.join(_ROOT, "data", "_bt_caches", "csi_000300.csv")

SOURCE = {}          # {key: 说明}，报告里标注来源与新鲜度
WARN = []


# ---------------------------------------------------------------- 1. 参数（唯一权威源）
CFG = {
    # ---- R-A02 分位
    "W": 2520,               # 10 年交易日窗口（网格 1260 / 1890 / 2520 / 3780）
    "MIN_SAMP": 500,         # 最小有效样本（约 2 年），不足 → 冷启动（倍数 1.0）
    # ---- R-A03 分档（左闭右开；阈值 4 个 → 5 档）
    "TIER_EDGES": [0.20, 0.40, 0.60, 0.80],
    "TIER_MULT": [2.0, 1.5, 1.0, 0.5, 0.0],
    # ---- R-A01 定投日历
    "DCA_RULE": "first_trading_day",     # "first_trading_day" / "day_15"
    "DAY_OF_MONTH": 15,
    # ---- R-A05 / R-A06 现金流
    "B": 10000.0,            # 月度基础额度（只影响绝对金额，不影响份额化净值）
    "BUDGET_MULT": 2.0,      # BUDGET_M = 2B
    # ---- R-A04 止盈（默认关闭，单独对照组）
    "SELL_ENABLED": False,
    "SELL_PCT": 0.90,
    "SELL_PROFIT": 0.30,
    "SELL_FRAC": 0.50,
    "SELL_COOLDOWN": 20,     # 交易日
    "SELL_RELEASE": 0.60,    # pct 回落到此以下才解除「已减仓」标记
    # ---- R-A07 现金池再投资（增强版，单独对照组）
    "REINVEST": False,
    "REINVEST_BELOW": 0.20,
    "REINVEST_MULT": 1.0,    # 额外投入 min(cash_pool, 1.0×B)
    # ---- 执行与成本（规格 §4：成交价 px×(1+s)，s=0.05%；佣金 0.025%）
    "S": 0.0005,             # 单边滑点
    "COMM": 0.00025,         # 佣金（买入）
    "SELL_FEE": 0.005,       # 赎回费（本库场外 A 类量级；规格未写，见 README §3.2）
    "LOT": 0,                # 0 = 场外小数份额（默认）；100 = 模拟 ETF 整手（对照用）
    # ---- 现金
    "RF": 0.018,             # 现金池年化（规格 §7 默认 1.8%）
    # ---- 区间
    "START": None,           # None = 第一个「分位可用」的决策日（实测 2013-11-01）
}


def params(**kw):
    """参数打包：回测 / 敏感性扫描时用 `**{...}` 覆盖，不必改模块常量。"""
    p = dict(CFG)
    p.update(kw)
    return p


# ---------------------------------------------------------------- 2. 数据
def _read_csv(path, what):
    if not os.path.exists(path):
        raise FileNotFoundError(f"缺 {os.path.relpath(path, _ROOT)}（{what}）→ 先跑 "
                                f"python strategies/vol_legs/v4_erp_band/erp.py --refresh")
    df = pd.read_csv(path, parse_dates=["date"]).dropna(subset=["close"])
    df = df.sort_values("date").drop_duplicates("date", keep="last")
    return df.set_index("date")


def load_frame(start=None):
    """-> DataFrame(index=交易日, cols=close/pe)。close = **全收益**，pe = 滚动 PE(TTM)。

    两个缓存的 `peg` 列逐日相同（同一接口同一指数族），不一致直接抛 —— 不静默取其一。
    """
    tr = _read_csv(_HS300_TR, "沪深300 全收益 H00300")
    pe_df = _read_csv(_HS300_PE, "沪深300 价格指数 000300（PE 源）")
    # 交叉校验 PE 列（中证官网对 000300 / H00300 给的 peg 相同）
    both = pd.concat([tr["peg"].rename("tr"), pe_df["peg"].rename("px")],
                     axis=1, join="inner").dropna()
    if len(both):
        diff = float((both["tr"] - both["px"]).abs().max())
        if diff > 1e-6:
            raise ValueError(f"H00300 与 000300 的 peg 不一致（max|Δ|={diff:.4g}）—— "
                             f"两个缓存被不同口径覆盖过，先刷新再跑")
        SOURCE["pe"] = (f"中证官网 000300 滚动 PE · {both.index[0]:%Y-%m-%d} ~ "
                        f"{both.index[-1]:%Y-%m-%d} · {len(both)} 个交易日")
    else:
        raise ValueError("H00300 与 000300 的 peg 无重叠日期")

    df = pd.DataFrame({"close": tr["close"].astype("float64"),
                       "pe": pe_df["peg"].astype("float64")}).sort_index()
    df = df.dropna(subset=["close"])
    SOURCE["tr"] = (f"中证官网 H00300 全收益（含分红再投）· {df.index[0]:%Y-%m-%d} ~ "
                    f"{df.index[-1]:%Y-%m-%d} · {len(df)} 根")
    if start is not None:
        df = df.loc[pd.Timestamp(start):]
    return df


def data_asof(frame=None):
    df = load_frame() if frame is None else frame
    return df.index[-1], df.dropna(subset=["pe"]).index[-1]


# ---------------------------------------------------------------- 3. 分位（R-A02）
def pe_percentile(pe, W=None, MIN_SAMP=None):
    """PE 序列 -> DataFrame(pct, n_eff, warmup)。**`t` 是 PE 序列里的位置**，不是收盘价序列。

    `pct = count(win <= PE_t) / n_eff`（含当日，见模块 docstring §3-①）。
    `n_eff = min(W, t+1)`：历史不足 10 年时自适应用全部可用历史。
    `n_eff < MIN_SAMP` → `pct = None`（冷启动），`warmup=True`。
    """
    p = params(W=W, MIN_SAMP=MIN_SAMP) if (W is not None or MIN_SAMP is not None) else params()
    w, mn = int(p["W"]), int(p["MIN_SAMP"])
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
        win = arr[i - n_eff + 1:i + 1]
        pct[i] = float((win <= arr[i]).sum()) / float(n_eff)
    out = pd.DataFrame({"pct": pct, "n_eff": eff}, index=s.index)
    out["warmup"] = out["n_eff"] < w          # 窗口未满 10 年 = warmup 段
    out["pe"] = s
    return out


def tier_index(pct, edges=None):
    """分位 -> 档位序号（0..4）。**左闭右开**：0.20 归第二档，0.80 归第五档。"""
    e = params()["TIER_EDGES"] if edges is None else edges
    if pct is None or pct != pct:
        return None
    i = 0
    for th in e:
        if pct >= th:
            i += 1
        else:
            break
    return i


def mult_of(pct, p=None):
    """分位 -> 投入倍数；`pct=None`（冷启动）→ 1.0（基础档）。**这是 R-A02 与 R-A03 的接口。**
    """
    p = p or params()
    i = tier_index(pct, p["TIER_EDGES"])
    return 1.0 if i is None else float(p["TIER_MULT"][i])


TIER_NAMES = ("深度低估", "偏便宜", "正常", "偏贵", "高估")


# ---------------------------------------------------------------- 4. 定投日历（R-A01）
def decision_mask(index, rule=None, day=None):
    """月度决策日布尔序列。`first_trading_day` = 每月第一个交易日；
    `day_15` = 每月 `day` 日，非交易日顺延到下一个交易日（即该月首个 ≥day 的交易日）。
    """
    p = params()
    rule = p["DCA_RULE"] if rule is None else rule
    day = int(p["DAY_OF_MONTH"] if day is None else day)
    idx = pd.DatetimeIndex(index)
    out = [False] * len(idx)
    if rule == "first_trading_day":
        prev = None
        for i, d in enumerate(idx):
            if prev is None or (d.year, d.month) != (prev.year, prev.month):
                out[i] = True
            prev = d
    elif rule == "day_15":
        seen = set()
        for i, d in enumerate(idx):
            key = (d.year, d.month)
            if key in seen or d.day < day:
                continue
            out[i] = True
            seen.add(key)
    else:
        raise ValueError(f"未知 DCA_RULE: {rule}（first_trading_day / day_15）")
    return pd.Series(out, index=idx, dtype=bool)


# ---------------------------------------------------------------- 5. 账户模拟（R-A05~R-A08）
def simulate(frame, pct, p=None, mode="tier"):
    """逐日账户复算 -> dict。**T 日决策、T+1 收盘成交**（与 v1~v5 同一纪律）。

    参数：
      frame : load_frame() 的输出（close 必须是全收益）
      pct   : pe_percentile() 的 `pct` 列（index 与 frame 对齐；无分位的日子可为 NaN）
      mode  : "tier"  五档（G2/G3）
              "const2" 恒 2.0（G0 buy_hold_monthly）
              "const1" 恒 1.0（G1 equal_dca）
              "lump"  平时停投、`pct<0.20` 时把现金池一次投完（G4）

    返回 nav(份额化净值) / trades / utilization / cost_edge / 现金守恒明细 / 分档命中。
    """
    p = p or params()
    idx = frame.index
    close = frame["close"].to_numpy(dtype="float64")
    pct_v = pd.Series(pct).reindex(idx).to_numpy(dtype="float64")
    dec = decision_mask(idx, p["DCA_RULE"], p["DAY_OF_MONTH"]).to_numpy(dtype=bool)
    n = len(idx)

    B = float(p["B"])
    budget = float(p["BUDGET_MULT"]) * B
    s = float(p["S"])
    comm = float(p["COMM"])
    sell_fee = float(p["SELL_FEE"])
    lot = int(p["LOT"] or 0)
    rf_d = (1.0 + float(p["RF"])) ** (1.0 / 252.0) - 1.0
    reinvest = bool(p["REINVEST"])
    rev_below = float(p["REINVEST_BELOW"])
    rev_mult = float(p["REINVEST_MULT"])
    sell_on = bool(p["SELL_ENABLED"])

    shares, cost, cash, units = 0.0, 0.0, 0.0, 0.0
    nav = [1.0] * n
    pending = None                     # dict(cf, target, sell)
    trades = []
    # 守恒累计项（V-A01）
    tot_cf = tot_inv = tot_fee = tot_int = tot_sell = 0.0
    tiers = []                         # [(date, tier_idx, mult, invest)]
    cf_log = []                        # [(到账日, 金额)]，XIRR 用
    sold = dict(flag=False, last=-10 ** 9)

    def _nav_unit(px):
        return (shares * px + cash) / units if units > 1e-12 else 1.0

    for i in range(n):
        px = float(close[i])

        # ---- 1) 执行上一交易日的挂单（T+1 收盘成交，无前视）
        if pending is not None:
            nav_before = _nav_unit(px)
            cf_amt = float(pending.get("cf") or 0.0)
            if cf_amt > 1e-12:
                units += cf_amt / nav_before
                cash += cf_amt
                tot_cf += cf_amt
                cf_log.append((idx[i], cf_amt))
            sf = float(pending.get("sell") or 0.0)
            if sf > 1e-12 and shares > 1e-12:
                qty = shares * sf
                gross = qty * px * (1.0 - s)
                fee = gross * sell_fee
                shares -= qty
                cost -= gross                 # 成本基础按卖出市值等比例冲减
                cash += gross - fee
                tot_sell += gross
                tot_fee += fee
                trades.append(dict(date=idx[i], side="sell", amount=float(gross),
                                   fee=float(fee), price=px, why="R-A04 止盈"))
            tgt = float(pending.get("target") or 0.0)
            invest = min(tgt, budget + cash, cash)      # R-A05：不能超支，也不能超过可用现金
            if lot > 0:
                px_all = px * (1.0 + s)
                qty = math.floor(invest / (px_all * lot)) * lot
                invest = qty * px_all
            if invest > 1e-9:
                fee = invest * comm
                if invest + fee > cash:                 # 现金刚好不够付佣金 → 等比缩减
                    invest = cash / (1.0 + comm)
                    fee = invest * comm
                shares += invest / (px * (1.0 + s))
                cost += invest + fee
                cash -= invest + fee
                tot_inv += invest
                tot_fee += fee
                trades.append(dict(date=idx[i], side="buy", amount=float(invest),
                                   fee=float(fee), price=px, why="月度定投"))
            pending = None

        # ---- 2) 份额化净值（当日收盘、成交之后）
        nav_u = _nav_unit(px)
        nav[i] = nav_u

        # ---- 3) 现金池按日计息（R-A06；日频，非月末一次性）
        if cash > 1e-12:
            it = cash * rf_d
            cash += it
            tot_int += it

        if i >= n - 1:
            continue

        # ---- 4) 收盘后决策 → 明日挂单
        pv = pct_v[i]
        has_pct = (pv == pv) and (pv is not None)

        # R-A04 止盈（独立于月度决策，逐日判定）
        if sell_on and shares > 1e-12 and cost > 1e-12:
            profit = (shares * px) / cost - 1.0
            cooling = (i - sold["last"]) >= int(p["SELL_COOLDOWN"])
            if (has_pct and pv >= float(p["SELL_PCT"]) and profit >= float(p["SELL_PROFIT"])
                    and not sold["flag"] and cooling):
                pending = dict(cf=0.0, target=0.0, sell=float(p["SELL_FRAC"]))
                sold.update(flag=True, last=i)
                continue
            if sold["flag"] and has_pct and pv < float(p["SELL_RELEASE"]):
                sold["flag"] = False

        if not dec[i]:
            continue

        if mode == "const2":
            mult = 2.0
        elif mode == "const1":
            mult = 1.0
        elif mode == "lump":
            mult = 0.0
        else:
            mult = mult_of(pv if has_pct else None, p)

        target = mult * B
        if mode == "lump" and has_pct and pv < rev_below:
            target = cash + budget                       # 一次性投出全部现金（含本月到账）
        if reinvest and has_pct and pv < rev_below:
            target += min(cash, rev_mult * B)            # R-A07：深跌时动用存量

        pending = dict(cf=budget, target=float(target), sell=0.0)
        ti = tier_index(pv if has_pct else None, p["TIER_EDGES"])
        tiers.append(dict(date=idx[i], tier=ti, mult=float(mult), pct=(pv if has_pct else None),
                          target=float(target)))

    nav_s = pd.Series(nav, index=idx, dtype="float64", name="nav")
    final_value = shares * float(close[-1]) + cash if n else 0.0
    util = (tot_inv / tot_cf) if tot_cf > 1e-12 else 0.0
    avg_cost = (cost / shares) if shares > 1e-12 else None
    mkt_mean = float(pd.Series(close).mean()) if n else None
    cost_edge = (avg_cost / mkt_mean - 1.0) if (avg_cost and mkt_mean) else None
    return dict(nav=nav_s, trades=trades, units=units, shares=shares, cash=cash,
                cost=cost, tot_cf=tot_cf, tot_inv=tot_inv, tot_fee=tot_fee,
                tot_int=tot_int, tot_sell=tot_sell, final_value=final_value,
                utilization=util, avg_cost=avg_cost, mkt_mean=mkt_mean,
                cost_edge=cost_edge, tiers=tiers, cf_log=cf_log,
                n_invest=sum(1 for t in trades if t["side"] == "buy"),
                n_sell=sum(1 for t in trades if t["side"] == "sell"))


# ---------------------------------------------------------------- 6. 绩效（R-A09）
def _downside(rs, periods=252):
    neg = [r for r in rs if r < 0]
    if not neg:
        return 0.0
    mu = sum(neg) / len(neg)
    return math.sqrt(sum((x - mu) ** 2 for x in neg) / len(neg)) * math.sqrt(periods)


def perf(nav, rf=None, days=None):
    """绩效：复用 `engine.metrics`（cum/ann/vol/mdd/sharpe），再补 sortino/calmar/years。

    `ann` 是**份额化净值**的几何年化（R-A09 口径①），不是 `(V_T/ΣCF)` 那个错式。
    """
    p = params()
    rf = float(p["RF"] if rf is None else rf)
    m = engine.metrics(nav, rf=rf)
    rs = nav.pct_change().dropna().tolist()
    dn = _downside(rs)
    m["sortino"] = ((m["ann"] - rf) / dn) if dn > 1e-9 else 0.0
    m["calmar"] = (m["ann"] / abs(m["mdd"])) if m["mdd"] else None
    n = int(m.get("days") or len(nav))
    m["years"] = n / 252.0
    return m


def xirr(cfs, guess=0.10):
    """XIRR（R-A09 口径②）。`cfs` = [(date, amount)]，到账为负、期末净值为正。"""
    if len(cfs) < 2:
        return None
    t0 = cfs[0][0]

    def npv(r):
        try:
            return sum(a / (1.0 + r) ** ((d - t0).days / 365.0) for d, a in cfs)
        except (OverflowError, ZeroDivisionError):
            return float("nan")

    lo, hi = -0.9999, 10.0
    flo, fhi = npv(lo), npv(hi)
    if flo * fhi > 0:
        f0 = npv(guess)
        if f0 is None or f0 != f0:
            return None
        lo, hi, flo = guess - 0.5, guess + 0.5, npv(guess - 0.5)
        fhi = npv(guess + 0.5)
        if flo * fhi > 0:
            return None
    for _ in range(200):
        mid = (lo + hi) / 2.0
        fm = npv(mid)
        if fm != fm:
            return None
        if abs(fm) < 1e-9 or (hi - lo) < 1e-10:
            return mid
        if flo * fm <= 0:
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2.0


def cashflows(sim):
    """XIRR 现金流：每次到账 = −CF，期末 = +期末资产（持仓市值 + 现金池）。

    只用**到账记录**而不是成交记录：某些月份的到账可能因「现金够但份额太小」而不成交
    （整手口径），那笔钱仍进了账户，必须计入现金流，否则 XIRR 会被抬高。
    """
    out = [(d, -float(a)) for d, a in sim.get("cf_log", [])]
    out.append((sim["nav"].index[-1], float(sim["final_value"])))
    return out


def simple_ret(sim):
    """R-A09 口径③：`(V_T - ΣCF)/ΣCF`。**不可跨策略比较**（智能定投分母小，会虚高）。"""
    if sim["tot_cf"] <= 0:
        return None
    return (sim["final_value"] - sim["tot_cf"]) / sim["tot_cf"]


# ---------------------------------------------------------------- 7. 分档统计（R-A12）
def tier_stats(frame, pct, horizon=250):
    """分档命中统计：各档月份数 / 投入比例 / 分位均值 / **其后 12 个月标的收益均值**。

    最后一项是「信号有没有预测力」的检验：深度低估档的后 12 个月收益若不高于高估档，
    则分位对中期收益无预测力（规格 §8 场景 1/2 就是这种情况）。
    """
    p = params()
    close = frame["close"]
    dec = decision_mask(frame.index, p["DCA_RULE"], p["DAY_OF_MONTH"])
    fwd = close.shift(-horizon) / close - 1.0
    rows = []
    for ti, name in enumerate(TIER_NAMES):
        sel = []
        for d in frame.index[dec]:
            pv = pct.reindex(frame.index).loc[d]
            if pv != pv:
                continue
            if tier_index(float(pv), p["TIER_EDGES"]) == ti:
                sel.append(d)
        if not sel:
            rows.append(dict(tier=ti, name=name, n=0, share=0.0, mult=p["TIER_MULT"][ti],
                             pct_mean=None, fwd12_mean=None, fwd12_win=None,
                             invest_share=0.0))
            continue
        pv = [float(pct.reindex(frame.index).loc[d]) for d in sel]
        fw = [float(fwd.loc[d]) for d in sel if fwd.loc[d] == fwd.loc[d]]
        rows.append(dict(
            tier=ti, name=name, n=len(sel), share=len(sel) / float(len(frame.index[dec])),
            mult=float(p["TIER_MULT"][ti]), pct_mean=sum(pv) / len(pv),
            fwd12_mean=(sum(fw) / len(fw)) if fw else None,
            fwd12_win=(sum(1 for x in fw if x > 0) / len(fw)) if fw else None,
            invest_share=float(p["TIER_MULT"][ti]) / sum(p["TIER_MULT"]),
        ))
    # 冷启动月份（无分位）
    cold = [d for d in frame.index[dec] if pct.reindex(frame.index).loc[d] != pct.reindex(frame.index).loc[d]]
    if cold:
        rows.append(dict(tier=-1, name="冷启动(无分位)", n=len(cold),
                         share=len(cold) / float(len(frame.index[dec])), mult=1.0,
                         pct_mean=None, fwd12_mean=None, fwd12_win=None, invest_share=0.0))
    return rows


def yearly(frame, sim, pct):
    """分年度：份额化净值收益 / 年内最大回撤 / utilization / 分位均值。

    年收益用「上年末份额化净值 → 本年末」——nav_unit 是连续曲线，跨年直接相除即可，
    不需要重算现金流（这正是份额化口径的好处）。
    """
    nav = sim["nav"]
    buy_by_year, cf_by_year = {}, {}
    for t in sim["trades"]:
        if t["side"] == "buy":
            buy_by_year[t["date"].year] = buy_by_year.get(t["date"].year, 0.0) + t["amount"]
    for d, a in sim.get("cf_log", []):
        cf_by_year[d.year] = cf_by_year.get(d.year, 0.0) + float(a)
    rows, prev = [], None
    for y in sorted({d.year for d in nav.index}):
        seg = nav.loc[str(y)]
        if len(seg) < 2:
            prev = float(seg.iloc[-1]) if len(seg) else prev
            continue
        base = prev if prev else float(seg.iloc[0])
        ret = float(seg.iloc[-1]) / base - 1.0
        peak, mdd = float(seg.iloc[0]), 0.0
        for v in seg:
            peak = max(peak, float(v))
            mdd = min(mdd, float(v) / peak - 1.0)
        sub_pct = pct.reindex(seg.index).dropna()
        cfy = cf_by_year.get(y, 0.0)
        rows.append(dict(year=y, ret=ret, mdd=mdd,
                         utilization=(buy_by_year.get(y, 0.0) / cfy) if cfy > 1e-9 else None,
                         pct_mean=float(sub_pct.mean()) if len(sub_pct) else None))
        prev = float(seg.iloc[-1])
    return rows


# ---------------------------------------------------------------- 8. 工具
def fmt_pct(x, nd=2):
    return "—" if x is None or (isinstance(x, float) and x != x) else f"{x * 100:+.{nd}f}%"


def fmt_x(x, nd=2):
    if x is None or (isinstance(x, float) and x != x):
        return "—"
    if isinstance(x, float) and math.isinf(x):
        return "∞"
    return f"{x:.{nd}f}"


def latest(frame=None, p=None):
    """当前读数（每日报告 / 自检共用）：最新分位、档位、本月目标投入。"""
    p = p or params()
    df = load_frame() if frame is None else frame
    pc = pe_percentile(df["pe"], p["W"], p["MIN_SAMP"])
    pct = pc["pct"]
    last = df.index[-1]
    has = pct.dropna()
    if not len(has):
        raise RuntimeError("分位全为空 —— PE 历史不足 MIN_SAMP")
    d0 = has.index[-1]
    ti = tier_index(float(has.iloc[-1]), p["TIER_EDGES"])
    return dict(
        date=last, pe_date=d0, df=df, pc=pc,
        pe=float(df.loc[d0, "pe"]), close=float(df.loc[last, "close"]),
        n_eff=int(pc.loc[d0, "n_eff"]), pct=float(has.iloc[-1]),
        warmup=bool(pc.loc[d0, "warmup"]), tier=ti,
        tier_name=(TIER_NAMES[ti] if ti is not None else "冷启动"),
        mult=mult_of(float(has.iloc[-1]), p),
        source=dict(SOURCE), warn=list(WARN),
    )


def month_pct_series(frame, p=None):
    """按月末取分位 —— 报告里画「分位随时间的漂移」用（不是信号本身）。"""
    p = p or params()
    pc = pe_percentile(frame["pe"], p["W"], p["MIN_SAMP"])
    s = pc["pct"].dropna()
    for freq in ("ME", "M"):
        try:
            return s.resample(freq).last().dropna()
        except ValueError:
            continue
    return s


# ---------------------------------------------------------------- 9. 自检
def _self_check():
    df = load_frame()
    r = latest(df)
    pc = r["pc"]
    ok = pc["pct"].dropna()
    print(f"{LABEL} · 标的 {IDX_NAME}（全收益 H00300）")
    print(f"  价格 {df.index[0]:%Y-%m-%d} ~ {df.index[-1]:%Y-%m-%d}（{len(df)} 根）")
    print(f"  PE   {pc.index[0]:%Y-%m-%d} ~ {pc.index[-1]:%Y-%m-%d}（{len(pc)} 个交易日）")
    print(f"  首个有效分位 {ok.index[0]:%Y-%m-%d}（第 {int(pc.loc[ok.index[0], 'n_eff'])} 个 PE 交易日）")
    full = pc[~pc["warmup"] & pc["pct"].notna()]
    if len(full):
        print(f"  10 年窗口成立 {full.index[0]:%Y-%m-%d}（此前为 warmup 段，n_eff 逐月漂移）")
    print(f"  最新 {r['date']:%Y-%m-%d}: 收盘 {r['close']:.0f} · PE {r['pe']:.2f}"
          f"（{r['pe_date']:%Y-%m-%d}）· 分位 {r['pct']:.1%}（n_eff={r['n_eff']}"
          f"{'·warmup' if r['warmup'] else ''}）")
    print(f"  档位 {r['tier_name']} → 倍数 {r['mult']:.1f}× · "
          f"本月目标投入 {r['mult'] * CFG['B']:,.0f} 元（B={CFG['B']:,.0f}）")
    cnt = {}
    for pv in ok:
        ti = tier_index(float(pv), CFG["TIER_EDGES"])
        cnt[ti] = cnt.get(ti, 0) + 1
    print("  分档日数（PE 逐日口径）: " + " / ".join(
        f"{TIER_NAMES[i]}{cnt.get(i, 0)}" for i in range(len(TIER_NAMES))))
    print(f"  来源: {SOURCE.get('pe')} | {SOURCE.get('tr')}")


if __name__ == "__main__":
    _self_check()
