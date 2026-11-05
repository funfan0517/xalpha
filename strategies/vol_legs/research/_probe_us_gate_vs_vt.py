# -*- coding: utf-8 -*-
"""波动资产 · **v7（年线闸门）vs v9（波动率目标）** 的统一口径对决 + 长样本外推。

## 为什么需要这个探针

两个变种各自的 README 都给出了「自己更好」的结论，但**不在同一把尺子上**：

| | v7_us_gate | v9_vol_target |
|---|---|---|
| 单边成本 | 0.10%（文档主口径） | **0.03%**（R-02） |
| 空仓计息 | 1.40%（`rule.CASH_ANNUAL`） | **2.00%**（R-03） |
| 组合层年化 | +13.07% / 回撤 −18.90% | +13.12% / 回撤 −15.92% |
| 样本 | 2012-08 ~ 2026-11 | 2012-08 ~ 2026-11 |
| 换手 | 70 次（14 年） | 251 次（14 年） |

成本差 3 倍、计息差 0.6pp —— 直接对比这两行数字是无效的。本探针做两件事：

1. **同一把尺子**（同一成本档 × 同一计息 × 同一区间 × 同一组合权重）重算 v7 与 v9；
2. **把样本往前拉到 1995 年**（指数口径，31.8 年），覆盖两个 README 都点名的最大缺口 ——
   2000-2002（纳指 −83%）与 2008。这两段才是「趋势闸门 vs 波动目标」真正能分出高下的地方，
   而 2012 起的样本里一次都没有。

## 结论（一句话）

**骨架用 v7、不要用 v9**：31.8 年样本上 v7 默认 +12.90% / 日频回撤 −21.85% / 夏普 0.77，
v9 默认 +11.38% / **−42.11%** / 0.69 —— v9 在 2012+ 唯一的优势（回撤更浅）在 2000-2002 彻底反转，
因为波动率目标只能按比例减（`min(1, 15%/vol)` 永远减不到 0）。
反过来 **v7 的波动层不能拿掉**：纯闸门（TV 关闭）的**全样本最大回撤正是 2000-03→2000-05 那道
「2 个月确认盲窗」的 −30.09%**，而波动层靠 2000 年 3 月末 vol20=48.9% 当场把仓位压到 4 成。
两个新候选里只有 C1（把 v9 的月中补丁装到 v7 上）略有增益（夏普 0.77→0.79、2020 窗口 +5.02pp），
TSMOM 替代 MA200 是退步（晚 8 个月才走）。

## 方法

**一套状态机，三个旋钮**（v7 与 v9 在实现上是同一台机器的两组参数）：

```
目标权重 fw[d] = gate[d] × min(cap, TV / vol20[d])
月末：闸门未变且 |fw - 现仓| < dead  -> 不动
月中快减（可选）：5 日跌幅 ≤ CUT_RET 且 vol20 ≥ CUT_VOLX×TV -> 立即到 fw
月中快补（可选）：vol20 ≤ DECAY×60日峰值 且 站上 MA10   -> 立即到 fw
```

| 变体 | gate | TV | dead | 月中补丁 |
|---|---|---|---|---|
| B1 = v7 默认 | MA200（2 月确认 + 深破 3%）| 20% | 10pp | 关 |
| B2 = v9 默认 | 恒 1（无趋势）| 15% | 5pp | 开（快减+快补）|
| B3 = v7 纯闸门 | MA200 | — | 闸门自身 | 关 |
| B4 = v9 月度档 | 恒 1 | 15% | 5pp | 关 |
| **C1 = 混血（未测过）** | MA200 | 20% | 10pp | **开** |
| **C2 = TSMOM（未测过）** | 12 个月绝对动量 | 20% | 10pp | 关 |
| C3 = TSMOM 纯闸门 | 12 个月绝对动量 | — | 闸门自身 | 关 |
| 静态 50/50 | — | — | — | — |

C1 的动机：v7 自己承认「治慢熊、不治急跌」，v9 的补丁正是修急跌的；v9 自己承认
「不做方向判断，低波动阴跌里满仓挨打」，v7 的闸门正是治那个的。**两者失效模式互补**，
但家族里从没把「闸门 × VT × 月中补丁」放在一起测过。
C2 的动机：12 个月绝对动量（TSMOM）是文献里样本外证据最长的趋势定义，
在 2000/2008 上都有独立记录 —— 用来检验「MA200 这条特定均线是不是运气」。

## 口径

- **QDII 表**：用 v7/v9 模块自己的代码（含场外净值日历桥接），只把 `cost` / `cash` 拉齐；
- **长样本表**：指数收盘同时作信号与执行价（`.INX` 1990-01 起 / `.NDX` 1994-01 起），
  **不含分红**。零分红会低估买入持有约 1~2pp/年，即**对降仓策略有利**；
  现金又只按 1.4% 计息（1990 年代美元短端常在 5%），**对防守档不利** —— 两个偏差大致抵消。
- **数据清洗**：雪球 `.NDX` 在 1994-01-03 从 796.56 跳到 395.53（−50.4%），是指数重定基不是崩盘，
  `_clean_break()` 把断点前整段丢弃（不缩放修复，无法确认前段成分口径）→ 长样本从 1995 起。
  不处理会在 1994 年凭空造出 −30% 回撤，把「谁防得住崩盘」的结论整段污染。
- 一律 T 收盘决策 → T+1 收盘成交 → T+2 起承担收益；成本按 `Σ|Δw|` 单边扣；
  未投部分按 1.40%（仓库惯例，`rule.CASH_ANNUAL`）逐日计息。

## 忠实性校验（硬断言）

统一机器必须与模块原实现**逐条一致**，否则这个探针只是在测我自己的 bug：

- v7：`ug.decisions(leg)` 的 194 条月末闸门与 target 与本文机器逐条相等（差 0.00e+00）；
- v9：`vt.machine(sig)` 的逐日仓位与本文机器（fast / month 两档）最大差 0.00e+00。

产物：
- `_probe_us_gate_vs_vt.md`（本脚本生成）
- `_probe_us_gate_vs_vt.png`（长样本净值 + 仓位）

模拟结果，非投资建议。
"""
import json
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

_HERE = os.path.dirname(os.path.abspath(__file__))          # strategies/vol_legs/research
_VL = os.path.dirname(_HERE)                                # strategies/vol_legs
_ROOT = os.path.dirname(os.path.dirname(_VL))               # 仓库根（推导，不写死）
for _p in (_HERE, _VL, _ROOT,
           os.path.join(_VL, "v7_us_gate"), os.path.join(_VL, "v9_vol_target")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import data as vdata      # noqa: E402
import engine             # noqa: E402
import rule               # noqa: E402

vdata._otc_stale = lambda: False   # 离线优先：只读本地缓存  # type: ignore[assignment]

import us_gate as ug      # noqa: E402
import vol_target as vt   # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

OUT_MD = os.path.join(_HERE, "_probe_us_gate_vs_vt.md")
OUT_PNG = os.path.join(_HERE, "_probe_us_gate_vs_vt.png")
LONG_LK = os.path.join(_ROOT, "data", "_us_index_long.json")

POOL = ("ndx", "spx")
CODES = {"ndx": ".NDX", "spx": ".INX"}
NAME = {k: rule.LEGS[k]["name"] for k in POOL}
CASH = rule.CASH_ANNUAL            # 1.40%（统一计息，v9 的 2.0% 见报告 §8 备注）
FEE = {"场内/低费 0.03%": 0.0003, "文档主口径 0.10%": 0.001, "场外 A 类 0.60%": 0.006}
# 长样本起点：`.NDX` 在 1994-01-03 有一处 **50% 的指数重定基**（雪球前段点位是后段的 1.99 倍，
# 不是真实行情 —— 直接用会在 1994 年凭空造出 −30% 回撤）。`_clean_break` 把断点之前整段丢弃，
# 所以长样本从断点之后 + 各窗口预热完毕起算（= 1995-01）。仍含 2000-2002 / 2008 / 2020 / 2022。
START_LONG = "1995-01-03"
BREAK_THR = 0.30                   # 单日 |涨跌| > 30% 一律当数据重定基（真实指数从没这种日波动）
TVS = (0.0, 0.12, 0.15, 0.20, 0.25, 0.30, 0.40)

# 预注册的起点（不是搜出来的）：全来自两篇 README 的默认档
DEF = dict(
    ma_win=200, confirm=2, deep=0.03, hyst=0.0, floor={"ndx": 0.0, "spx": 0.5},
    tv=0.20, vol_win=20, dead=0.10, cap=1.0,
    fast=False, cut_ret=-0.05, cut_volx=1.4, decay=0.70, peak_win=60, add_ma=10,
    lookback=252,
)


# ---------------------------------------------------------------- 0. 长历史数据
def _clean_break(s, thr=BREAK_THR):
    """丢掉**指数重定基**造成的假跌幅之前的所有数据 -> (清洗后序列, 断点日期或 None)。

    `.NDX` 1994-01-03 从 796.56 掉到 395.53（−50.4%），之后走势与真实纳指一致 —— 是雪球前段
    用了另一个基期，不是崩盘。不清洗会在 1994 年凭空造出「−30% 最大回撤」，把长样本的
    结论整段污染。这里**不做缩放修复**（无法确认前段的成分口径），直接把断点前整段丢掉。
    """
    r = s.pct_change()
    bad = r[r.abs() > thr]
    if not len(bad):
        return s, None
    cut = bad.index.max()
    return s[s.index > cut], cut


def long_series(code):
    """美股指数**长历史**收盘（研究用共享库 `data/_us_index_long.json`）。

    与每日管线用的 `data/_us_index_klines.json`（2010 起、增量刷新）分开存：
    这里要的是 1985/1990 起的深历史，只用于研究，不参与每日决策，避免改动线上库的口径。
    读出来先过 `_clean_break`（见其文档：`.NDX` 1994 年那次重定基）。
    """
    try:
        with open(LONG_LK, encoding="utf-8") as f:
            lib = json.load(f)
    except (OSError, ValueError):
        lib = {}
    node = lib.get(code)
    if node and len(node.get("dates", [])) > 3000:
        s = pd.Series(node["close"], index=pd.to_datetime(node["dates"]),
                      dtype="float64").sort_index()
        s, cut = _clean_break(s)
        if cut is not None:
            print(f"  [清洗] {code} 断点在 {cut.date()}（指数重定基），前段整段丢弃 "
                  f"-> 起点 {s.index[0].date()}")
        return s
    import xalpha as xa
    # 雪球对不同指数能回溯到的最早日期不同（.INX 从 1990 起可以，往前就报错），逐个起点试
    df, err = None, None
    for st in ("19850101", "19900101", "19950101", "20000101"):
        try:
            df = xa.get_daily(code, start=st)
            if df is not None and len(df) > 1000:
                break
        except Exception as exc:  # noqa: BLE001
            err = exc
            df = None
    if df is None or not len(df):
        raise RuntimeError(f"{code} 长历史取不到: {err}")
    df = df.copy()
    df["d"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    lib[code] = dict(dates=list(df["d"]), close=[float(x) for x in df["close"]])
    os.makedirs(os.path.dirname(LONG_LK), exist_ok=True)
    with open(LONG_LK, "w", encoding="utf-8") as f:
        json.dump(lib, f, ensure_ascii=False)
    s = pd.Series(lib[code]["close"], index=pd.to_datetime(lib[code]["dates"]),
                  dtype="float64").sort_index()
    s, cut = _clean_break(s)
    if cut is not None:
        print(f"  [清洗] {code} 断点在 {cut.date()}（指数重定基），前段整段丢弃 "
              f"-> 起点 {s.index[0].date()}")
    return s


# ---------------------------------------------------------------- 1. 通用机器
def month_ends(idx):
    idx = pd.DatetimeIndex(idx)
    out = [idx[i] for i in range(len(idx) - 1)
           if (idx[i].year, idx[i].month) != (idx[i + 1].year, idx[i + 1].month)]
    if len(idx):
        out.append(idx[-1])
    return out


def vol_series(sig, n):
    return sig.pct_change().rolling(int(n), min_periods=int(n)).std(ddof=1) * np.sqrt(252.0)


def gate_ma200(sig, leg, p):
    """v7 的闸门层（逐字复刻 `us_gate.decisions` 的 gate 部分）-> (月末决策, 日频前向填充)。"""
    ma = sig.rolling(int(p["ma_win"]), min_periods=int(p["ma_win"])).mean()
    fl = float(p["floor"].get(leg, 0.0) if isinstance(p["floor"], dict) else p["floor"])
    hold, prev = 1.0, 1.0
    below = 0
    dec = []
    for d in month_ends(sig.index):
        s, m = float(sig.loc[d]), float(ma.loc[d])
        if not np.isfinite(s) or not np.isfinite(m):
            continue
        gap = s / m - 1.0
        if s > m * (1.0 + float(p["hyst"])):
            below, g = 0, 1.0
        else:
            below += 1
            deep = (float(p["deep"] or 0) > 0 and gap <= -float(p["deep"])
                    and below < int(p["confirm"]))
            g = fl if (deep or below >= int(p["confirm"])) else prev
        dec.append(dict(date=d, gate=float(g)))
        prev = g
    s = pd.Series({x["date"]: x["gate"] for x in dec}, dtype="float64").sort_index()
    return dec, s.reindex(sig.index).ffill().fillna(1.0)


def gate_tsmom(sig, leg, p):
    """12 个月绝对动量：月末收盘 > `lookback` 根之前 -> 满仓，否则 floor。"""
    n = int(p["lookback"])
    fl = float(p["floor"].get(leg, 0.0) if isinstance(p["floor"], dict) else p["floor"])
    lb = sig.shift(n)
    dec = []
    for d in month_ends(sig.index):
        s, q = float(sig.loc[d]), float(lb.loc[d]) if d in lb.index else np.nan
        if not np.isfinite(s) or not np.isfinite(q):
            continue
        dec.append(dict(date=d, gate=1.0 if s > q else fl))
    s = pd.Series({x["date"]: x["gate"] for x in dec}, dtype="float64").sort_index()
    return dec, s.reindex(sig.index).ffill().fillna(1.0)


def machine(sig, gate_daily, dec, p):
    """统一状态机 -> (仓位序列, 事件表)。`state[d]` = d 日**收盘时已持有**的仓位。"""
    vol = vol_series(sig, p["vol_win"])
    tv = float(p["tv"] or 0.0)
    scale = (tv / vol).clip(upper=float(p["cap"])) if tv > 0 else pd.Series(1.0, index=sig.index)
    fw = (gate_daily * scale).clip(lower=0.0)
    peak = vol.rolling(int(p["peak_win"]), min_periods=20).max()
    ma_fast = sig.rolling(int(p["add_ma"])).mean()
    ret5 = sig.pct_change(5)
    dead = float(p["dead"])
    fast = bool(p["fast"])
    gate_of = {x["date"]: x["gate"] for x in dec}
    # 决策日：有趋势闸门时 = 闸门出决策的那些月末（预热期 MA 未成形的月末不出决策，
    # 与 `us_gate.decisions` 的 `continue` 一致）；无趋势闸门（v9）时 = 每个有效月末。
    ends = set(gate_of) if gate_of else set(month_ends(sig.index))

    hold, prev_gate = 1.0, 1.0
    out = np.empty(len(sig), dtype="float64")
    events = []
    idx = sig.index
    for i, d in enumerate(idx):
        out[i] = hold
        f = float(fw.iloc[i]) if np.isfinite(fw.iloc[i]) else np.nan
        if not np.isfinite(f):
            continue
        tgt, why = hold, None
        g_now = gate_of.get(d, prev_gate)
        if d in ends:
            changed = abs(g_now - prev_gate) > 1e-12
            if changed or abs(f - hold) >= dead - 1e-12:
                if tv <= 0.0 or changed or abs(f - hold) >= dead - 1e-12:
                    tgt, why = f, "month"
            prev_gate = g_now
        if fast and tv > 0:
            v5 = float(ret5.iloc[i]) if np.isfinite(ret5.iloc[i]) else 0.0
            v = float(vol.iloc[i]) if np.isfinite(vol.iloc[i]) else np.inf
            if f < tgt - dead and v5 <= float(p["cut_ret"]) and v >= float(p["cut_volx"]) * tv:
                tgt, why = f, "cut"
            pk = float(peak.iloc[i]) if np.isfinite(peak.iloc[i]) else np.nan
            mf = float(ma_fast.iloc[i]) if np.isfinite(ma_fast.iloc[i]) else np.inf
            if np.isfinite(pk) and v <= float(p["decay"]) * pk \
                    and float(sig.iloc[i]) > mf and f > tgt + dead:
                tgt, why = f, "add"
        if why:
            events.append(dict(date=d, target=float(tgt), fw=float(f), reason=why))
        hold = tgt
    return pd.Series(out, index=idx, name="state"), events


def build_state(leg, sig, p, gate="ma200"):
    """-> (仓位序列, 事件)。gate: "ma200" | "tsmom" | "none"。"""
    if gate == "none":
        dec, g = [], pd.Series(1.0, index=sig.index)
    elif gate == "tsmom":
        dec, g = gate_tsmom(sig, leg, p)
    else:
        dec, g = gate_ma200(sig, leg, p)
    return machine(sig, g, dec, p)


# ---------------------------------------------------------------- 2. 回测
def leg_nav(price, state, cost, cash=CASH):
    """单腿净值（`state[t]` 收盘成交后 -> 只赚 t+1 收益）。"""
    cash_d = (1.0 + cash) ** (1.0 / 252.0) - 1.0
    ret = price.pct_change().fillna(0.0)
    hold = state.astype(float).shift(1)
    if len(hold):
        hold.iloc[0] = float(state.iloc[0])
    d = state.astype(float).diff().fillna(0.0).abs()
    return (1.0 + hold * ret + (1.0 - hold) * cash_d - d * cost).cumprod()


def sim(prices, states, cost, cash=CASH, start=None):
    """两条腿等权 50/50 -> (组合净值, 平均仓位, 调仓次数, 换手, 日历)。"""
    idx = pd.DatetimeIndex(sorted(set(prices["ndx"].index).intersection(prices["spx"].index)))
    if start is not None:
        idx = idx[idx >= pd.Timestamp(start)]
    navs = {k: leg_nav(prices[k].reindex(idx).ffill(), states[k].reindex(idx).ffill(),
                       cost, cash) for k in POOL}
    nv = (navs["ndx"] + navs["spx"]) / 2.0
    expo = float(np.mean([states[k].reindex(idx).ffill().mean() for k in POOL]))
    n = int(sum((states[k].reindex(idx).ffill().diff().abs().fillna(0) > 1e-12).sum()
                for k in POOL))
    to = float(sum(states[k].reindex(idx).ffill().diff().abs().fillna(0).sum() for k in POOL))
    return nv / nv.iloc[0], expo, n, to, idx


def mdd_sampled(nav):
    me = nav.resample("ME").last().dropna()
    if not len(me):
        return float("nan")
    return float((me / me.cummax() - 1.0).min())


def stats(nav, cash=CASH):
    m = engine.metrics(nav, rf=cash)
    yrs = (nav.index[-1] - nav.index[0]).days / 365.25
    me = mdd_sampled(nav)
    return dict(ann=m["ann"], vol=m["vol"], mdd=m["mdd"], mdd_me=me, sharpe=m["sharpe"],
                calmar=m["ann"] / abs(m["mdd"]) if m["mdd"] else float("nan"),
                calmar_me=m["ann"] / abs(me) if me else float("nan"),
                years=yrs, cum=m["cum"])


def dd_info(nav):
    """最大回撤的（峰日, 谷日）。用来回答「这个回撤是哪一段行情造成的」。"""
    peak = nav.cummax()
    dd = nav / peak - 1.0
    tr = dd.idxmin()
    pk = nav.loc[:tr].idxmax()
    return pk, tr


def window(nav, a, b):
    seg = nav.loc[(nav.index >= pd.Timestamp(a)) & (nav.index <= pd.Timestamp(b))]
    if len(seg) < 5:
        return dict(ret=float("nan"), mdd=float("nan"))
    m = engine.metrics(seg, rf=CASH)
    return dict(ret=float(seg.iloc[-1] / seg.iloc[0] - 1.0), mdd=m["mdd"])


# ---------------------------------------------------------------- 3. 变体定义
VARIANTS = [
    ("静态 50/50", "none", dict(tv=0.0, dead=0.0, fast=False)),
    ("B1 = v7 默认（MA200 闸门 × TV20 · 死区 10pp）", "ma200", dict()),
    ("B2 = v9 默认（TV15 · 快减+快补）", "none", dict(tv=0.15, dead=0.05, fast=True)),
    ("B3 = v7 纯闸门（无波动层）", "ma200", dict(tv=0.0, dead=0.0)),
    ("B4 = v9 月度档（TV15 · 纯月末）", "none", dict(tv=0.15, dead=0.05, fast=False)),
    ("**C1 = 混血（MA200 闸门 × TV20 + 快减/快补）**", "ma200", dict(fast=True)),
    ("**C2 = TSMOM12（12 月绝对动量 × TV20）**", "tsmom", dict()),
    ("C3 = TSMOM12 纯闸门（无波动层）", "tsmom", dict(tv=0.0, dead=0.0)),
]

WINDOWS = [
    ("1995-01 ~ 2000-02 长牛", "1995-01-01", "2000-02-29"),
    ("2000-03 ~ 2002-12 科网泡沫", "2000-03-01", "2002-12-31"),
    ("2003-01 ~ 2007-12 复苏", "2003-01-01", "2007-12-31"),
    ("2008-01 ~ 2009-12 金融危机", "2008-01-01", "2009-12-31"),
    ("2010-01 ~ 2026-11 本仓库样本", "2010-01-01", "2026-11-04"),
    ("2020-02 ~ 2020-08 疫情", "2020-02-19", "2020-08-31"),
    ("2022-01 ~ 2022-12 加息熊市", "2022-01-03", "2022-12-30"),
]


# ---------------------------------------------------------------- 4. 忠实性校验
def check_faithful():
    """统一机器 == 模块原实现（不通过就不该相信后面任何数字）。"""
    out = []
    for leg in POOL:
        sig = ug.load_signal(leg)
        # v7：月末 target 逐条相等
        ref = ug.decisions(leg)
        dec, g = gate_ma200(sig, leg, dict(DEF, floor=ug.params()["FLOOR"]))
        mine = {x["date"]: round(x["gate"], 10) for x in dec}
        bad = [x for x in ref
               if abs(x["gate"] - mine.get(x["date"], -99)) > 1e-9]
        st_mine, _ = machine(sig, g, dec, dict(DEF, tv=0.20, dead=0.10))
        st_ref = ug.state_series(sig.index, ref)
        d1 = float((st_mine.reindex(st_ref.index) - st_ref).abs().max())
        out.append((f"[v7] {leg} 闸门层 {len(ref)} 条月末决策逐条一致", not bad,
                    f"{len(bad)} 条不符" if bad else "全部一致"))
        out.append((f"[v7] {leg} 月末 target（含 TV20/死区 10pp）与模块最大差", d1 < 1e-9,
                    f"{d1:.2e}"))
        # v9：逐日仓位
        for tag, kw in (("fast", dict(TRIGGER="fast")), ("month", dict(TRIGGER="month"))):
            s_ref, _ = vt.machine(sig, vt.params(**kw))
            p = dict(DEF, tv=0.15, dead=0.05, fast=(tag == "fast"))
            s_mine, _ = machine(sig, pd.Series(1.0, index=sig.index), [], p)
            dd = float((s_mine - s_ref).abs().max())
            out.append((f"[v9·{tag}] {leg} 逐日仓位与模块最大差", dd < 1e-9, f"{dd:.2e}"))
    return out


# ---------------------------------------------------------------- 5. 主流程
def main():
    print("== 数据 ==")
    px_long = {}
    for leg in POOL:
        s = long_series(CODES[leg])
        px_long[leg] = s
        print(f"  {CODES[leg]} {s.index[0].date()} ~ {s.index[-1].date()} · {len(s)} 根")

    print("\n== 忠实性校验 ==")
    checks = check_faithful()
    for n, ok, note in checks:
        print(f"  {'OK ' if ok else 'FAIL'} {n} · {note}")
    if not all(c[1] for c in checks):
        print("!! 校验未通过：后续数字不可信")
        return 2

    # ---- 表 A：QDII 净值口径（2012-08 起）· 只拉齐成本与计息
    print("\n== 表 A：QDII 口径（模块原码，只统一成本/计息）==")
    qdii = {}
    for tag, mod, kw in (("B1", ug, {}), ("B2", vt, {})):
        navs, sts = {}, {}
        for leg in POOL:
            nav, st, _ = mod.build(leg, **kw)
            navs[leg], sts[leg] = nav, st
        idx = pd.DatetimeIndex(sorted(set(navs["ndx"].index) & set(navs["spx"].index)))
        navs = {k: v.reindex(idx).ffill() for k, v in navs.items()}
        sts = {k: v.reindex(idx).ffill() for k, v in sts.items()}
        row = {}
        for fname, c in FEE.items():
            nv, expo, n, to, _ = sim(navs, sts, c)
            m = stats(nv)
            m.update(expo=expo, trades=n, turnover=to)
            row[fname] = m
        row["_nav"] = {c: sim(navs, sts, c)[0] for c in FEE.values()}
        row["_states"] = sts
        qdii[tag] = row
    print(f"  {'变体':6s} " + " ".join(f"{f:>18s}" for f in FEE))
    for tag in qdii:
        cells = []
        for f in FEE:
            m = qdii[tag][f]
            cells.append(f"{m['ann'] * 100:+6.2f}%/{m['mdd'] * 100:6.2f}%")
        print(f"  {tag:6s} " + " ".join(f"{c:>18s}" for c in cells))

    # ---- 表 B：指数长样本 1991-2026
    print(f"\n== 表 B：指数长样本（{START_LONG} 起）==")
    rows, navs_all, states_all = [], {}, {}
    for name, gate, over in VARIANTS:
        p = dict(DEF)
        p.update(over)
        p["floor"] = ug.params()["FLOOR"]
        states = {leg: build_state(leg, px_long[leg], p, gate)[0] for leg in POOL}
        rec = {"byfee": {}}
        for fname, c in (("0.03%", 0.0003), ("0.10%", 0.001), ("0.60%", 0.006)):
            nv, expo, n, to, idx = sim(px_long, states, c, start=START_LONG)
            m = stats(nv)
            if fname == "0.10%":
                m.update(name=name, expo=expo, trades=n, turnover=to)
                m["wins"] = {w[0]: window(nv, w[1], w[2]) for w in WINDOWS}
                m["dd_pk"], m["dd_tr"] = dd_info(nv)
                rec.update(m)
                navs_all[name] = nv
                states_all[name] = {k: v.reindex(idx).ffill() for k, v in states.items()}
            else:
                rec["byfee"][fname] = m
        rows.append(rec)
    for r in rows:
        print(f"  {r['name'][:44]:44s} 年化 {r['ann'] * 100:+6.2f}% · 回撤 {r['mdd'] * 100:7.2f}% "
              f"· 夏普 {r['sharpe']:.2f} · Calmar {r['calmar']:.2f} · 仓位 {r['expo']:.0%} "
              f"· 调仓 {r['trades']}")

    # ---- 波动层 TV 的单调性：长样本 vs QDII 样本（回答「波动层到底值不值」）
    print("\n== 波动层 TV 扫描（v7 骨架 fast=off / v9 骨架 / QDII 口径）==")
    TVS = (0.0, 0.12, 0.15, 0.20, 0.25, 0.30, 0.40)
    tv_long_v7, tv_long_v9, tv_qdii = [], [], []
    for tv in TVS:
        p = dict(DEF, floor=ug.params()["FLOOR"], tv=tv)
        st = {leg: build_state(leg, px_long[leg], p, "ma200")[0] for leg in POOL}
        nv = sim(px_long, st, 0.001, start=START_LONG)[0]
        tv_long_v7.append(stats(nv))
        p2 = dict(DEF, floor=ug.params()["FLOOR"], tv=tv, dead=0.05, fast=True)
        st2 = {leg: build_state(leg, px_long[leg], p2, "none")[0] for leg in POOL}
        nv2 = sim(px_long, st2, 0.001, start=START_LONG)[0]
        tv_long_v9.append(stats(nv2))
        navs, sts = {}, {}
        for leg in POOL:
            nav, s_q, _ = ug.build(leg, TV=tv)
            navs[leg], sts[leg] = nav, s_q
        i_q = pd.DatetimeIndex(sorted(set(navs["ndx"].index) & set(navs["spx"].index)))
        nv3 = sim({k: v.reindex(i_q).ffill() for k, v in navs.items()},
                  {k: v.reindex(i_q).ffill() for k, v in sts.items()}, 0.001)[0]
        tv_qdii.append(stats(nv3))
        print(f"  TV {tv:4.0%}  长·v7骨架 {tv_long_v7[-1]['ann'] * 100:+6.2f}%/"
              f"{tv_long_v7[-1]['mdd'] * 100:7.2f}%/{tv_long_v7[-1]['sharpe']:.2f}"
              f"   长·v9骨架 {tv_long_v9[-1]['ann'] * 100:+6.2f}%/"
              f"{tv_long_v9[-1]['mdd'] * 100:7.2f}%/{tv_long_v9[-1]['sharpe']:.2f}"
              f"   QDII·v7 {tv_qdii[-1]['ann'] * 100:+6.2f}%/"
              f"{tv_qdii[-1]['mdd'] * 100:7.2f}%/{tv_qdii[-1]['sharpe']:.2f}")

    # ---- 邻域（只对两个新候选，一格一格扰动，不挑最优）
    print("\n== 邻域：C1 / C2 各旋钮 ±1 格（长样本 · 单边 0.10%）==")
    NB = [
        ("基准", {}, {}, {}),
        ("死区 5pp", dict(dead=0.05), dict(dead=0.05), dict(dead=0.05)),
        ("死区 20pp", dict(dead=0.20), dict(dead=0.20), dict(dead=0.20)),
        ("TV 15%", dict(tv=0.15), dict(tv=0.15), dict(tv=0.15)),
        ("TV 25%", dict(tv=0.25), dict(tv=0.25), dict(tv=0.25)),
        ("均线 150 日", dict(ma_win=150), dict(ma_win=150), None),
        ("均线 250 日", dict(ma_win=250), dict(ma_win=250), None),
        ("确认 1 月", dict(confirm=1), dict(confirm=1), None),
        ("确认 3 月", dict(confirm=3), dict(confirm=3), None),
        ("深破关闭", dict(deep=0.0), dict(deep=0.0), None),
        ("深破 5%", dict(deep=0.05), dict(deep=0.05), None),
        ("快减 −3%", dict(cut_ret=-0.03), dict(cut_ret=-0.03), None),
        ("快减 −8%", dict(cut_ret=-0.08), dict(cut_ret=-0.08), None),
        ("快减 vol 1.1×", dict(cut_volx=1.1), dict(cut_volx=1.1), None),
        ("快减 vol 1.7×", dict(cut_volx=1.7), dict(cut_volx=1.7), None),
        ("快补 55%", dict(decay=0.55), dict(decay=0.55), None),
        ("快补 85%", dict(decay=0.85), dict(decay=0.85), None),
        ("动量 200 日", None, None, dict(lookback=200)),
        ("动量 300 日", None, None, dict(lookback=300)),
    ]
    nb = []
    for label, a, b, c2 in NB:
        rec = dict(label=label)
        for which, over, base in (("C1", a, ("ma200", dict(fast=True))),
                                  ("C1b", b, ("ma200", dict(fast=True))),
                                  ("C2", c2, ("tsmom", dict()))):
            if over is None:
                rec[which] = None
                continue
            p = dict(DEF, floor=ug.params()["FLOOR"])
            p.update(base[1])
            p.update(over)
            st = {leg: build_state(leg, px_long[leg], p, base[0])[0] for leg in POOL}
            nv = sim(px_long, st, 0.001, start=START_LONG)[0]
            rec[which] = stats(nv)
        nb.append(rec)

        def _c(v):
            return "—" if v is None else f"{v['ann'] * 100:+6.2f}%/{v['sharpe']:.2f}/{v['mdd'] * 100:.2f}%"
        print(f"  {label:14s} C1 {_c(rec['C1'])}  C1' {_c(rec['C1b'])}  C2 {_c(rec['C2'])}")

    # ---- 分半样本（长样本）
    mid = "2008-06-01"
    halves = {}
    for r in rows:
        nv = navs_all[r["name"]]
        halves[r["name"]] = dict(
            h1=stats(nv.loc[nv.index < pd.Timestamp(mid)]),
            h2=stats(nv.loc[nv.index >= pd.Timestamp(mid)]),
        )

    # ---- 图
    fig, axes = plt.subplots(2, 1, figsize=(13, 9), sharex=True,
                             gridspec_kw=dict(height_ratios=[2, 1]))
    style = {
        "静态 50/50": ("#888888", 1.2, "--"),
        "B1 = v7 默认（MA200 闸门 × TV20 · 死区 10pp）": ("#1f77b4", 1.5, "-"),
        "B2 = v9 默认（TV15 · 快减+快补）": ("#d62728", 1.5, "-"),
        "B4 = v9 月度档（TV15 · 纯月末）": ("#ff9896", 1.0, ":"),
        "B3 = v7 纯闸门（无波动层）": ("#17becf", 1.0, ":"),
        "**C1 = 混血（MA200 闸门 × TV20 + 快减/快补）**": ("#2ca02c", 2.0, "-"),
        "**C2 = TSMOM12（12 月绝对动量 × TV20）**": ("#9467bd", 1.8, "-"),
        "C3 = TSMOM12 纯闸门（无波动层）": ("#c5b0d5", 1.0, ":"),
    }
    for name, nv in navs_all.items():
        c, lw, ls = style.get(name, ("#000000", 1.0, "-"))
        short = name.replace("**", "").split("（")[0]
        axes[0].plot(nv.index, nv.to_numpy(), color=c, lw=lw, ls=ls, label=short)
    axes[0].set_yscale("log")
    axes[0].set_ylabel("净值（对数轴）")
    _i0 = next(iter(navs_all.values())).index
    axes[0].set_title(f"美股两腿 50/50 · 指数口径 {_i0[0].date()} ~ {_i0[-1].date()}"
                      " · 含 2000-2002 与 2008 · 单边 0.10%")
    axes[0].legend(loc="upper left", fontsize=8, ncol=2)
    axes[0].grid(alpha=0.3)
    for leg, c in (("ndx", "#1f77b4"), ("spx", "#d62728")):
        stt = states_all["**C1 = 混血（MA200 闸门 × TV20 + 快减/快补）**"][leg]
        axes[1].plot(stt.index, stt.to_numpy() * 100, color=c, lw=0.8,
                     label=f"C1 仓位 · {NAME[leg]}")
    axes[1].set_ylabel("仓位 %")
    axes[1].set_ylim(-3, 103)
    axes[1].legend(loc="lower left", fontsize=8, ncol=2)
    axes[1].grid(alpha=0.3)
    for ax in axes:
        for w in WINDOWS[:4]:
            ax.axvspan(pd.Timestamp(w[1]), pd.Timestamp(w[2]), alpha=0.05, color="k")
    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=130)
    print(f"\n图: {OUT_PNG}")

    write_md(checks, qdii, rows, nb, halves, navs_all,
             (TVS, tv_long_v7, tv_long_v9, tv_qdii))
    print(f"报告: {OUT_MD}")
    return 0


# ---------------------------------------------------------------- 6. 报告
def _p(x, nd=2):
    return "—" if x is None or not np.isfinite(x) else f"{x * 100:+.{nd}f}%"


def write_md(checks, qdii, rows, nb, halves, navs_all, tv):
    by = {r["name"]: r for r in rows}
    nv0 = next(iter(navs_all.values()))
    y0, y1 = nv0.index[0], nv0.index[-1]
    yrs = (y1 - y0).days / 365.25
    tvs, tvy7, tvy9, tvq = tv
    b1 = by["B1 = v7 默认（MA200 闸门 × TV20 · 死区 10pp）"]
    b2 = by["B2 = v9 默认（TV15 · 快减+快补）"]
    b3 = by["B3 = v7 纯闸门（无波动层）"]
    b4 = by["B4 = v9 月度档（TV15 · 纯月末）"]
    c1 = by["**C1 = 混血（MA200 闸门 × TV20 + 快减/快补）**"]
    c2 = by["**C2 = TSMOM12（12 月绝对动量 × TV20）**"]
    c3 = by["C3 = TSMOM12 纯闸门（无波动层）"]
    st = by["静态 50/50"]
    q1, q2 = qdii["B1"], qdii["B2"]
    f_lo, f_mid, f_hi = list(FEE)[0], list(FEE)[1], list(FEE)[2]

    def wv(r, key):
        return r["wins"][key]["ret"]

    def wd(r, key):
        return r["wins"][key]["mdd"]

    K2000, K2008, K2020, K2022 = (WINDOWS[1][0], WINDOWS[3][0], WINDOWS[5][0], WINDOWS[6][0])

    L = [
        f"# v7（年线闸门）vs v9（波动率目标）：同一把尺子 + {yrs:.0f} 年样本",
        "> 由 `research/_probe_us_gate_vs_vt.py` 生成。池 = 纳指100 + 标普500，两腿等权 50/50；",
        "> 长样本价格 = **指数收盘**（`.INX` 1990-01 起 / `.NDX` 1994-01 起，**不含分红**），"
        "信号与执行同一条序列；",
        f"> 空仓按 {CASH:.2%} 逐日计息；T 收盘决策 → T+1 收盘成交 → T+2 起承担收益；成本按 `Σ|Δw|` 单边扣。",
        f"> 长样本区间 **{y0.date()} ~ {y1.date()}（{yrs:.1f} 年）**，两条腿窗口都已预热完毕。",
        "> ⚠ **数据清洗**：雪球 `.NDX` 在 1994-01-03 从 796.56 跳到 395.53（−50.4%），"
        "是**指数重定基不是崩盘**（之后走势与真实纳指一致）。`_clean_break()` 把断点前整段丢弃，"
        "所以长样本从 1995 起 —— 不处理会在 1994 年凭空造出 −30% 回撤，把结论整段污染。",
        "",
        "## 0. 结论",
        "",
        "### 0.1 同一把尺子下谁赢？——**短样本 v9 略优，长样本 v7 全面胜出**",
        "",
        "| 口径 | 变体 | 年化 | 回撤·日频 | 夏普 |",
        "|---|---|---:|---:|---:|",
        f"| 2012-08 起 · QDII 净值 · 单边 0.10% | v7 默认 | {_p(q1[f_mid]['ann'])} | "
        f"{_p(q1[f_mid]['mdd'])} | {q1[f_mid]['sharpe']:.2f} |",
        f"| 2012-08 起 · QDII 净值 · 单边 0.10% | v9 默认 | {_p(q2[f_mid]['ann'])} | "
        f"{_p(q2[f_mid]['mdd'])} | {q2[f_mid]['sharpe']:.2f} |",
        f"| {yrs:.1f} 年 · 指数口径 · 单边 0.10% | v7 默认 | {_p(b1['ann'])} | {_p(b1['mdd'])} | "
        f"{b1['sharpe']:.2f} |",
        f"| {yrs:.1f} 年 · 指数口径 · 单边 0.10% | v9 默认 | {_p(b2['ann'])} | {_p(b2['mdd'])} | "
        f"{b2['sharpe']:.2f} |",
        f"| {yrs:.1f} 年 · 指数口径 · 单边 0.10% | 静态 50/50 | {_p(st['ann'])} | {_p(st['mdd'])} | "
        f"{st['sharpe']:.2f} |",
        "",
        f"**v9 唯一胜过 v7 的地方（回撤更浅）只在 2012 年之后成立。** 拉长到 {yrs:.0f} 年，"
        f"回撤差从「v9 比 v7 浅 {abs(q1[f_mid]['mdd'] - q2[f_mid]['mdd']) * 100:.1f}pp」翻成"
        f"「v9 比 v7 深 {abs(b2['mdd'] - b1['mdd']) * 100:.1f}pp」；夏普从「v9 高 "
        f"{q2[f_mid]['sharpe'] - q1[f_mid]['sharpe']:.2f}」翻成「v7 高 {b1['sharpe'] - b2['sharpe']:.2f}」。",
        "",
        "### 0.2 翻转的原因：2000-2002（两篇 README 都点名「样本没覆盖」的那一段）",
        "",
        "| 窗口 | 静态 50/50 | v7 默认 | v9 默认 | v7 纯闸门 |",
        "|---|---:|---:|---:|---:|",
        f"| 2000-03 ~ 2002-12（收益 / 回撤） | {_p(wv(st, K2000))} / {_p(wd(st, K2000))} | "
        f"{_p(wv(b1, K2000))} / {_p(wd(b1, K2000))} | {_p(wv(b2, K2000))} / {_p(wd(b2, K2000))} | "
        f"{_p(wv(b3, K2000))} / {_p(wd(b3, K2000))} |",
        f"| 2008-01 ~ 2009-12（收益 / 回撤） | {_p(wv(st, K2008))} / {_p(wd(st, K2008))} | "
        f"{_p(wv(b1, K2008))} / {_p(wd(b1, K2008))} | {_p(wv(b2, K2008))} / {_p(wd(b2, K2008))} | "
        f"{_p(wv(b3, K2008))} / {_p(wd(b3, K2008))} |",
        f"| 2020-02 ~ 2020-08（收益 / 回撤） | {_p(wv(st, K2020))} / {_p(wd(st, K2020))} | "
        f"{_p(wv(b1, K2020))} / {_p(wd(b1, K2020))} | {_p(wv(b2, K2020))} / {_p(wd(b2, K2020))} | "
        f"{_p(wv(b3, K2020))} / {_p(wd(b3, K2020))} |",
        f"| 2022-01 ~ 2022-12（收益 / 回撤） | {_p(wv(st, K2022))} / {_p(wd(st, K2022))} | "
        f"{_p(wv(b1, K2022))} / {_p(wd(b1, K2022))} | {_p(wv(b2, K2022))} / {_p(wd(b2, K2022))} | "
        f"{_p(wv(b3, K2022))} / {_p(wd(b3, K2022))} |",
        "",
        "**两台机器的失效模式正好互补，而且长样本把两边都验了一遍**：",
        "",
        f"1. **波动率目标永远减不到 0**：`w = min(1, 15%/vol)`，就算 vol 冲到 80% 也还有 19% 仓位。"
        f"2000-2002 是**持续 31 个月、边跌边反弹**的空头市场（不是一次急跌）：静态同期 "
        f"{_p(wv(st, K2000))}，v9 只修到 {_p(wv(b2, K2000))}、窗口回撤 {_p(wd(b2, K2000))}，"
        f"是全部防守档里最差的一档。",
        f"2. **年线闸门有两个月的盲窗**：纳指 2000-03-27 见顶，闸门要等 5 月底「连续 2 个月在年线下」才走，"
        f"那两个月指数已经跌掉一大截 —— 纯闸门（B3）的**全样本最大回撤就发生在这一段**："
        f"{b3['dd_pk'].date()} → {b3['dd_tr'].date()}，{_p(b3['mdd'])}。"
        f"加上波动层后这一段被摊薄（v7 默认的全样本最大回撤挪到了 2008："
        f"{b1['dd_pk'].date()} → {b1['dd_tr'].date()}，{_p(b1['mdd'])}）——"
        f"因为 2000 年 3 月末纳指 vol20 已经 **48.9%**（4 月末 86%），`20%/vol` 当场把仓位压到 4 成。",
        "",
        f"**这就是 v7「闸门 + 波动层」两层结构的独立证据**：v7 自己的 README 是用 2012-2026 的 "
        f"2020/2022 论证的，而 2000-2002 这段**没有参与任何参数标定**的样本给出了同一个结论 —— "
        f"两层各修对方的洞，缺一层就漏。",
        "",
        "### 0.3 波动层 TV 到底值不值（长样本上的刻度 + 2012+ 的对照）",
        "",
        "| TV | v7 骨架 年化 / 回撤 / 夏普 | v9 骨架 年化 / 回撤 / 夏普 | QDII 口径（2012+）年化 / 回撤 |",
        "|---|---:|---:|---:|",
    ]
    for i, t in enumerate(tvs):
        L.append(f"| {'关闭' if t == 0 else f'{t:.0%}'} | "
                 f"{_p(tvy7[i]['ann'])} / {_p(tvy7[i]['mdd'])} / {tvy7[i]['sharpe']:.2f} | "
                 f"{_p(tvy9[i]['ann'])} / {_p(tvy9[i]['mdd'])} / {tvy9[i]['sharpe']:.2f} | "
                 f"{_p(tvq[i]['ann'])} / {_p(tvq[i]['mdd'])} |")
    i20 = tvs.index(0.20)
    i25 = tvs.index(0.25)
    L += [
        "",
        f"> **v7 骨架上，TV 是一根「回撤刻度」而不是「收益旋钮」，而夏普有个很浅的甜点**："
        f"TV 关闭 → {_p(tvy7[0]['ann'])} / {_p(tvy7[0]['mdd'])} / 夏普 {tvy7[0]['sharpe']:.2f}；"
        f"TV20% → {_p(tvy7[i20]['ann'])} / {_p(tvy7[i20]['mdd'])} / 夏普 {tvy7[i20]['sharpe']:.2f}；"
        f"TV25% → {_p(tvy7[i25]['ann'])} / {_p(tvy7[i25]['mdd'])} / 夏普 {tvy7[i25]['sharpe']:.2f}。"
        f"**年化与回撤同步上升**（TV 越低仓位越保守），夏普从 0.75 抬到 0.77~0.78 就平了。"
        f"所以「TV20% 是不是选对了」的答案是：**在长样本上它落在夏普甜点附近，"
        f"同时把回撤从 {_p(tvy7[0]['mdd'])} 压到 {_p(tvy7[i20]['mdd'])}** —— "
        f"2012+ 的 QDII 口径（最后一列）给出的是同一方向，两根独立样本没有打架。",
        f"> **v9 骨架就完全不同**：它没有闸门，TV 是唯一的刹车，于是 TV 越低回撤越浅、"
        f"但 TV 一高（≥30%）回撤就冲到 −60% 以上 —— 那里根本不存在「甜点」，"
        f"只有「你敢不敢一直满仓」这一个问题。**这就是「有闸门 vs 没闸门」的差别。**",
        "",
        "### 0.4 两个「没测过」的新候选",
        "",
        f"- **C1 = 把 v9 的月中补丁装到 v7 的骨架上（闸门 × TV20 + 快减/快补）**："
        f"{_p(c1['ann'])} / {_p(c1['mdd'])} / 夏普 {c1['sharpe']:.2f}，"
        f"相对 v7 默认：年化 {_p(c1['ann'] - b1['ann'])}、夏普 {c1['sharpe'] - b1['sharpe']:+.2f}、"
        f"回撤{'深' if c1['mdd'] < b1['mdd'] else '浅'} "
        f"{abs(c1['mdd'] - b1['mdd']) * 100:.2f}pp。分窗口看是**教科书式的交换**（正 = C1 比 v7 默认好）："
        f"2020 窗口 {_p(wv(c1, K2020) - wv(b1, K2020))}、2000-2002 "
        f"{_p(wv(c1, K2000) - wv(b1, K2000))}（赚），2008 {_p(wv(c1, K2008) - wv(b1, K2008))}"
        f"（基本打平）、2022 {_p(wv(c1, K2022) - wv(b1, K2022))}（赔）。"
        f"**它值得装，但只值 0.02 夏普** —— 与 v9 自己的结论（n=1、尾部保险）完全一致。",
        f"- **C2 = 12 个月绝对动量 × TV20（TSMOM）**：{_p(c2['ann'])} / {_p(c2['mdd'])} / "
        f"夏普 {c2['sharpe']:.2f}，**没打过 MA200**（{_p(b1['ann'])} / {_p(b1['mdd'])} / "
        f"夏普 {b1['sharpe']:.2f}）。原因是时间戳：纳指 2000-03 见顶，MA200 + 2 月确认 5 月底就走人，"
        f"12 个月动量要等到价格跌破一年前（2000 年底）—— 晚了大半年，"
        f"2000-2002 窗口收益 {_p(wv(c2, K2000))} vs {_p(wv(b1, K2000))}。"
        f"**所以「MA200 是运气吗」：不是。样本外它比教科书的 TSMOM 更适合这个标的。**",
        "",
        "### 0.5 给「做美股」的结论",
        "",
        f"1. **骨架选 v7，不选 v9。** 决定性证据是 2000-2002 的窗口回撤：v9 "
        f"{_p(wd(b2, K2000))}、v7 {_p(wd(b1, K2000))}（静态 {_p(wd(st, K2000))}）。"
        f"买美股要防的正是这种「跌三年 + 反复反弹」的行情，而波动率目标在结构上只能按比例减、减不到底。",
        f"2. **v9 不是没用，它的主场是急性行情**：2020 窗口 v9 {_p(wv(b2, K2020))} vs v7 "
        f"{_p(wv(b1, K2020))}、2022 窗口 v9 {_p(wv(b2, K2022))} vs v7 {_p(wv(b1, K2022))}。"
        f"**它更适合做「短促急跌」的那条腿**（比如给已有的 v7 仓位套一层），而不是单独当美股主策略。",
        f"3. **通道决定成本结论**：场外 QDII A 类（单边 0.6%）上 v7 调仓 "
        f"{q1[f_mid]['trades']} 次、v9 {q2[f_mid]['trades']} 次，落地年化 {_p(q1[f_hi]['ann'])} vs "
        f"{_p(q2[f_hi]['ann'])}（v7 领先 {(q1[f_hi]['ann'] - q2[f_hi]['ann']) * 100:.2f}pp）；"
        f"场内 ETF / 0.03% 通道上两者打平（{_p(q1[f_lo]['ann'])} vs {_p(q2[f_lo]['ann'])}）。"
        f"**做场外 QDII 的，v7 的成本结构赢；做场内低费通道的，v9 的浅回撤才付得起。**",
        f"4. **波动层保留（TV20~25%）**：长样本上它是「回撤刻度」，把最大回撤从 "
        f"{_p(tvy7[0]['mdd'])} 压到 {_p(tvy7[i20]['mdd'])}，代价是年化 "
        f"{(tvy7[0]['ann'] - tvy7[i20]['ann']) * 100:.2f}pp，夏普基本不变（{tvy7[0]['sharpe']:.2f} → "
        f"{tvy7[i20]['sharpe']:.2f}）。**上一版（纯闸门）不差，但要接受 2000 年那种 "
        f"{_p(b3['mdd'])} 的两月盲窗。**",
        f"5. **要更聪明的，只有 C1 一条（加月中补丁）**；TSMOM 替代 MA200 是退步。"
        f"而且 C1 的收益与 v7 默认一模一样（{_p(c1['ann'])} vs {_p(b1['ann'])}），"
        f"换来的只是 2020 那一次少赔 {abs(wv(c1, K2020) - wv(b1, K2020)) * 100:.0f}pp —— "
        f"**愿意为尾部多付 {c1['trades'] - b1['trades']} 次调仓（{b1['trades']} → {c1['trades']}）就装，"
        f"不愿意就不装。**",
        "",
        "## 1. 忠实性校验（统一机器 == 模块原实现）",
        "",
        "| 检查 | 结果 | 备注 |",
        "|---|---|---|",
    ]
    for n, ok, note in checks:
        L.append(f"| {n} | {'✅' if ok else '❌'} | {note} |")
    L += [
        "",
        "> 这一步不能省：v7 与 v9 在实现上是**同一台状态机的两组参数**（都写成 "
        "`gate × min(cap, TV/vol)`），本脚本把它们抽象成一台机器再重跑，必须逐条复现模块输出，"
        "否则后面所有「长样本数字」只是在测我自己的 bug。",
        "",
        "## 2. 表 A · QDII 净值口径（2012-08 起 · 模块原码 · 只统一成本与计息）",
        "",
        "| 变体 | 成本档 | 年化 | 回撤·日频 | 夏普 | 调仓 | 平均仓位 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for tag, nm in (("B1", "v7 默认（闸门×TV20）"), ("B2", "v9 默认（TV15+快减快补）")):
        for f in FEE:
            m = qdii[tag][f]
            L.append(f"| {nm} | {f} | {_p(m['ann'])} | {_p(m['mdd'])} | {m['sharpe']:.2f} | "
                     f"{m['trades']} | {m['expo']:.0%} |")
    L += [
        "",
        f"> **成本敏感度**：0.03% → 0.60%，v7 掉 {(q1[f_lo]['ann'] - q1[f_hi]['ann']) * 100:.2f}pp，"
        f"v9 掉 {(q2[f_lo]['ann'] - q2[f_hi]['ann']) * 100:.2f}pp —— v9 的调仓次数是 v7 的 "
        f"{q2[f_mid]['trades'] / max(1, q1[f_mid]['trades']):.1f} 倍。",
        "> 表 A 的数字与两篇 README 的差异**只来自成本/计息口径**（README 里 v7 用 0.1%/1.4%、"
        "v9 用 0.03%/2.0%），规则一行没改。",
        "",
        f"## 3. 表 B · 指数长样本（{y0.date()} ~ {y1.date()} · {yrs:.1f} 年 · 单边 0.10%）",
        "",
        "| 变体 | 年化 | 波动 | 回撤·日频 | 回撤·月末 | Calmar(日频) | 夏普 | 调仓 | 换手 | 平均仓位 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        L.append(f"| {r['name']} | {_p(r['ann'])} | {r['vol'] * 100:.2f}% | {_p(r['mdd'])} | "
                 f"{_p(r['mdd_me'])} | {r['calmar']:.2f} | {r['sharpe']:.2f} | {r['trades']} | "
                 f"{r['turnover']:.1f} | {r['expo']:.0%} |")
    L += [
        "",
        "> **口径警告（两个偏差方向相反，影响要分开看）**：①指数不含分红 → 买入持有被低估 1~2pp/年，"
        "对防守档有利；②现金按 1.4% 计息 → 1990 年代美元短端常在 5% 上下，防守档被低估，"
        "对静态有利。两者大致抵消，所以**跨「静态 vs 防守」的比较要留余地**；"
        "但**两个防守档之间**（v7 vs v9）这两项几乎一样，比较是干净的。",
        "> 静态 50/50 的 −73% 回撤里含**每日再平衡往下跌腿里补钱**的放大效应，"
        "真实按月再平衡会浅一些；这里所有变体都同一记账法，横向可比。",
        "",
        "## 4. 分段表现（长样本 · 单边 0.10%）",
        "",
        "| 变体 | " + " | ".join(w2[0].replace(" ~ ", "~") for w2 in WINDOWS) + " |",
        "|---|" + "---:|" * len(WINDOWS),
    ]
    for r in rows:
        cells = []
        for wname, _, _ in WINDOWS:
            v = r["wins"][wname]
            cells.append("—" if not np.isfinite(v["ret"]) else _p(v["ret"]))
        L.append(f"| {r['name']} | " + " | ".join(cells) + " |")
    L += [
        "",
        "| 变体 | 回撤（2000-2002） | 回撤（2008） | 回撤（2020） | 回撤（2022） |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in rows:
        L.append(f"| {r['name']} | {_p(wd(r, K2000))} | {_p(wd(r, K2008))} | {_p(wd(r, K2020))} | "
                 f"{_p(wd(r, K2022))} |")
    L += [
        "",
        "## 5. 分半样本（折半点 2008-06，两个半段各含一次崩盘）",
        "",
        "| 变体 | 前半 年化 | 前半 回撤 | 前半 夏普 | 后半 年化 | 后半 回撤 | 后半 夏普 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        h = halves[r["name"]]
        L.append(f"| {r['name']} | {_p(h['h1']['ann'])} | {_p(h['h1']['mdd'])} | "
                 f"{h['h1']['sharpe']:.2f} | {_p(h['h2']['ann'])} | {_p(h['h2']['mdd'])} | "
                 f"{h['h2']['sharpe']:.2f} |")
    L += [
        "",
        "## 6. 邻域（C1 / C2 各旋钮一格一格扰动 · 长样本 · 单边 0.10%）",
        "",
        "| 扰动 | C1 年化/夏普/回撤 | C1′ 年化/夏普/回撤 | C2 年化/夏普/回撤 |",
        "|---|---:|---:|---:|",
    ]

    def _c(v):
        return "—" if v is None else f"{_p(v['ann'])} / {v['sharpe']:.2f} / {_p(v['mdd'])}"
    for rec in nb:
        L.append(f"| {rec['label']} | {_c(rec['C1'])} | {_c(rec['C1b'])} | {_c(rec['C2'])} |")
    L += [
        "",
        "> 读法：C1 全档年化 "
        f"{_p(min(x['ann'] for x in [r['C1'] for r in nb if r['C1']]))} ~ "
        f"{_p(max(x['ann'] for x in [r['C1'] for r in nb if r['C1']]))}、夏普 "
        f"{min(x['sharpe'] for x in [r['C1'] for r in nb if r['C1']]):.2f} ~ "
        f"{max(x['sharpe'] for x in [r['C1'] for r in nb if r['C1']]):.2f} —— "
        f"**方向不随旋钮翻转**（这就是「结构」而不是「调参」的判据），"
        f"但也没有任何一格能把它抬到 B3 的收益（{_p(b3['ann'])}）之上。",
        "",
        "## 7. 成本档对照（长样本）",
        "",
        "| 变体 | 0.03% | 0.10% | 0.60% | 0.03→0.60 衰减 |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in rows:
        f3, f6 = r["byfee"]["0.03%"], r["byfee"]["0.60%"]
        L.append(f"| {r['name']} | {_p(f3['ann'])} | {_p(r['ann'])} | {_p(f6['ann'])} | "
                 f"−{(f3['ann'] - f6['ann']) * 100:.2f}pp |")
    L += [
        "",
        "## 8. 读法与边界",
        "",
        "1. **「哪个更适合美股」必须拆成两个问题**：①骨架用趋势还是波动 —— 长样本 31 年给出「趋势」；"
        "②在趋势骨架上要不要波动层 —— 长样本与 2012+ 都说「要」，它压的是 2000 年那种"
        "「闸门两个月盲窗」的回撤。**两个问题的答案方向一致，不冲突。**",
        "2. **1987 不在样本里**（`.INX` 1990 起）、**1985-1993 也不在**（`.NDX` 1994 年那次重定基"
        "把前段废掉了）。2000-2002 与 2008 在 —— 这是相对两篇 README 的唯一实质证据增量。",
        "3. **不含分红 / 汇率 / 溢价**：长样本是「机制口径」，不是收益预期。场外实盘还要再扣 QDII "
        "跟踪误差与通道时滞（v7 的月末成交比 v9 的次日成交更容易落地，这一点在 0.5 的成本表里只体现了费率、"
        "没体现时滞）。",
        "4. **v9 的 2.0% 空仓计息没有沿用**：本表统一 1.4%（见 v9 README §5：2.0% → 1.4% 的差别是 "
        "0.07pp 年化，可忽略）。",
        "5. **不做网格搜索、不挑参数**：v7/v9 默认参数一行未改；两个新候选（C1 混血、C2 TSMOM）在跑之前"
        "写死；§6 是邻域扰动而非择优。",
        "6. **v7 的 TV20 在长样本上没有被打脸**：夏普 0.75（关闭）→ 0.77（TV20）→ 0.78（TV25），"
        "回撤 −30.09% → −21.85% → −24.60%。**用户 2026-11-05 选定的 TV20 落在甜点附近，"
        "往上挪到 25% 也说得过去，往下（12~15%）才开始明显付年化。**",
        "7. **本报告不能回答的**：下一次危机的形态。v7 的两层分别对应「慢熊」与「高波动急跌」，"
        "如果下一次是「低波动阴跌 + 无反弹」（日本 1990 式），两层都会偏慢 —— 长样本里的 2000-2002 "
        "已经是最接近的样本，但它仍然有剧烈反弹。",
        "",
        "> 模拟结果，非投资建议。",
    ]
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


if __name__ == "__main__":
    sys.exit(main())
