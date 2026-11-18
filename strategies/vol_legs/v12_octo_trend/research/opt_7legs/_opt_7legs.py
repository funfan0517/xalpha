# -*- coding: utf-8 -*-
"""V12.1 扩展到 7 个标的 · 逐腿优化方向研究（**不做组合**）。

背景：v12 的执行范围只有沪深300（`rule.CORE_LEGS`），其余 6 腿一直在跑但在报告里被标成
「诊断」—— 理由是规格 §7.4 自己写的：`MA20/60/250` 的时间尺度可移植，但 `z_th` / `rvol`
阈值是在沪深300 上标的，直接搬到美股/黄金属于跨样本搬运。
v12.1 只对 **hs300**（强回调 20%）与 **kc50**（三条 + σ覆盖）做了覆盖。

本研究回答两个问题：
1. **把 v12.1 的机械口径铺到 7 腿，每腿的短板在哪**（判定层 / 映射层 / 数据层）？
2. **每条腿的优化方向该往哪走**，哪些腿现在就不该上线。

方法（逐腿，单旋钮、可解释）：
- 先出**该腿自己的八态后验**（fwd1 / fwd5 / fwd20 的均值、t 值、占比）—— 这是规格
  `CAP` 表（按 fwd20 定的）在该腿上成不成立的原始证据；
- 再跑一组候选项，**同一份价格、同一个成本模型**（`engine.run`：T 日收盘定仓、T+1 起承担收益、
  单边 5bp、空仓计货基 1.40%），同一 `start`（第一条出状态的 bar）；
- 每个候选项都配 **曝光匹配基准**（恒定仓位 = 该腿 S1 的平均许可）——
  八态这一层必须打得过「同样的仓位一直拿着」，否则它只是低仓位伪装的低风险；
- **CAP 重估类的候选必须做分折样本外**：C1/C2 是拿同一段样本的 fwd5 反推映射，
  全样本夏普天然偏高，故另给「前半估→后半测」「后半估→前半测」两个数。

价格与量能口径（**全部真指数，不用代理**；2026-11-13 修订）：
| 腿 | 价格 | 量能 | 样本 |
|---|---|---|---|
| hs300 | 指数 `SH000300` | 同库指数量能 | 2006-02 起（20.0y 预热后） |
| kc50 | 指数 `SH000688` | 同库指数量能 | 2021-02 起（5.8y） |
| a500 | 指数 `SH000510` 收盘 | **同指数成交量** | 2006-02 起（21.9y 原始） |
| ndx | 指数 `.NDX` | **同指数成交量** | 2006-01 起 |
| spx | 指数 `.INX` | **同指数成交量** | 2006-01 起 |
| hkt | 指数 `HKHSTECH` | **同指数成交量** | 2020-07 起（6.0y） |
| gold | 场内 ETF `518880` 前复权 | ETF 自身量（**现货无量**） | 2014-01 起 |

指数库在 `data/_global_index_klines.json`（`strategies/vol_legs/data.py::refresh_global_index`；
xueqiu/sina 的 `get_daily` 本来就返回 volume，此前只是**没存**）。黄金是唯一例外：现货黄金是
OTC、**没有成交量**这个数据，能取的只有 ETF 量或 COMEX 期货量（东财 `101.GC00Y`）。

同一份研究还保留 ETF 口径做对照（`leg_frame(leg, caliber="etf")`），用来量「换口径值多少」。

产物：`_opt_7legs.md`（本目录）。**不改 `rule.py`** —— 上线与否的改动建议写在报告末尾。

用法：
    python strategies/vol_legs/v12_octo_trend/research/opt_7legs/_opt_7legs.py
"""
import io
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
_V12 = os.path.dirname(os.path.dirname(_HERE))            # v12_octo_trend
if _V12 not in sys.path:
    sys.path.insert(0, _V12)

import engine   # noqa: E402
import rule     # noqa: E402
import data as vdata  # noqa: E402

OUT_MD = os.path.join(_HERE, "_opt_7legs.md")
TD = rule.TRADING_DAYS
STATES = rule.STATE_ORDER
WARN_VOL = {"gold"}      # 只有黄金没有「指数成交量」这个数据（现货 OTC 无量）

# 逐腿裁决（结论见报告末尾；写在代码里是为了让表与正文不脱钩）
VERDICT = {
    "a500": "口径成立（+0.38）→ **可上线，用 S1**；CAP 重估不成立",
    "hs300": "已是 v12.1 → **不动**",
    "kc50": "已是 v12.1 → **不动**（重估更差）",
    "ndx": "夏普持平但回撤 −30.9%→−14.8% → **可上线（压回撤）**；CAP 不动",
    "spx": "八态层 +0.16、回撤减半 → **可上线**；CAP 重排样本外不稳，不动",
    "hkt": "5.1y、层零增量、腿本身 −2.6%/年 → **降级诊断**，沿用 v3",
    "gold": "方向层零贡献 → **只留 σ覆盖 15%**（0.61 → 0.75）",
}


# ---------------------------------------------------------------- 1. 逐腿行情
def _idx_df(code):
    lib = json.load(io.open(os.path.join(rule._ROOT, "data", "_cn_index_klines.json"),
                            encoding="utf-8"))
    node = lib[code]
    idx = pd.to_datetime(node["dates"])
    return pd.DataFrame({"close": np.array(node["close"], dtype=float),
                         "volume": np.array(node["volume"], dtype=float)}, index=idx).sort_index()


# 带成交量的指数库（`data/_global_index_klines.json`，维护方 `strategies/vol_legs/data.py::GIDX`）。
# 这里只放代码映射、直接读 JSON —— 不 import vol_legs/data.py，否则它的模块级 `import rule`
# 会被 v12 的 `rule` 抢走（两个 rule.py 同名）。
_GIDX = {"a500": ("SH000510", "中证A500 指数"),
         "ndx": (".NDX", "纳斯达克100 指数"),
         "spx": (".INX", "标普500 指数"),
         "hkt": ("HKHSTECH", "恒生科技指数")}


def _gidx_df(leg):
    with io.open(os.path.join(rule._ROOT, "data", "_global_index_klines.json"),
                 encoding="utf-8") as f:
        node = json.load(f)[_GIDX[leg][0]]
    return pd.DataFrame({"close": np.array(node["close"], dtype=float),
                         "volume": np.array(node["volume"], dtype=float)},
                        index=pd.to_datetime(node["dates"])).sort_index()


def leg_frame(leg, caliber="index"):
    """-> (df[close, volume], 价格口径说明, 量能口径说明)。

    `caliber="index"`（默认）：**真指数收盘 + 真指数成交量**，无代理。
    `caliber="etf"`：场内 ETF 前复权 + ETF 自身量（旧口径，用来量「换口径值多少」）。
    黄金是唯一例外：现货没有成交量这个数据（OTC），只能用 ETF 量或期货量。
    """
    if leg == "gold":
        df = vdata.frame("gold")[["close", "volume"]]
        return (df, "场内 ETF 518880 前复权",
                "ETF 自身量（**现货黄金无成交量**，见 §0 ③）")
    if caliber == "etf":
        df = vdata.frame(leg)[["close", "volume"]]
        return (df, f"场内 ETF {rule.LEGS[leg][0]} 前复权",
                "ETF 自身量（做市/申赎主导）")
    if leg == "hs300":
        return _idx_df("SH000300"), "指数 `SH000300` 收盘", "同库指数量能"
    if leg == "kc50":
        return _idx_df("SH000688"), "指数 `SH000688` 收盘", "同库指数量能"
    df = _gidx_df(leg)
    return df, f"{_GIDX[leg][1]}（`{_GIDX[leg][0]}`）收盘", "同库指数量能"


# ---------------------------------------------------------------- 2. 后验
def posterior(df, cx, horizons=(1, 5, 20)):
    """逐态后验：占比 / 各 horizon 的均值、t 值、胜率。

    `df` 已被调用方截到统计区间时，`shift(-h)` 越界自然变 NaN —— 分折估 CAP 靠这一点保证
    不把下一折的信息带进来。
    """
    px = df["close"]
    st = cx["state"]
    m = st.notna()
    fwd = {h: px.shift(-h) / px - 1.0 for h in horizons}
    rows = []
    for s in STATES:
        k = m & (st == s)
        if not k.sum():
            continue
        r = {"state": s, "n": int(k.sum()), "share": float(k.sum() / m.sum())}
        for h in horizons:
            v = fwd[h][k & fwd[h].notna()]
            r[f"f{h}"] = float(v.mean()) if len(v) else np.nan
            if len(v) > 2 and v.std() > 0:
                r[f"t{h}"] = float(v.mean() / (v.std() / np.sqrt(len(v))))
            else:
                r[f"t{h}"] = np.nan
        rows.append(r)
    return pd.DataFrame(rows)


def post_range(df, cx, lo, hi):
    return posterior(df.loc[lo:hi], {"state": cx["state"].loc[lo:hi]})


# ---------------------------------------------------------------- 3. CAP 重估（两种口径）
def cap_min_fix(post):
    """C1「最小改动」：从规格 `CAP` 出发，只改**该腿 fwd5 与规格依据反号**的格。

    规则**先定好、不看回测**：fwd5 < 0 → 0.2；fwd5 > 0 且 t5 > 2 且规格给 0.5 → 1.0；其余不动。
    """
    cap = dict(rule.CAP)
    for _, r in post.iterrows():
        if not np.isfinite(r["f5"]):
            continue
        if r["f5"] < 0 and cap[r["state"]] > 0.2:
            cap[r["state"]] = 0.2
        elif r["f5"] > 0 and np.isfinite(r["t5"]) and r["t5"] > 2.0 and cap[r["state"]] == 0.5:
            cap[r["state"]] = 1.0
    return cap


def cap_rank(post):
    """C2「全重排」：按该腿 fwd5 排序，前 3 名 100% / 中 3 名 50% / 末 2 名 20%。

    不假装是「最优映射」，只是把「用本腿数据重估」做到极致，看它比最小改动多赚什么。
    """
    p = post.dropna(subset=["f5"]).sort_values("f5", ascending=False)
    cap = dict(rule.CAP)
    for i, (_, r) in enumerate(p.iterrows()):
        cap[r["state"]] = 1.0 if i < 3 else (0.5 if i < 6 else 0.2)
    return cap


def _diff_txt(cap):
    d = [f"{k} {rule.CAP[k]:.0%}→{v:.0%}" for k, v in cap.items()
         if abs(v - rule.CAP[k]) > 1e-9]
    return "、".join(d) if d else "**无**（与规格一致）"


# ---------------------------------------------------------------- 4. 回测
_ID_CACHE = {}


def states_of(leg, df):
    """按 (腿, 该 df 的形状/起点) 缓存 —— 同一腿的「指数口径」与「ETF 口径」要分别算。"""
    key = (leg, len(df), str(df.index[0].date()))
    if key not in _ID_CACHE:
        p = rule.params(leg)
        ind = engine.indicators(df, p)
        _ID_CACHE[key] = (ind, engine.classify(ind, p))
    return _ID_CACHE[key]


def run_w(leg, df, cap_map, vt=None, up_confirm=None, cx=None):
    """-> (w_full, nav_full, start)。`nav_full` 从 df 首日开始，调用方按需切片。"""
    if cx is None:
        _, cx = states_of(leg, df)
    w = engine.positions(cx["state"], up_confirm=up_confirm, cap_map=cap_map)
    if vt:
        w = engine.vol_target(df, w, vt)
    first = cx["state"].notna()
    start = df.index[int(np.argmax(first.to_numpy()))]
    return w, engine.run(df, w), start


def stats(nav, w):
    p = engine.perf(nav)
    yrs = max(p["days"] / TD, 1e-9)
    tno = float(w.diff().fillna(w).abs().sum())
    return dict(ann=p["ann"], vol=p["vol"], mdd=p["mdd"], sharpe=p["sharpe"],
                expo=float(w.mean()), acts=len(engine.actions(w)),
                apy=len(engine.actions(w)) / yrs, days=p["days"],
                fee=tno * rule.FEE_SIDE / yrs * 100)


def half_sharpe(nav):
    n = len(nav)
    return (engine.perf(nav.iloc[: n // 2])["sharpe"],
            engine.perf(nav.iloc[n // 2 - 1:])["sharpe"])


def eval_slice(nav, w, lo, hi):
    """在 [lo, hi] 上评分（仓位按同一区间对齐）。"""
    nav2 = nav.loc[lo:hi]
    w2 = w.reindex(nav2.index).ffill().fillna(0.0)
    return stats(nav2, w2)


def s1_and_increment(leg, df):
    """-> (S1 统计, 八态增量 = S1 夏普 − 曝光匹配夏普)。口径对照与主表共用这一份。"""
    _, cx = states_of(leg, df)
    w, nav, start = run_w(leg, df, rule.cap_map(leg), vt=rule.vol_target_of(leg), cx=cx)
    s = stats(nav.loc[start:], w.loc[start:])
    expo = float(w.loc[start:].mean())
    wb = pd.Series(expo, index=w.index)
    sb = stats(engine.run(df, wb).loc[start:], wb.loc[start:])
    return s, s["sharpe"] - sb["sharpe"], sb


def row(name, s):
    return (f"| {name} | {s['ann'] * 100:+.2f}% | {s['vol'] * 100:.1f}% | {s['mdd'] * 100:+.2f}% | "
            f"**{s['sharpe']:.2f}** | {s['expo']:.0%} | {s['acts']} | {s['apy']:.1f} | "
            f"{s['fee']:.2f}pp |")


HEAD = ("| 候选项 | 年化 | 波动 | 最大回撤 | 夏普 | 平均许可 | 调仓 | 次/年 | 摩擦 |\n"
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|")


def main():
    L = ["# V12.1 扩展到 7 个标的 · 逐腿优化方向", "",
         "> 由 `research/opt_7legs/_opt_7legs.py` 生成。**模拟结果，非投资建议。**",
         "> 同一个八态判定器（`engine.indicators/classify`）、同一个成本模型"
         "（`engine.run`：T 日收盘定仓、T+1 起承担收益、单边 5bp、空仓计货基 1.40%）、"
         "每腿同一个 `start`（第一条出状态的 bar，预热 270 根）。**不含组合层。**",
         "",
         "## 0. 数据可得性（这是本研究的第一结论）",
         "",
         "| 腿 | 价格口径 | 量能口径 | 数据起点 | 可回测样本 | 够不够下结论 |",
         "|---|---|---|---|---|---|",
         ]

    frames = {}
    for leg in rule.LEG_ORDER:
        df, pdesc, vdesc = leg_frame(leg)
        frames[leg] = df
        bars = len(df) - rule.WARMUP
        enough = ("够" if bars >= 1200 else
                  ("勉强（≈5 年下限）" if bars >= 900 else "**不够**"))
        L.append(f"| {rule.NAMES[leg]} | {pdesc} | {vdesc} | {df.index[0].date()} | "
                 f"{max(bars, 0)} 根 | {enough} |")
    L += ["",
          "> **价格与量能都用真指数，不用代理**（2026-11-13 修订）。六条腿是「该市场指数收盘 +"
          "该指数自己的成交量」；数据来自 `data/_global_index_klines.json`"
          "（`strategies/vol_legs/data.py::refresh_global_index`，xueqiu/sina 日线自带 volume）。",
          "",
          "> 只剩一处**数据本身就不存在**的：**③ 黄金没有成交量**。现货黄金（伦敦金 / XAU）是 OTC，"
          "没有「成交量」这个字段；能拿到的只有 ① 场内 `518880` 的量（实物申赎 + 场内交易，"
          "v18 文档归「谨慎可试」而非宽基做市噪声）② COMEX 期货量（东财 `101.GC00Y` 有量，"
          "但那是期货、不是现货）。本表黄金腿仍用 518880 —— 它是**唯一可得**，不是「拿别的市场代理」。",
          "",
          "> 另：`hkt` 预热后只有 5.1 年（恒生科技指数 2020-07 起，比 ETF 的 2021-05 早 10 个月）、"
          "`kc50` 5.5 年 —— 这两条腿的结论都只能算方向。",
          "",
          "> **C1 的门槛比 v12.1 当年用的更严**：C1 只在「fwd5 为负」时才降档，而 v12.1 的沪深300"
          "「强回调 20%」用的是**相对**证据（次日期望比其它态差 −3.5pp）。所以下面沪深300 的 C1"
          "会显示「与规格一致」却不等于 v12.1 的 `CAP_OPT` —— 两者不矛盾，是证据口径不同。",
          ""]

    summaries = {}
    for leg in rule.LEG_ORDER:
        df = frames[leg]
        ind, cx = states_of(leg, df)
        post = posterior(df, cx)
        cap_c1, cap_c2 = cap_min_fix(post), cap_rank(post)

        cands = []
        w0, n0, start = run_w(leg, df, rule.CAP, cx=cx)
        cands.append(("S0 规格 CAP", w0.loc[start:], n0.loc[start:]))
        w1, n1, _ = run_w(leg, df, rule.cap_map(leg), vt=rule.vol_target_of(leg), cx=cx)
        cands.append(("S1 v12.1 现状", w1.loc[start:], n1.loc[start:]))
        wc1, nc1, _ = run_w(leg, df, cap_c1, cx=cx)
        cands.append(("C1 最小改动（fwd5 反号修正）·全样本", wc1.loc[start:], nc1.loc[start:]))
        wc2, nc2, _ = run_w(leg, df, cap_c2, cx=cx)
        cands.append(("C2 fwd5 全重排（3/3/2）·全样本", wc2.loc[start:], nc2.loc[start:]))
        for tv in (0.15, 0.20, 0.25):
            wv, nv, _ = run_w(leg, df, rule.cap_map(leg), vt=tv, cx=cx)
            cands.append((f"C3 S1 + σ覆盖 {tv:.0%}", wv.loc[start:], nv.loc[start:]))
        for uc in (1, 3):
            wu, nu, _ = run_w(leg, df, rule.cap_map(leg), up_confirm=uc, cx=cx)
            cands.append((f"C4 S1 + UP_CONFIRM={uc}", wu.loc[start:], nu.loc[start:]))
        expo1 = float(w1.loc[start:].mean())
        wb = pd.Series(expo1, index=w1.index)
        cands.append((f"B 曝光匹配（恒定 {expo1:.0%}）", wb.loc[start:],
                      engine.run(df, wb).loc[start:]))

        L += [f"## {rule.NAMES[leg]}（{rule.LEGS[leg][0]}）", "",
              f"样本 **{start.date()} ~ {df.index[-1].date()}**"
              f"（{len(n1.loc[start:]) / TD:.1f} 年）· 价格 {leg_frame(leg)[1]} · "
              f"量能 {leg_frame(leg)[2]}", "",
              "### 八态后验（该腿自己样本）", "",
              "| 态 | 占比 | fwd1 | t | fwd5 | t | fwd20 | t | 规格 CAP | fwd5 判读 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
        for _, r in post.iterrows():
            f5, t5 = r["f5"], r["t5"]
            verdict = ("—" if not np.isfinite(f5) else
                       "**负期望**" if f5 < 0 else
                       "显著正" if (np.isfinite(t5) and t5 > 2) else "打平")
            L.append(f"| {r['state']} | {r['share']:.0%} | {r['f1'] * 100:+.2f}% | "
                     f"{r['t1']:+.1f} | {f5 * 100:+.2f}% | {t5:+.1f} | "
                     f"{r['f20'] * 100:+.2f}% | {r['t20']:+.1f} | {rule.CAP[r['state']]:.0%} | "
                     f"{verdict} |")
        L += ["", f"- **C1 映射**（只动反号格）：{_diff_txt(cap_c1)}",
              f"- **C2 映射**（fwd5 排序）：{_diff_txt(cap_c2)}", ""]

        L += ["### 候选回测", "", HEAD]
        res = {}
        for nm, ww, nn in cands:
            s = stats(nn, ww)
            res[nm] = (s, nn, ww)
            L.append(row(nm, s))

        # 分折（对 S1 / 最优候选）
        L += ["", "| 候选项 | 前半夏普 | 后半夏普 |", "|---|---:|---:|"]
        for nm, (s, nn, _) in res.items():
            a, b = half_sharpe(nn)
            L.append(f"| {nm} | {a:+.2f} | {b:+.2f} |")

        # CAP 重估的样本外：一折估映射、另一折评分（各折的 fwd5 不跨界）
        valid = df.loc[start:].index
        mid = valid[len(valid) // 2]
        oos = {}
        for tag, cf in (("C1", cap_min_fix), ("C2", cap_rank)):
            for lab, glo, ghi, elo, ehi in (
                    ("前→后", df.index[0], mid, mid, df.index[-1]),
                    ("后→前", mid, df.index[-1], start, mid)):
                capx = cf(post_range(df, cx, glo, ghi))
                wx, nx, _ = run_w(leg, df, capx, cx=cx)
                oos[(tag, lab)] = eval_slice(nx, wx, elo, ehi)
        L += ["", "**CAP 重估的样本外**（用一折的 fwd5 估映射，另一折上评分）：", "",
              "| 口径 | 前→后 | 后→前 | 全样本（含调参偏差） |", "|---|---:|---:|---:|"]
        for tag, nm_in in (("C1", "C1 最小改动（fwd5 反号修正）·全样本"),
                           ("C2", "C2 fwd5 全重排（3/3/2）·全样本")):
            L.append(f"| {tag} | {oos[(tag, '前→后')]['sharpe']:.2f} "
                     f"（回撤 {oos[(tag, '前→后')]['mdd'] * 100:+.1f}%） | "
                     f"{oos[(tag, '后→前')]['sharpe']:.2f} "
                     f"（回撤 {oos[(tag, '后→前')]['mdd'] * 100:+.1f}%） | "
                     f"{res[nm_in][0]['sharpe']:.2f} |")

        s1 = res["S1 v12.1 现状"][0]
        sb = res[f"B 曝光匹配（恒定 {expo1:.0%}）"][0]
        best = max((k for k in res if k.startswith(("C1", "C2", "C3", "C4"))),
                   key=lambda k: res[k][0]["sharpe"])
        L += ["",
              f"> **S1 夏普 {s1['sharpe']:.2f}** vs 曝光匹配 {sb['sharpe']:.2f}"
              f"（八态层增量 **{s1['sharpe'] - sb['sharpe']:+.2f}**）；全样本最优候选 "
              f"`{best}`（{res[best][0]['sharpe']:.2f} / 回撤 {res[best][0]['mdd'] * 100:+.2f}%）。"
              + (" ⚠ 该腿量能**不是指数成交量**（现货黄金无成交量），强弱回调/反弹四格打折。"
                 if leg in WARN_VOL else ""),
              ""]
        summaries[leg] = dict(s1=s1, sb=sb, best=best, bs=res[best][0], oos=oos,
                              years=len(n1.loc[start:]) / TD)

    # ---------------- 口径对照
    L += ["## 口径对照：换成真指数（价格 + 量能）值多少", "",
          "同一支 S1 在两套口径上重算。**ETF 口径** = 场内 ETF 前复权 + ETF 自身成交量"
          "（旧口径；`research/probe_volume` 已证明宽基 ETF 的量会把「弱回调 − 强回调」做成负值）；"
          "**指数口径** = 该市场指数收盘 + 该指数成交量。两套口径各用各自 `start`，"
          "所以「Δ夏普」里同时含**价格口径**（汇率/溢价/分红）与**量能口径**两项差。", "",
          "| 腿 | ETF 口径 S1 | 指数口径 S1 | Δ夏普 | ETF 口径八态增量 | 指数口径八态增量 |",
          "|---|---:|---:|---:|---:|---:|"]
    for leg in rule.LEG_ORDER:
        got = []
        for cal in ("etf", "index"):
            try:
                s, inc, _ = s1_and_increment(leg, leg_frame(leg, cal)[0])
                got.append((s["sharpe"], inc))
            except Exception:  # noqa: BLE001
                got.append((np.nan, np.nan))
        (se, ie), (si, ii) = got
        L.append(f"| {rule.NAMES[leg]} | {se:.2f} | {si:.2f} | **{si - se:+.2f}** | "
                 f"{ie:+.2f} | {ii:+.2f} |")
    L += ["",
          "> ⚠ 中证A500 的 ETF 口径只有 231 根（563360 于 2024-10 上市，扣 270 根预热），"
          "那一行的 −0.28 与 Δ+0.99 **是样本长度造成的，不可比**；其余六腿的 Δ 才有意义。",
          "",
          "> 读法：Δ夏普 > 0 说明「真指数」比「ETF 口径」更好。ETF 口径同时混三件事 ——"
          "**价格**（QDII 的汇率与溢价、A股的折溢价）、**量能**（做市与申赎套利主导）、"
          "**样本长度**（ETF 上市晚 3~7 年）。所以这不是「量能口径单独值多少」的干净估计；"
          "干净的对照在 `research/probe_volume`（同价格、只换量能分母），"
          "那里的结论是：宽基 ETF 自身量**不能用**（符号反转），指数量（含上证大盘）**可以用**。",
          "",
          ]

    # ---------------- 汇总
    L += ["## 汇总：逐腿裁决", "",
          "| 腿 | 样本 | S1 夏普 | 曝光匹配 | 八态增量 | 最优全样本候选 | C1 样本外(前/后) | "
          "C2 样本外(前/后) | 裁决 |",
          "|---|---|---:|---:|---:|---|---|---|---|"]
    for leg in rule.LEG_ORDER:
        m = summaries[leg]
        d = m["s1"]["sharpe"] - m["sb"]["sharpe"]
        d = 0.0 if abs(d) < 0.005 else d
        c1 = (f"{m['oos'][('C1', '前→后')]['sharpe']:.2f} / "
              f"{m['oos'][('C1', '后→前')]['sharpe']:.2f}")
        c2 = (f"{m['oos'][('C2', '前→后')]['sharpe']:.2f} / "
              f"{m['oos'][('C2', '后→前')]['sharpe']:.2f}")
        oos_min = min(m["oos"][(t, f)]["sharpe"] for t in ("C1", "C2")
                      for f in ("前→后", "后→前"))
        if leg in VERDICT:
            verdict = VERDICT[leg]
        elif m["years"] < 5.0:
            verdict = "样本偏短，只作方向参考"
        elif d <= 0.05:
            verdict = "**八态层≈曝光匹配** → 该腿应换层"
        elif oos_min >= m["s1"]["sharpe"]:
            verdict = "CAP 重估两折都赢 → 可覆盖"
        else:
            verdict = "维持 S1（重估只在单折赢）"
        L.append(f"| {rule.NAMES[leg]} | {m['years']:.1f}y | {m['s1']['sharpe']:.2f} | "
                 f"{m['sb']['sharpe']:.2f} | {d:+.2f} | {m['best']} "
                 f"({m['bs']['sharpe']:.2f}) | {c1} | {c2} | {verdict} |")
    L += ["",
          "> 裁决口径（先定好再看数）：① **八态增量** = S1 夏普 − 曝光匹配夏普（恒定仓位一直拿着），"
          "这是「八态层到底有没有信息」的唯一不含调参偏差的检验；② **CAP 重估必须是两折样本外都赢**"
          "（C1/C2 的全样本夏普天然偏高，含调参偏差）；③ 量能不是指数成交量的腿只给方向、不给裁决。",
          "",
          "## 结论：七条腿的优化方向不一样，别用同一把尺子",
          "",
          "### 1. 八态层的价值分两类：A股**提夏普**、美股**压回撤**",
          "",
          "| 腿 | 八态增量（夏普） | 回撤：恒定仓位 → S1 | 读法 |",
          "|---|---:|---|---|",
          "| 科创50 | **+0.66** | −21.5% → −16.6% | 5.8 年：把 0.03 的恒定仓位打成 0.70 |",
          "| 沪深300 | **+0.44** | −38.7% → −31.8% | 20 年：0.30 → 0.73 |",
          "| 中证A500 | **+0.38** | −42.5% → −30.2% | 20 年：0.33 → 0.71 |",
          "| 标普500 | **+0.16** | −31.7% → **−14.9%** | 20.8 年：0.44 → 0.60，**回撤砍半** |",
          "| 纳指100 | 0.00 | −30.9% → **−14.8%** | 20.7 年：夏普持平，**同样收益换一半回撤** |",
          "| 恒生科技 | −0.01 | −25.8% → −24.0% | 5.1 年：层没帮上也没拖累（腿本身 −2.6%/年） |",
          "| 黄金 | −0.01 | −16.6% → −26.6% | 11.7 年：与「恒定 48%」打平，**且回撤更差** |",
          "",
          "**美股两腿不是「不成立」，此前那个负结论是口径造成的。** 上一版用场内 ETF（人民币、"
          "含汇率与溢价、2014 起 11 年）做价格、用 ETF 自身量做 `rvol`，得到 ndx −0.10 / spx −0.25；"
          "换成 `NDX`/`INX` 指数收盘 + 指数成交量、样本拉到 20.7/20.8 年后变成 0.00 / **+0.16**，"
          "而且两腿的最大回撤都从 −31% 砍到 −15%。这条本身就是方法论教训："
          "**跨市场比结论之前先把口径统一**（价格、量能、窗口三件事）。",
          "",
          "⚠ 但 A股三腿的增量**在衰减**：hs300 前半夏普 1.03 / 后半 **0.33**，a500 1.00 / **0.33**，"
          "kc50 只够切两折（5.8y）。后半折仍显著高于同折的曝光匹配（0.12~0.15），"
          "但「八态层 vs 一直拿着」的差距从 0.6 收窄到 0.2 —— 这与 `beat_best` 里沪深300"
          "找不到稳定跑赢 V2 的候选是同一件事。**A股三腿的边际在变薄，不是消失了**。",
          "",
          "美股两腿的机制与 A股不同：长牛里八态层平均只给 52% 的许可，"
          "所以它**放弃了收益、换来回撤**（spx +5.62%/−31.7% → +6.12%/−14.9%；"
          "ndx +8.71%/−30.9% → +7.81%/−14.8%）。这与家族已有结论同向："
          "**方向层在美股上是「风控」不是「alpha」**（`v8_golden_cross` 金叉门在波动层之上是负优化）、"
          "**要收益该换波动率目标**（`v9_vol_target` 纳指 0.89 / 标普 0.85）。",
          "",
          "### 2. 「按本腿 fwd5 重估 CAP」这条路**不可复制**（负面结论）",
          "",
          "v12.1 的做法（拿本腿的次日/5 日期望重估 `CAP`）在沪深300 上成立，但铺到其余六腿后：",
          "",
          "| 腿 | C1 样本外（前/后） | C2 样本外（前/后） | S1 | 判断 |",
          "|---|---|---|---:|---|",
          "| 中证A500 | 0.10 / 0.66 | 0.01 / 0.88 | 0.71 | 两折相差 0.6~0.9，**不成立** |",
          "| 沪深300 | 0.32 / 0.93 | 0.08 / 0.66 | 0.73 | 前折崩到 0.08，**不成立** |",
          "| 科创50 | 0.26 / −0.04 | 0.59 / −0.44 | 0.70 | 后折为负，**不成立** |",
          "| 纳指100 | 0.59 / 0.53 | 0.53 / 0.33 | 0.63 | 两折都赢不了 S1，**不成立** |",
          "| 标普500 | 0.85 / 0.48 | 0.88 / 0.16 | 0.60 | 前折赢、后折崩（0.16），**不成立** |",
          "| 恒生科技 | 0.08 / −0.49 | −0.39 / −0.27 | −0.21 | 全负，**不成立** |",
          "| 黄金 | 0.70 / 0.44 | 0.47 / 0.43 | 0.61 | 一折赢一折输，**不成立** |",
          "",
          "> 原因：`CAP` 是 8 个格子 × 少量样本，一折（10 年、300~500 个态样本）估出来的排序，"
          "在另一折上复现不了。**「用同一套数据估映射、再用同一套数据评分」的漂亮数字都是这样来的**"
          " —— 本表 C1/C2 的全样本夏普（0.69~0.74）与样本外（0.01~0.93）的差距，"
          "就是调参偏差的量级。**七条腿没有一条能靠重估 `CAP` 稳定赢过 S1。**",
          "",
          "### 3. 但规格 `CAP` 在美股上是**系统性偏保守**的（这是「方向」不是「结论」）",
          "",
          "换了真指数后，美股两腿的态期望几乎全是正的：标普的 `强回调` fwd5 **+0.31%（t=4.2）**、"
          "`慢涨` +0.10%（t=2.6）、`弱反弹` +0.22%（t=2.2）都显著为正，合起来占 **69%** 的日子，"
          "而规格分别给 50% / 50% / 20%；纳指更极端，`慢跌`（10% 的日子）fwd5 **+0.44%（t=2.2）**、"
          "`弱反弹` **+0.47%（t=3.9）** 也压到 20%。这说明**规格的映射是「A股震荡市」的产物**，"
          "在长牛里它把仓位系统性压低 —— 这正是「回撤砍半、夏普不涨」的来源。",
          "",
          "把这几格抬上去的候选 C1（`慢涨`/`强回调`/`急跌`→100%）确实把夏普抬到 0.69/0.70，"
          "但**样本外只在单折成立**（标普 0.85/0.48、纳指 0.59/0.53），且平均许可是 80%~81%"
          "（等于换了风险预算，不是同一把尺子的改进）。所以结论是：**方向明确（美股该更进取），"
          "但幅度不可由 20 年单一样本定** —— 要么接受「这一层只管压回撤」，要么走 `v9` 的波动率目标。",
          "",
          "### 4. 黄金：删掉方向层、只留波动层",
          "",
          "八态层在黄金上与恒定仓位打平（0.61 vs 0.62），但 `σ覆盖 15%` 把 S1 推到 "
          "**0.75 / 回撤 −18.90%（vs −26.60%）**：后折夏普 0.76 → **1.03**、前折基本不动"
          "（0.43 → 0.42），即**改善全部来自高波动的后半段**。"
          "这与 `v6_gold` 的「B 控回撤」完全同向（黄金上纯波动目标 15% 最好、叠方向闸门反而更差）。"
          "⚠ 但 σ 覆盖的最优值落在**网格边界**（15% 最好、20% 0.71、25% 0.67，没有内部峰），"
          "等价于「整体少拿一点」，不是找到了甜点 —— 与 v6-B 的 `VOL_TARGET=15%` 是同一件事的两种实现。",
          "",
          "### 5. A股三腿：优化空间不在 CAP / σ，在执行成本",
          "",
          "- **CAP 重估无效**（见 §2）：hs300 已是 v12.1 口径（0.73），kc50 的三条旋钮 + σ覆盖 25% "
          "就是它自己的局部最优（重估掉到 0.46/0.32）。",
          "- **σ覆盖在 A股上是负的**：hs300 全网格 0.70/0.73/0.73（＝S1），kc50 0.55/0.65/0.70 "
          "（15%/20%/25%，25% 即现状）—— A股的波动层压不出超额，因为八态层本身已经在压仓位。",
          "- **加迟滞换低成本不行**：`UP_CONFIRM=3` 把换手砍一半（kc50 49→16 次/年），"
          "但夏普 0.70→0.40、a500 0.71→0.63、hs300 0.73→0.67。**省下的摩擦（0.13pp）买不回夏普**。",
          "- 所以这三条腿真正剩下的是**执行层**：31~49 次/年、0.53~0.70pp 摩擦。"
          "碎调来自 σ 覆盖而不是 CAP 换档：kc50 的调仓 S0 161 次 → S1 271 次（+110 次全是 σ 微调），"
          "hs300 546 → 677（+131 次）。方向应是「只在跨档时下单 / σ 覆盖加死区」，"
          "但 `portfolio_ops` 已测过 `deadband` 在**组合层**无效（`CAP` 档距 20~50pp 太大，"
          "能削的只有 σ 那一层）—— 这块本研究**没有动**，是下一步唯一还剩的执行层工作。",
          "",
          "### 6. 恒生科技：这条腿本身是负的，层救不了",
          "",
          "换真指数（`HKHSTECH`）后样本 5.1 年、S1 **−2.60%/年、夏普 −0.21**，"
          "八态增量 −0.01（与恒定 37% 打平）。它的八态后验几乎全负（`急涨` fwd20 −2.68%、"
          "`弱回调` −3.29%、`慢涨` −2.06%），六条候选里最好的只有 −0.08。"
          "也就是说：**这段窗口里恒生科技是下跌腿，八态层能做的只是少亏**（回撤 −25.8% → −24.0%），"
          "救不回来。它在 `portfolio_ops` 里已经是「唯一在拖累组合的腿（+0.21）」，"
          "本表给出的方向是**降级为诊断腿或换 `v3_macd_boll`**（5.0 年、0.39），"
          "而不是在 v12 里调参数。",
          "",
          "## 若要落地，`rule.py` 该怎么改（本研究未改）",
          "",
          "```python",
          "# ① 执行范围：从 1 条腿扩到 5 条（口径都已是真指数，不再需要「代理」争论）",
          "CORE_LEGS = [\"a500\", \"hs300\", \"kc50\", \"ndx\", \"spx\"]",
          "#   依据（八态增量）：kc50 +0.66 / hs300 +0.44 / a500 +0.38 / spx +0.16 / ndx 0.00",
          "#   —— 美股两腿的贡献是「回撤 −31% → −15%」而不是夏普，别按夏普排序看它们。",
          "",
          "# ② 黄金：加 σ 覆盖，不加 CAP 覆盖（方向层在黄金上是零贡献，甚至更差）",
          "VOL_TARGET = {\"kc50\": 0.25, \"gold\": 0.15}   # 黄金 0.61 → 0.75，回撤 −26.6% → −18.9%",
          "",
          "# ③ CAP_OPT 不加任何一条腿 —— 七条腿的 CAP 重估没有一条通过两折样本外（§2）",
          "CAP_OPT = {\"hs300\": ..., \"kc50\": ...}       # 保持现状，不动",
          "",
          "# ④ 恒生科技：不扩进执行范围（5.1 年、8 态后验全负、层零增量）",
          "```",
          "",
          "> 落地前必须承认的两件事：① 美股腿的**执行标的是 QDII ETF（513100/513500）**，"
          "指数口径回测不含溢价、额度与汇率 —— 从指数口径切到实盘会有 0.5~1pp/年的缺口，"
          "这不是规则问题，是通道问题；② `CORE_LEGS` 只影响报告里的「执行/诊断」标签，不影响回测。",
          "",
          "## 数据侧改动（已落地）",
          "",
          "- 新增 `data/_global_index_klines.json`：`SH000510` / `.NDX` / `.INX` / `HKHSTECH` 的"
          "**收盘 + 成交量**，2005 起（`HKHSTECH` 2020-07 起），维护函数 "
          "`strategies/vol_legs/data.py::refresh_global_index`。",
          "- 事实核对：`xalpha.get_daily` 对上述代码**本来就返回 volume** —— "
          "此前 `_us_index_klines.json` 只存了 close，是「没存」而不是「取不到」。"
          "所以「美股没有量能」这个前提是错的，v12 的美股腿不必用 ETF 量。",
          "- 黄金仍是唯一拿不到指数量能的腿：现货黄金是 OTC、**没有成交量**。"
          "要彻底去掉 ETF 量，只能换 COMEX 期货量（东财 `101.GC00Y`，有量）—— "
          "那是「用期货代理现货」，与「用 ETF 代理」是一个性质的取舍，未做。",
          "",
          "> 机械规则输出，非投资建议。",
          "",
          ]

    with io.open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))
    print("报告:", OUT_MD)
    for leg in rule.LEG_ORDER:
        m = summaries[leg]
        print(f"  {rule.NAMES[leg]:8s} {m['years']:5.1f}y  S1 {m['s1']['sharpe']:+.2f}  "
              f"基准 {m['sb']['sharpe']:+.2f}  C1oos {m['oos'][('C1', '前→后')]['sharpe']:+.2f}"
              f"/{m['oos'][('C1', '后→前')]['sharpe']:+.2f}  "
              f"C2oos {m['oos'][('C2', '前→后')]['sharpe']:+.2f}/"
              f"{m['oos'][('C2', '后→前')]['sharpe']:+.2f}")


if __name__ == "__main__":
    main()
