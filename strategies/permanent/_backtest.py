# -*- coding: utf-8 -*-
"""永久投资组合 · 组合层月度回测(倾斜 vs 静态基准 vs 等权)。

要点:
  - 同一份数据、同一执行框架, 只换「是否倾斜」, 隔离倾斜本身的贡献;
  - 月末算信号 → 次月持有, 无前视;
  - 权重口径与 _signal.py 完全一致(同一 rule.target_weights)。

数据口径(代理延长历史, 见 README §5):
  A500 联接 023299 成立于 2025-03 → 用中证A500 指数 SH000510 代理
  标普 联接 017641 成立于 2023-04 → 用标普500ETF 513500 代理
  货基无有效净值         → 按 rule.CASH_ANNUAL 常数计
  其余腿一律用场外真实净值(与执行口径一致)

用法:
  python strategies/permanent/_backtest.py            # 主口径(SCORE_GAIN=rule 值)
  python strategies/permanent/_backtest.py --sweep     # 扫描 SCORE_GAIN 找最优
输出: strategies/permanent/backtest/_permanent_bt.{json,md}
"""
import argparse
import io
import json
import math
import os
import sys
from datetime import datetime

import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import xalpha as xa  # noqa: E402

import rule  # noqa: E402
import data  # noqa: E402

_DIR = os.path.dirname(os.path.abspath(__file__))
_BT_DIR = os.path.join(_DIR, "backtest")
_PX_CACHE = os.path.join(_DIR, "data", "_bt_px.json")
_OUT_MD = os.path.join(_BT_DIR, "_permanent_bt.md")
_OUT_JSON = os.path.join(_BT_DIR, "_permanent_bt.json")

# 腿 -> 回测用价格源 ("nav"=场外净值 / "px"=场内或指数收盘)
_PROXY = {
    "a500": ("px", "SH000510"),     # 023299 联接 2025-03 成立, 历史不足
    "spx": ("px", "SH513500"),      # 017641 联接 2023-04 成立, 历史不足
}
_START = "2021-01-01"               # 各腿真实净值共同起点约 2021-05, 前留 120 日缓冲


def _sh(code):
    return ("SH" if code.startswith(("5", "6", "9")) else "SZ") + code


def _load_px(key):
    """场内/指数收盘 -> Series。"""
    cache = {}
    if os.path.exists(_PX_CACHE):
        with open(_PX_CACHE, encoding="utf-8") as f:
            cache = json.load(f)
    if key in cache:
        node = cache[key]
        return pd.Series(node["close"], index=pd.to_datetime(node["date"]), dtype="float64")
    df = xa.get_daily(key, start="2019-01-01").dropna(subset=["close"]).sort_values("date")
    cache[key] = {"date": [str(d)[:10] for d in pd.to_datetime(df["date"])],
                  "close": [float(x) for x in df["close"]]}
    os.makedirs(os.path.dirname(_PX_CACHE), exist_ok=True)
    with open(_PX_CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)
    return pd.Series(cache[key]["close"],
                     index=pd.to_datetime(cache[key]["date"]), dtype="float64")


def load_legs():
    """-> {leg: Series}, cash 腿不在此(由常数收益处理)。"""
    out = {}
    for leg in rule.LEG_ORDER:
        if leg == "cash":
            continue
        kind, code = _PROXY.get(leg, ("nav", rule.LEGS[leg]["code"]))
        out[leg] = _load_px(code) if kind == "px" else data.load_nav(code)
    return out


def month_ends(legs):
    """所有腿交易日并集的月末; 起点取各腿真实起点的最大值(保证同时可见)。"""
    start = max(s.index[0] for s in legs.values())
    idx = None
    for s in legs.values():
        idx = s.index if idx is None else idx.union(s.index)
    idx = idx[idx >= max(start, pd.Timestamp(_START))]
    ser = pd.Series(idx, index=idx)
    return [d for _, d in ser.groupby([idx.year, idx.month]).last().items()]


def _ret_between(s, a, b):
    try:
        va = float(s.asof(a))
        vb = float(s.asof(b))
        if va != va or vb != vb or va <= 0:
            return 0.0
        return vb / va - 1.0
    except Exception:
        return 0.0


def _metrics_from_returns(rs, periods_per_year=12, rf=rule.CASH_ANNUAL):
    nav = [1.0]
    for r in rs:
        nav.append(nav[-1] * (1 + r))
    cum = nav[-1] - 1.0
    n = len(rs)
    ann = (nav[-1]) ** (periods_per_year / n) - 1.0 if n else 0.0
    mu = sum(rs) / n if n else 0.0
    sd = math.sqrt(sum((x - mu) ** 2 for x in rs) / n) * math.sqrt(periods_per_year) if n else 0.0
    peak, mdd = nav[0], 0.0
    for v in nav:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1.0)
    sharpe = (ann - rf) / sd if sd > 1e-9 else 0.0
    return dict(cum=cum, ann=ann, vol=sd, mdd=mdd, sharpe=sharpe, nav=nav)


def run(legs, gain=None, mode="tilt"):
    """mode: tilt=按信号倾斜 / static=每月回到基准 / equal=等权 / hold=买入持有。"""
    if gain is not None:
        rule.SCORE_GAIN = gain
    ds = month_ends(legs)
    bw = rule.base_weights()
    total = sum(bw.values())
    rs, wlog = [], []

    for i in range(len(ds) - 1):
        t, t2 = ds[i], ds[i + 1]
        days = max((t2 - t).days, 1)
        if mode == "tilt":
            met, ok = {}, True
            for leg, s in legs.items():
                hist = s[s.index <= t]
                if len(hist) < 120:
                    ok = False
                    break
                met[leg] = rule.leg_metrics(hist)
            w_item = rule.target_weights(rule.item_scores(rule.leg_scores(met)))[0] if ok else bw
        elif mode == "equal":
            w_item = {k: total / len(bw) for k in bw}
        elif mode == "hold":
            w_item = bw if i == 0 else wlog[-1]
        else:
            w_item = bw

        w = rule.leg_weights(w_item)          # item 级 -> leg 级
        port = 0.0
        for leg, s in legs.items():
            port += w[leg] * _ret_between(s, t, t2)
        r_cash = (1 + rule.CASH_ANNUAL) ** (days / 365.0) - 1.0
        port += w.get("cash", 0.0) * r_cash
        rs.append(port / total)
        wlog.append(w_item)

    return rs, wlog, ds


def _tbl(name, m):
    return (f"| {name} | {m['cum']:.0%} | {m['ann']:.1%} | {m['vol']:.1%} | "
            f"{m['mdd']:.1%} | {m['sharpe']:.2f} |")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", action="store_true", help="扫描 SCORE_GAIN")
    args = ap.parse_args()

    legs = load_legs()
    print("[data] legs:", {k: (str(v.index[0].date()), len(v)) for k, v in legs.items()})

    results, logs = {}, {}
    for mode in ("tilt", "static", "equal", "hold"):
        rs, wlog, ds = run(legs, mode=mode)
        results[mode] = _metrics_from_returns(rs)
        logs[mode] = (rs, wlog, ds)

    sweep = {}
    if args.sweep:
        for g in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
            rs, _, _ = run(legs, gain=g, mode="tilt")
            sweep[g] = _metrics_from_returns(rs)

    # 主口径
    n = len(results["tilt"]["nav"]) - 1
    ds = logs["tilt"][2]
    L = []
    A = L.append
    A("# 永久投资组合 · 组合层回测")
    A("")
    A(f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 样本 {ds[0]:%Y-%m-%d} ~ {ds[-1]:%Y-%m-%d} "
      f"({n} 个月, 月度调仓, 未计申赎费与 C 类年费)")
    A("> 口径: A500→中证A500指数(SH000510); 标普500→513500; 货基按 "
      f"{rule.CASH_ANNUAL:.2%} 常数; 其余腿=场外真实净值。机械输出, 非投资建议。")
    A("")
    A("## 一、绩效")
    A("")
    A("| 组合 | 累计 | 年化 | 年化波动 | 最大回撤 | 夏普 |")
    A("|---|---:|---:|---:|---:|---:|")
    A(_tbl("**策略(±50% 倾斜)**", results["tilt"]))
    A(_tbl("静态基准(每月回锚)", results["static"]))
    A(_tbl("8 项等权", results["equal"]))
    A(_tbl("买入持有(首月建仓后不动)", results["hold"]))
    A("")

    if sweep:
        A("## 二、倾斜强度扫描(SCORE_GAIN)")
        A("")
        A("| SCORE_GAIN | 累计 | 年化 | 年化波动 | 最大回撤 | 夏普 |")
        A("|---:|---:|---:|---:|---:|---:|")
        for g, mm in sweep.items():
            A(f"| {g} | {mm['cum']:.0%} | {mm['ann']:.1%} | {mm['vol']:.1%} | "
              f"{mm['mdd']:.1%} | {mm['sharpe']:.2f} |")
        A("")

    # 平均权重
    _, wlog, _ = logs["tilt"]
    items = list(rule.ITEM_ORDER)
    avg = {it: sum(w[it] for w in wlog) / len(wlog) for it in items}
    mn = {it: min(w[it] for w in wlog) for it in items}
    mx = {it: max(w[it] for w in wlog) for it in items}
    sec = "三" if sweep else "二"
    A(f"## {sec}、策略实际权重分布(月度)")
    A("")
    A("| 资产 | 基准 | 平均 | 最低 | 最高 | 触及下界月数 | 触及上界月数 |")
    A("|---|---:|---:|---:|---:|---:|---:|")
    bd = rule.bounds()
    for it in items:
        lo_n = sum(1 for w in wlog if w[it] <= bd[it][0] + 1e-6)
        hi_n = sum(1 for w in wlog if w[it] >= bd[it][1] - 1e-6)
        A(f"| {rule.ITEMS[it]['name']} | {rule.base_weights()[it]:.1f}% | {avg[it]:.1f}% | "
          f"{mn[it]:.1f}% | {mx[it]:.1f}% | {lo_n} | {hi_n} |")
    A("")

    sec_n = "四" if sweep else "三"
    A(f"## {sec_n}、结论与局限")
    A("")
    d_ann = results["tilt"]["ann"] - results["static"]["ann"]
    d_mdd = results["tilt"]["mdd"] - results["static"]["mdd"]
    A(f"- 倾斜相对静态基准: 年化 {d_ann:+.2%}, 最大回撤 {d_mdd:+.2%}。")
    A(f"- 相对买入持有: 年化 {results['tilt']['ann'] - results['hold']['ann']:+.2%}。")
    A("- 样本仅 "
      f"{n} 个月, 覆盖 2022 熊市 / 2023-24 震荡 / 2025-26 上行各一段, "
      "但单一市场周期, 对「趋势策略在长横盘里反复打脸」这一典型失效模式检验力不足。")
    A("- 未计 C 类销售服务费(约 0.3-0.5%/年)与申赎费; 倾斜会提高换手, 实际成本高于静态。")
    A("- A500/S&P 为指数与 ETF 代理, 与联接基金净值存在跟踪误差; 513500 含场内溢价。")
    A("")
    A("> 免责声明: 代理口径估算, 不构成投资建议。")
    md = "\n".join(L)

    os.makedirs(_BT_DIR, exist_ok=True)
    with open(_OUT_MD, "w", encoding="utf-8") as f:
        f.write(md + "\n")
    with open(_OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(dict(
            generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
            window=[f"{ds[0]:%Y-%m-%d}", f"{ds[-1]:%Y-%m-%d}"], months=n,
            score_gain=rule.SCORE_GAIN, tilt=rule.TILT,
            perf={k: {kk: round(vv, 4) for kk, vv in v.items() if kk != "nav"}
                  for k, v in results.items()},
            sweep={str(k): {kk: round(vv, 4) for kk, vv in v.items() if kk != "nav"}
                   for k, v in sweep.items()},
            avg_weights={k: round(v, 2) for k, v in avg.items()},
        ), f, ensure_ascii=False, indent=2)
    print(f"[ok] {_OUT_MD}")
    for mode in ("tilt", "static", "equal", "hold"):
        print(f"  {mode:7s} 累计 {results[mode]['cum']:7.1%}  年化 {results[mode]['ann']:6.2%}  "
              f"回撤 {results[mode]['mdd']:7.1%}  夏普 {results[mode]['sharpe']:.2f}")


if __name__ == "__main__":
    main()
