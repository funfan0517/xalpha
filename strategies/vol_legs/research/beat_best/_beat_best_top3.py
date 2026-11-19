# -*- coding: utf-8 -*-
"""在前三最优策略之上，还能不能再往前推一步？—— 原生引擎 · 同尺版（2026-11-19）

与同目录另两份研究的区别（**这是本课题存在的理由**）：

| 研究 | 价格 | 基线 | 问题 |
|---|---|---|---|
| `_beat_best.py`（11-13） | 场外净值 | 手写重写、纯 0/1 | 口径已废弃；C8/C11 已被吸收为 v2/v3 默认 |
| `_beat_best_index.py`（11-16） | 真实指数 | **手写近似** | 基线错位：NDX 用 V9 而非当前 ★1 V10；SPX 用 V13 而非 ★1 V12 |
| **本文件** | 真实指数 | **原生引擎逐日权重** | 直接用 `best_by_leg.md` §0 的当前前三名，不重写规则 |

2026-11-19 重跑（一）：`v12` 把 `慢跌` 从 20% 改到 0% 后，沪深300 / 标普500 / 科创50 三条腿
的 ★1 都换成了 V12；黄金腿纳入重构后的 `v5_gold_score`（V5.1 风险预算 × 利率闸门）后 ★1 变成 V5.1。
2026-11-19 重跑（二）：**v12 的波动层改为按腿**（`rule.VOL_LAYER`，见
`v12_octo_trend/research/er_vol_layer/`）后，**A500 的 ★2 与 ★3 互换（V12 0.87 挤掉 V2 0.83）、
黄金的 ★2 由 V6-B 换成 V12（0.81 vs 0.74）**，其余五腿名次不变。
`TOP3` 的**顺序**参与计算（E2/MIN2/MAX2/E2×TV 只取前三里的前两条），所以这两个互换会直接改变
A500 与黄金的候选结果 —— 不能当成「只是标签变了」。

方法三条硬约束：
1. **不重写规则**：九个变种的逐日权重全部来自各自原生引擎（`boll.weight` / `macd_boll.weight` /
   `vol_target.machine` / `vol_layer.state_daily` / `engine.backtest_leg` / `engine.state_machine` /
   `gold.state_b` / `ma20.signal` / `score.run`），用 `pipeline/intraday_preview.py::_isolated` 范式隔离同名模块。
   import 与调用都必须在隔离上下文里（V12 `engine.py:296` 会在**调用时**再 `import data` 并 fail-fast）。
2. **同尺**：统一价格（`data.series_for_leg`）、统一窗口（预热 300 根）、统一成本（单边 10bp、
   空仓 1.40%），不复用各变种自己的费率。附录另给「原生费率复现」列，验证引擎加载无误。
3. **★ 双闸门**：候选全样本夏普 ≥ 当前 ★1 + 0.02，且后段（后 40%）夏普 > ★1 后段。
   候选全部是**无参数或单一固定参数**的组合/叠加层，不做网格搜索。

候选（机制先写，参数固定）：
- `E2` / `E3`：前二 / 前三持仓等权平均 —— 跨信号族分散（突破 × 趋势 × 波动层）。
- `MIN2` / `MAX2`：两两条信号的「双确认」/「任一在场」—— 只改仓位深度，不换信息源。
- `ROT`：月末切到过去 252 日夏普最高的那条（因果、无拟合）—— 策略层面的动量。
- `SW`：MA200 上方用 ★1、下方用三条里平均仓位最低的一条 —— 补 ★1 缺的方向层。
- `E2×TV`：E2 再乘一层 TV20 波动上限（复用 v2/v3 的 `vol_cap`），只压 σ 高时的仓位。

产物：`_beat_best_top3.md`（本目录）。
用法：`python strategies/vol_legs/research/beat_best/_beat_best_top3.py`
模拟结果，非投资建议。
"""
import contextlib
import importlib
import io
import math
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../research/beat_best
_RES = os.path.dirname(_HERE)                               # .../research
_VL = os.path.dirname(_RES)                                 # .../vol_legs
_ROOT = os.path.dirname(os.path.dirname(_VL))               # 仓库根
sys.path.insert(0, _VL)

import data as vdata  # noqa: E402

OUT = os.path.join(_HERE, "_beat_best_top3.md")
TD = 252
CASH_ANNUAL = 0.014
CASH_D = (1.0 + CASH_ANNUAL) ** (1.0 / TD) - 1.0
FEE_SIDE = 0.0010          # 统一单边 10bp（v7~v10 惯例；报告内所有候选/单腿共用）
WARMUP = 300               # 覆盖 V13(290) / V12(270) / V6(250+120) / V3(60)
CUT = 0.60                 # 前 60% / 后 40%
GATE = 0.02                # 全样本夏普门槛（排除舍入级伪超越）
POST_GATE = 0.02           # 后段夏普门槛：后段只赢 0.00x 属噪声，不予采纳

# 当前前三名——逐字抄自 research/best_by_leg/_best_by_leg.md §0（2026-11-19 重跑）
# key 必须与下方 ENGINE 的 key 一致；第 0 项 = 该腿当前 ★1（基线）
TOP3 = {
    "a500": [("v3", "V3 MACD+BOLL"), ("v12", "V12 八态趋势"), ("v2", "V2 BOLL")],
    "hs300": [("v12", "V12 八态趋势"), ("v2", "V2 BOLL"), ("v9", "V9 波动目标")],
    "ndx": [("v10", "V10 波动层"), ("v9", "V9 波动目标"), ("v13", "V13 九宫格")],
    "spx": [("v12", "V12 八态趋势"), ("v13", "V13 九宫格"), ("v9", "V9 波动目标")],
    "kc50": [("v12", "V12 八态趋势"), ("v2", "V2 BOLL"), ("v3", "V3 MACD+BOLL")],
    "hkt": [("v3", "V3 MACD+BOLL"), ("v9", "V9 波动目标"), ("v2", "V2 BOLL")],
    "gold": [("v5", "V5.1 黄金仓位"), ("v12", "V12 八态趋势"), ("v6b", "V6-B 控回撤")],
}
NAMES = {"a500": "中证A500", "hs300": "沪深300", "ndx": "纳指100", "spx": "标普500",
         "kc50": "科创50", "hkt": "恒生科技", "gold": "黄金"}

# 引擎规格：key -> (子目录, 隔离时要清的模块名, 入口模块名, 原生单边费率近似)
ENGINE_SPECS = {
    "v1": ("v1_price_volume/v1_1_ma20", ("data", "rule", "engine", "ma20"), "ma20", 0.0015),
    "v2": ("v2_boll_breakout", ("data", "rule", "engine", "boll"), "boll", 0.0030),
    "v3": ("v3_macd_boll", ("data", "rule", "engine", "macd_boll"), "macd_boll", 0.0030),
    # v5 的 score.py 会 `import engine`（家族根目录那份，取 metrics）与 `import factors`
    "v5": ("v5_gold_score", ("data", "rule", "engine", "factors", "score"), "score", 0.00025),
    "v6b": ("v6_gold", ("data", "rule", "engine", "gold"), "gold", 0.0008),
    "v9": ("v9_vol_target", ("data", "rule", "engine", "vol_target"), "vol_target", 0.0010),
    "v10": ("v10_vol_layer", ("data", "rule", "engine", "vol_layer"), "vol_layer", 0.0010),
    "v12": ("v12_octo_trend", ("data", "rule", "engine"), "engine", 0.0005),
    "v13": ("v13_quad_9grid", ("data", "rule", "engine"), "engine", 0.0005),
}


@contextlib.contextmanager
def isolated(directory, names):
    """与 pipeline/intraday_preview.py::_isolated 同一范式：隔离 sys.path / sys.modules。"""
    old_path = sys.path[:]
    saved = {n: sys.modules.pop(n) for n in names if n in sys.modules}
    sys.path.insert(0, directory)
    try:
        yield
    finally:
        sys.path[:] = old_path
        for n in names:
            sys.modules.pop(n, None)
        sys.modules.update(saved)


class Engines:
    """一次性加载九个原生引擎模块；**调用**时仍套隔离（V12 在调用时二次 import data）。"""

    def __init__(self):
        self.mods, self.dmods = {}, {}
        for key, (sub, names, mod, _fee) in ENGINE_SPECS.items():
            d = os.path.join(_VL, sub)
            with isolated(d, names):
                self.mods[key] = importlib.import_module(mod)
                self.dmods[key] = importlib.import_module("data")

    def _ctx(self, key):
        sub, names, _mod, _fee = ENGINE_SPECS[key]
        return isolated(os.path.join(_VL, sub), names)

    def hold(self, key, leg, px):
        """返回「第 t 根收益所对应的仓位」Series（已含各引擎自己的时滞）。"""
        m = self.mods[key]
        with self._ctx(key):
            if key == "v1":
                sig, _ = m.signal(px)
                h = pd.Series(sig, index=px.index).astype(float).shift(1).fillna(0.0)
            elif key == "v2":
                h = m.weight(px, m.signal(px)[0]).shift(1).fillna(0.0)
            elif key == "v3":
                h = m.weight(px, m.signal(px)).shift(1).fillna(0.0)
            elif key == "v5":
                # `score.run` 的 `state[t]` = t 日收盘成交后的仓位（已含 T+1 成交滞后），
                # 所以「t 日收益对应的仓位」= state.shift(1)。状态机必须**在全样本上跑**
                # （START=0.40 起手档 + 120 日波动预热），再 reindex 进研究窗口，
                # 否则窗口起点会重放一次 0.40 的假建仓。
                F = importlib.import_module("factors")
                full = vdata.series_for_leg(leg).dropna().astype("float64")
                df5 = m.run(pd.DataFrame({"close": full}), F.read_rate())
                h = df5["state"].shift(1).reindex(px.index).ffill().fillna(0.0)
            elif key == "v6b":
                h = m.state_b(px)["state"].shift(1).fillna(0.0)
            elif key == "v9":
                s, _ = m.machine(px, m.leg_params(leg), leg=leg)
                h = s.shift(1).fillna(1.0)
            elif key == "v10":
                h = m.state_daily(px.index, leg).shift(1).fillna(1.0)
            elif key == "v12":
                r = m.backtest_leg(leg, df=self.dmods[key].frame(leg))
                h = r["w"].reindex(px.index).ffill().fillna(0.0).shift(1).fillna(0.0)
            elif key == "v13":
                sm = m.state_machine(self.dmods[key].frame(leg), callback_add=True)
                h = sm["w"].reindex(px.index).ffill().fillna(0.0).shift(1).fillna(0.0)
            else:
                raise KeyError(key)
        return h.reindex(px.index).fillna(0.0).clip(0.0, 1.0)


# ---------------------------------------------------------------- 同尺引擎
def metrics(hold, px, fee=FEE_SIDE):
    """统一日频记账：h[t] 赚 t 日收益，成本按 |Δh| 单边扣，空仓吃货基。"""
    h = hold.reindex(px.index).fillna(0.0).astype(float).clip(0.0, 1.0)
    ret = px.pct_change().fillna(0.0)
    d = h.diff().fillna(h)
    cost = d.abs() * fee if not isinstance(fee, tuple) else (
        d.clip(lower=0.0) * fee[0] + (-d.clip(upper=0.0)) * fee[1])
    r = h * ret + (1.0 - h) * CASH_D - cost
    nav = (1.0 + r).cumprod()
    n = len(r)
    if n < 2:
        return None
    ann = float(nav.iloc[-1] ** (TD / n) - 1.0)
    vol = float(r.std() * math.sqrt(TD))
    mdd = float((nav / nav.cummax() - 1.0).min())
    return dict(ann=ann, vol=vol, mdd=mdd,
                sharpe=(ann - CASH_ANNUAL) / vol if vol > 0 else np.nan,
                calmar=ann / abs(mdd) if mdd < 0 else np.nan,
                expo=float(h.mean()), tpy=float((d.abs() > 1e-9).sum() / (n / TD)))


def native_fee(key, leg, eng):
    """自检专用：取各引擎真实费率档（C 类腿 0 费率 / A 类 10+50bp / 场内 16bp / 单边 5~10bp）。"""
    if key in ("v1", "v2", "v3"):
        return tuple(eng.mods[key].FEE[leg])
    if key == "v5":
        m = eng.mods[key]
        return float(m.FEE) + float(m.SLIP)          # 场内万 2.5 单边、默认计滑点
    if key == "v6b":
        m = eng.mods[key]
        return (float(m.FEE[0]) + float(m.SLIP), float(m.FEE[1]) + float(m.SLIP))
    if key in ("v9", "v10"):
        return 0.001
    return 0.0005


def evaluate(hold, px, cut):
    full = metrics(hold, px)
    hh, pp = hold.loc[cut:], px.loc[cut:]
    post = metrics(hh, pp)
    return full, post


# ---------------------------------------------------------------- 候选（参数事先固定）
def vol_cap(px, tv=0.20, win=20, dead=0.05, gap=5):
    """复用 v2/v3 的波动上限机制（不是重新拟合）：σ 高时压仓位。"""
    vol = px.pct_change().rolling(win, min_periods=win).std() * math.sqrt(TD)
    raw = (tv / vol).clip(upper=1.0).shift(1)
    out = np.zeros(len(raw))
    cur, last = 0.0, -10 ** 9
    for i, v in enumerate(raw.to_numpy(dtype=float)):
        if np.isfinite(v) and (i - last) >= gap and abs(v - cur) >= dead - 1e-12:
            cur, last = float(v), i
        out[i] = cur
    return pd.Series(out, index=px.index)


def rotating(holds, px, look=252):
    """月末切到「过去 252 日同尺夏普」最高的一条；决策日收盘已知 → 次日起生效。"""
    rets = {}
    for k, h in holds.items():
        hh = h.reindex(px.index).fillna(0.0)
        d = hh.diff().fillna(hh)
        rets[k] = hh * px.pct_change().fillna(0.0) + (1 - hh) * CASH_D - d.abs() * FEE_SIDE
    idx = px.index
    ends = {idx[i] for i in range(len(idx) - 1)
            if (idx[i].year, idx[i].month) != (idx[i + 1].year, idx[i + 1].month)}
    keys = list(holds)
    cur, out = keys[0], np.zeros(len(idx))
    for i, day in enumerate(idx):
        if day in ends and i >= look:
            scores = {}
            for k in keys:
                r = rets[k].iloc[i - look + 1:i + 1]
                v = float(r.std() * math.sqrt(TD))
                scores[k] = (float((1 + r).prod() ** (TD / look) - 1) - CASH_ANNUAL) / v if v > 0 else -np.inf
            cur = max(scores, key=scores.get)
        out[i] = float(holds[cur].iloc[i])
    return pd.Series(out, index=idx).shift(1).fillna(float(holds[cur].iloc[0]))


def switch_ma(h_hi, h_lo, px, ma=200):
    """MA200 上方用 ★1，下方用平均仓位最低的一条；闸门 shift(1) 防前视。"""
    gate = (px > px.rolling(ma).mean()).astype(float).shift(1).fillna(1.0)
    return (h_hi * gate + h_lo * (1.0 - gate)).clip(0.0, 1.0)


def candidates(holds, px):
    ks = list(holds)
    h1, h2, h3 = (holds[ks[i]] for i in range(3))
    out = [(f"S{i + 1} {TOP3_LABEL[ks[i]]}", holds[ks[i]]) for i in range(3)]
    out += [
        ("E2 前二等权融合", ((h1 + h2) / 2.0).clip(0.0, 1.0)),
        ("E3 前三等权融合", ((h1 + h2 + h3) / 3.0).clip(0.0, 1.0)),
        ("MIN2 双确认", pd.Series(np.minimum(h1, h2), index=px.index)),
        ("MAX2 任一在场", pd.Series(np.maximum(h1, h2), index=px.index)),
        ("ROT 月末轮动(252d)", rotating(holds, px)),
        ("E2×TV20 波动上限", ((h1 + h2) / 2.0 * vol_cap(px)).clip(0.0, 1.0)),
    ]
    lo_key = min(ks, key=lambda k: float(holds[k].mean()))
    if lo_key != ks[0]:
        out.append((f"SW MA200→{TOP3_LABEL[lo_key]}", switch_ma(h1, holds[lo_key], px)))
    return out


# ---------------------------------------------------------------- 报告
def fmt(x, kind="pct"):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    return f"{x * 100:+.2f}%" if kind == "pct" else f"{x:.2f}"


def main():
    global TOP3_LABEL
    TOP3_LABEL = {}
    for leg, rows in TOP3.items():
        for k, lab in rows:
            TOP3_LABEL[k] = lab
    eng = Engines()
    L = [
        "# 在「当前前三」之上，还能再往前推一步吗？—— 原生引擎 · 同尺版",
        "",
        "> 由 `research/beat_best/_beat_best_top3.py` 生成（2026-11-19）。"
        "基线逐字取自 `research/best_by_leg/_best_by_leg.md` §0 的**当前前三名**，"
        "逐日权重来自各变种**原生引擎**，不重写规则、不复用旧研究的近似函数。",
        "",
        "## 0. 口径与判定",
        "",
        "- 价格：`data.series_for_leg()`（六腿真实指数 + 黄金 518880）；统一预热 "
        f"{WARMUP} 根，前后段切分在样本 {CUT:.0%} 处。",
        f"- 成本：所有单腿与候选**统一**单边 {FEE_SIDE:.2%}、空仓货基 {CASH_ANNUAL:.2%}、"
        "T 日收盘决策 → T+1 收益（V9/V10 按引擎自身的 T+2 语义换算成「当日收益对应仓位」后再同尺）。",
        "- 引擎隔离：`pipeline/intraday_preview.py::_isolated` 范式，import 与调用都在隔离上下文里"
        "（V12 `engine.py:296` 在调用时二次 `import data` 且有 fail-fast 断言）。",
        "- **★ = 全样本夏普 ≥ 当前 ★1 + 0.02，且后段（后 40%）夏普 ≥ ★1 后段 + 0.02**。"
        "两段都设 0.02 门槛：只赢 0.00x 属舍入级噪声。全样本赢、后段输的标 `全样本↑·后段↓`，"
        "视为过拟合，不采纳。",
        "- 候选全部无参数或单固定参数（E2/E3 等权、双确认、月末 252 日轮动、MA200 切换、TV20 上限），"
        "不做网格搜索。",
        "- **毛夏普 = 完全不计费**：与净夏普并排看，能分辨「信号更准」还是只是「换手少、省了费」。"
        "若净夏普赢而毛夏普输，那不是策略进步，是成本会计。",
        "",
        "### 引擎加载自检（原生费率 · 统一 300 根预热窗口）",
        "",
        "> 只验证「引擎加载无误、量级对得上」：本表用**各引擎真实费率档**，但仍统一截 300 根预热，"
        "故与 `best_by_leg.md` 各自原生起点的数字会有差距（尤其短样本腿）。**不参与候选比较**。",
        "",
        "| 腿 | 策略 | 年化 | 回撤 | 夏普 | 已发布夏普 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    checks, sections = [], []
    detail, wins = {}, []
    for leg, rows in TOP3.items():
        px = vdata.series_for_leg(leg).dropna()
        if len(px) <= WARMUP + 120:
            continue
        px = px.iloc[WARMUP:]
        cut = px.index[int(len(px) * CUT)]
        holds = {}
        for key, label in rows:
            holds[key] = eng.hold(key, leg, px)
        # 自检：用各引擎原生费率近似复现（仅验证加载，不参与候选比较）
        pub = {"a500": [0.88, 0.87, 0.83], "hs300": [0.92, 0.75, 0.73],
               "ndx": [0.73, 0.66, 0.65], "spx": [0.66, 0.59, 0.56],
               "kc50": [0.89, 0.70, 0.61], "hkt": [0.34, 0.16, 0.14],
               "gold": [1.02, 0.81, 0.74]}[leg]
        for i, (key, label) in enumerate(rows):
            m = metrics(holds[key], px, fee=native_fee(key, leg, eng))
            checks.append(f"| {NAMES[leg]} | {label} | {fmt(m['ann'])} | {fmt(m['mdd'])} | "
                          f"{m['sharpe']:.2f} | {pub[i]:.2f} |")
        # 同尺比较
        cands = candidates(holds, px)
        ev = []
        for label, h in cands:
            f, p = evaluate(h, px, cut)
            ev.append((label, h, f, p))
        base_f, base_p = ev[0][2], ev[0][3]
        passed = []
        for label, h, f, p in ev[3:]:
            if (f["sharpe"] >= base_f["sharpe"] + GATE
                    and p["sharpe"] >= base_p["sharpe"] + POST_GATE):
                passed.append((label, h, f, p))
        best = max(passed, key=lambda x: x[2]["sharpe"]) if passed else None
        passed_names = {x[0] for x in passed}
        detail[leg] = dict(rows=rows, ev=ev, base_f=base_f, base_p=base_p,
                           best=best, passed=passed, cut=cut, px=px, pub=pub)
        if best:
            wins.append((leg, best))
        sections += ["", f"## {NAMES[leg]} —— 当前 ★1：{rows[0][1]}", "",
                     f"同尺基线（★1）：夏普 {base_f['sharpe']:.2f} / 年化 {fmt(base_f['ann'])} / "
                     f"回撤 {fmt(base_f['mdd'])}，后段 {base_p['sharpe']:.4f}；"
                     f"样本 {px.index[0].date()} ~ {px.index[-1].date()}（{len(px)} 根，后段自 {cut.date()}）。", "",
                     "| 候选 | 年化 | 回撤 | 净夏普 | 毛夏普 | 后段夏普 | Calmar | 平均仓位 | 调仓/年 | 判定 |",
                     "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
        for label, _h, f, p in ev:
            if f is None or p is None:
                sections.append(f"| {label} | — | — | — | — | — | — | — | — | — | 样本不足 |")
                continue
            gross = metrics(_h, px, fee=0.0)
            star = ("**★ 超越（最佳）**" if best and label == best[0] else
                    "★ 超越" if label in passed_names else
                    "全样本↑·后段↓" if f["sharpe"] >= base_f["sharpe"] + GATE else "—")
            sections.append(f"| {label} | {fmt(f['ann'])} | {fmt(f['mdd'])} | {f['sharpe']:.2f} | "
                            f"{gross['sharpe']:.2f} | {p['sharpe']:.2f} | {f['calmar']:.2f} | "
                            f"{f['expo']:.0%} | {f['tpy']:.1f} | {star} |")
        if best:
            others = [x[0] for x in passed if x[0] != best[0]]
            g_base = metrics(ev[0][1], px, fee=0.0)["sharpe"]
            g_best = metrics(best[1], px, fee=0.0)["sharpe"]
            attr = ("**信号效应为主**" if g_best > g_base + 0.02 else
                    "**主要是成本效应**（毛夏普没有更高，净夏普靠换手下降换来）")
            sections += ["", f"> **找到超越项：{best[0]}** —— 夏普 {base_f['sharpe']:.2f} → "
                         f"**{best[2]['sharpe']:.2f}**，后段 {base_p['sharpe']:.4f} → "
                         f"**{best[3]['sharpe']:.4f}**，年化 {fmt(base_f['ann'])} → "
                         f"{fmt(best[2]['ann'])}，回撤 {fmt(base_f['mdd'])} → {fmt(best[2]['mdd'])}。",
                         f"> 归因：毛夏普 {g_base:.2f} → {g_best:.2f}，换手 "
                         f"{base_f['tpy']:.1f} → {best[2]['tpy']:.1f} 次/年 —— {attr}。"
                         + (f"另有 {'、'.join(others)} 也通过双闸门。" if others else "")]
        else:
            top = max(ev[3:], key=lambda x: x[2]["sharpe"])
            sections += ["", f"> 无候选通过双闸门。最佳候选 **{top[0]}**：夏普 "
                         f"{top[2]['sharpe']:.2f}（基线 {base_f['sharpe']:.2f}，差 "
                         f"{top[2]['sharpe'] - base_f['sharpe']:+.2f}），后段 "
                         f"{top[3]['sharpe']:.2f}（基线 {base_p['sharpe']:.2f}）。"]
        sections.append("")

    L += checks
    L += sections
    L += ["## 汇总", "",
          f"七条腿中 **{len(wins)} 条**能在当前 ★1 之上再往前推一步：", "",
          "| 标的 | 当前 ★1（同尺夏普） | 超越项 | 新夏普 | 后段 新 vs 旧 |",
          "|---|---:|---|---:|---|"]
    for leg, best in wins:
        d = detail[leg]
        others = [x[0] for x in d["passed"] if x[0] != best[0]]
        L.append(f"| {NAMES[leg]} | {d['rows'][0][1]}（{d['base_f']['sharpe']:.2f}） | "
                 f"**{best[0]}**" + (f"（另：{'、'.join(others)}）" if others else "") + " | "
                 f"**{best[2]['sharpe']:.2f}** | "
                 f"{best[3]['sharpe']:.2f} vs {d['base_p']['sharpe']:.2f} |")
    if not wins:
        L.append("| — | — | 无 | — | — |")
    L += ["", "## 结论与边界", "",
          "1. **候选是「组合/叠加」，不是新信号源**：E2/E3、双确认、轮动、MA200 切换、TV20 上限"
          "用的全是前三名已有的择时信息。能赢说明『怎么把已有的好策略放在一起』还有空间；"
          "不能赢说明当前 ★1 在这条腿上已经贴着这组规则的边界。",
          "2. **同尺成本会压低所有数字**：统一 10bp 单边对 V13（原生 5bp）和 C 类腿（原生 0 费率）"
          "是加压，所以自检表的『同尺夏普』会低于 `best_by_leg.md` 的含费数字。"
          "**同尺内基线 vs 候选的比较仍成立**，但绝对数不要跨报告对账。",
          "3. **短样本腿要打折看**：科创50 / 恒生科技只有约 6 年，后段 40% 只有 2~3 年；"
          "这两条腿上的任何 ★ 置信度都低于长样本腿。",
          "4. **净夏普赢不等于信号更准**：看毛夏普列。若毛夏普没升而净夏普升，增益全部来自"
          "换手下降省下的费用 —— 这类改进在**原生低费率通道**（如 V13 的 5bp）里会消失，"
          "不能当成策略本身的进步。逐腿结论已自动标注归因。",
          "5. **轮动与切换有过拟合风险**：ROT 的 252 日、SW 的 MA200 都是事先固定的常用值，"
          "不是扫出来的；但换一个 lookback 就可能翻号，采纳前应做邻域检验。",
          "6. **A500 含 2005~2024 指数公司回溯模拟值**，其行应作『有前视偏差的上界』读。",
          "",
          "> 机械规则输出，非投资建议。"]
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("WROTE", OUT)
    for leg, best in wins:
        d = detail[leg]
        print(f"  {NAMES[leg]:8s} {d['base_f']['sharpe']:.2f} -> {best[0]} {best[2]['sharpe']:.2f} "
              f"(post {best[3]['sharpe']:.2f} vs {d['base_p']['sharpe']:.2f})")
    for leg, d in detail.items():
        if leg not in [x[0] for x in wins]:
            print(f"  {NAMES[leg]:8s} {d['base_f']['sharpe']:.2f} | no beat")


if __name__ == "__main__":
    main()
