# -*- coding: utf-8 -*-
"""灯消融台：回答「某盏灯到底有没有起作用、作用在入场还是离场」。

为什么不能直接「关掉一盏灯」就下结论：灯的权重会改变**分数分布** -> 改变 enter_min
的实际门槛 -> 结果里混杂了「分数水平效应」和「信息效应」两件事。

本台用**置换对照 (permutation control)** 分离两者:
  * 置换: 把该灯的取值序列**随机打乱**（rng.permutation）。取值多重集逐位保留 ->
    总分水平与基线**完全相同**; 唯一被摧毁的是「什么时候亮」这个时间信息。
  * 基线 vs 置换 的差 = 该灯的**时间信息**价值。
  * 若基线落在多个置换种子的分布之内 -> 该灯没有可辨识的信息贡献。

另附 **enter 差异率**: 两配置的入场序列逐日差异占比。
  > 0 说明该灯能影响**买点**; == 0 说明它对买点零影响, 只可能作用于离场。

用法:
  python strategies/lights/_ablate.py                 # 五盏灯全跑, 输出摘要网格 + 逐灯明细
  python strategies/lights/_ablate.py --light heat    # 只跑一盏
输出: strategies/lights/_ablate_report.md
"""
import os
import sys

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_DIR)
for p in (_ROOT, _DIR, os.path.dirname(_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import rule  # noqa: E402
from _tune import Bench, metrics  # noqa: E402

OUT = os.path.join(_DIR, "_ablate_report.md")
N_PERM = 5
LIGHTS_ALL = ("trend", "momentum", "capital", "sustain", "heat")

_REAL = [None]


def _mk_const(v):
    def fn(f):
        return np.full(len(f["c"]), float(v), dtype=float)
    return fn


def _mk_perm(seed):
    """打乱真实灯的取值顺序（保留多重集 = 保留分数水平, 摧毁时间信息）。"""
    def fn(f):
        return np.random.default_rng(seed).permutation(_REAL[0](f))
    return fn


def _mk_px_ma20(f):
    """纯价格代理: 收 > MA20 (=1); 且 ret3 > 3% (=2)。与 MACD 灯同结构, 不含 MACD。"""
    c = f["c"]
    base = c > f["ma20"]
    out = np.zeros(len(c), dtype=float)
    out[base.fillna(False).to_numpy()] = 1
    out[(base & (f["ret3"] > 0.03)).fillna(False).to_numpy()] = 2
    return out


def register(light_key, real_fn):
    """把消融变体注册进 LIGHT_REGISTRY（仅在本次进程内有效）。"""
    _REAL[0] = real_fn
    reg = rule.LIGHT_REGISTRY[light_key]
    for v in (0, 1, 2):
        reg[f"ab_const{v}"] = (_mk_const(v), f"恒为 {v}")
    for s in range(1, N_PERM + 1):
        reg[f"ab_perm{s}"] = (_mk_perm(s), f"置换种子 {s}")
    reg["ab_px_ma20"] = (_mk_px_ma20, "纯价格代理 收>MA20 同结构")
    return reg


def cfg_lights(light_key, name, weight=2, base=None):
    b = base or rule.ACTIVE
    L = dict(b.lights)
    L[light_key] = None if name is None else (name, weight)
    return b.with_(lights=L)


def pooled(rows):
    r = np.array([t["ret"] for x in rows for t in (x.get("trade_log") or [])])
    if not len(r):
        return None
    w, l = r[r > 0], r[r <= 0]
    return dict(n=len(r), win=len(w) / len(r), exp=r.mean())


def enter_diff(bench, cfg_a, cfg_b):
    """两配置入场序列逐日差异占比（全池合计, 只看样本窗口）。"""
    tot = diff = 0
    for meta in rule.POOL:
        code = meta["code"]
        df = bench.frame(code)
        if len(df) < cfg_a.min_bars + 60:
            continue
        begin = int(np.argmax(np.asarray(df.index >= np.datetime64(cfg_a.sample_from))))
        f = bench.factors(code, cfg_a)
        ea = rule.build_signals(f, cfg_a)["enter"][begin:]
        eb = rule.build_signals(f, cfg_b)["enter"][begin:]
        tot += len(ea)
        diff += int((ea != eb).sum())
    return diff / tot if tot else 0.0


def eval_light(light_key, bench):
    real_name, real_w = rule.ACTIVE.lights[light_key]
    register(light_key, rule.LIGHT_REGISTRY[light_key][real_name][0])

    plan = [
        ("**基线（真实定义）**", "real", real_name, real_w),
        ("关掉该灯", "none", None, real_w),
        ("权重降为 1", "w1", real_name, 1),
        ("恒为 0（永不得分）", "const0", "ab_const0", real_w),
        ("恒为 1（永远 1 分）", "const1", "ab_const1", real_w),
        ("恒为 2（永远满分）", "const2", "ab_const2", real_w),
        ("纯价格代理: 收>MA20（与其它灯共线, 见注）", "px1", "ab_px_ma20", real_w),
    ] + [(f"置换对照 #{s}（同分布·无时序）", f"perm{s}", f"ab_perm{s}", real_w)
         for s in range(1, N_PERM + 1)]

    res = []
    for tag, kind, name, w in plan:
        cfg = cfg_lights(light_key, name, w)
        rows, _ = bench.run(cfg)
        m, p = metrics(rows), pooled(rows)
        if m is None or p is None:
            continue
        ed = enter_diff(bench, rule.ACTIVE, cfg)
        res.append((tag, kind, m, p, ed))

    real = next(r for r in res if r[1] == "real")
    off = next((r for r in res if r[1] == "none"), None)
    const0 = next((r for r in res if r[1] == "const0"), None)
    perms = [r for r in res if r[1].startswith("perm")]
    pr = [r[2]["edge"] for r in perms]
    be = sum(1 for x in pr if real[2]["edge"] > x)
    bs = sum(1 for x in perms if real[2]["score"] > x[2]["score"])
    verdict = ("有可辨识的信息贡献" if (be >= N_PERM and bs >= N_PERM)
               else "无法与同分布噪声区分" if (be <= N_PERM // 2 or bs <= N_PERM // 2)
               else "贡献弱/不稳定")

    # 失效对照: 关掉优先, 退化到「恒为 0」(两者在分数上等价, 但后者不破坏引用)。
    # 两个都取不到 = 该灯在 enter_required 里, 一旦失效全池无法交易 -> 硬约束。
    # 失效对照: 关掉优先, 退化到「恒为 0」(两者在分数上等价, 但后者不破坏引用)。
    # 两个都取不到 = 该灯在 enter_required 里, 一旦失效全池无法交易 -> 硬约束。
    # 判据用**目标增量**(年化 x 边际), 不用边际增量 —— 边际对持仓变化不敏感,
    # 会漏掉「边际不变但暴露/年化变化」的贡献 (heat 就是这种)。
    off_e = off[2]["edge"] if off else (const0[2]["edge"] if const0 else None)
    off_s = off[2]["score"] if off else (const0[2]["score"] if const0 else None)
    hard = off_s is None
    delta = None if hard else real[2]["score"] - off_s
    d_ann = None if hard else real[2]["ann"] - (off[2]["ann"] if off else const0[2]["ann"])
    if hard:
        kind_label = "**硬约束**(失效即全池停摆)"
    elif abs(delta) < 0.02:
        kind_label = "无贡献(可删)"
    elif delta >= 0.15:
        kind_label = "**真有效**"
    else:
        kind_label = "有贡献(小)"
    # 失效时的入场差异率: 取「恒为 0」那一行(代表"该灯不再给分"的对照)
    ed_off = const0[4] if const0 else None
    perm_contaminated = hard     # 硬约束灯的置换会抹掉入场机会, 不是纯时序打乱
    return dict(key=light_key, real_name=real_name, real_w=real_w, res=res,
                real=real, off=off, const0=const0, perms=perms, pr=pr,
                be=be, bs=bs, verdict=verdict, off_e=off_e, delta=delta,
                hard=hard, kind_label=kind_label, ed_off=ed_off, off_s=off_s,
                d_ann=d_ann, perm_contaminated=perm_contaminated)


def main():
    if "--light" in sys.argv:
        keys = [sys.argv[sys.argv.index("--light") + 1]]
    else:
        keys = list(LIGHTS_ALL)

    bench = Bench()
    base_m = metrics(bench.run(rule.ACTIVE)[0])
    print(f"池子 {len(rule.POOL)} 只 · 样本 {rule.ACTIVE.sample_from} ~ {bench.end.date()}")
    print(f"基线 c7: 年化 {base_m['ann'] * 100:+.1f}% · 边际 {base_m['edge']:+.1f}bp · "
          f"目标 {base_m['score']:.3f}")
    print(f"enter_min={rule.ACTIVE.enter_min} · exit_max={rule.ACTIVE.exit_max} · "
          f"enter_required={rule.ACTIVE.enter_required}\n")

    out = [eval_light(k, bench) for k in keys]

    print(f"{'灯':<10}{'真实目标':>9}{'失效对照':>9}{'目标增量':>9}{'年化增量':>10}"
          f"{'置换均值':>9}{'胜置换':>8}  性质")
    for o in out:

        def f3(v):
            return f"{v:.3f}" if v is not None else "n/a"

        da = f"{o['d_ann'] * 100:+.2f}pp" if o["d_ann"] is not None else "n/a"
        print(f"{o['key']:<10}{o['real'][2]['score']:>9.3f}{f3(o['off_s']):>9}"
              f"{f3(o['delta']):>9}{da:>10}{np.mean(o['pr']):>9.3f}"
              f"{o['be']:>6}/{N_PERM:<3}{o['kind_label']}")

    L = ["# 灯消融图谱：五盏灯分别有没有起作用、作用在哪一端", "",
         f"> 目标 = 年化 x 择时边际。池子 {len(rule.POOL)} 只 · "
         f"样本 {rule.ACTIVE.sample_from} ~ {bench.end.date()} · 成本单边 {rule.ACTIVE.fee:.2%}",
         f"> 基线 c7: 年化 {base_m['ann'] * 100:+.1f}% · 边际 {base_m['edge']:+.1f}bp · "
         f"目标 {base_m['score']:.3f} · enter_min={rule.ACTIVE.enter_min} · "
         f"exit_max={rule.ACTIVE.exit_max} · enter_required={rule.ACTIVE.enter_required}", "",
         "> **方法**: 直接关灯会同时改变分数分布与实际门槛，把「分数水平效应」和「信息效应」"
         "混在一起。改用**置换对照**: 把该灯取值序列随机打乱，取值多重集逐位保留"
         "（总分水平与基线完全相同），只摧毁「什么时候亮」这一时间信息。", "",
         "> **入场差异率** = 关掉该灯后入场序列逐日变化的占比。为 0 说明该灯对买点零影响，"
         "只可能通过 `exit_max` 作用于离场（当前配置 `enter_min` 无约束力，见 P1）。", "",
         "## 摘要", "",
         "| 灯 | 权重 | 真实目标 | 失效对照目标 | **目标增量** | 年化增量 | 置换均值 | "
         "真实胜置换 | 入场差异率 | 性质 |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for o in out:
        oe = "不可测" if o["off_s"] is None else f"{o['off_s']:.3f}"
        dl = "—" if o["delta"] is None else f"**{o['delta']:+.3f}**"
        da = "—" if o["d_ann"] is None else f"{o['d_ann'] * 100:+.2f}pp"
        ed = "—" if o["ed_off"] is None else f"{o['ed_off'] * 100:.1f}%"
        L.append(f"| `{o['key']}` | {o['real_w']} | {o['real'][2]['score']:.3f} | {oe} | "
                 f"{dl} | {da} | {np.mean(o['pr']):.3f} | {o['be']}/{N_PERM} | {ed} | "
                 f"{o['kind_label']} |")
    L += ["",
          "> 读法与两条必须知道的陷阱：",
          "> 1. **失效对照** = 关掉该灯（取不到时退化到「恒为 0」，两者在分数上等价）。"
          "**目标增量 = 真实目标 − 失效对照目标**，这才是「这盏灯值多少钱」。"
          "不能用边际增量做判据：边际对持仓变化不敏感，会漏掉「边际不变但暴露/年化变化」"
          "的贡献 —— heat 就是这样（关掉它边际仍是 15.8bp，但目标 0.972 -> 0.941、"
          "年化 +6.2% -> +5.9%）。",
          "> 2. **在 `enter_required` 里的灯（trend / momentum）失效即全池停摆**，"
          "所以它们既没有可测的失效对照，**其置换对照也被污染** —— 置换会把 "
          "`>=1` 的天数打散，直接抹掉大量入场机会，那不是「时序信息被摧毁」，"
          "而是「根本不能买」。这两盏灯的置换数字不可解读为信息价值。",
          "> 3. **入场差异率** = 让该灯不再给分后，入场序列逐日变化的占比。为 0 说明"
          "该灯对买点零影响，只可能通过 `exit_max` 作用于离场。", ""]

    for o in out:
        L += [f"## `{o['key']}`（基线定义 `{o['real_name']}`，权重 {o['real_w']}）", "",
              "| 变体 | 年化 | 超额 | 择时边际(bp) | 目标 | 持仓 | 胜率 | 笔数 | 入场差异率 |",
              "|---|---|---|---|---|---|---|---|---|"]
        for tag, kind, m, p, ed in o["res"]:
            if kind.startswith("perm"):
                tag = "　" + tag
            L.append(f"| {tag} | {m['ann'] * 100:+.1f}% | {m['excess'] * 100:+.1f}% | "
                     f"{m['edge']:+.1f} | **{m['score']:.3f}** | {m['pos'] * 100:.1f}% | "
                     f"{p['win'] * 100:.1f}% | {p['n']} | {ed * 100:.1f}% |")
        note = ("⚠ 该灯在 `enter_required` 里，置换会抹掉入场机会，上面的置换对照"
                "**不可解读**为信息价值。"
                if o["perm_contaminated"] else
                f"判定: **{o['verdict']}** —— 真实边际优于 {o['be']}/{N_PERM} 个置换，"
                f"真实目标优于 {o['bs']}/{N_PERM} 个置换。")
        L += ["", f"> {note}", ""]

    open(OUT, "w", encoding="utf-8").write("\n".join(L))
    print(f"\n-> {OUT}")


if __name__ == "__main__":
    main()
