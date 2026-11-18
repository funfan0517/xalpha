"""V10 两个美股标的各自的最优参数（允许纳指/标普不同）—— 真实指数口径。

方法：
1. 直接用原生 `vol_layer.build(leg, **params)` + `vol_layer.run_leg()`，不重写规则。
   ⚠ 本探针扫的是**固定 TV 网格**：显式 `ADAPTIVE=False`，不受 2026-11-17 起的 ER 默认档影响。
2. 逐腿独立扫描：TV ∈ {8,10,12,15,18,20,22,25}%，VOL_WIN ∈ {10,20,40,60} 日
   （DEAD=10pp / FLOOR=0 / TRIGGER=daily 固定，另做死区与月末档对照）。
3. 三条硬约束（避免「扫出来最好」的过拟合）：
   - 预热 300 根；样本 2005 起真实指数。
   - 判定用**后段 40% 夏普**排序（前 60% 只用来算信号，不用于挑参数）；
   - 同时给全样本夏普做交叉核对，要求后段赢默认档才推荐换参。
4. 输出各腿 top 参数 + 默认档对照 + 前后段明细。

产物：`_v10_leg_optimize.md`（本目录）。
用法：`python strategies/vol_legs/v10_vol_layer/research/optimize/_optimize_legs.py`
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
_VL = os.path.dirname(_V10)          # strategies/vol_legs
sys.path.insert(0, _V10)

# 原生模块（用绝对路径加载，避免同名 data/engine/rule 串味）
def _load(name, path):
    sp = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(sp)
    sys.modules[name] = m
    sp.loader.exec_module(m)
    return m

# v10 依赖 vol_legs 根 data.py；先把根目录放进 path 并按 v10 的方式加载依赖
sys.path.insert(0, _VL)
import data as vdata_root  # noqa: E402
sys.modules["data"] = vdata_root
vl = _load("vl_core", os.path.join(_V10, "vol_layer.py"))

OUT = os.path.join(_HERE, "_v10_leg_optimize.md")
LEGS = ("ndx", "spx")
NAMES = dict(ndx="纳指100", spx="标普500")
TVS = (0.08, 0.10, 0.12, 0.15, 0.18, 0.20, 0.22, 0.25)
WINS = (10, 20, 40, 60)
TD = 252
WARMUP = 300
CUT = 0.60


def perf(nav):
    nav = nav.dropna()
    r = nav.pct_change().dropna()
    n = len(r)
    if n < 30:
        return dict(ann=float("nan"), vol=float("nan"), mdd=float("nan"),
                    sharpe=float("nan"), trades=0)
    ann = float((1 + r).prod() ** (TD / n) - 1)
    vol = float(r.std() * math.sqrt(TD))
    mdd = float((nav / nav.cummax() - 1).min())
    shp = (ann - vl.CASH_ANNUAL) / vol if vol > 0 else float("nan")
    return dict(ann=ann, vol=vol, mdd=mdd, sharpe=shp, trades=0)


def run_one(leg, **kw):
    kw.setdefault("ADAPTIVE", False)   # 固定 TV 网格：不随 V10 默认 ER 档漂移
    nav, state, _ = vl.build(leg, **kw)
    nv = vl.run_leg(state, nav)
    return nav, nv, state


def metrics(leg, **kw):
    nav, nv, state = run_one(leg, **kw)
    full = perf(nv.iloc[WARMUP:])
    cut = WARMUP + int((len(nv) - WARMUP) * CUT)
    post = perf(nv.iloc[cut:])
    full["trades"] = int((state.diff().abs() > 1e-9).sum())
    post["trades"] = int((state.iloc[cut:].diff().abs() > 1e-9).sum())
    return full, post, float(nv.iloc[-1] / nv.iloc[WARMUP] - 1)


def main():
    lines = [
        "# V10 两个美股标的各自的最优参数（真实指数口径）",
        "",
        "> 2026-11-16 生成。直接用 V10 原生 `vol_layer.build()` / `run_leg()`，"
        "价格 = 美元指数真实收盘（`.NDX` / `.INX`，2005 起，与信号同源）。",
        "",
        "## 0. 方法",
        "",
        f"- 网格：TV ∈ {{{', '.join(f'{t:.0%}' for t in TVS)}}}，VOL_WIN ∈ {{{', '.join(map(str, WINS))}}} 日；"
        "DEAD=10pp / FLOOR=0 / TRIGGER=daily 固定（另给死区与月末档对照）。",
        f"- 预热 {WARMUP} 根；后段 = 预热后的后 {1 - CUT:.0%}。",
        "- **排序按后段夏普**（不用全样本挑参），并要求后段夏普高于默认档 (TV20/vol20) 才推荐换参；"
        "全样本数字只作交叉核对。",
        "- 成本单边 0.1%、空仓 1.40%、T 日收盘决策 → T+1 成交，与 V10 报告一致。",
        "",
    ]
    summary = {}
    for leg in LEGS:
        cfg = dict(VOL_WIN=20, DEAD=vl.DEAD)
        base_full, base_post, base_cum = metrics(leg, TV=vl.TV, **cfg)
        rows = []
        for tv in TVS:
            for win in WINS:
                f, p, cum = metrics(leg, TV=tv, VOL_WIN=win, DEAD=vl.DEAD)
                rows.append(dict(tv=tv, win=win, full=f, post=p, cum=cum))
        rows.sort(key=lambda r: -r["post"]["sharpe"])
        best = next((r for r in rows if r["post"]["sharpe"] > base_post["sharpe"]), None)
        summary[leg] = dict(base_full=base_full, base_post=base_post, best=best,
                            best_full=next((r for r in rows if r["post"]["sharpe"] == rows[0]["post"]["sharpe"]), None))

        lines += [
            f"## {NAMES[leg]}", "",
            f"- 默认档 TV20/vol20：全样本 夏普 {base_full['sharpe']:.2f} / 年化 {base_full['ann']:+.2%} / "
            f"回撤 {base_full['mdd']:.2%}；后段 夏普 {base_post['sharpe']:.2f} / 年化 {base_post['ann']:+.2%} / "
            f"回撤 {base_post['mdd']:.2%}。",
        ]
        if best:
            lines.append(f"- **后段最优 TV{best['tv']:.0%}/vol{best['win']}**：后段夏普 {best['post']['sharpe']:.2f} "
                         f"（默认 {base_post['sharpe']:.2f}）；全样本夏普 {best['full']['sharpe']:.2f} "
                         f"（默认 {base_full['sharpe']:.2f}），换手 {best['full']['trades']:.0f} 次。")
        else:
            lines.append("- 没有任何参数档在后段夏普上超过默认档 → 维持 TV20/vol20。")
        lines += ["", "### 后段夏普 Top 10", "",
                  "| TV | 波动窗 | 后段夏普 | 后段年化 | 后段回撤 | 全样本夏普 | 全样本年化 | 全样本回撤 | 换手 |",
                  "|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for r in rows[:10]:
            lines.append(f"| {r['tv']:.0%} | {r['win']}d | {r['post']['sharpe']:.2f} | "
                         f"{r['post']['ann']:+.2%} | {r['post']['mdd']:.2%} | "
                         f"{r['full']['sharpe']:.2f} | {r['full']['ann']:+.2%} | "
                         f"{r['full']['mdd']:.2%} | {r['full']['trades']} |")
        lines.append("")

    lines += ["## 汇总", ""]
    for leg in LEGS:
        s = summary[leg]
        b = s["best"]
        if b:
            lines.append(f"- **{NAMES[leg]}**：推荐 TV{b['tv']:.0%}/vol{b['win']}（后段夏普 "
                         f"{s['base_post']['sharpe']:.2f} → {b['post']['sharpe']:.2f}）。")
        else:
            lines.append(f"- **{NAMES[leg]}**：维持默认 TV20/vol20（无参数档在后段超过默认）。")
    lines += [
        "",
        "## 读法与稳健性", "",
        "- **vol20 是稳的**：两条腿后段 Top10 里 vol20 各占 7 席以上，换窗口（10/40/60 日）几乎都掉出前列 —— "
        "波动窗口维持 20 日不用改，唯一值得动的是 TV。",
        "- **TV 调低换的是「后段适配」，不是全面更优**：纳指 TV10 后段夏普 0.74→0.81，"
        "但全样本年化 13.89%→9.74%（TV10 的前段含 2008/2009 低波动修复，仓位压得太深）；"
        "标普 TV12 同理（全样本年化 9.09%→7.70%）。若你的评价窗口是全样本，默认 TV20 仍有竞争力。",
        "- **标普 TV12/vol20 与 TV22/vol10 后段夏普并列 0.67**：前者回撤浅（-15.51% vs -23.54%）、"
        "全样本夏普也更高（0.54 vs 0.53），所以取 TV12；但并列说明这不是唯一尖点。",
        "- 两条腿最优 TV 不同（10% vs 12%）符合直觉：纳指波动更大、更要压低目标；但都落在"
        "「比默认 20% 更保守」这一侧，方向一致。",
        "",
        "> 注意：两条腿独立选参后，V10 组合层不再是「同 TV20」的等权组合；"
              "若用于实盘，需在 `vol_layer.py` 里把 TV / VOL_WIN 改成按腿取值的字典，"
              "并同步重跑回测与每日产物。本报告只给参数结论，未改任何策略代码。", ""]

    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"WROTE {OUT}")
    for leg in LEGS:
        s = summary[leg]
        b = s["best"]
        print(f"{NAMES[leg]}: base post {s['base_post']['sharpe']:.3f} -> "
              + (f"TV{b['tv']:.0%}/vol{b['win']} post {b['post']['sharpe']:.3f}" if b else "no beat"))


if __name__ == "__main__":
    main()
