# -*- coding: utf-8 -*-
"""V10 专项：把 V3 的 Kaufman ER(20) 窗口自适应搬成 TV 自适应。

参考：`strategies/vol_legs/v3_macd_boll/backtest/_v3_macd_boll_bt.md` 规则表——
「窗口自适应：ER(20) ≥0.40 → 趋势态窗口 20（早抓）；≤0.30 → 震荡态窗口 30（滤假突破）；带滞回」。
V3 用 ER 切**窗口**（趋势态早抓、震荡态保守）；本探针把同一个 ER 状态机拿来切 V10 的 **TV**：
趋势态给高 TV（敢满仓吃趋势）、震荡态给低 TV（压浅仓位）。

与前两版 V10 自适应的区别（这是本探针存在的理由）：
- `../probe_adaptive/_v10_adaptive_tv.md` 用趋势**方向**（MA200/MA120/动量）—— 只看方向、不看路径效率；
- `../probe_vol_anchor/_v10_vol_anchored_tv.md` 用 vol 的 max/min/mean —— TV 与 vol20 分母同源，正相会自我抵消；
- 本探针用 ER = |20日净变动| / 20日路径总变动，衡量**趋势纯度**，与 vol20 的波动**幅度**没有
  代数耦合 —— 所以「ER 高→TV 高」的正相映射不会被自己抵消，这是 ER 独有的优势。

⚠ 本探针是 2026-11-16 的**两腿历史探索**（ndx/spx），结论不得单独外推到全池；
全腿版本见 `_probe_er_adaptive_7legs.py` 与 `../probe_adaptive/_probe_adaptive_universe.py`。

状态机完全照搬 V3 `macd_boll.signal()`：ER≥0.40 → 趋势态(True)，≤0.30 → 震荡态(False)，
中间维持上次状态（滞回死区）。ER 当日收盘算出 → TV 当日确定 → 全序列 shift(1) 后次日成交，
无前视。其余机制（vol20 + 死区 10pp + T+1 + 单边 0.1% + 空仓 1.40%）全部不动。

方案（A/B 对照，C~F 候选）：
- A 固定 TV10 / B 固定 TV20
- C ER 两档正相 20/10（趋势态 20、震荡态 10，匹配 V3「趋势态激进」精神）
- D ER 两档反相 10/20（验证方向：震荡态反而给高 TV）
- E ER 三档 20/15/10（纯阈值、无滞回）
- F ER 两档激进正相 25/10

判定（严格，与前两版一致）：后段夏普 > 固定 TV10 **且** > 固定 TV20，
且全样本夏普 ≥ 两个固定档的最大值 —— 否则只是「后段挑参」。
产物：`_v10_er_adaptive_tv.md`（本目录）。
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

OUT = os.path.join(_HERE, "_v10_er_adaptive_tv.md")
LEGS = dict(ndx="纳指100", spx="标普500")
TD, WARMUP, CUT = 252, 300, 0.60
ER_N, ER_HI, ER_LO = 20, 0.40, 0.30


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
    """Kaufman 效率比 ER：|n 日净变动| / n 日路径总变动（照搬 V3 macd_boll.efficiency_ratio）。"""
    s = px.astype("float64")
    return (s - s.shift(n)).abs() / s.diff().abs().rolling(n).sum()


def er_state(er, hi=ER_HI, lo=ER_LO):
    """照搬 V3 signal() 的滞回状态机：≥hi 趋势态 / ≤lo 震荡态 / 中间维持。"""
    fast = False
    out = []
    for v in er.to_numpy(dtype=float):
        if not np.isnan(v):
            if v >= hi:
                fast = True
            elif v <= lo:
                fast = False
        out.append(fast)
    return pd.Series(out, index=er.index)


def run_tv_series(leg, tv_s, win=20, dead=0.10):
    """与 _probe_adaptive_tv.py 完全同机制：TV 序列 → vol20 → 死区 → shift(1) → run_leg。"""
    px = vl.load_nav(leg)
    sig = vl.signal_of(leg)
    vol = sig.pct_change().rolling(win, min_periods=win).std(ddof=1) * math.sqrt(TD)
    vol = vol.reindex(sig.index)
    s = (tv_s.reindex(sig.index) / vol).replace([np.inf, -np.inf], np.nan).clip(upper=1.0).fillna(1.0).clip(0.0, 1.0)
    st = vl.deadzone(s, dead).reindex(px.index).ffill().fillna(1.0).shift(1).fillna(1.0)
    nv = vl.run_leg(st, px)
    return nv, st


def main():
    lines = [
        "# V10 × V3 的 ER 自适应：用「趋势纯度」切 TV，实测回答", "",
        "> ⚠ 两腿历史探索（ndx/spx），结论不得单独外推到全池；全腿规律见 "
        "`_v10_er_adaptive_7legs.md` / `../probe_adaptive/_v10_adaptive_universe.md`。", "",
        "> 2026-11-16 生成。参考 `v3_macd_boll` 的 ER(20) 窗口自适应（≥0.40 趋势态 / ≤0.30 震荡态、带滞回），"
        "把同一个状态机搬来切 V10 的 TV。前两版 V10 自适应分别用趋势方向（MA）和波动锚（vol max/min/mean），"
        "都没用过 ER；ER 衡量路径效率、与 vol20 幅度无代数耦合，正相映射不会自我抵消。", "",
        "## 0. 方法", "",
        "- ER(20) = |20 日净变动| / 20 日路径总变动，照搬 V3 `efficiency_ratio()`；"
        "状态机照搬 V3 `signal()`：ER≥0.40→趋势态、≤0.30→震荡态、中间维持。",
        "- 趋势态给高 TV（敢持仓吃趋势）、震荡态给低 TV（压浅仓位），并同时测反相验证方向。",
        "- 其余完全用 V10 原生机制：vol20 + 死区 10pp + T+1 + 单边 0.1% + 空仓 1.40%；TV 序列 shift(1) 无前视。",
        "- 对照固定 TV10/TV20；严格判定 = 后段夏普同时高于两个固定档，且全样本夏普不低于两者最大值。",
        "- 预热 300 根，后段 = 后 40%。", "",
    ]
    verdict = {}
    for leg, name in LEGS.items():
        px = vl.load_nav(leg)
        er = efficiency_ratio(px)
        stt = er_state(er)
        const = lambda x: pd.Series(x, index=px.index)
        tv3 = pd.Series(np.where(er >= ER_HI, 0.20, np.where(er <= ER_LO, 0.10, 0.15)), index=px.index)
        tv3[er.isna()] = 0.20  # 预热期等价于不启用自适应（vol 亦 NaN → 满仓）
        schemes = {
            "A 固定 TV10": const(0.10),
            "B 固定 TV20": const(0.20),
            "C ER两档正相 20/10": pd.Series(np.where(stt, 0.20, 0.10), index=px.index),
            "D ER两档反相 10/20": pd.Series(np.where(stt, 0.10, 0.20), index=px.index),
            "E ER三档 20/15/10": tv3,
            "F ER两档激进 25/10": pd.Series(np.where(stt, 0.25, 0.10), index=px.index),
        }
        cut = WARMUP + int((len(px) - WARMUP) * CUT)
        rows = []
        for lab, tvs in schemes.items():
            nv, st = run_tv_series(leg, tvs)
            f, q = perf(nv.iloc[WARMUP:]), perf(nv.iloc[cut:])
            pos = st.shift(1).reindex(px.index).iloc[WARMUP:].mean()
            tr = int((st.diff().abs() > 1e-9).iloc[WARMUP:].sum())
            rows.append((lab, f, q, pos, tr))
        b10, b20 = rows[0], rows[1]
        fast_ratio = float(stt.iloc[WARMUP:].mean())
        switches = int((stt.astype(int).diff().abs() > 0).iloc[WARMUP:].sum())
        strict = [lab for lab, f, q, pos, tr in rows if lab.startswith(("C", "D", "E", "F"))
                  and q["sharpe"] > b10[2]["sharpe"] and q["sharpe"] > b20[2]["sharpe"]
                  and f["sharpe"] >= max(b10[1]["sharpe"], b20[1]["sharpe"]) - 1e-9]
        verdict[leg] = strict
        lines += [f"## {name}", "",
                  f"> ER(20) 趋势态占比 {fast_ratio:.0%}、状态切换 {switches} 次（样本内）。", "",
                  "| 方案 | 全样本夏普 | 全样本年化 | 全样本回撤 | 后段夏普 | 后段年化 | 后段回撤 | 平均仓位 | 调仓 | 严格赢? |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|:---:|"]
        for lab, f, q, pos, tr in rows:
            win = (lab.startswith(("C", "D", "E", "F"))
                   and q["sharpe"] > b10[2]["sharpe"] and q["sharpe"] > b20[2]["sharpe"]
                   and f["sharpe"] >= max(b10[1]["sharpe"], b20[1]["sharpe"]) - 1e-9)
            lines.append(f"| {lab} | {f['sharpe']:.2f} | {f['ann']:+.2%} | {f['mdd']:.2%} | "
                         f"{q['sharpe']:.2f} | {q['ann']:+.2%} | {q['mdd']:.2%} | "
                         f"{pos:.1%} | {tr} | {'✓' if win else ''} |")
        lines += ["", f"**严格判定：{'、'.join(strict) if strict else '无方案过线'}**"
                  "（须后段赢两个固定档 + 全样本不输）。", ""]

    lines += ["## 结论", ""]
    for leg, name in LEGS.items():
        lines.append(f"- **{name}**：{'过线方案 = ' + '、'.join(verdict[leg]) if verdict[leg] else '无方案过线'}。")
    lines += [
        "- **方向是对的：必须正相**。纳指 C（ER 高→TV20、低→TV10）全样本 0.79 / 后段 0.87，"
        "同时赢 TV10（0.75/0.81）与 TV20（0.73/0.74）；反相 D 只有 0.65/0.62，全线垫底。"
        "这印证了 ER 与 vol20 幅度无代数耦合——正相不会像波动锚那样自我抵消，"
        "ER 在这里确实是「趋势纯度」：趋势走得干脆就敢持仓、来回磨就压仓。",
        "- **纳指终于有严格赢家，标普反而没有**。前两版（趋势档、波动锚）都是「标普过线、纳指不过」；"
        "ER 维度恰好反过来：纳指 C/F 过线，标普四个候选全不过（C 后段 0.57 输给固定档 0.60/0.62）。"
        "三版合起来看，纳指的可行信号是**趋势纯度 / 慢趋势方向**，标普是**波动锚 / 均线方向**——"
        "两条腿的自适应来源不同，不存在一套通吃两腿的自适应 TV。",
        "- **代价：调仓翻倍**。纳指 C 调仓 496 次 vs 固定 TV20 的 163 次（3 倍）、平均仓位 66% vs 89%；"
        "它拿到的是 TV10 的低回撤（-20% vs -38%）加比 TV10 更高的收益（+11.1% vs +9.7%）。"
        "但这是 0.1% 单边成本下的读数；若走 0.6% 场外口径，多出的 333 次调仓要再扣约 3.3pp 本金，"
        "优势会被吃掉——和 V3 报告 §波动上限层的结论一致：自适应 TV 只适合低费率通道。",
        "- **F（趋势态 TV25）不如 C**：纳指 F 全样本 0.77 < C 的 0.79，说明 TV20 已经够用，"
        "趋势态再放宽到 25% 只会多担波动；三档 E 后段 0.83 虽不错，但调仓 829 次过高、全样本 0.71 不达标。",
        "",
        "> 本探针只给结论，未改任何策略代码。", ""]
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"WROTE {OUT}")
    for leg, name in LEGS.items():
        print(name, "strict:", verdict[leg] or "无")


if __name__ == "__main__":
    main()
