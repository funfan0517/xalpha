# -*- coding: utf-8 -*-
"""波动资产 · V7 变种：中期趋势 + 急跌退出（**沪深300 · 单腿 0/1 状态机**）。

来源：`G:/tradingagents/index_strategy_backtest/strategy_B_trend_exit.md`（策略B 编码规格 v1.0）。
一句话：**用中期均线判断「该不该在场」，用急跌条件强制离场；出场容易、回场很难 ——
这是它的成本，也是它的收益来源。**

## 1. 它赚的是什么（判据不是「年化更高」）

这类策略的 `CAGR` 通常**低于**买入持有，唯一价值是 `MDD` 显著下降。所以判据必须是：

```
有效 ⟺ Calmar_A > Calmar_BH  且  CAGR_A > CAGR_BH − 2pp
```

`Calmar = CAGR / |MDD|`。年化掉 3pp 但回撤从 45% 降到 25%（Calmar 0.17 → 0.28）算有效；
年化掉 3pp 而回撤只降 5pp 就是无效劳动。**报告里每一张表都同时给 Calmar 和 BH 对照。**

## 2. 规则卡（逐条 → 代码）

| 编号 | 规格原文 | 代码 |
|---|---|---|
| R-B01 | 任一指标历史不足（`t<250`）→ `FLAT`、不交易、不计入绩效 | `CFG["WARMUP"]=250`，净值起点顺延 |
| R-B02 | 价格口径 = **H00300 沪深300 全收益** | `load_px("tr")` |
| R-B03 | `TREND_MODE ∈ {ma120, ma250, dual}`，默认 `ma120` | `indicators()` |
| R-B04 | 滞回带 `enter_thr = MA×(1+BAND)`、`exit_thr = MA×(1−BAND)`，`BAND` 默认 0 | 同上 |
| R-B05 | 斜率过滤 `MA120[t] > MA120[t-20]`（可选，默认关） | `CFG["SLOPE_FILTER"]` |
| R-B06 | `exit_signal = (dev60 ≤ −8%) or (vol20 ≥ 40%)` | `state_machine()` |
| R-B07 | 触发即清仓，**不看趋势状态** | 同上（trend 是第三类退出原因，见 §3） |
| R-B08 | 反向校验：统计 `exit_signal` 真实日的**后续 20 日**收益 | `backtest.py::exit_check()` |
| R-B09 | 回场四条件**全部**满足：冷却 5 日 + 趋势恢复 + `dev60 ≥ −3%` + `vol20 ≤ 35%` | `state_machine()` |
| R-B10 | 阈值必须满足滞回关系（否则死循环）→ **代码里断言拦截** | `_check_hysteresis()`（import 时执行） |
| R-B11 | 回场是**状态翻转**，一次性满仓/清仓，禁止分批 | `state_machine()` |
| R-B12 | 主指标含 Calmar / n_trades / avg_hold_days / exposure / win_rate / exit_reason | `metrics()` / `trade_stats()` |
| R-B13 | 基准 = 同标的、同区间、期初满仓、不交易 | `run_nav(state=1)` |
| R-B15 | 踏空量化：每笔 SELL 后 60 个交易日的最大涨幅分布 | `trade_stats()` |
| R-B18 | 邻域稳定性（±1 格，年化极差 ≤1.5pp、回撤极差 ≤5pp） | `backtest.py::neighbors()` |

## 3. 三处「规格写得不够死、不定就跑不下去」的口径

### ① 退出原因必须有三类（trend / dev60 / vol20）
规格 §5 的伪代码里 `sig_exit` 只含 `dev60` 与 `vol20`，但 R-B03 说均线「决定基础仓位」、
R-B12 又把 `trend` 列为一类退出原因。两者只能这样调和：

```
exit_signal = (not 趋势仍成立)  or  (dev60 ≤ EXIT_DEV)  or  (vol20 ≥ EXIT_VOL)
             └── trend ──┘         └── dev60 ──┘          └── vol20 ──┘
```

`dev60` / `vol20` 是**附加**的离场理由（R-B07：触发即清仓，即使 `close > MA`），
`trend` 是基础离场。不这么写，均线就完全不起作用，「中期趋势 + 急跌退出」只剩后半句。

> 副作用：`V-B03 恒多测试`（规格写「`EXIT_DEV=-9.99, EXIT_VOL=9.99` → 全程 LONG」）
> 在含 trend 退出的实现里不成立。故本模块加 `TREND_MODE="none"`（趋势恒成立），
> 让这条断言可以**真的执行**，而不是靠「反正不会触发」蒙过去。

### ② `last_exit_day` 记的是**成交日**，不是信号日
R-B09 的冷却从「卖出的那一天」开始算。信号在 `t`、成交在 `t+1`，若把 `t` 记成 last_exit_day，
冷却会短 1 天 —— 5 日冷却实际只有 4 日，这类差一错误在参数扫描里会显示成「COOLDOWN=4 更好」。

### ③ 空仓现金必须计息
`RF = 1.8%`（规格 §5/§7 默认）。不计息会把回撤与年化同时低估：
回测里空仓约 40~45% 的时间，1.8% 的复利对 13 年样本是 **0.7~0.8pp/年**的量级，
大于本策略相对基准的全部超额之一半。

## 4. 与其它变种的边界
- 主策略（`../`）管沪深300 这 2.5pp 带的**回撤阶梯**；
- `../v4_erp_band/` 管「沪深300 vs 国债」的 80/50/20；`hs300_ladder/` 管单腿 0~100% 爬梯；
- `../v6_valuation_dca/` 管「每月往这条腿铺多少钱」（**横向资金节奏**）；
- **本变种管「此刻在不在场」（纵向仓位开关）**，是 0/1 二值，不是档位。

规格 §11 明令：A 与 B **不能简单相乘**（会双重择时、双重踏空）。要合并，正确做法是
A 产生月度预算、B 的状态只决定这笔钱「立即买入 or 先入现金池」，且必须重新做一次完整回测。

模拟结果，非投资建议。
"""
import json
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
_VL = os.path.dirname(_HERE)                          # strategies/vol_legs
_ROOT = os.path.dirname(os.path.dirname(_VL))         # 仓库根
if _VL not in sys.path:
    sys.path.insert(0, _VL)

import engine  # noqa: E402  —— 绩效口径复用主策略

LABEL = "V7 中期趋势 + 急跌退出"
IDX_NAME = "沪深300"

_CACHE = os.path.join(_ROOT, "data", "_bt_caches")
_TR_CSV = os.path.join(_CACHE, "csi_H00300.csv")       # 全收益（主口径）
_PX_CSV = os.path.join(_CACHE, "csi_000300.csv")       # 价格指数（对照口径）
_LK = os.path.join(_ROOT, "data", "_long_klines.json")  # 场内 ETF（510300，对照口径）

SOURCE = {}


# ---------------------------------------------------------------- 1. 参数（唯一权威源）
CFG = {
    # ---- 预热（R-B01）
    "WARMUP": 250,
    # ---- 均线（R-B03/R-B04/R-B05）
    "MA_FAST": 60,
    "MA_MID": 120,
    "MA_SLOW": 250,
    "TREND_MODE": "ma120",       # ma120 / ma250 / dual / none(仅测试用)
    "BAND": 0.0,                 # 滞回带：进 MA×(1+BAND) / 出 MA×(1−BAND)
    "SLOPE_FILTER": False,       # MA120 20 日斜率为正才允许回场
    "SLOPE_N": 20,
    # ---- 指标
    "VOL_WIN": 20,               # vol20 的窗口
    "DEV_WIN": 60,               # dev60 的分母均线
    # ---- 急跌退出（R-B06）
    "EXIT_DEV": -0.08,
    "EXIT_VOL": 0.40,
    # 趋势破位是否也清仓 —— 规格内部有矛盾，默认 False，见模块 docstring §2.5
    "TREND_EXIT": False,
    # ---- 回场（R-B09）
    "COOLDOWN": 5,               # 交易日
    "RECOVER_DEV": -0.03,
    "RECOVER_VOL": 0.35,
    # ---- 成交与成本（R-B19）
    "SLIP": 0.0005,              # 单边滑点（规格 R-E02 = 0.05%）
    "COMM": 0.00025,             # 佣金
    "RF": 0.018,                 # 空仓现金年化（规格 §5）
    "LAG": 1,                    # 信号 t → 成交 t+LAG（R-B19 里扫 2）
    # ---- 评价区间
    "START": "2012-05-28",       # 与规格 §0「自建基准锚点」同起点，便于对标
}


def params(**kw):
    p = dict(CFG)
    p.update(kw)
    return p


def _check_hysteresis(p):
    """R-B10：`RECOVER_DEV > EXIT_DEV` 且 `RECOVER_VOL < EXIT_VOL`，否则会退化成无限循环。

    在 `state_machine()` 入口调用（不是只在文档里写）。故意写成显式 raise 而不是 assert，
    这样 `python -O` 也拦得住 —— 这条错一旦漏过去，回测会给出「每天买卖」的假信号。
    """
    if not (float(p["RECOVER_DEV"]) > float(p["EXIT_DEV"])):
        raise ValueError(f"R-B10 违反：RECOVER_DEV({p['RECOVER_DEV']}) 必须 > "
                         f"EXIT_DEV({p['EXIT_DEV']})，否则退出当天就能回场")
    if not (float(p["RECOVER_VOL"]) < float(p["EXIT_VOL"])):
        raise ValueError(f"R-B10 违反：RECOVER_VOL({p['RECOVER_VOL']}) 必须 < "
                         f"EXIT_VOL({p['EXIT_VOL']})，否则波动条件不构成滞回")


# ---------------------------------------------------------------- 2. 数据
def load_px(kind="tr", start=None):
    """价格序列。`kind`:
      "tr"  —— H00300 **全收益**（主口径，R-B02）
      "px"  —— 000300 价格指数（R-B19 口径敏感性用，会漏约 2pp/年分红）
      "etf" —— 510300 场内 ETF 收盘（R-B19 标的口径，含折溢价与费率，**不作主口径**）
    """
    if kind == "etf":
        with open(_LK, encoding="utf-8") as f:
            lib = json.load(f)
        node = lib.get("510300")
        if not node:
            raise KeyError("510300 不在 data/_long_klines.json")
        s = pd.Series(node["close"], index=pd.to_datetime(node["dates"]), dtype="float64")
        SOURCE[kind] = f"`510300` 沪深300ETF（场内收盘）· {s.index[0]:%Y-%m-%d} 起"
    else:
        path = _TR_CSV if kind == "tr" else _PX_CSV
        if not os.path.exists(path):
            raise FileNotFoundError(f"缺 {os.path.relpath(path, _ROOT)} → 先跑 "
                                    f"python strategies/vol_legs/v4_erp_band/erp.py --refresh")
        df = pd.read_csv(path, parse_dates=["date"]).dropna(subset=["close"])
        df = df.sort_values("date").drop_duplicates("date", keep="last")
        s = pd.Series(df["close"].astype("float64").to_numpy(),
                      index=pd.DatetimeIndex(df["date"]))
        SOURCE[kind] = (f"中证官网 `{'H00300 全收益' if kind == 'tr' else '000300 价格指数'}`"
                        f" · {s.index[0]:%Y-%m-%d} ~ {s.index[-1]:%Y-%m-%d} · {len(s)} 根")
    s = s.dropna().sort_index()
    return s.loc[pd.Timestamp(start):] if start is not None else s


# ---------------------------------------------------------------- 3. 指标（R-B03~R-B06）
def indicators(px, p=None):
    """收盘价 -> 指标表。列：close / ma60 / ma120 / ma250 / dev60 / vol20 / slope120 /
    trend_enter / trend_hold / x_dev / x_vol / ready。

    全部只用当日及之前的数据（R-B01）。`ready` 从第 `WARMUP` 个交易日（0 基）起为 True。
    """
    p = p or params()
    c = px.astype("float64")
    out = pd.DataFrame(index=c.index)
    out["close"] = c
    out["ma60"] = c.rolling(int(p["MA_FAST"]), min_periods=int(p["MA_FAST"])).mean()
    out["ma120"] = c.rolling(int(p["MA_MID"]), min_periods=int(p["MA_MID"])).mean()
    out["ma250"] = c.rolling(int(p["MA_SLOW"]), min_periods=int(p["MA_SLOW"])).mean()
    dev_n = int(p["DEV_WIN"])
    out["ma_dev"] = c.rolling(dev_n, min_periods=dev_n).mean()
    out["dev60"] = c / out["ma_dev"] - 1.0
    lr = np.log(c).diff()
    out["vol20"] = (lr.rolling(int(p["VOL_WIN"]), min_periods=int(p["VOL_WIN"]))
                    .std(ddof=1) * math.sqrt(252.0))
    out["slope120"] = out["ma120"] / out["ma120"].shift(int(p["SLOPE_N"])) - 1.0

    band = float(p["BAND"])
    mode = p["TREND_MODE"]
    if mode == "none":
        enter = pd.Series(True, index=c.index)
        hold = enter.copy()
    elif mode == "ma120":
        enter = c > out["ma120"] * (1.0 + band)
        hold = c > out["ma120"] * (1.0 - band)
    elif mode == "ma250":
        enter = c > out["ma250"] * (1.0 + band)
        hold = c > out["ma250"] * (1.0 - band)
    elif mode == "dual":
        enter = (c > out["ma120"] * (1.0 + band)) & (c > out["ma250"] * (1.0 + band))
        hold = (c > out["ma120"] * (1.0 - band)) & (c > out["ma250"] * (1.0 - band))
    else:
        raise ValueError(f"未知 TREND_MODE: {mode}（ma120 / ma250 / dual / none）")
    if p["SLOPE_FILTER"]:
        enter = enter & (out["slope120"] > 0.0)
    out["trend_enter"] = enter.fillna(False)
    out["trend_hold"] = hold.fillna(False)
    out["x_dev"] = out["dev60"] <= float(p["EXIT_DEV"])
    out["x_vol"] = out["vol20"] >= float(p["EXIT_VOL"])

    pos = np.arange(len(c))
    need = out[["dev60", "vol20", "ma120"]].notna().all(axis=1).to_numpy()
    out["ready"] = (pos >= int(p["WARMUP"])) & need
    return out


# ---------------------------------------------------------------- 4. 状态机（R-B09~R-B11）
def state_machine(px, **kw):
    """-> DataFrame。`state[t]` = **t 日收盘成交之后**是否在场（1/0）。

    `decide[t]` ∈ {buy, sell, ""} 是 t 日收盘做出、t+1 日成交的决策；
    `done[t]` 是 t 日成交后的说明；`exit_reason[t]` ∈ {trend, dev60, vol20, ""}。

    严格遵守三条：①信号在 t 算、t+1 成交、**成交后才改 state**；
    ②`last_exit_day` 记**成交日**；③空仓现金计息在 `run_nav` 里做。
    """
    p = params(**kw)
    if int(p["LAG"]) != 1:
        raise ValueError("本模块只实现 T+1 成交（LAG=1）；t 日成交会引入前视，不提供。"
                         "R-B19 的「成交延迟 t+2」由 backtest.py 用 shift 模拟。")
    _check_hysteresis(p)
    I = indicators(px, p)
    n = len(I)
    close = I["close"].to_numpy(dtype="float64")
    ready = I["ready"].to_numpy(dtype=bool)
    t_enter = I["trend_enter"].to_numpy(dtype=bool)
    t_hold = I["trend_hold"].to_numpy(dtype=bool)
    x_dev = I["x_dev"].to_numpy(dtype=bool)
    x_vol = I["x_vol"].to_numpy(dtype=bool)
    dev = I["dev60"].to_numpy(dtype="float64")
    vol = I["vol20"].to_numpy(dtype="float64")
    ma = I["ma120"].to_numpy(dtype="float64")

    cd = int(p["COOLDOWN"])
    rec_dev = float(p["RECOVER_DEV"])
    rec_vol = float(p["RECOVER_VOL"])

    state = np.zeros(n, dtype=int)
    decide = np.empty(n, dtype=object)
    decide[:] = ""
    done = np.empty(n, dtype=object)
    done[:] = ""
    reason_v = np.empty(n, dtype=object)
    reason_v[:] = ""
    trades = []

    hold = 0
    pend = None                      # (new_state, reason, note, sig_index)
    last_exit = None                 # **成交日**索引（R-B09 ②）

    for t in range(n):
        if pend is not None:
            hold = int(pend[0])
            if pend[1] == "BUY":
                trades.append(dict(signal_date=I.index[pend[3]], exec_date=I.index[t],
                                   side="BUY", price=float(close[t]),
                                   frm=int(pend[4]), to=1,
                                   reason="entry", dev60=float(dev[pend[3]]),
                                   vol20=float(vol[pend[3]]), ma120=float(ma[pend[3]])))
            else:
                trades.append(dict(signal_date=I.index[pend[3]], exec_date=I.index[t],
                                   side="SELL", price=float(close[t]),
                                   frm=int(pend[4]), to=0, reason=pend[2],
                                   dev60=float(dev[pend[3]]), vol20=float(vol[pend[3]]),
                                   ma120=float(ma[pend[3]])))
                last_exit = t                     # 记成交日，不是信号日
            done[t] = pend[1]
            pend = None
        state[t] = hold
        if not ready[t] or t >= n - 1:
            continue

        if hold == 1:
            if bool(p["TREND_EXIT"]) and not t_hold[t]:
                pend = (0, "SELL", "trend", t, 1)
            elif x_dev[t]:
                pend = (0, "SELL", "dev60", t, 1)
            elif x_vol[t]:
                pend = (0, "SELL", "vol20", t, 1)
            if pend is not None:
                decide[t] = "sell"
                reason_v[t] = pend[2]
        else:
            cooling = (last_exit is None) or ((t - last_exit) >= cd)
            if (cooling and t_enter[t] and (dev[t] >= rec_dev) and (vol[t] <= rec_vol)):
                pend = (1, "BUY", "entry", t, 0)
                decide[t] = "buy"

    out = pd.DataFrame(dict(state=state, decide=decide, done=done, exit_reason=reason_v),
                       index=I.index)
    return out.join(I), trades


# ---------------------------------------------------------------- 5. 净值（R-B11/R-B13）
def run_nav(state, px, fee_buy=0.0, fee_sell=0.0, rf=None):
    """二值仓位的净值：持仓赚当日收益，空仓赚现金（日频复利），翻转日扣费。

    与 `../v5_trend_gate/trend_gate.run_leg` 同一套算法（`state.shift(1)` 归属当日收益）。
    """
    p = params()
    rf = float(p["RF"] if rf is None else rf)
    cash_d = (1.0 + rf) ** (1.0 / 252.0) - 1.0
    ret = px.pct_change().fillna(0.0)
    hold = state.shift(1).fillna(0.0).astype(float)
    r = hold * ret + (1.0 - hold) * cash_d
    d = state.diff().fillna(state).astype(float)
    r = r - (d > 0) * float(fee_buy) - (d < 0) * float(fee_sell)
    return (1.0 + r).cumprod()


def trade_stats(trades, px, hold_days_window=60):
    """R-B12 / R-B15：笔数、平均持仓、胜率、盈亏比、退出原因计数、踏空量化。"""
    buys = [t for t in trades if t["side"] == "BUY"]
    sells = [t for t in trades if t["side"] == "SELL"]
    closes = px.to_numpy(dtype="float64")
    idx = list(px.index)

    # 逐轮（BUY → 下一个 SELL）的收益与持仓交易日数。
    # 不能简单 zip：评价区间起点可能**卡在一轮中间**，此时第一笔是 SELL 而没有配对的 BUY
    # （它的买入发生在区间之前）。按顺序走一遍有状态机的配对才正确。
    rounds, open_buy = [], None
    for t in trades:
        if t["side"] == "BUY":
            open_buy = t
        elif open_buy is not None:
            rounds.append(dict(buy=open_buy["exec_date"], sell=t["exec_date"],
                               ret=t["price"] / open_buy["price"] - 1.0,
                               days=idx.index(t["exec_date"]) - idx.index(open_buy["exec_date"])))
            open_buy = None
    wins = [r for r in rounds if r["ret"] > 0]
    losses = [r for r in rounds if r["ret"] <= 0]
    avg_win = (sum(r["ret"] for r in wins) / len(wins)) if wins else None
    avg_loss = (sum(r["ret"] for r in losses) / len(losses)) if losses else None
    payoff = (-avg_win / avg_loss) if (avg_win and avg_loss and avg_loss < 0) else None

    # R-B15 踏空：每笔 SELL 之后 60 个交易日的最大涨幅
    maxtp = []
    for s in sells:
        i = idx.index(s["exec_date"])
        seg = closes[i + 1:i + 1 + hold_days_window]
        if len(seg):
            maxtp.append(float(seg.max() / closes[i] - 1.0))
    maxtp_s = pd.Series(maxtp, dtype="float64")
    return dict(n_trades=len(trades), n_round=len(rounds),
                avg_hold_days=(sum(r["days"] for r in rounds) / len(rounds)) if rounds else None,
                win_rate=(len(wins) / len(rounds)) if rounds else None,
                avg_win=avg_win, avg_loss=avg_loss, payoff=payoff, rounds=rounds,
                exit_trend=sum(1 for s in sells if s["reason"] == "trend"),
                exit_dev=sum(1 for s in sells if s["reason"] == "dev60"),
                exit_vol=sum(1 for s in sells if s["reason"] == "vol20"),
                exit_n=len(sells),
                gain_p50=(float(maxtp_s.median()) if len(maxtp_s) else None),
                gain_p90=(float(maxtp_s.quantile(0.90)) if len(maxtp_s) else None),
                gain_max=(float(maxtp_s.max()) if len(maxtp_s) else None),
                gain_mean=(float(maxtp_s.mean()) if len(maxtp_s) else None))


# ---------------------------------------------------------------- 6. 绩效
def _downside(rs, periods=252):
    """下行标准差（只对负收益算离散度，与 Sortino 的定义一致）。"""
    neg = [r for r in rs if r < 0]
    if not neg:
        return 0.0
    mu = sum(neg) / len(neg)
    return math.sqrt(sum((x - mu) ** 2 for x in neg) / len(neg)) * math.sqrt(periods)


def perf(nav, rf=None):
    """绩效（复用主策略 engine.metrics，再补 sortino / calmar / years）。"""
    p = params()
    rf = float(p["RF"] if rf is None else rf)
    m = engine.metrics(nav, rf=rf)
    dn = _downside(nav.pct_change().dropna().tolist())
    m["sortino"] = ((m["ann"] - rf) / dn) if dn > 1e-9 else 0.0
    m["calmar"] = (m["ann"] / abs(m["mdd"])) if m["mdd"] else None
    m["years"] = int(m.get("days") or len(nav)) / 252.0
    return m


def evaluate(px, **kw):
    """一次完整回测 -> dict(nav, bh, state, trades, stats, m, mbh, exp)。

    关键：`px` 传**完整序列**。状态机在全序列上跑（预热与 MA 连续、`last_exit` 不被切断），
    然后**在评价区间上取净值**。若先切后跑，`WARMUP=250` 会把净值起点再往后推一年，
    且起点前持有的仓位会被当成「起点空仓」——这是 2026-11-02 踩过的坑。
    """
    p = params(**kw)
    sm_full, trades_all = state_machine(px, **kw)
    start = pd.Timestamp(p["START"]) if p["START"] else sm_full.index[0]
    sm = sm_full.loc[start:]
    trades = [t for t in trades_all if t["exec_date"] >= start]
    fee = float(p["SLIP"]) + float(p["COMM"])
    nav = run_nav(sm["state"], sm["close"], fee, fee, p["RF"])
    bh = run_nav(pd.Series(1, index=sm.index), sm["close"], 0.0, 0.0, p["RF"])
    st = sm["state"]
    return dict(nav=nav, bh=bh, state=st, sm=sm, trades=trades,
                stats=trade_stats(trades, sm["close"]),
                m=perf(nav, p["RF"]), mbh=perf(bh, p["RF"]),
                exp=float(st.mean()) if len(st) else 0.0)


def slice_start(px, p=None):
    """把序列切到评价起点（默认 `CFG["START"]`）。仅用于**展示**，回测请用 `evaluate(full)`。"""
    p = p or params()
    s = p["START"]
    return px.loc[pd.Timestamp(s):] if s else px


# ---------------------------------------------------------------- 7. 工具
def fmt_pct(x, nd=2):
    return "—" if x is None or (isinstance(x, float) and x != x) else f"{x * 100:+.{nd}f}%"


def fmt_x(x, nd=2):
    if x is None or (isinstance(x, float) and x != x):
        return "—"
    if isinstance(x, float) and math.isinf(x):
        return "∞"
    return f"{x:.{nd}f}"


def latest(px=None, **kw):
    """当前读数（每日报告 / 自检共用）。"""
    p = params(**kw)
    full = load_px("tr") if px is None else px
    sm, trades = state_machine(full, **kw)
    last = sm.iloc[-1]
    return dict(date=sm.index[-1], sm=sm, trades=trades, close=float(last["close"]),
                ma120=float(last["ma120"]), ma250=float(last["ma250"]),
                dev60=float(last["dev60"]), vol20=float(last["vol20"]),
                slope120=float(last["slope120"]),
                trend_enter=bool(last["trend_enter"]), trend_hold=bool(last["trend_hold"]),
                state=int(last["state"]), ready=bool(last["ready"]),
                days_in_state=_days_in_state(sm["state"]),
                exit_dev_line=float(last["ma_dev"]) * (1.0 + float(p["EXIT_DEV"])),
                exit_vol_line=float(p["EXIT_VOL"]),
                rec_dev=p["RECOVER_DEV"], rec_vol=p["RECOVER_VOL"],
                source=dict(SOURCE), trades_n=len(trades))


def _days_in_state(state):
    s = int(state.iloc[-1])
    n = 0
    for v in reversed(state.tolist()):
        if int(v) != s:
            break
        n += 1
    return n


def _self_check():
    px = load_px("tr")
    r = latest(px)
    ev = evaluate(px)
    print(f"{LABEL} · 标的 {IDX_NAME}（{SOURCE.get('tr')}）")
    print(f"  评价区间 {ev['nav'].index[0]:%Y-%m-%d} ~ {ev['nav'].index[-1]:%Y-%m-%d} · "
          f"{len(ev['nav'])} 个交易日 · 预热 {CFG['WARMUP']} 日")
    print(f"  趋势口径 {CFG['TREND_MODE']} · BAND {CFG['BAND']:.2%} · "
          f"退出 dev60≤{CFG['EXIT_DEV']:.0%} 或 vol20≥{CFG['EXIT_VOL']:.0%} · "
          f"回场 dev60≥{CFG['RECOVER_DEV']:.0%} 且 vol20≤{CFG['RECOVER_VOL']:.0%}")
    print(f"  策略: 年化 {fmt_pct(ev['m']['ann'])} · 回撤 {fmt_pct(ev['m']['mdd'])} · "
          f"夏普 {ev['m']['sharpe']:.2f} · Calmar {fmt_x(ev['m']['calmar'])} · "
          f"曝光 {ev['exp']:.0%} · {ev['stats']['n_trades']} 笔")
    print(f"  基准: 年化 {fmt_pct(ev['mbh']['ann'])} · 回撤 {fmt_pct(ev['mbh']['mdd'])} · "
          f"Calmar {fmt_x(ev['mbh']['calmar'])}")
    print(f"  最新 {r['date']:%Y-%m-%d}: 收盘 {r['close']:.0f} · MA120 {r['ma120']:.0f} · "
          f"dev60 {r['dev60']:+.2%} · vol20 {r['vol20']:.1%} · "
          f"状态 {'在场' if r['state'] else '空仓'}（已 {r['days_in_state']} 日）")
    print(f"  退出原因: trend {ev['stats']['exit_trend']} / dev60 {ev['stats']['exit_dev']} / "
          f"vol20 {ev['stats']['exit_vol']}")


if __name__ == "__main__":
    _self_check()
