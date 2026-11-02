# -*- coding: utf-8 -*-
"""波动资产 · V6 变种：4 票打分制（MA / MACD / KDJ / 放量）—— 规则与回测引擎。

来源：`strategies/tech_indicators/v6_score_wf`（技术指标家族的打分制 + Walk-Forward）。
迁入 vol_legs 后**规则本体一条没改**，只换两样东西：**标的池**与**价格口径**：

- **池** = `rule.LEG_ORDER` 六腿（A500 / 纳指100 / 标普500 / 科创50 / 恒生科技 / 黄金），
  不另立池、不做 A/B 分级，增删腿只改 `rule.LEGS`；
- **价格** = `data.series_for_leg(leg)` 场外**复权净值**（`rule.SIGNAL_MODE`，含分红再投 /
  汇率 / 跟踪误差），与主策略、v1/v2/v3/v5 同一列；
- **成交** = T 日盘后算信号、**T+1 收盘净值成交**（`pos = sig.shift(1)`），与 vol_legs 同一纪律；
- **空仓**按货基 `rule.CASH_ANNUAL`（1.40%）逐日计息；成本另列（真实申赎费 + 单边滑点 0.1%）。

## 1. 打分制（4 票，每日各记 0/1，求和 = score）

| # | 票 | 条件 |
|---|---|---|
| ① | 双均线之上 | 价 > MA20 **且** 价 > MA60 |
| ② | MACD 多头 | DIF > DEA **且** DIF > 0 |
| ③ | KDJ 多头 | K > D |
| ④ | 放量上涨 | 价涨（收盘 > 前收）**且** 量 > 5 日均量 |

进出阈值**不写死**：`enter ∈ {2,3,4} × exit ∈ {1,2,3}`（仅取 exit < enter，6 组），
以「全池中位夏普」选参 —— 源变种的网格最优是 `(4, 2)`，本变种复算后同样为 `(4, 2)`
（`backtest.py` 会校验并在不一致时提示）。

## 2. 换池后绕不开的三处口径（必须知道）

### ① ④「放量」的成交量从哪来 —— 场内 ETF 成交量代理
场外**复权净值没有成交量**。本变种用同一资产的**场内 ETF 真实成交量**（`data/_indicators_hist.json`
的 `volume` 字段）代理，代码取 `rule.LEGS[leg]`：

| 腿 | 量能代理 | 说明 |
|---|---|---|
| 中证A500 | `563360`（2024-10 起）→ 前段 `510300` | 按重叠窗口的量级比例缩放拼接，与价格序列的桥接口径一致 |
| 纳指100 | `513100` | 美元指数 `.NDX` 无成交量，只能用人民币 ETF |
| 标普500 | `513500` | 同上 |
| 科创50 / 恒生科技 / 黄金 | `588000` / `513180` / `518880` | 即该腿的信号 ETF |

**这是本变种唯一的跨家族数据依赖**：`_indicators_hist.json` 由 `data/_refresh_data.py` 维护
（该文件按库内现有代码增量刷新，不看 tech_indicators 的池）。若某腿的成交量代码缺失，
`volume_for()` **直接抛错**而不是静默记 0 —— 静默会让 ④ 恒为 0、评分系统性偏低，且没有报错。

### ② KDJ / MACD 用源变种库里的算式，不用 v5 的
`data/_gen_indicators.py` 的 KDJ 是「RSV 的 3 日**简单**均线」再取 3 日均线（不是中式 SMA 递推），
MACD 用 `ewm(span=...)` 默认 `adjust=True`。本变种照抄这两个算式（`kdj_series` / `macd_series`），
所以与 xalpha 技术指标库外常见的「中式 SMA 递推 SKDJ + `adjust=False`」**数值不同**，两套阈值不可混用。
（v5 槽位 2026-11-02 已换成趋势四象限闸门，不再有 BOLL+SKDJ 变种可比。）

### ③ 净值口径把「价 > MA20 且 > MA60」变成复权口径
场外净值含分红再投，均线天然上移；这与源变种（场内 ETF 不复权收盘）不可直接比。
另外 A500/标普的早期段是桥接（`000051` / `050025`），那段不是本基金业绩。

### ④ 成本是第一阶问题：平均持仓 2.6~3.1 日 × 场外「7 日内 1.5% 惩罚赎回费」
源变种**完全不计成本**（场内 ETF kline、无手续费），所以它的 4/2 能做出 +3.5% 的中位年化。
本变种的信号在净值上平均只持仓 **2.6~3.1 个交易日**、年 5~8 笔 —— 而按 2017 年起的强制规定，
开放式基金**持有不足 7 天的赎回费不低于 1.5%**。也就是说每笔往返的真实成本约
**1.5% + 0.1% 申购 + 0.2% 滑点 ≈ 1.8%**，年 6 笔就是 ~11% 的 NAV 拖累，这条规则在
场外 A/C 类上**不可执行**。

所以本变种把成本拆成四档并列（`FEE_SCENARIOS`），不做单一口径的假设：

| scenario | 含义 | 单边费率 |
|---|---|---|
| `none` | 源变种口径（不计成本） | 0 |
| `std` | 场外标准费率，假设都持有 >7 天 | 申购 0.1% / 赎回 0.5%（C 类免费）+ 滑点 0.1% |
| `pen`（默认） | 场外真实口径：持仓 <7 天按 1.5% 赎回费 | 同上 + 短持卖出 1.5% |
| `etf` | 换成**场内 ETF 载体**（同一资产、同一信号） | 佣金万 1.5 + 滑点 0.05%/边，无惩罚赎回费 |

`pen` 与 `etf` 的差值就是「这条规则该用什么载体」的答案 —— 结论见 README §4。

## 3. 与其它变种的关系
同 v1/v2/v3/v5 一层：管**逐腿进出时点**，与主策略的组合层仓位带可叠加不可替代。
它与 v1_ma20 同向（做多趋势），区别是**用 4 个条件投票 + 滞回**而不是单均线穿越 ——
换手更低、空仓更久。模拟结果，非投资建议。
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
_VL = os.path.dirname(_HERE)                        # strategies/vol_legs
_ROOT = os.path.dirname(os.path.dirname(_VL))       # 仓库根
for _p in (_HERE, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import data as vdata    # noqa: E402
import engine           # noqa: E402
import rule             # noqa: E402

LABEL = "V6 打分制(4票)"

# ---------------------------------------------------------------- 1. 参数（唯一权威源）
MA_FAST, MA_SLOW = 20, 60
MACD_FAST, MACD_SLOW, MACD_SIG = 12, 26, 9
KDJ_N, KDJ_K, KDJ_D = 9, 3, 3
VOL_MA = 5
WARMUP = MA_SLOW                 # 与源变种一致：MA60 预热完成后起算

GRID_ENTER = (2, 3, 4)
GRID_EXIT = (1, 2, 3)
PARAMS = [(e, x) for e in GRID_ENTER for x in GRID_EXIT if x < e]   # 6 组
DEFAULT_PARAM = (4, 2)           # 源变种的中位夏普最优组

CASH_ANNUAL = rule.CASH_ANNUAL
CASH_DAILY = (1.0 + CASH_ANNUAL) ** (1.0 / 252) - 1.0
LEGS = rule.LEG_ORDER
W = {k: float(rule.LEGS[k]["band"]) / float(rule.SLEEVE_PP) for k in LEGS}   # 带权归一化(Σ=1)

# 场外申赎成本（(买入, 卖出) 单边费率），与 v1/v2/v3/v5 同一张表：
# A 类（270042 / 017641 / 000216）申购 0.10% + 赎回 0.50%（持有 >7 天）；
# C 类（023299 / 011609 / 012805）申赎免费，改收约 0.2%/年销售服务费 —— 后者**未计入**。
FEE = {
    "a500": (0.0, 0.0),
    "ndx": (0.001, 0.005),
    "spx": (0.001, 0.005),
    "kc50": (0.0, 0.0),
    "hkt": (0.0, 0.0),
    "gold": (0.001, 0.005),
}
SLIP = 0.001                     # 场外单边滑点 0.1%（源变种未计成本，本变种补上）
# 场外基金「持有 <7 天强制赎回费 1.5%」（2017 年起对所有开放式基金适用，货币基金/ETF 除外）。
# 本变种平均持仓只有 2.5~4.5 个交易日，这条硬约束是第一阶成本，必须建模，否则结论会假乐观。
# 判定用**自然日**（监管口径是 7 天，不是 7 个交易日）：7 个交易日 ≈ 10 天，用交易日会多收费。
SHORT_FEE, SHORT_DAYS = 0.015, 7
# 场内 ETF 口径（同一资产、同一信号，只换载体）：佣金万 1.5 + 滑点 0.05%/边，无惩罚赎回费
ETF_COMM, ETF_SLIP = 0.00015, 0.0005
FEE_SCENARIOS = ("none", "std", "pen", "etf")
FEE_LABEL = {
    "none": "不计费（源变种口径）",
    "std": "场外标准费率（持有 >7 天）",
    "pen": "场外 + 7 日内 1.5% 惩罚赎回费",
    "etf": "场内 ETF（佣金万1.5 + 滑点 0.05%）",
}

# ④「放量」的成交量代理（见模块 docstring §2-①）
VOL_PRIMARY = {"a500": "563360", "ndx": "513100", "spx": "513500",
               "kc50": "588000", "hkt": "513180", "gold": "518880"}
VOL_BRIDGE = {"a500": rule.A500_PROXY_CODE}      # 563360 上市 2024-10，之前用 510300

_IND_LIB = os.path.join(_ROOT, "data", "_indicators_hist.json")
_VOL_CACHE = {}


def load(leg, mode=None):
    """该腿价格序列（默认场外复权净值）；缺失/刷新失败直接抛错，不静默补位。"""
    return vdata.series_for_leg(leg, mode=mode).dropna()


def fee_of(leg, scenario="pen", slip=None):
    """-> (买入费率, 卖出费率, 短持卖出费率 | None)。`scenario` 见 `FEE_SCENARIOS`。"""
    s = SLIP if slip is None else float(slip)
    b, sl = FEE[leg]
    if scenario == "none":
        return 0.0, 0.0, None
    if scenario == "etf":
        c = ETF_COMM + ETF_SLIP
        return c, c, None
    if scenario == "std":
        return float(b) + s, float(sl) + s, None
    if scenario == "pen":
        return float(b) + s, float(sl) + s, SHORT_FEE
    raise ValueError(f"未知成本口径 {scenario}（可选 {FEE_SCENARIOS}）")


def _vol_series(code, lib=None):
    """`data/_indicators_hist.json` 取单码成交量 -> Series(index=DatetimeIndex)。"""
    if code in _VOL_CACHE:
        return _VOL_CACHE[code]
    lib = lib if lib is not None else json.load(open(_IND_LIB, encoding="utf-8"))
    node = lib.get(code)
    if not node or not node.get("volume"):
        raise KeyError(f"{code} 不在 data/_indicators_hist.json 或没有 volume 字段"
                       f"（④「放量」票依赖场内成交量；先跑 data/_refresh_data.py）")
    s = pd.Series([np.nan if v is None else float(v) for v in node["volume"]],
                  index=pd.to_datetime(node["dates"]), dtype="float64").sort_index()
    _VOL_CACHE[code] = s
    return s


def volume_for(leg, index):
    """该腿的成交量序列（场内 ETF 代理），对齐到净值日期。

    A500 的 563360 2024-10 才上市：之前用 510300 桥接，并按**重叠窗口的量级比例**缩放，
    使拼接处不出现假的「放量」（与 `data._splice` 对价格的处理同一思路）。
    对齐方式 = reindex + ffill(limit=5)；仍缺的日子留 NaN（该日 ④ 记 0，不猜）。
    """
    s = _vol_series(VOL_PRIMARY[leg])
    bridge = VOL_BRIDGE.get(leg)
    if bridge:
        b = _vol_series(bridge)
        head = b[b.index < s.index[0]]
        common = b.index.intersection(s.index)
        if len(head) and len(common) > 20:
            scale = float(s.loc[common].median()) / float(b.loc[common].median())
            s = pd.concat([head * scale, s]).sort_index()
    return s.reindex(index).ffill(limit=5)


# ---------------------------------------------------------------- 2. 指标（算式抄自 _gen_indicators）
def macd_series(c):
    """与 `_gen_indicators.macd_series` 同一算式（ewm 默认 adjust=True）-> (DIF, DEA, HIST)。"""
    s = c.astype("float64")
    e1, e2 = s.ewm(span=MACD_FAST).mean(), s.ewm(span=MACD_SLOW).mean()
    dif = e1 - e2
    dea = dif.ewm(span=MACD_SIG).mean()
    return dif, dea, dif - dea


def kdj_series(c):
    """与 `_gen_indicators.kdj_series` 同一算式（RSV 的简单均线，不是 SMA 递推）-> (K, D)。"""
    s = c.astype("float64")
    roll = s.rolling(KDJ_N)
    lo, hi = roll.min(), roll.max()
    rsv = ((s - lo) / (hi - lo)).where(hi != lo, 0.5)
    k = rsv.rolling(KDJ_K).mean()
    d = k.rolling(KDJ_D).mean()
    return k * 100.0, d * 100.0


def indicators(px, vol):
    """-> DataFrame(close/ma_fast/ma_slow/dif/dea/hist/k/d/vol/vol_ma5/prev)。全部因果。"""
    out = pd.DataFrame(index=px.index)
    c = px.astype("float64")
    out["close"] = c
    out["prev"] = c.shift(1)
    out["ma_fast"] = c.rolling(MA_FAST).mean()
    out["ma_slow"] = c.rolling(MA_SLOW).mean()
    dif, dea, hist = macd_series(c)
    out["dif"], out["dea"], out["hist"] = dif, dea, hist
    k, d = kdj_series(c)
    out["k"], out["d"] = k, d
    v = vol.reindex(px.index).astype("float64")
    out["vol"] = v
    out["vol_ma5"] = v.rolling(VOL_MA).mean()
    return out


def votes(ind):
    """4 票 -> DataFrame(v1..v4)，NaN 表示该日该票不可判定（上游指标未成形）。"""
    c = ind["close"]
    out = pd.DataFrame(index=ind.index)
    out["v1"] = ((c > ind["ma_fast"]) & (c > ind["ma_slow"])).astype(float)
    out["v2"] = ((ind["dif"] > ind["dea"]) & (ind["dif"] > 0)).astype(float)
    out["v3"] = (ind["k"] > ind["d"]).astype(float)
    out["v4"] = ((c > ind["prev"]) & (ind["vol"] > ind["vol_ma5"])).astype(float)
    need = ["ma_fast", "ma_slow", "dif", "dea", "k", "d", "vol_ma5", "prev"]
    bad = ind[need].isna().any(axis=1)
    out.loc[bad, :] = np.nan
    return out


def score(ind):
    """score = 4 票求和（含 NaN -> NaN，与源变种「任一指标缺失则维持原仓位」一致）。"""
    return votes(ind).sum(axis=1, skipna=False)


def signal(px, vol, enter=DEFAULT_PARAM[0], exit_th=DEFAULT_PARAM[1]):
    """0/1 目标仓位：score ≥ enter 买入、≤ exit 离场（滞回）；NaN 日维持原仓位。"""
    sc = score(indicators(px, vol)).to_numpy(dtype=float)
    n = len(sc)
    sig = np.zeros(n, dtype=int)
    p = 0
    for t in range(1, n):
        if np.isnan(sc[t]):
            sig[t] = p
            continue
        if p == 0 and sc[t] >= enter:
            p = 1
        elif p == 1 and sc[t] <= exit_th:
            p = 0
        sig[t] = p
    return pd.Series(sig, index=px.index)


# ---------------------------------------------------------------- 3. 回测引擎
def run(state, px, fee=(0.0, 0.0)):
    """单腿净值（起点 = 1）。持仓按 `state.shift(1)` 赚当日收益，成交日扣双边成本。

    `fee` = `fee_of()` 的返回值 `(买入, 卖出, 短持卖出 | None)`；持有不足
    `SHORT_DAYS` 个**自然日**时卖出费率换成 `短持卖出`（场外 1.5% 惩罚赎回费），
    故必须逐笔判断持有天数，不能只看 `diff` 的符号。
    """
    ret = px.pct_change().fillna(0.0)
    hold = state.shift(1).fillna(0.0).astype(float)
    r = (hold * ret + (1.0 - hold) * CASH_DAILY).to_numpy(dtype=float)
    s = state.to_numpy(dtype=float)
    dates = px.index
    cost = np.zeros(len(s))
    entry = None
    for i, v in enumerate(s):
        if v > 0 and entry is None:
            entry = i
            cost[i] += float(fee[0])
        elif v <= 0 and entry is not None:
            short = fee[2] if (len(fee) > 2 and fee[2] is not None
                               and (dates[i] - dates[entry]).days < SHORT_DAYS) else fee[1]
            cost[i] += float(short)
            entry = None
    return (1.0 + pd.Series(r - cost, index=px.index)).cumprod()


def trades(state, px, fee=(0.0, 0.0, None)):
    """按 0→1 / 1→0 翻转折出单笔（成交日 = 信号次日）。最后一段未平仓也计入。"""
    s = state.to_numpy(dtype=float)
    idx, val = px.index, px.to_numpy(dtype=float)
    short = fee[2] if len(fee) > 2 else None
    out, entry = [], None
    for i, v in enumerate(s):
        if v > 0 and entry is None:
            entry = i
        elif v <= 0 and entry is not None:
            bars = i - entry
            sell = short if (short is not None
                             and (idx[i] - idx[entry]).days < SHORT_DAYS) else fee[1]
            gross = val[i] / val[entry] - 1.0
            out.append(dict(entry=idx[entry], exit=idx[i], bars=bars,
                            ret=gross - (fee[0] + sell), gross=gross, open=False))
            entry = None
    if entry is not None:
        gross = val[-1] / val[entry] - 1.0
        out.append(dict(entry=idx[entry], exit=idx[-1], bars=len(s) - 1 - entry,
                        ret=gross - fee[0], gross=gross, open=True))
    return out


def trade_stats(tr):
    """单笔统计：笔数 / 胜率 / 盈亏比 / 利润因子 / 平均持仓交易日。"""
    if not tr:
        return dict(n=0, win_rate=0.0, avg_win=0.0, avg_loss=0.0, payoff=0.0,
                    pf=0.0, bars=0.0)
    wins = [t["ret"] for t in tr if t["ret"] > 0]
    loss = [t["ret"] for t in tr if t["ret"] <= 0]
    gp, gl = sum(wins), -sum(loss)
    aw = gp / len(wins) if wins else 0.0
    al = gl / len(loss) if loss else 0.0
    return dict(n=len(tr), win_rate=len(wins) / len(tr),
                avg_win=aw, avg_loss=-al if loss else 0.0,
                payoff=(aw / al) if al > 1e-12 else float("inf") if aw else 0.0,
                pf=(gp / gl) if gl > 1e-12 else float("inf") if gp else 0.0,
                bars=sum(t["bars"] for t in tr) / len(tr))


def perf(nav):
    """绩效指标（复用主策略 engine.metrics：无风险利率 = 货基 CASH_ANNUAL）。"""
    return engine.metrics(nav)


def exposure(state):
    """平均曝光（0–1）。"""
    return float(state.mean()) if len(state) else 0.0


def leg_slice(leg, param=DEFAULT_PARAM):
    """-> (px, vol, sig)。样本自第 WARMUP+1 个交易日起算（MA60 成形）。"""
    px_full = load(leg)
    vol_full = volume_for(leg, px_full.index)
    sig_full = signal(px_full, vol_full, param[0], param[1])
    start = px_full.index[min(WARMUP, max(len(px_full) - 2, 0))]
    return px_full.loc[start:], vol_full.loc[start:], sig_full.loc[start:]


def common_frame():
    """六腿净值的共同窗口（各自 MA60 预热之后再取交集）。"""
    cols = {}
    for k in LEGS:
        px = load(k)
        if len(px) > WARMUP:
            px = px.iloc[WARMUP:]
        cols[k] = px
    return pd.DataFrame(cols).dropna(how="any").sort_index()


def portfolio(frame, states=None, weight=None, scenario="pen", detail=False):
    """组合层净值：Σ w·[pos·ret + (1−pos)·货基] − 成本。`detail=True` 返回 (nav, 平均投入)。

    成本按**逐腿逐笔**计（`scenario` 见 `FEE_SCENARIOS`），短持卖出费率按该腿的实际持仓天数判定。
    """
    weight = weight or W
    ret = frame.pct_change().fillna(0.0)
    cols = list(frame.columns)
    idx = frame.index
    if states is None:
        raw = {k: pd.Series(1.0, index=idx) for k in cols}
    else:
        raw = {k: states[k].reindex(idx).fillna(0.0).astype(float) for k in cols}
    hold = {k: raw[k].shift(1).fillna(0.0) for k in cols}
    fee_of_leg = {k: fee_of(k, scenario) for k in cols}
    nav = 1.0
    entry = {k: None for k in cols}
    out, inv = [], []
    for i in range(len(idx)):
        if i == 0:
            out.append(nav)
            inv.append(0.0)
            continue
        r, cost, e_sum = 0.0, 0.0, 0.0
        for k in cols:
            e = float(hold[k].iloc[i])
            e_sum += weight[k] * e
            r += weight[k] * (e * float(ret[k].iloc[i]) + (1.0 - e) * CASH_DAILY)
            v = float(raw[k].iloc[i])
            if v > 0 and entry[k] is None:                 # 该腿当日成交建仓
                entry[k] = i
                cost += weight[k] * fee_of_leg[k][0]
            elif v <= 0 and entry[k] is not None:           # 该腿当日成交平仓
                short = fee_of_leg[k][2]
                sell = (short if (short is not None
                                  and (idx[i] - idx[entry[k]]).days < SHORT_DAYS)
                        else fee_of_leg[k][1])
                cost += weight[k] * sell
                entry[k] = None
        nav *= (1.0 + r - cost)
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
    """快速自检：六腿序列可读、量能代理可用、4 票与信号对得上。"""
    for k in LEGS:
        px, vol, sig = leg_slice(k)
        sc = score(indicators(px, vol))
        assert len(sig) == len(px) and set(sig.unique()) <= {0, 1}
        tr = trade_stats(trades(sig, px))          # 胜率看信号本身，成本单列在年化里
        yrs = max((px.index[-1] - px.index[0]).days / 365.25, 1e-9)
        nav_g = perf(run(sig, px, fee_of(k, "none")))["ann"]
        nav_e = perf(run(sig, px, fee_of(k, "etf")))["ann"]
        nav_p = perf(run(sig, px, fee_of(k, "pen")))["ann"]
        print(f"{k:6s} {rule.LEGS[k]['name']:8s} {px.index[0].date()} ~ {px.index[-1].date()} "
              f"n={len(px):5d} 仓位={int(sig.iloc[-1])} 曝光={exposure(sig):.0%} "
              f"score末={sc.iloc[-1] if sc.iloc[-1] == sc.iloc[-1] else '—'} "
              f"{tr['n']:3d} 笔({tr['n'] / yrs:.1f}/年) 胜率={tr['win_rate']:.0%} "
              f"年化 毛{nav_g:+.2%}/ETF{nav_e:+.2%}/场外真实{nav_p:+.2%}")
    print(f"参数网格 {PARAMS} · 默认 {DEFAULT_PARAM} · 场外滑点 {SLIP:.2%}/边 · "
          f"短持({SHORT_DAYS}日内)赎回费 {SHORT_FEE:.1%} · "
          f"带权 Σ=1: " + "  ".join(f"{k}={W[k]:.3f}" for k in LEGS))


if __name__ == "__main__":
    _self_check()
