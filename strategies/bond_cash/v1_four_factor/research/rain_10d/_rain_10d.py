# -*- coding: utf-8 -*-
"""V1 · 过去 10 个交易日「债券一直跌」的事后复盘：V1 当时能不能提前降久期？

只读本地缓存（不联网、不写任何策略产物），用 engine 的向量化函数逐日重算
温度 / 四因子 / score / 目标长端占比，再按 R-18 滞后带串成实际持仓路径。

输出两个视角：
  ① 事实：长端 003376 与底仓 011983 的逐日涨跌、区间累计；
  ② V1 视角：每天收盘的 score / 目标 / 实际 w_long（T+1 才生效），
     以及「首次转向」发生在哪一天、比净值高点早还是晚。

用法: python strategies/bond_cash/v1_four_factor/research/rain_10d/_rain_10d.py [--days 10]
产物: 同目录 _rain_10d.md
"""
import argparse
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../v1_four_factor/research/rain_10d
_RES = os.path.dirname(_HERE)                               # .../v1_four_factor/research
_PKG = os.path.dirname(_RES)                                # .../v1_four_factor
for _p in (os.path.dirname(_PKG), _PKG, _RES, _HERE):
    while _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)

import pandas as pd  # noqa: E402

import rule    # noqa: E402
import data    # noqa: E402
import engine  # noqa: E402

CFG = rule.CFG
OUT_MD = os.path.join(_HERE, "_rain_10d.md")
STATE = os.path.join(_PKG, "daily", "_v1_state.json")


def load_cached():
    """只读 data/ 下的缓存，不触发任何联网刷新。"""
    with open(data._NAV_CACHE, encoding="utf-8") as f:
        c = json.load(f)
    navs = {k: pd.Series(v["tot"], index=pd.to_datetime(v["dates"]), dtype="float64").sort_index()
            for k, v in (c.get("series") or {}).items()}
    curve = pd.read_csv(data._CURVE, index_col=0, parse_dates=True) \
        if os.path.exists(data._CURVE) else pd.DataFrame()
    shibor = pd.read_csv(data._SHIBOR, index_col=0, parse_dates=True)["shibor_1w"].astype(float) \
        if os.path.exists(data._SHIBOR) else pd.Series(dtype="float64")
    return navs, curve, shibor, data.load_y10(), c.get("date", "")


def build(navs, curve, shibor, y10):
    bench = navs[CFG["bench"]]
    temps = engine.build_temps(navs, y10, shibor, curve)
    factors = engine.build_factors(temps, bench)
    sig = engine.build_signal(factors)
    sig["w"] = engine.apply_hysteresis(sig["target"], start=CFG["long_base"])
    return temps, factors, sig


def state_w():
    try:
        with open(STATE, encoding="utf-8") as f:
            return float(json.load(f)["w_long"]) * 100.0
    except (OSError, ValueError, KeyError, TypeError):
        return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=10)
    args = ap.parse_args(argv)
    n = int(args.days)

    navs, curve, shibor, y10, cache_date = load_cached()
    temps, factors, sig = build(navs, curve, shibor, y10)

    long_c, base_c = CFG["long_leg"], CFG["base_leg"]
    px = pd.concat({"long": navs[long_c], "base": navs[base_c]}, axis=1, join="inner").dropna()
    ret = px.pct_change()

    idx = px.index
    win = idx[-n:]
    prev = idx[-n - 1] if len(idx) > n else idx[0]

    L = []
    A = L.append
    A("# V1 · 过去 %d 个交易日债券下跌复盘" % n)
    A("")
    A(f"> 净值缓存日 {cache_date} · 区间 {win[0].date()} ~ {win[-1].date()} "
      f"（{len(win)} 个交易日）· 只读缓存，不联网")
    A("")
    A("## 一、事实：这 %d 天到底跌了多少" % len(win))
    A("")
    A("| 日期 | %s 长端 | 日涨跌 | %s 底仓 | 日涨跌 |" % (long_c, base_c))
    A("|---|---:|---:|---:|---:|")
    for d in win:
        A(f"| {d.date()} | {px.loc[d, 'long']:.4f} | {ret.loc[d, 'long'] * 100:+.3f}% "
          f"| {px.loc[d, 'base']:.4f} | {ret.loc[d, 'base'] * 100:+.3f}% |")
    seg = px.loc[win]
    long_ret = seg["long"].iloc[-1] / px.loc[prev, "long"] - 1.0
    base_ret = seg["base"].iloc[-1] / px.loc[prev, "base"] - 1.0
    A("")
    A(f"- 长端累计 **{long_ret * 100:+.3f}%**，底仓累计 **{base_ret * 100:+.3f}%**")
    A(f"- 长端下跌天数 {int((ret.loc[win, 'long'] < 0).sum())}/{len(win)}，"
      f"底仓 {int((ret.loc[win, 'base'] < 0).sum())}/{len(win)}")

    A("")
    A("## 二、V1 逐日说了什么（收盘信号，T+1 才承担权重）")
    A("")
    A("| 日期 | 估值温度 | 资金 | 动能 | 信用 | 合成 | 估值 | 趋势 | 资金 | 信用 | score | 信号 | 目标% | 实际w% |")
    A("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|")
    look = idx[-(n + 6):]
    for d in look:
        if d not in temps.index:
            continue
        t = temps.loc[d]
        f = factors.loc[d]
        s = sig.loc[d]
        A(f"| {d.date()} | {t['valuation']:.0f} | {t['funding']:.0f} | {t['momentum']:.0f} "
          f"| {t['credit']:.0f} | {t['composite']:.0f} {rule.temp_band(t['composite'])} "
          f"| {int(f['valuation']):+d} | {int(f['trend']):+d} | {int(f['funding']):+d} "
          f"| {int(f['credit']):+d} | **{int(s['score']):+d}** | {s['side']} "
          f"| {s['target'] * 100:.1f} | {s['w'] * 100:.1f} |")

    A("")
    A("## 三、能不能预测到")
    A("")
    hi_win = px.loc[idx[-60:], "long"].idxmax()
    A(f"- 长端近 60 日高点：**{hi_win.date()}**"
      f"（距区间末 {(idx[-1] - hi_win).days} 个自然日）")

    # 首次转向：score ≤ -2（偏空）与趋势因子首次 -1
    tail = idx[-120:]
    bear = [d for d in tail if int(sig.loc[d, "score"]) <= CFG["side_down"]]
    tr_dn = [d for d in tail if int(factors.loc[d, "trend"]) < 0]
    A(f"- 近 120 个交易日里 **score ≤ {CFG['side_down']}（偏空）首次出现在 "
      f"{bear[0].date() if bear else '从未'}**；趋势因子首次转 −1 "
      f"**{tr_dn[0].date() if tr_dn else '从未'}**")
    A(f"- 区间首日（{win[0].date()}）收盘 score = "
      f"**{int(sig.loc[win[0], 'score']):+d}**（{sig.loc[win[0], 'side']}），"
      f"实际长端 {sig.loc[win[0], 'w'] * 100:.1f}%")
    sw = state_w()
    A(f"- 状态文件 `daily/_v1_state.json` 当前 w_long = "
      f"**{sw:.1f}%**" if sw is not None else "- 状态文件缺失（按基准 35%）")

    # 10 日区间 V1 组合 vs 固定哑铃（w 用 T 日权重 → T+1 收益）
    w_eff = sig["w"].reindex(ret.index).shift(1)
    v1 = (w_eff * ret["long"] + (1.0 - w_eff) * ret["base"]).loc[win]
    fixed = (0.70 * ret["long"] + 0.30 * ret["base"]).loc[win]
    A(f"- 同期债腿收益：V1 路径 **{(1 + v1).prod() - 1:+.3%}** vs "
      f"v0 固定 70/30 **{(1 + fixed).prod() - 1:+.3%}**"
      f"（差 **{((1 + v1).prod() - (1 + fixed).prod()) * 100:+.3f}pp**）")

    A("")
    A("## 四、趋势因子为什么没提前转空")
    A("")
    bt = CFG["trend"]
    c = navs[CFG["bench"]].reindex(idx).ffill()
    ma5 = c.rolling(bt["ma_fast"]).mean()
    ma20 = c.rolling(bt["ma_slow"]).mean()
    hi20 = c.rolling(bt["h20"]).max()
    lo20 = c.rolling(bt["h20"]).min()
    A("| 日期 | 511260 累计净值 | 日涨跌 | MA5 | MA20 | close<MA5 | MA5<MA20 | close/lo20 | 距下穿阈值 |")
    A("|---|---:|---:|---:|---:|:---:|:---:|---:|---:|")
    for d in look:
        if d not in c.index or pd.isna(ma20.loc[d]):
            continue
        ratio = float(c.loc[d] / lo20.loc[d] - 1.0)
        need = (1.0 + bt["near"]) * float(lo20.loc[d])
        A(f"| {d.date()} | {c.loc[d]:.4f} | {c.loc[d] / c.shift(1).loc[d] * 100 - 100:+.3f}% "
          f"| {ma5.loc[d]:.4f} | {ma20.loc[d]:.4f} "
          f"| {'✓' if c.loc[d] < ma5.loc[d] else '✗'} "
          f"| {'✓' if ma5.loc[d] < ma20.loc[d] else '✗'} "
          f"| {ratio * 100:+.2f}% | {c.loc[d] - need:+.4f} |")
    A("")
    A(f"> 趋势转 −1 需要三条同时成立：close<MA{bt['ma_fast']}、MA{bt['ma_fast']}<MA{bt['ma_slow']}、"
      f"close ≤ {1 + bt['near']:.3f}×近 {bt['h20']} 日最低。上表最后一列 = 收盘价还差多少才触到第三条"
      "（正数=还高多少，负数=已跌破）。")

    A("")
    A("> 说明：V1 的输入全部来自**收盘后**的净值/曲线/SHIBOR，"
      "且 T 日信号 T+1 才建仓；任何「比高点早 N 天」的说法都已经扣掉了这层滞后。")
    A("")
    A("> 由 `research/rain_10d/_rain_10d.py` 生成，机械口径，不构成投资建议。")

    txt = "\n".join(L)
    print(txt)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(txt + "\n")
    print(f"\n[ok] {OUT_MD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
