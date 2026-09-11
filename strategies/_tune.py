# -*- coding: utf-8 -*-
"""亮灯策略 · 参数搜索（目标函数 = 策略年化 × 择时边际，即方案 C）。

为什么用乘积: 年化受**暴露度**约束（持仓占比低、绝对收益上不去），择时边际衡量**信号质量**
（不受暴露影响）。乘积要求两者同时不差 —— 单独把暴露堆上去或单独把边际刷高都不算赢。

工程要点（决定扫描规模）:
  * 因子只算一次：`compute_factors` 的结果按「因子参数组合」缓存（FKEY）。
    只改决策层的变体（得分门槛 / 灯开关 / 权重 / 风控 / rebal / 门槛）复用缓存, 单变体亚秒级;
    改到因子参数的变体（窗口/标定系数/周线口径）触发一次重算。
  * 进程内跑完全部变体, 不重复启动、不重复读盘。

输出: strategies/_tune_report.md
用法:
  python strategies/_tune.py                 # 全量单变量扫描（基线 = c7）
  python strategies/_tune.py --group 门槛     # 只扫某一组
  python strategies/_tune.py --oos           # 额外做样本外确认
"""
import json
import os
import sys
import time

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_DIR)
for p in (_ROOT, _DIR, os.path.join(_DIR, "lights")):
    if p not in sys.path:
        sys.path.insert(0, p)

import rule  # noqa: E402
import backtest as bt  # noqa: E402

OUT = os.path.join(_DIR, "_tune_report.md")
OOS_FROM = "2023-01-01"          # 样本外确认的起点
# 会改变因子序列的参数（改到这些就必须重算因子, 否则可复用缓存）
FACTOR_PARAMS = ("macd", "turn_win", "div_win", "div_max", "band_wide", "band_sector",
                 "vol_fast", "vol_hist", "cap_proxy_scale", "cap_win", "weekly_order")


def fkey(cfg):
    return tuple(repr(cfg[k]) for k in FACTOR_PARAMS)


class Bench:
    """进程内回测台：因子缓存 + 变体评估。"""

    def __init__(self, want=None):
        self.panels = rule.load_panels_raw()
        self.end = rule._data.complete_end(self.panels["close"].index)
        self.want = want
        self._df = {}
        self._fcache = {}
        self.n_eval = 0

    def frame(self, code):
        if code not in self._df:
            self._df[code] = rule.trading_frame(self.panels, code, self.end)
        return self._df[code]

    def factors(self, code, cfg):
        k = (code, fkey(cfg))
        if k not in self._fcache:
            self._fcache[k] = rule.compute_factors(self.frame(code), cfg, code)
        return self._fcache[k]

    def run(self, cfg):
        """cfg -> (rows, fails)。统计口径与 backtest.run_one 一致。"""
        self.n_eval += 1
        rows, fails = [], []
        for meta in rule.POOL:
            code = meta["code"]
            if self.want and code not in self.want:
                continue
            df = self.frame(code)
            if cfg.sample_to:
                df = df.loc[:pd.Timestamp(cfg.sample_to)]
            if len(df) < cfg.min_bars + 60:
                fails.append(code)
                continue
            try:
                f = self.factors(code, cfg)
                if cfg.sample_to:
                    # 因子按全样本算并缓存, 这里按日期右边界切片。滚动/EWM/ffill 都是因果的,
                    # 所以「全样本算完取前缀」与「截断后重算」逐位相同。
                    ts = pd.Timestamp(cfg.sample_to)
                    f = {k: (v.loc[:ts] if hasattr(v, "loc") else v) for k, v in f.items()}
                sig = rule.build_signals(f, cfg)
                o = df["open"].to_numpy(float)
                c = df["close"].to_numpy(float)
                begin = int(np.argmax(np.asarray(df.index >= np.datetime64(cfg.sample_from))))
                if len(c) - begin < cfg.min_bars:
                    fails.append(code)
                    continue
                nav, pos, trades, _h, pos_log = rule.run_engine(o, c, sig, begin=begin, cfg=cfg)
                if not np.isfinite(nav[begin]) or nav[begin] <= 0:
                    fails.append(code)
                    continue
                years = max((df.index[-1] - df.index[begin]).days / 365.0, 1e-9)
                bh = c[begin:]
                _r, a, smdd = bt._stat(nav[begin:], years)
                _r2, bb, bmdd = bt._stat(bh, years)
                rows.append(dict(
                    code=code, cat=meta["cat"], theme=meta["theme"],
                    st_ann=a, base_ann=bb, st_mdd=smdd, base_mdd=bmdd,
                    pos_ratio=pos, n_trades=len(trades), trade_log=trades,
                    timing_edge=rule._eng.timing_edge(c, pos_log, begin)))
            except Exception as e:  # noqa: BLE001
                fails.append(f"{code}:{type(e).__name__}")
        return rows, fails


def metrics(rows):
    if not rows:
        return None
    ag = lambda k: float(np.mean([r[k] for r in rows]))  # noqa: E731
    ann, base = ag("st_ann"), ag("base_ann")
    edge = ag("timing_edge") * 1e4
    return dict(n=len(rows), ann=ann, base=base, excess=ann - base, edge=edge,
                pos=ag("pos_ratio"), mdd=ag("st_mdd"), mdd_base=ag("base_mdd"),
                beat=int(sum(1 for r in rows if r["st_ann"] > r["base_ann"])),
                edge_pos=int(sum(1 for r in rows if r["timing_edge"] > 0)),
                score=ann * edge)


# ======================================================================
# 扫描计划：每组 = (组名, [(变体标签, 覆盖参数), ...])
# ======================================================================
def sweep_plan():
    A = rule.ACTIVE
    L = A.lights
    def lights(**kw):
        d = dict(L)
        d.update(kw)
        return d

    return [
        ("得分门槛", [
            (f"enter_min={v}", dict(enter_min=float(v))) for v in (1, 2, 3, 4, 5, 6, 7, 8)
        ] + [
            (f"exit_max={v}", dict(exit_max=float(v))) for v in (0, 1, 2, 3, 4)
        ]),
        ("灯开关（每次关一盏）", [
            ("关 trend", dict(lights=lights(trend=None))),
            ("关 momentum", dict(lights=lights(momentum=None))),
            ("关 capital", dict(lights=lights(capital=None))),
            ("关 sustain", dict(lights=lights(sustain=None))),
            ("关 heat", dict(lights=lights(heat=None))),
            ("加 emotion(换手分位)", dict(lights=lights(emotion=("turn_pct", 1)))),
            ("加 divergence(分化度)", dict(lights=lights(divergence=("div", 1)))),
        ]),
        ("灯定义替换", [
            ("trend→ma_long_mom", dict(lights=lights(trend=("ma_long_mom", 1)))),
            ("capital→vr5_ge07", dict(lights=lights(capital=("vr5_ge07", 1)))),
            ("capital→vr5_any", dict(lights=lights(capital=("vr5_any", 1)))),
            ("sustain→macd_weekly", dict(lights=lights(sustain=("macd_weekly", 1)))),
            ("sustain→macd_binary", dict(lights=lights(sustain=("macd_binary", 1)))),
            ("heat→ret5_vr5", dict(lights=lights(heat=("ret5_vr5", 1)))),
            ("heat→vr60_band", dict(lights=lights(heat=("vr60_band", 1)))),
            ("momentum→ret3_band", dict(lights=lights(momentum=("ret3_band", 1)))),
        ]),
        ("灯权重", [
            (f"{k} 权重 2", dict(lights=lights(**{k: (L[k][0], 2)})))
            for k in ("trend", "momentum", "capital", "sustain", "heat")
        ]),
        ("门槛开关", [
            (f"关 {g}", dict(gates=tuple(x for x in A.gates if x != g)))
            for g in A.gates
        ] + [
            ("关 exit_on_gate_fail", dict(exit_on_gate_fail=False)),
            ("关全部门槛", dict(gates=("indicators_ready",))),
        ]),
        ("建仓灯要求", [
            ("无要求", dict(enter_required={})),
            ("trend>=2", dict(enter_required={"trend": 2})),
            ("trend>=1 & momentum>=1", dict(enter_required={"trend": 1, "momentum": 1})),
            ("trend>=1 & sustain>=1", dict(enter_required={"trend": 1, "sustain": 1})),
        ]),
        ("风控与执行", [
            ("trail 6%", dict(trail_stop=0.06)), ("trail 8%", dict(trail_stop=0.08)),
            ("trail 10%", dict(trail_stop=0.10)), ("trail 12%", dict(trail_stop=0.12)),
            ("max_hold 10", dict(max_hold=10)), ("max_hold 20", dict(max_hold=20)),
            ("max_hold 40", dict(max_hold=40)),
            ("rebal 3", dict(rebal=3)), ("rebal 5", dict(rebal=5)), ("rebal 10", dict(rebal=10)),
        ]),
        ("因子参数", [
            (f"ret3_max={v}", dict(ret3_max=v)) for v in (0.08, 0.10, 0.15, 0.20)
        ] + [
            (f"cap_abs_max={v}", dict(cap_abs_max=float(v))) for v in (3, 4, 5, 8, 10)
        ] + [
            ("liq_min=0", dict(liq_amt_min=0.0)),
            ("liq_min=2000万", dict(liq_amt_min=2e7)),
            ("liq_min=1亿", dict(liq_amt_min=1e8)),
            ("age=120日", dict(list_min_bars=120)),
            ("age=500日", dict(list_min_bars=500)),
        ]),
        # 滑点模型: 额外单边成本 = k / sqrt(20日均额/1亿) bp。用来检验
        # 「放宽/去掉流动性门槛」的收益是不是被未建模的冲击成本吃掉了。
        ("滑点模型", [
            (f"slip {k}bp（流动性门槛不变）", dict(slip_k_bp=float(k))) for k in (0, 3, 6, 10)
        ] + [
            (f"slip 6bp + liq={tag}", dict(slip_k_bp=6.0, liq_amt_min=v))
            for tag, v in (("2000万", 2e7), ("1000万", 1e7), ("0", 0.0))
        ] + [
            ("slip 10bp + liq=2000万", dict(slip_k_bp=10.0, liq_amt_min=2e7)),
            ("slip 10bp + 关liq", dict(slip_k_bp=10.0,
                                     gates=tuple(g for g in A.gates if g != "liq"))),
            ("slip 20bp + 关liq", dict(slip_k_bp=20.0,
                                     gates=tuple(g for g in A.gates if g != "liq"))),
        ]),
    ]


def candidates():
    """组合候选：把单变量扫描里**互相独立、且不带交易性副作用**的改进叠起来。"""
    L = rule.ACTIVE.lights
    def lights(**kw):
        d = dict(L)
        d.update(kw)
        return d

    mom = dict(enter_required={"trend": 1, "momentum": 1})
    sus2 = dict(lights=lights(sustain=("macd", 2)))
    heat2 = dict(lights=lights(heat=("ret5_vr5", 1), sustain=("macd", 2)))
    return [
        ("C1 加动量要求", mom),
        ("C2 C1+sustain权2", {**mom, **sus2}),
        ("C3 C2+heat换量价灯", {**mom, **heat2}),
        ("C4 C3+enter_min=4", {**mom, **heat2, "enter_min": 4.0}),
        ("C5 C3+enter_min=5", {**mom, **heat2, "enter_min": 5.0}),
        ("C6 C3+ret3_max=0.15", {**mom, **heat2, "ret3_max": 0.15}),
        ("C7 C6+cap_abs_max=5", {**mom, **heat2, "ret3_max": 0.15, "cap_abs_max": 5.0}),
        ("C8 C7+关capital灯", {**mom, **heat2, "ret3_max": 0.15, "cap_abs_max": 5.0,
                              "lights": lights(heat=("ret5_vr5", 1), sustain=("macd", 2),
                                               capital=None)}),
        ("C9 C7+liq降到2000万(可交易下限参考)",
         {**mom, **heat2, "ret3_max": 0.15, "cap_abs_max": 5.0, "liq_amt_min": 2e7}),
        ("C10 C7+liq=0(不可交易, 仅作上限参考)",
         {**mom, **heat2, "ret3_max": 0.15, "cap_abs_max": 5.0, "liq_amt_min": 0.0}),
    ]


def fmt(m, key):
    if m is None:
        return "—"
    return {"ann": f"{m['ann'] * 100:+.1f}%", "base": f"{m['base'] * 100:+.1f}%",
            "edge": f"{m['edge']:+.1f}", "pos": f"{m['pos'] * 100:.1f}%",
            "mdd": f"{m['mdd'] * 100:+.1f}%", "mdd_base": f"{m['mdd_base'] * 100:+.1f}%",
            "beat": f"{m['beat']}/{m['n']}",
            "edge_pos": f"{m['edge_pos']}/{m['n']}", "score": f"{m['score']:.3f}"}[key]


def row_of(tag, m):
    return (f"| {tag} | {fmt(m, 'ann')} | {fmt(m, 'edge')} | **{fmt(m, 'score')}** | "
            f"{fmt(m, 'beat')} | {fmt(m, 'pos')} | {fmt(m, 'mdd')} |")


HEAD = ("| 变体 | 策略年化 | 择时边际(bp) | **目标=年化×边际** | 跑赢 | 持仓 | 策略回撤 |",
        "|---|---|---|---|---|---|---|")


def main():
    only = None
    if "--group" in sys.argv:
        only = sys.argv[sys.argv.index("--group") + 1]
    do_oos = "--oos" in sys.argv

    bench = Bench()
    t0 = time.time()
    base_rows, _ = bench.run(rule.ACTIVE)
    base = metrics(base_rows)
    print(f"基线: 年化 {base['ann'] * 100:+.1f}% · 边际 {base['edge']:+.1f}bp · "
          f"目标 {base['score']:.3f} · 跑赢 {base['beat']}/{base['n']}")
    oos_base = metrics(bench.run(rule.ACTIVE.with_(sample_from=OOS_FROM, sample_to=None))[0])
    print(f"基线(样本外 {OOS_FROM} 起): 年化 {oos_base['ann'] * 100:+.1f}% · "
          f"边际 {oos_base['edge']:+.1f}bp · 目标 {oos_base['score']:.3f}")

    plan = sweep_plan()
    if only:
        plan = [(g, vs) for g, vs in plan if only in g]

    results = []          # (组, 标签, 指标)
    for gname, variants in plan:
        for tag, ov in variants:
            cfg = rule.ACTIVE.with_(**ov)
            rows, fails = bench.run(cfg)
            m = metrics(rows)
            if m is None:      # 全部标的失败 = 配置本身有问题（例如引用了未注册的定义）
                print(f"  [{gname}] {tag:<26} ✘ 无有效标的, 已跳过; 首个失败: "
                      f"{fails[0] if fails else '—'}")
                continue
            results.append((gname, tag, m, ov))
            print(f"  [{gname}] {tag:<26} 年化 {m['ann'] * 100:+6.1f}%  "
                  f"边际 {m['edge']:>6.1f}bp  目标 {m['score']:>7.3f}"
                  f"  {'★' if m['score'] > base['score'] else ''}")

    if not results:
        sys.exit(f"没有匹配的变体（--group {only!r}）; 可用组名见 sweep_plan(): "
                 + "、".join(g for g, _ in sweep_plan()))

    # ---------------- 报告 ----------------
    L = ["# 亮灯策略 · 参数搜索（目标 = 策略年化 × 择时边际）", "",
         f"> 目标函数 **年化 × 择时边际**：年化受暴露度约束，边际衡量信号质量（不受暴露影响），"
         f"乘积要求两者同时不差。择时边际 = 持仓日日均收益 − 空仓日日均收益（bp）。",
         f"> 样本 2016-09-01 ~ {bench.end.date()} · 38 只等权 · 成本单边 {rule.ACTIVE.fee:.2%}"
         f" · 变体数 {bench.n_eval} · 耗时 {time.time() - t0:.0f}s", ""]

    L += ["## 0. 基线（c7）", "", *HEAD[:1], HEAD[1], row_of("**c7（当前生效）**", base), "",
          f"> 年化 {fmt(base, 'ann')}（基准 {fmt(base, 'base')}）· 回撤 {fmt(base, 'mdd')}"
          f"（基准 {fmt(base, 'mdd_base')}）"
          f" · 边际为正 {fmt(base, 'edge_pos')} · 平均单笔 {np.mean([r['n_trades'] for r in base_rows]):.0f} 笔/标的", ""]

    for gname, _ in plan:
        g = [r for r in results if r[0] == gname]
        if not g:
            continue
        L += [f"## {gname}", "", *HEAD, row_of("**基线**", base)]
        best = max(g, key=lambda r: r[2]["score"])
        for entry in sorted(g, key=lambda r: -r[2]["score"]):
            tag, m = entry[1], entry[2]
            mark = " ★" if (entry is best and m["score"] > base["score"]) else ""
            L.append(row_of(tag + mark, m))
        L.append("")
        d = best[2]["score"] - base["score"]
        L.append(f"> 组内最优 **{best[1]}**（目标 {fmt(best[2], 'score')}，"
                 f"较基线 {d:+.3f}）")
        L.append("")

    allr = sorted(results, key=lambda r: -r[2]["score"])
    L += ["## 全部变体排名（Top 20）", "", *HEAD]
    for gname, tag, m, _ov in allr[:20]:
        L.append(row_of(f"{gname} / {tag}", m))
    L.append("")

    L += ["## 最差 8 个变体（避雷）", "", *HEAD]
    for gname, tag, m, _ov in allr[-8:]:
        L.append(row_of(f"{gname} / {tag}", m))
    L.append("")

    # ---------------- 组合候选（含样本外） ----------------
    L += ["## 组合候选：全样本 vs 样本外", "",
          "把单变量扫描里**互相独立、且不带交易性副作用**的改进叠起来。"
          f"样本外 = 同一配置在 {OOS_FROM} 起重跑（只换窗口，参数不动）。", "",
          "| 候选 | 全样本年化 | 全样本边际 | **全样本目标** | 跑赢 | 持仓 | "
          "样本外年化 | 样本外边际 | **样本外目标** |",
          "|---|---|---|---|---|---|---|---|---|"]
    cand_res = []
    for tag, ov in candidates():
        cfg = rule.ACTIVE.with_(**ov)
        m1 = metrics(bench.run(cfg)[0])
        m2 = metrics(bench.run(cfg.with_(sample_from=OOS_FROM, sample_to=None))[0])
        cand_res.append((tag, m1, m2))
        L.append(f"| {tag} | {fmt(m1, 'ann')} | {fmt(m1, 'edge')} | **{fmt(m1, 'score')}** | "
                 f"{fmt(m1, 'beat')} | {fmt(m1, 'pos')} | {fmt(m2, 'ann')} | {fmt(m2, 'edge')} | "
                 f"**{fmt(m2, 'score')}** |")
    L.append("")
    L.append(f"| **c7（基线）** | {fmt(base, 'ann')} | {fmt(base, 'edge')} | "
             f"**{fmt(base, 'score')}** | {fmt(base, 'beat')} | {fmt(base, 'pos')} | "
             f"{fmt(oos_base, 'ann')} | {fmt(oos_base, 'edge')} | **{fmt(oos_base, 'score')}** |")
    L.append("")
    both = [t for t, m1, m2 in cand_res
            if m1["score"] > base["score"] and m2["score"] > oos_base["score"]]
    L.append("> **全样本与样本外都优于基线**的候选：" + ("、".join(both) if both else "无"))
    L.append(f"> 样本外基线：目标 {fmt(oos_base, 'score')} · 年化 {fmt(oos_base, 'ann')} · "
             f"边际 {fmt(oos_base, 'edge')}bp")
    L.append("")

    # ---------------- 样本外确认 ----------------
    if do_oos:
        L += [f"## 样本外确认（{OOS_FROM} 起）", "",
              "只把「基线」与「全量扫描目标最高的 3 个变体」放到后半段重跑 —— 参数多时单点最优"
              "极易是噪音，样本外能跑赢才算数。", "",
              *HEAD]
        oos = [(("c7（基线）", {}))]
        oos += [(f"{g} / {t}", ov) for g, t, m, ov in allr[:3]]
        for tag, ov in oos:
            cfg = rule.ACTIVE.with_(**ov).with_(sample_from=OOS_FROM, sample_to=None)
            rows, _ = bench.run(cfg)
            L.append(row_of(tag, metrics(rows)))
        L.append("")

    L += ["", f"> 生成 {pd.Timestamp.now():%Y-%m-%d %H:%M} · 目标函数与口径见文首；"
              f"变体覆盖参数记录在各组行内，可用 backtest.py --set 原样复跑。", ""]

    txt = "\n".join(L)
    open(OUT, "w", encoding="utf-8").write(txt)
    print(f"\n-> {OUT}")
    print(f"变体 {bench.n_eval} 个 · 因子缓存 {len(bench._fcache)} 份 · 耗时 {time.time() - t0:.0f}s")
    top = allr[0]
    print(f"最优: [{top[0]}] {top[1]} · 年化 {fmt(top[2], 'ann')} · 边际 {fmt(top[2], 'edge')}bp"
          f" · 目标 {fmt(top[2], 'score')}（基线 {fmt(base, 'score')}）")


if __name__ == "__main__":
    main()
