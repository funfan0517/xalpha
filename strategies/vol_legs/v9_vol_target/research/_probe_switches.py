# -*- coding: utf-8 -*-
"""V9 × 三开关九格回测探针（8 种全池统一 + 1 种按腿定稿）。

用户 2026-11-18 要求：把三个机制做成 0/1 开关，看「会不会变好」。
3 个二元开关本身有 **2³ = 8** 种全池统一组合：000 / 100 / 010 / 001 / 110 / 101 / 011 / 111；
随后用户定稿「标普 010、其余 111」的按腿混合配置，作为第 9 格 `9` 一并回测（即当前策略默认）。

口径完全沿用 `v9_vol_target/backtest.py`：全七腿等权（共同样本 2020-07 起）、
文档两腿 2:1、单边 0.10%、空仓 1.40%、信号=真实指数、T+1 成交、t+2 生效。
只改 `vol_target.params()` 里三个开关，其余参数一个不动 —— 所以 000 档与
`backtest/_v9_vol_target_bt.jsonl` 的默认读数一致。

产物（研究类，落 `research/`）：
- `_probe_switches.md` / `_probe_switches.json` / `_probe_switches_eq.png`

模拟结果，非投资建议。
"""
import itertools
import json
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V9 = os.path.dirname(_HERE)
for _p in (_HERE, _V9):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import vol_target as vt  # noqa: E402

OUT_MD = os.path.join(_HERE, "_probe_switches.md")
OUT_JSON = os.path.join(_HERE, "_probe_switches.json")
OUT_PNG = os.path.join(_HERE, "_probe_switches_eq.png")

# 8 种全池统一组合 + 1 种用户定稿的按腿混合配置（标普 010 / 其余 111）
COMBOS = [tuple(x) for x in itertools.product((0, 1), repeat=3)] + [(9,)]
MIXED = (9,)
CODE = lambda c: "".join(map(str, c))  # noqa: E731
LABEL = {
    (9,): "用户定稿：标普010 / 其余111",
    (0, 0, 0): "旧口径：三开关全关",
    (1, 0, 0): "仅 v10 自适应波动层",
    (0, 1, 0): "仅 v7 年线闸门",
    (0, 0, 1): "仅 v8 金叉 50/200",
    (1, 1, 0): "自适应 + 年线闸门",
    (1, 0, 1): "自适应 + 金叉",
    (0, 1, 1): "年线闸门 + 金叉",
    (1, 1, 1): "三开关全开",
}
WINDOWS = [
    ("2020 疫情", "2020-02-19", "2020-08-31"),
    ("2022 加息熊市", "2022-01-03", "2022-12-30"),
    ("2025 至今", "2025-01-02", "2026-11-16"),
]
G1_SHARPE = 0.02   # 夏普至少 +0.02
G1_ANN = -0.005    # 年化至多让渡 0.5pp


def override(c):
    """(9,) = 走 vol_target 的按腿默认；其余 = 全池统一开关。"""
    if c == MIXED:
        return None
    return dict(ADAPTIVE=bool(c[0]), GATE=bool(c[1]), XCROSS=bool(c[2]))


def clean(o):
    """递归把 numpy 标量/NaN 变成可 JSON 序列化的 float/None。"""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        v = float(o)
        return None if not np.isfinite(v) else round(v, 4)
    if isinstance(o, (np.integer, int)):
        return int(o)
    return o


def metrics(nav):
    m = vt.perf(nav)
    me = vt.mdd_sampled(nav)
    return dict(ann=m["ann"], vol=m["vol"], mdd=m["mdd"], sharpe=m["sharpe"],
                mdd_me=me, calmar=vt.calmar(nav), calmar_me=vt.calmar(nav, me),
                cum=m["cum"])


def run_combo(c):
    """-> dict：两档组合 + 七腿 + 逐年 + 窗口 + 分半 + 组合净值（画图用）。"""
    over = override(c)
    navs, states, idx = {}, {}, None
    for leg in vt.POOL:
        nav, st, _dec = vt.build(leg) if over is None else vt.build(leg, **over)
        navs[leg], states[leg] = nav, st
        idx = nav.index if idx is None else idx.intersection(nav.index)
    idx = pd.DatetimeIndex(idx).sort_values()
    navs = {k: v.reindex(idx).ffill() for k, v in navs.items()}
    states = {k: v.reindex(idx).ffill() for k, v in states.items()}
    yrs = (idx[-1] - idx[0]).days / 365.25

    ports = {}
    for mode, weight in (("eq", dict(vt.W)), ("doc", dict(vt.W_DOC))):
        nv, inv = vt.portfolio(navs, states, weight=weight)
        p = metrics(nv)
        trades = sum(vt.n_trades(s) for s in states.values())
        p.update(expo=inv, trades=trades, trades_py=trades / yrs,
                 years=round(yrs, 2), start=idx[0].date().isoformat(),
                 end=idx[-1].date().isoformat())
        ports[mode] = dict(stats=p, nav=nv)

    legs = {}
    for leg in vt.POOL:
        nv = vt.run_leg(states[leg], navs[leg])
        m = metrics(nv)
        m.update(expo=vt.exposure(states[leg]), trades=vt.n_trades(states[leg]),
                 turnover=vt.turnover(states[leg]))
        legs[leg] = m

    eq = ports["eq"]["nav"]
    yearly = {y: round(float(v), 4) for y, v in vt.yearly(eq).items()}
    wins = {}
    for name, a, b in WINDOWS:
        seg = vt.window_nav(eq, a, b)
        wins[name] = round(float(seg.iloc[-1] / seg.iloc[0] - 1.0), 4) if len(seg) > 1 else None

    mid = idx[0] + (idx[-1] - idx[0]) / 2
    halves = {}
    for mode, nv in (("eq", eq), ("doc", ports["doc"]["nav"])):
        halves[mode] = {}
        for name, a, b in (("前半段", idx[0], mid), ("后半段", mid, idx[-1])):
            seg = nv.loc[(nv.index >= a) & (nv.index <= b)]
            if len(seg) > 20:
                mm = vt.perf(seg)
                halves[mode][name] = dict(ann=mm["ann"], mdd=mm["mdd"], sharpe=mm["sharpe"])

    bh = navs[vt.POOL[0]] / navs[vt.POOL[0]].iloc[0]
    return dict(combo=CODE(c), label=LABEL[c], ports=ports, legs=legs,
                yearly=yearly, windows=wins, halves=halves, eq_nav=eq, bh_nav=bh,
                idx=idx)


def pct(x, nd=2):
    return "—" if x is None else f"{x * 100:+.{nd}f}%"


def num(x, nd=2):
    return "—" if x is None else f"{x:.{nd}f}"


def md_table(header, rows):
    return "\n".join(["| " + " | ".join(header) + " |",
                      "|" + "|".join(["---"] * len(header)) + "|"] +
                     ["| " + " | ".join(r) + " |" for r in rows])


def build_md(res, verdict, vdoc):
    out = []
    out += ["# V9 × 三开关九格回测（8 种全池统一 + 1 种按腿定稿）", ""]
    out += ["> 2026-11-18 实测。3 个二元开关本身只有 **2³ = 8** 种全池统一组合（000~111）；"
            "第 9 格 `9` 是用户定稿的按腿混合配置（标普 010 / 其余 111），走 `vol_target` 当前默认。",
            "> 代码接法见 `../vol_target.py` §3.1；当前口径与理由见 `../README.md`；数字由 `python research/_probe_switches.py` 重算。",
            "> 模拟结果，非投资建议。", ""]

    out += ["## 0. 结论速览", "", "**全七腿等权（研究口径）**：" + verdict["summary"], "",
            "**文档两腿 2:1（实际执行口径）**：" + vdoc["summary"], ""]
    out += [md_table(["组合", "开关", "年化", "回撤(日)", "夏普", "Calmar", "调仓/年", "vs 基准夏普"],
                     verdict["quick"]), ""]

    b = res[(0, 0, 0)]
    out += ["## 1. 口径", "",
            "- 000 = 三开关全关（旧口径）；**当前策略默认不是 000**，而是第 9 格 `9`（按腿混合），与 `backtest/_v9_vol_target_bt.jsonl` 的默认档同数值。",
            "- 开关：`A` = v10 自适应波动层（ER20 滞回，TV 15%/10%）；`G` = v7 年线闸门（月末 MA200、2 月确认、破位 ≥3% 免确认，纳指 0/标普 50%）；`X` = v8 金叉（MA50>MA200，死叉日收盘清仓）。",
            "- 组合 A/G/X 为 (自适应/年线/金叉)；组合层共同样本 " +
            b["ports"]["eq"]["stats"]["start"] + " ~ " + b["ports"]["eq"]["stats"]["end"] +
            "（" + num(b["ports"]["eq"]["stats"]["years"]) + " 年）。七腿等权权重 = `vt.W`；两腿档 = `vt.W_DOC`（标普 2:1 纳指）。",
            "- 成本单边 0.10%、空仓 1.40%、信号=真实指数、T+1 成交、新仓 t+2 生效 —— 与 v9 正式回测一字不差，只动三个开关。", ""]

    out += ["## 2. 全七腿等权组合（共同样本）", ""]
    rows = []
    for c in COMBOS:
        s = res[c]["ports"]["eq"]["stats"]
        rows.append([CODE(c), LABEL[c], pct(s["ann"]), pct(s["mdd"]), pct(s["mdd_me"]),
                     num(s["sharpe"]), num(s["calmar"]), pct(s["expo"]),
                     num(s["trades_py"], 1)])
    out += [md_table(["组合", "开关", "年化", "回撤·日", "回撤·月末", "夏普", "Calmar", "平均仓位", "调仓/年"], rows), ""]

    out += ["## 3. 文档两腿 2:1（标普:纳指，重点执行腿）", ""]
    rows = []
    for c in COMBOS:
        s = res[c]["ports"]["doc"]["stats"]
        rows.append([CODE(c), LABEL[c], pct(s["ann"]), pct(s["mdd"]), num(s["sharpe"]),
                     num(s["calmar"]), pct(s["expo"]), num(s["trades_py"], 1)])
    out += [md_table(["组合", "开关", "年化", "回撤·日", "夏普", "Calmar", "平均仓位", "调仓/年"], rows), ""]

    out += ["## 4. 重点单腿（纳指 100 / 标普 500）", ""]
    rows = []
    for c in COMBOS:
        for leg in ("ndx", "spx"):
            s = res[c]["legs"][leg]
            rows.append([CODE(c), LABEL[c], leg, pct(s["ann"]), pct(s["mdd"]),
                         num(s["sharpe"]), num(s["calmar"]), pct(s["expo"]),
                         num(s["trades"])])
    out += [md_table(["组合", "开关", "腿", "年化", "回撤·日", "夏普", "Calmar", "平均仓位", "调仓次数"], rows), ""]

    out += ["## 5. 逐年收益（全七腿等权）", ""]
    years = sorted({y for c in COMBOS for y in res[c]["yearly"]})
    head = ["年份"] + [CODE(c) for c in COMBOS]
    rows = [[y] + [pct(res[c]["yearly"].get(y)) for c in COMBOS] for y in years]
    out += [md_table(head, rows), ""]

    out += ["## 6. 压力窗口（区间累计收益）", ""]
    rows = [[name] + [pct(res[c]["windows"].get(name)) for c in COMBOS]
            for name, _a, _b in WINDOWS]
    out += [md_table(["窗口"] + [CODE(c) for c in COMBOS], rows), ""]

    out += ["## 7. 分半稳健性（全七腿等权）", ""]
    rows = []
    for seg in ("前半段", "后半段"):
        for c in COMBOS:
            v = res[c]["halves"]["eq"].get(seg)
            if v:
                rows.append([seg, CODE(c), pct(v["ann"]), pct(v["mdd"]), num(v["sharpe"])])
    out += [md_table(["样本段", "组合", "年化", "回撤", "夏普"], rows), ""]

    out += ["## 8. 有没有变好（判定口径预注册）", "",
            "**算「改进」必须同时满足**：夏普 ≥ 基准 + 0.02、年化 ≥ 基准 − 0.5pp、回撤不深于基准；" +
            "再要求分半两段的夏普都不低于基准对应段（容差 0.03）。单看回撤变小不算改进。", ""]
    out += [md_table(["组合", "Δ夏普", "Δ年化", "Δ回撤", "前半段Δ夏普", "后半段Δ夏普", "判定"],
                     verdict["gates"]), ""]
    out += verdict["notes"]

    out += ["## 8.1 文档两腿 2:1 的判定（实际执行口径）", "",
            "同一套闸门放在**真正执行的标普:纳指 2:1** 上，结论明显弱于七腿等权 —— 七腿的改善主要由 A 股/港股腿在 2022 的深跌贡献，不能外推到重点两腿。", ""]
    out += [md_table(["组合", "Δ夏普", "Δ年化", "Δ回撤", "前半段Δ夏普", "后半段Δ夏普", "判定"],
                     vdoc["gates"]), ""]
    out += vdoc["notes"]

    out += ["## 9. 边界与复现", "",
            "- 八个组合是**同一份样本、同一套参数**上比较，多重检验下「最好那格」有样本内偏差；本探针只做机制对照，不拿来当实盘调参依据。",
            "- `GATE`/`XCROSS` 一旦打开，v9 就从「只看风险预算」变成「引入方向判断」，与原变种定位不同；`ADAPTIVE` 仍只调风险预算。",
            "- 自适应层移植的是 v10 的 ER 滞回机制，低挡 TV 沿用 v10 的 10%（v9 高挡 15%，比值 0.67 而非 v10 的 0.50）；未引入 v10 的 DEAD_DAYS/FLOOR（v9 的死区由 DRIFT 管）。",
            "- 年线闸门按 v7 口径是 **MA200**，不是 v5 的 MA250；「年线」中文歧义已按 v7 源码落定。",
            "- 复现：`cd g:\\xalpha && python strategies\\vol_legs\\v9_vol_target\\research\\_probe_switches.py`。", ""]
    return "\n".join(out)


def judge(res, mode):
    """按预注册闸门判定某档组合层（eq / doc）是否真的改进。"""
    base = res[(0, 0, 0)]["ports"][mode]["stats"]
    base_h = res[(0, 0, 0)]["halves"][mode]
    quick, gates, passed = [], [], []
    for c in COMBOS:
        s = res[c]["ports"][mode]["stats"]
        dsh = s["sharpe"] - base["sharpe"]
        dann = s["ann"] - base["ann"]
        dmdd = s["mdd"] - base["mdd"]
        quick.append([CODE(c), LABEL[c], pct(s["ann"]), pct(s["mdd"]), num(s["sharpe"]),
                      num(s["calmar"]), num(s["trades_py"], 1), f"{dsh:+.2f}"])
        if c == (0, 0, 0):
            gates.append(["000（基准）", "—", "—", "—", "—", "—", "基准"])
            continue
        h1 = res[c]["halves"][mode].get("前半段", {}).get("sharpe", np.nan) - base_h["前半段"]["sharpe"]
        h2 = res[c]["halves"][mode].get("后半段", {}).get("sharpe", np.nan) - base_h["后半段"]["sharpe"]
        ok = bool(dsh >= G1_SHARPE and dann >= G1_ANN and dmdd >= 0.0
                  and h1 >= -0.03 and h2 >= -0.03)
        if ok:
            passed.append(CODE(c))
        gates.append([CODE(c), f"{dsh:+.2f}", f"{dann * 100:+.2f}pp",
                      f"{dmdd * 100:+.2f}pp", f"{h1:+.2f}", f"{h2:+.2f}",
                      "改进" if ok else "不采纳"])
    ranked = sorted(COMBOS, key=lambda c: res[c]["ports"][mode]["stats"]["sharpe"], reverse=True)
    best = ranked[0]
    bs = res[best]["ports"][mode]["stats"]
    summary = (
        f"夏普排序最好 **{CODE(best)}（{LABEL[best]}）**：年化 {pct(bs['ann'])}、"
        f"回撤 {pct(bs['mdd'])}、夏普 {num(bs['sharpe'])}、Calmar {num(bs['calmar'])}；"
        f"基准 000 为年化 {pct(base['ann'])}、回撤 {pct(base['mdd'])}、夏普 {num(base['sharpe'])}、"
        f"Calmar {num(base['calmar'])}。按四道闸门**采纳 {len(passed)} 档**："
        f"{', '.join(passed) if passed else '无'}。"
    )
    notes = ["", "- 夏普排序（高→低）：" + " > ".join(
        f"{CODE(c)}({num(res[c]['ports'][mode]['stats']['sharpe'])})" for c in ranked) + "。",
        "- 采纳档：" + (", ".join(passed) if passed else "无一个组合同时通过全部闸门") + "。", ""]
    return dict(base=base, quick=quick, gates=gates, passed=passed, ranked=ranked,
                best=best, summary=summary, notes=notes)


def main():
    res = {}
    for c in COMBOS:
        print(f"[{CODE(c)}] {LABEL[c]} ...", flush=True)
        res[c] = run_combo(c)

    vd_eq = judge(res, "eq")
    vd_doc = judge(res, "doc")
    summary = vd_eq["summary"]

    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write(build_md(res, vd_eq, vd_doc))

    payload = dict(as_of="2026-11-18",
                   note="8 种全池统一组合（2^3）+ 第 9 格按腿定稿配置（标普010/其余111）",
                   verdict=dict(eq=dict(best=CODE(vd_eq["best"]), adopted=vd_eq["passed"],
                                        summary=vd_eq["summary"]),
                                doc=dict(best=CODE(vd_doc["best"]), adopted=vd_doc["passed"],
                                         summary=vd_doc["summary"])),
                   combos=[dict(code=CODE(c), label=LABEL[c], flags=list(c),
                                eq=res[c]["ports"]["eq"]["stats"],
                                doc=res[c]["ports"]["doc"]["stats"],
                                legs=res[c]["legs"], yearly=res[c]["yearly"],
                                windows=res[c]["windows"], halves=res[c]["halves"])
                           for c in COMBOS])
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(clean(payload), fh, ensure_ascii=False, indent=2)

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        plt.figure(figsize=(11, 6))
        for c in COMBOS:
            nv = res[c]["eq_nav"]
            plt.plot(nv.index, nv.to_numpy(dtype=float), lw=1.5, label=CODE(c))
        bh = res[(0, 0, 0)]["bh_nav"]
        plt.plot(bh.index, bh.to_numpy(dtype=float), lw=1.0, ls="--", color="0.55", label="B&H(a500)")
        plt.title("V9 x 3 switches - 7-leg equal-weight portfolio")
        plt.xlabel("date")
        plt.ylabel("nav")
        plt.legend(fontsize=8, ncol=2)
        plt.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig(OUT_PNG, dpi=130)
        plt.close()
    except Exception as exc:  # noqa: BLE001
        print("PNG skipped:", exc)

    print("\n" + summary)
    print("wrote:", OUT_MD, OUT_JSON, OUT_PNG)


if __name__ == "__main__":
    main()
