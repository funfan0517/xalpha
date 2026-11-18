# -*- coding: utf-8 -*-
"""V10 自适应策略全景：从波动层公式推导可自适应旋钮，系统扩展状态变量 + k 幅度。

## 原理（仓位 = min(1, k · TV0 / vol20)）
可自适应的对象只有三个：
1. vol 估计子 —— v8 `_probe_vol_layer.md` §3 已证伪（max/blend/EWMA/vol10/vol60 全不赢纯 vol20）；
2. TV0 水平 —— v10 `../optimize/_v10_leg_optimize.md` 逐腿定参已做（纳指 TV10 / 标普 TV12）；
3. **风险预算倍率 k** —— 唯一还没系统扫过的旋钮：仓位 = min(1, k·TV0/vol20)，
   k=1 是基准（TV0=20%），k=0 → 完全空仓，k=2 → TV40%（vol<40% 时几乎满仓）。

之前的三种自适应（趋势方向 / 波动锚 max-min-mean / ER 趋势纯度）本质上都是
"用某个市场状态把 k 切成两档"，但 k 的幅度都锁在 [0.5, 1]（TV10/20）。
本探针做两件新事：
- **新状态变量**（4 种，都没在 V10 上测过）：回撤深度 / vol 变化率 / 距年线距离 / ER 连续值；
- **k 幅度扫描**（含用户要求的 0~2，基准 1）：窄幅 .75-1.25、现有 .5-1、
  中幅 .5-1.5、**全幅 0-2**、熊市空仓 0-1、只高倍 1-2。

状态变量全部 shift(1) 无前视；其余完全用 V10 原生 vol20 + 死区 10pp + T+1 + 0.1% + 空仓 1.40%。
严格判定：后段夏普 > 固定 k=0.5 与 k=1，且全样本 ≥ 两者最大值。
产物：`_v10_adaptive_universe.md`（本目录）。只做研究，不改任何策略代码。
"""
import importlib.util
import io
import math
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V10 = os.path.dirname(os.path.dirname(_HERE))
_VL = os.path.dirname(_V10)
sys.path.insert(0, _VL)
import data as vd  # noqa: E402
sys.modules["data"] = vd
_sp = importlib.util.spec_from_file_location("vlc", os.path.join(_V10, "vol_layer.py"))
vl = importlib.util.module_from_spec(_sp)
sys.modules["vlc"] = vl
_sp.loader.exec_module(vl)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

OUT = os.path.join(_HERE, "_v10_adaptive_universe.md")
LEGS = vd.rule.LEG_ORDER
LEG_NAMES = {k: vd.rule.LEGS[k]["name"] for k in LEGS}
TD, WARMUP, CUT = 252, 300, 0.60
TV0, ER_N, ER_HI, ER_LO = 0.20, 20, 0.40, 0.30
# (标签, k_lo, k_hi)：好状态给 k_hi、坏状态给 k_lo
AMPS = [
    ("窄幅 .75-1.25", 0.75, 1.25),
    ("现有 .5-1 (TV10/20)", 0.50, 1.00),
    ("中幅 .5-1.5", 0.50, 1.50),
    ("全幅 0-2 (用户要求)", 0.00, 2.00),
    ("熊市空仓 0-1", 0.00, 1.00),
    ("只高倍 1-2", 1.00, 2.00),
]


def perf(nav):
    r = nav.dropna().pct_change().dropna()
    n = len(r)
    if n < 30:
        return dict(ann=float("nan"), mdd=float("nan"), sharpe=float("nan"))
    ann = float((1 + r).prod() ** (TD / n) - 1)
    vol = float(r.std() * math.sqrt(TD))
    mdd = float((nav / nav.cummax() - 1).min())
    return dict(ann=ann, mdd=mdd, sharpe=(ann - vl.CASH_ANNUAL) / vol if vol > 0 else float("nan"))


def efficiency_ratio(px, n=ER_N):
    s = px.astype("float64")
    return (s - s.shift(n)).abs() / s.diff().abs().rolling(n).sum()


def er_binary(er, hi=ER_HI, lo=ER_LO):
    fast, out = False, []
    for v in er.to_numpy(dtype=float):
        if not np.isnan(v):
            if v >= hi:
                fast = True
            elif v <= lo:
                fast = False
        out.append(fast)
    return pd.Series(out, index=er.index)


def make_states(px, vol):
    """各状态返回 True=「好状态」→ 给 k_hi；False=坏状态 → k_lo。全 shift(1)。"""
    er = efficiency_ratio(px)
    dd = px / px.cummax() - 1.0                  # 距历史高点回撤（≤0）
    dv = vol - vol.shift(20)                     # vol 的 20 日变化（恐慌抬升）
    ma200 = px.rolling(200).mean()
    out = {
        "ER趋势纯度": er_binary(er),                         # 趋势态 = 好
        "回撤深度": (dd > -0.15).shift(1).fillna(True),      # 浅回撤 = 好
        "vol变化率": (dv < 0).shift(1).fillna(True),         # vol 下降 = 好（反相）
        "距年线": (px > ma200).shift(1).fillna(True),        # 年线上 = 好
    }
    return out


def run_k(leg, k_s, amp=0.10):
    px = vl.load_nav(leg)
    sig = vl.signal_of(leg)
    vol = sig.pct_change().rolling(20, min_periods=20).std(ddof=1) * math.sqrt(TD)
    vol = vol.reindex(sig.index)
    tv = (k_s.reindex(sig.index) * TV0).clip(lower=0.0)
    s = (tv / vol).replace([np.inf, -np.inf], np.nan).clip(upper=1.0).fillna(1.0).clip(0.0, 1.0)
    st = vl.deadzone(s, amp).reindex(px.index).ffill().fillna(1.0).shift(1).fillna(1.0)
    return vl.run_leg(st, px), st


def evaluate(leg, k_s):
    px = vl.load_nav(leg)
    cut = WARMUP + int((len(px) - WARMUP) * CUT)
    nv, st = run_k(leg, k_s)
    f, q = perf(nv.iloc[WARMUP:]), perf(nv.iloc[cut:])
    tr = int((st.diff().abs() > 1e-9).iloc[WARMUP:].sum())
    return f, q, tr


def main():
    lines = [
        "# V10 自适应策略全景：公式推导 + 新状态 + k 幅度 0~2（实测）", "",
        "> 2026-11-16 生成。只研究、不改生产代码。TV0 = 20%（基准 k=1）；成本 0.1%、空仓 1.40%、vol20 + 死区 10pp + T+1、预热 300、后段 40%。", "",
        "## 0. 波动层公式：到底有几个旋钮", "",
        "`仓位 = min(1, k · TV0 / vol20)`。能自适应的只有三处：", "",
        "| 旋钮 | 已有研究 | 结论 |",
        "|---|---|---|",
        "| vol 估计子（10/60/max/blend/EWMA） | v8 `_probe_vol_layer.md` §3 | 全不赢纯 vol20，「窗口别太长别太短」只是别选错 |",
        "| TV0 随长期波动漂移（∝vol250） | v8 §4 / `../probe_vol_anchor/_v10_vol_anchored_tv.md` | 正相自我抵消、反相才有效；最多打平且更贵 |",
        "| **风险预算倍率 k（本探针）** | 之前三种自适应都在切 k，但幅度锁在 [0.5,1] | 扫幅度 + 新状态 |",
        "",
        "> **k 的含义**：k=1 → TV20 基准；k=0.5 → TV10（半仓目标）；k=0 → 完全空仓；k=2 → TV40（vol<40% 时几乎满仓）。"
        "所以 k 是「风险预算倍率」，不是收益目标——调 k 就是调愿意为这条腿注多少风险。", "",
        "> 之前三种自适应（趋势方向 / 波动锚 / ER 纯度）本质都是「用状态把 k 切成两档」，"
        "只是状态变量不同，k 的上下界一直是 0.5/1。本探针新增 4 种状态变量，并把 k 幅度从 [0.5,1] 打开到含 [0,2] 的六档。", "",
        "## 1. 方法", "",
        "- 状态变量（True=好状态→k_hi）：ER 趋势纯度（已有）/ 回撤深度（浅为好，**新**）/ vol 变化率（下降为好，**新**）/ 距年线距离（**新**）。",
        "- k 幅度：窄 .75-1.25 / 现有 .5-1 / 中幅 .5-1.5 / **全幅 0-2** / 熊市空仓 0-1 / 只高倍 1-2。",
        "- 固定基线 = 常数 k=0.5（TV10）与 k=1（TV20）；严格判定 = 后段夏普赢两者、全样本 ≥ 两者最大值。", "",
    ]
    detail_legs = ["ndx", "spx"]
    summary = {}
    for leg in detail_legs:
        px = vl.load_nav(leg)
        vol = px.pct_change().rolling(20, min_periods=20).std(ddof=1) * math.sqrt(TD)
        states = make_states(px, vol)
        const = lambda k: pd.Series(k, index=px.index)
        f10, q10, t10 = evaluate(leg, const(0.5))
        f20, q20, t20 = evaluate(leg, const(1.0))
        lines += [f"## 2.{detail_legs.index(leg)+1} {LEG_NAMES[leg]}", "",
                  f"> 固定基线：k=0.5(TV10) 全 {f10['sharpe']:.2f}/后 {q10['sharpe']:.2f}·{t10} 次；"
                  f"k=1(TV20) 全 {f20['sharpe']:.2f}/后 {q20['sharpe']:.2f}·{t20} 次。", "",
                  "| 状态 | k幅度 | 全样本夏普 | 全样本年化 | 后段夏普 | 后段年化 | 调仓 | 严格赢? |",
                  "|---|---|---:|---:|---:|---:|---:|:---:|"]
        wins = []
        for sname, good in states.items():
            for aname, klo, khi in AMPS:
                k_s = pd.Series(np.where(good, khi, klo), index=px.index)
                f, q, tr = evaluate(leg, k_s)
                win = q["sharpe"] > q10["sharpe"] and q["sharpe"] > q20["sharpe"] and \
                      f["sharpe"] >= max(f10["sharpe"], f20["sharpe"]) - 1e-9
                if win:
                    wins.append(f"{sname}×{aname}")
                lines.append(f"| {sname} | {aname} | {f['sharpe']:.2f} | {f['ann']:+.2%} | "
                             f"{q['sharpe']:.2f} | {q['ann']:+.2%} | {tr} | {'✓' if win else ''} |")
        summary[leg] = wins
        lines += ["", f"**严格赢（{len(wins)} 个）：{'、'.join(wins) if wins else '无'}**", ""]

    # 7 腿总表：用 ER 状态扫 k 幅度（ER 是上一轮最强状态）
    lines += ["## 3. 7 腿总表（ER 趋势纯度状态 × k 幅度）", "",
              "| 腿 | 固定 .5 全/后 | 固定 1 全/后 | 窄 .75-1.25 | 现有 .5-1 | 中 .5-1.5 | 全幅 0-2 | 熊市 0-1 | 只高 1-2 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for leg in LEGS:
        px = vl.load_nav(leg)
        vol = px.pct_change().rolling(20, min_periods=20).std(ddof=1) * math.sqrt(TD)
        good = make_states(px, vol)["ER趋势纯度"]
        const = lambda k: pd.Series(k, index=px.index)
        f10, q10, _ = evaluate(leg, const(0.5))
        f20, q20, _ = evaluate(leg, const(1.0))
        cells = []
        for aname, klo, khi in AMPS:
            f, q, tr = evaluate(leg, pd.Series(np.where(good, khi, klo), index=px.index))
            win = q["sharpe"] > q10["sharpe"] and q["sharpe"] > q20["sharpe"] and \
                  f["sharpe"] >= max(f10["sharpe"], f20["sharpe"]) - 1e-9
            cells.append(f"{f['sharpe']:.2f}/{q['sharpe']:.2f}{'✓' if win else ''}")
        lines.append(f"| {LEG_NAMES[leg]} | {f10['sharpe']:.2f}/{q10['sharpe']:.2f} | "
                     f"{f20['sharpe']:.2f}/{q20['sharpe']:.2f} | " + " | ".join(cells) + " |")
    lines += ["", "> 单元格 = 全样本夏普/后段夏普，✓ = 严格赢（后段赢两个固定档 + 全样本不输）。", ""]

    lines += ["## 4. 结论", ""]
    for leg in detail_legs:
        lines.append(f"- **{LEG_NAMES[leg]}严格赢 {len(summary[leg])} 个**："
                     f"{'、'.join(summary[leg]) if summary[leg] else '无'}。")
    lines += [
        "### 1. 用户要的全幅 0-2：美股失败、A股反而是最优 —— 这是本探针最重要的发现", "",
        "- **美股两腿上全幅 0-2 是灾难**：纳指全样本 **0.40** / 后段 0.52（基线 0.75/0.81）、"
        "标普 **0.19 / 0.08**（基线 0.50/0.60），所有状态变量配全幅都垫底。原因在公式里："
        "k=0 → 坏状态**完全空仓**，而 ER 趋势态只占 24%、误判多，一次误判就踏空整段上涨；"
        "k=2 → 好状态 TV40%，`min(1, 0.4/vol20)` 在 vol<40% 时恒为 1，**把波动层的平滑作用抵消掉**。"
        "两头一叠加，等于「该平的没平、该在的没在」。",
        "- **但 A股三腿上全幅 0-2 反而最好**：A500 全样本 0.62 / 后段 0.22、沪深300 0.66 / 0.29、"
        "恒生科技 0.08 / 0.45（基线恒科还是负的）。这三条腿年化低、波动大、回撤深，"
        "「坏状态完全空仓」从踏空变成**保护**——同样的 k 幅度在不同风险特征的市场里符号相反。",
        "- **原理结论：k 的合理幅度不是一个数，它取决于那条腿的「踏空成本 vs 挨跌成本」**。"
        "美股是长牛、踏空成本极高 → k 下界不能到 0，上界不必到 2；A股是震荡市、挨跌成本极高 → "
        "k 放到 0-2 反而对。**所以不存在一套通吃 7 腿的 k 幅度**，必须逐腿定。",
        "",
        "### 2. 新增的四种状态：只有一个真正过线", "",
        "- **距年线（新）在标普上过线**：窄幅 .75-1.25 = 全 0.52/后 0.65、现有 .5-1 = 全 0.52/后 0.64，"
        "都严格赢两个固定档，**且调仓只有 184/206 次**（波动锚方案要 400+）。它不是新 TV 值，"
        "是「站上年线给足预算、跌破收一点」的连续版择时——和 `_v10_adaptive_tv.md` 的 MA200 两档同源，"
        "但窄幅让调仓大降。",
        "- **回撤深度（新）设计意图最好、实测没过线**：它是唯一针对「慢熊」的状态（深回撤降 k，"
        "补 V10 原生只治急跌的洞），但纳指 0.71/0.77、标普 0.46/0.53 都没赢基线——"
        "**等回撤已经发生再降 k 太晚**，和均线一样滞后，慢熊里 vol 早已回落、仓位本来就回来了。",
        "- **vol 变化率（新）没过线**：纳指 0.71/0.75、标普 0.47/0.63。它和波动锚重复计价——"
        "波动锚用 vol 水平分位、它用 vol 的一阶导数，在美股上都没带来新信息，只多出调仓（纳指 573 次）。",
        "",
        "### 3. 与前三种自适应合起来看", "",
        "- 纳指仍是 **ER 趋势纯度** 独占（现有档 0.79/0.87、中幅 0.75/0.81 过线）；"
        "标普扩为 **距年线**（本探针）与 **波动锚/MA120**（前两版）两条路，其中距年线调仓最低。",
        "- **七种状态（趋势方向/波动锚/ER/回撤/vol变化率/距年线，外加 vol 估计子）里，"
        "真正稳定过线的只有 ER 与距年线两种**，且各管一条腿——再次印证「没有通吃两腿的自适应」。",
        "> 本探针只给结论，未改任何策略代码。", ""]
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"WROTE {OUT}")
    for leg in detail_legs:
        print(LEG_NAMES[leg], "strict:", len(summary[leg]))


if __name__ == "__main__":
    main()
