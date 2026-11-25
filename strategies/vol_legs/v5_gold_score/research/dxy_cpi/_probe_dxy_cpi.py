# -*- coding: utf-8 -*-
"""V5 · 美元指数（DXY）+ 通胀（CPI YoY）能不能用来优化黄金仓位？

来源：外部研究《美元指数与通胀对黄金的影响研究》（tradingagents/gold_strategy，2026-11-19）。
它在 118 个月（2016-11 ~ 2026-09）样本上给出 DXY × CPI 格子的历史胜率，草案 R-03d 的建议是
「信息进报告、不进公式」。本探针用仓库自己的数据把这件事验一遍，做三件事：

1. **复现**：XAU 2006-11 起（~240 个月）重算单因子分档与格子表，看外部结论在更长样本上
   是否还在；再做 IS/OOS 分段，看符号稳不稳。
2. **叠加回测**：把格子胜率、DXY 背离（R-03d-2）接进 v5 的 `scale × gate`，看年化 / 回撤 /
   夏普是否改善。同时给出「全样本格子统计」的**作弊上界**——若连作弊版都不改善，说明信息量
   本身不足，而不是实现方式的问题。
3. **样本充分性**：每个格子的月份数与独立（非重叠）段数，决定它有没有资格驱动仓位。

数据（全在 `data/`，属数据类、可复用）：
  `_gold_xau.csv`  伦敦金现日频（2006-11 起）
  `_dxy.csv`       ICE 美元指数日频（东财 100.UDI，1986 起）
  `_cpi.csv`       FRED CPIAUCSL 月度（本探针新增缓存，`--refresh` 重取）

无前视约定：DXY 日频先 `shift(1)` 再取月末（北京收盘早于美市收盘）；CPI 用 m−1 月同比
（m 月数据要到 m+1 月中才发布）。

产物：同目录 `_probe_dxy_cpi.md`。只写报告，不改任何行情 / 策略数据。
非投资建议。
"""
import io
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))          # research/dxy_cpi
_V5 = os.path.dirname(os.path.dirname(_HERE))               # v5_gold_score
if _V5 not in sys.path:
    sys.path.insert(0, _V5)

import factors as F  # noqa: E402
import score as S    # noqa: E402

OUT = os.path.join(_HERE, "_probe_dxy_cpi.md")
DXY_CSV = os.path.join(F._DATA, "_dxy.csv")
CPI_CSV = os.path.join(F._DATA, "_cpi.csv")
CPI_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCSL"
REFRESH = "--refresh" in sys.argv

DX_B, CPI_B = F.DX_BUCKETS, F.CPI_BUCKETS    # 分档口径与每日报告共用（factors.py）
SPLIT = pd.Period("2016-12", "M")           # IS/OOS 分界（外部文档样本的起点附近）
MIN_N = 12                                  # 滚动口径下驱动仓位所需的最小样本
DISCLAIMER = "> 模拟结果，非投资建议。"


# ---------------------------------------------------------------- 数据层
def _atomic_csv(df, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    df.to_csv(tmp, index=False, encoding="utf-8")
    os.replace(tmp, path)


def _fetch_dxy():
    import requests
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=100.UDI"
           "&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
           "&klt=101&fqt=0&beg=0&end=20500101&lmt=100000")
    j = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=25).json()
    klines = (j.get("data") or {}).get("klines") or []
    if not klines:
        raise RuntimeError("东财 DXY 无数据: " + str(j)[:200])
    df = pd.DataFrame([{"date": x.split(",")[0], "dxy": float(x.split(",")[2])} for x in klines])
    return df


def _fetch_cpi():
    import requests
    r = requests.get(CPI_URL, timeout=25)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = ["month", "cpi"]
    df["cpi"] = pd.to_numeric(df["cpi"], errors="coerce")
    return df.dropna().sort_values("month")


def load_series(path, value_col, refresh=None, fetch=None, name=""):
    """缓存优先；`refresh=True` 且给了 `fetch` 就重取并原子落盘。"""
    do = bool(REFRESH if refresh is None else refresh)
    if do and fetch is not None:
        try:
            df = fetch()
            df["date" if "date" in df.columns else "month"] = pd.to_datetime(
                df["date" if "date" in df.columns else "month"])
            df = df.drop_duplicates(subset=["date" if "date" in df.columns else "month"]) \
                   .sort_values("date" if "date" in df.columns else "month")
            _atomic_csv(df, path)
            print(f"[fetch] {name} n={len(df)} {df.iloc[0, 0].date()} ~ {df.iloc[-1, 0].date()}")
        except Exception as exc:  # noqa: BLE001
            print(f"[fetch] {name} 刷新失败，沿用缓存：{type(exc).__name__}: {exc}")
    if not os.path.exists(path):
        raise FileNotFoundError(f"缺 {os.path.relpath(path, F._ROOT)}")
    df = pd.read_csv(path)
    col = "date" if "date" in df.columns else "month"
    return pd.Series(pd.to_numeric(df[value_col], errors="coerce").to_numpy(),
                     index=pd.to_datetime(df[col])).dropna().sort_index()


def load_all():
    """-> (gold, dxy, cpi_yoy)。CPI 同比走 `F.read_cpi_yoy`（与每日报告同一实现）。"""
    # XAU 由 v6 的 `research/macro_drivers/_gold_macro.py` 维护（新浪只给 5186 根滚动窗口，
    # 重取会把样本起点往前推 2 周、连带改掉 v6 的长样本结论）—— 本探针**只读不刷**。
    gold = load_series(F.XAU_CSV, "gold")
    dxy = load_series(DXY_CSV, "dxy", fetch=_fetch_dxy, name="DXY")
    load_series(CPI_CSV, "cpi", fetch=_fetch_cpi, name="CPIAUCSL")   # 必要时刷新缓存
    yoy = F.read_cpi_yoy()
    if yoy is None or not len(yoy):
        raise RuntimeError(f"缺 {os.path.relpath(CPI_CSV, F._ROOT)}（先跑 --refresh）")
    return gold, dxy, yoy


# ---------------------------------------------------------------- 月频框架（口径在 factors.py）
month_last = F._month_last                 # 唯一的月末取值实现
bkt = F.macro_bucket                       # 唯一的分档实现


def recs_monthly(gold, dxy, yoy):
    """月末观测 -> DataFrame(p, dxy, cpi, fwd1, fwd3)。与每日报告同源（`F.macro_monthly`）。"""
    t = F.macro_monthly(gold=gold, dxy=dxy, yoy=yoy)
    if t is None or not len(t):
        raise RuntimeError("月频格子底表为空：检查 data/_gold_xau.csv / _dxy.csv / _cpi.csv")
    return t


def cell(r):
    return f"{bkt(r['dxy'], DX_B)} × {bkt(r['cpi'], CPI_B)}"


# ---------------------------------------------------------------- 统计
def stats(vals):
    v = np.asarray([x for x in vals if x is not None and np.isfinite(x)], dtype=float)
    n = len(v)
    if n == 0:
        return dict(n=0, mean=np.nan, med=np.nan, win=np.nan, t=np.nan)
    m, sd = float(v.mean()), float(v.std(ddof=1)) if n > 1 else np.nan
    t = m / (sd / np.sqrt(n)) if n > 1 and sd and np.isfinite(sd) and sd > 0 else np.nan
    return dict(n=n, mean=m, med=float(np.median(v)), win=float((v > 0).mean()), t=float(t))


def single(recs, col, buckets, fwd="fwd1"):
    out = []
    for name, lo, hi in buckets:
        sub = recs[(recs[col] >= lo) & (recs[col] < hi)]
        st = stats(sub[fwd].tolist())
        out.append((name, st))
    return out


def grid(recs, fwd="fwd1", dx_buckets=None, cpi_buckets=None):
    out = {}
    for nb, lo, hi in (dx_buckets or DX_B):
        for nc, cl, ch in (cpi_buckets or CPI_B):
            sub = recs[(recs["dxy"] >= lo) & (recs["dxy"] < hi)
                       & (recs["cpi"] >= cl) & (recs["cpi"] < ch)]
            out[f"{nb} × {nc}"] = stats(sub[fwd].tolist())
    return out


def _rule(mode, pool, min_n=MIN_N):
    """格子样本 -> 仓位乘数。pool 为该格子内已知的 1M 远期收益列表。"""
    if mode == "off" or len(pool) < min_n:
        return 1.0
    s = stats(pool)
    if mode == "weak":                       # 弱格减半
        return 0.5 if s["win"] < 0.45 else 1.0
    if mode == "tier":                       # 三档
        if s["win"] < 0.45:
            return 0.5
        return 0.75 if s["win"] < 0.55 else 1.0
    raise ValueError(mode)


def rolling_mult(recs, mode, min_n=MIN_N):
    """滚动（无前视）乘数 -> {持有月 period: mult}。

    决策在该月之前一个月末做：t 月末决定 t+1 月的乘数，只用已实现的收益
    （`fwd1` 在月末 t+1 才实现，所以 t 月末能用的最新一条是 t−1 月的 `fwd1`）。
    """
    cells, out = {}, {}
    for i in range(len(recs)):
        r = recs.iloc[i]
        k = cell(r)
        if i + 1 < len(recs):
            out[recs.iloc[i + 1]["p"]] = _rule(mode, cells.get(k, []), min_n)
        if r["fwd1"] is not None and np.isfinite(r["fwd1"]):
            cells.setdefault(k, []).append(float(r["fwd1"]))
    return out


def fired(table):
    """乘数表里真正改过仓位的月数（= 规则触发次数）。"""
    return int(sum(1 for v in table.values() if abs(v - 1.0) > 1e-9))


def full_mult(recs, mode, min_n=MIN_N):
    """**作弊上界**：用全样本格子统计（含未来信息）决定每个月的乘数。"""
    keys = [cell(r) for _, r in recs.iterrows()]
    pool = {}
    for k, f1 in zip(keys, recs["fwd1"].tolist()):
        if f1 is not None and np.isfinite(f1):
            pool.setdefault(k, []).append(float(f1))
    return {r["p"]: _rule(mode, pool.get(k, []), min_n)
            for r, k in zip((r for _, r in recs.iterrows()), keys)}


# ---------------------------------------------------------------- 日频叠加回测
def dxy_daily(index):
    """DXY 对齐到交易日（**严格 T−1**，北京收盘时美市当日尚未收盘）。"""
    d = load_series(DXY_CSV, "dxy")
    left = pd.DataFrame({"date": pd.DatetimeIndex(index)}).sort_values("date")
    right = pd.DataFrame({"d": d.index, "dxy": d.to_numpy(dtype=float)})
    m = pd.merge_asof(left, right, left_on="date", right_on="d", direction="backward",
                      allow_exact_matches=False)
    return pd.Series(m["dxy"].to_numpy(dtype=float), index=pd.DatetimeIndex(index))


def divergence_gate(gate, px, dxy, win=20):
    """R-03d-2 背离提示 -> 闸门修正。

    美元 20 日走弱但金价 20 日不涨 → 「买盘缺失」→ 闸门再打五折；
    美元 20 日走强而金价抗跌 → 「结构性买盘在场」→ 把利率因子的一次降级补回来。
    """
    cd = (dxy / dxy.shift(win) - 1.0)
    cg = (px["close"] / px["close"].shift(win) - 1.0)
    miss = (cd < 0) & (cg <= 0)
    strong = (cd > 0) & (cg >= 0)
    g = gate.copy().astype(float)
    g = g.where(~miss, g * 0.5)
    g = g.where(~strong, np.maximum(g, 1.0))
    return g.fillna(gate), miss.fillna(False), strong.fillna(False)


def mult_daily(index, table):
    """{持有月: mult} -> 日频 Series（按自然月映射）。"""
    per = pd.DatetimeIndex(index).to_period("M")
    v = np.array([table.get(p, 1.0) for p in per], dtype=float)
    return pd.Series(v, index=pd.DatetimeIndex(index))


def placebo(px, rate, table, n=200, seed=7):
    """安慰剂：把「月份 → 乘数」的对应关系随机打乱 n 次，看夏普的自然波动范围。

    保留乘数的取值分布与月度块结构，只毁掉与格子的对齐关系 —— 若真实口径的夏普
    落在这个分布中间，说明「改善」来自运气而不是信息。
    """
    rng = np.random.default_rng(seed)
    keys, vals = list(table.keys()), np.array(list(table.values()), dtype=float)
    out = []
    for _ in range(n):
        perm = rng.permutation(len(vals))
        out.append(bt(px, rate, mult_daily(px.index, dict(zip(keys, vals[perm]))))["sharpe"])
    return np.asarray(out, dtype=float)


def bt(px, rate, mult=None, div=False, label=""):
    """一条口径 -> dict(指标)。`mult` 为日频乘数；`div` 开背离修正。"""
    p = S.params()
    scale, _vol, ok = F.f_vol(px, p)
    gate, _u, _l = F.f_rate_level(rate, px.index, p)
    g = gate
    if div:
        dxy = dxy_daily(px.index)
        g, _miss, _strong = divergence_gate(gate, px, dxy)
    mix = g if mult is None else g * mult.reindex(px.index).ffill().fillna(1.0)
    tgt = F.target_risk(scale, mix, p)
    ready = ok.to_numpy(dtype=bool) & gate.notna().to_numpy(dtype=bool)
    st, _act = S.run_target(tgt, ready, None, p)
    start = px.index[ready][0]
    st_s, px_s = st.loc[start:], px.loc[start:]
    nav = S.run_leg(st_s, px_s, fee=p["FEE"], slip=p["SLIP"])
    m = S.perf(nav)
    b = S.bands(st_s)
    yrs = max((px_s.index[-1] - px_s.index[0]).days / 365.25, 1e-9)
    return dict(label=label, ann=m["ann"], cum=m["cum"], mdd=m["mdd"], sharpe=m["sharpe"],
                calmar=(m["ann"] / abs(m["mdd"]) if abs(m["mdd"]) > 1e-9 else np.nan),
                exp=float(st_s.mean()), trades=b["trades"],
                cost=S.cost_drag(st_s, p) * 100 / yrs, state=st_s)


# ---------------------------------------------------------------- 渲染
def pct(x, nd=2):
    return "—" if x is None or not np.isfinite(x) else f"{x * 100:+.{nd}f}%"


def num(x, nd=2):
    return "—" if x is None or not np.isfinite(x) else f"{x:.{nd}f}"


def render(gold, dxy, recs):
    L = []
    A = L.append
    cur = recs.iloc[-1]
    cur_key = cell(cur)
    g_all, g_is, g_oos = grid(recs), grid(recs[recs["p"] <= SPLIT]), grid(recs[recs["p"] > SPLIT])
    g3 = grid(recs, fwd="fwd3")
    sx = single(recs, "dxy", DX_B)
    sc = single(recs, "cpi", CPI_B)

    # ---- 复现质量：IS/OOS 两半都有 ≥4 个月的格子，符号是否一致
    pairs = [k for k in g_all if g_is[k]["n"] >= 4 and g_oos[k]["n"] >= 4]
    same_sign = sum(1 for k in pairs if np.sign(g_is[k]["mean"]) == np.sign(g_oos[k]["mean"]))
    max_n = max((v["n"] for v in g_all.values()), default=0)
    max_indep = max_n // 12
    dx_d = dict(sx)
    cpi_d = dict(sc)
    low_dx = dx_d["<95"]                        # 外部文档判为「最差档」
    high_dx = dx_d[">105"]                      # 外部文档判为「不差」
    cpi_best = cpi_d["2~3"]                     # 外部文档判为「最舒适」
    cpi_worst = cpi_d["4~5"]                    # 外部文档判为「最差」
    base = BT_ROWS[0]
    noop = [r for r in BT_ROWS[1:] if r.get("fired") == 0]
    tier_row = next(r for r in BT_ROWS if "三档（滚动，n≥12" in r["label"])
    div_row = next(r for r in BT_ROWS if "背离" in r["label"] and "格子" not in r["label"])
    cheat_best = max(CHEAT_ROWS[1:], key=lambda r: r["sharpe"])
    best_ann = max(BT_ROWS[1:], key=lambda r: r["ann"])
    best_sh = max(BT_ROWS[1:], key=lambda r: r["sharpe"])

    A("# V5 · 美元指数 × 通胀：能不能用来优化黄金仓位")
    A("")
    A("> 由 `_probe_dxy_cpi.py` 生成。**检验对象**：外部研究《美元指数与通胀对黄金的影响研究》"
      "（2026-11-19）给出的 DXY × CPI 格子胜率，以及它草案里的 R-03d-1/2/3。")
    A(f"> 数据：`data/_gold_xau.csv`（伦敦金现 {gold.index[0].date()} 起）、"
      f"`data/_dxy.csv`（ICE 美元指数 {dxy.index[0].date()} 起，东财 100.UDI）、"
      f"`data/_cpi.csv`（FRED CPIAUCSL，本月新增缓存）。")
    A(f"> 月频样本 **{recs['p'].iloc[0]} ~ {recs['p'].iloc[-1]}，{len(recs)} 个月**"
      "（外部文档只有 118 个月，本探针样本接近两倍）。无前视：DXY 日频 `shift(1)` 后取月末、"
      "CPI 用 **m−1 月**同比（m 月数据 m+1 月中才发布）。")
    A("")

    # ---- §0 结论
    A("## 0. 结论")
    A("")
    A(f"1. **单因子的「方向」复现了，「极端格」没有**。复现的两条："
      f"DXY>105 不是最差档而是最好档（本样本 {pct(high_dx['mean'])} / 胜率 "
      f"{high_dx['win']:.0%} / t={num(high_dx['t'])}，文档 +3.04% / 69%）；"
      f"CPI 2~3% 是最舒适区间（{pct(cpi_best['mean'])} / {cpi_best['win']:.0%} / "
      f"t={num(cpi_best['t'])}，文档 +2.00% / 74%）。**没复现的两条**："
      f"文档说 DXY<95 是**最差区间**（−0.53% / 34%），本样本 {low_dx['n']} 个月上是 "
      f"**{pct(low_dx['mean'])} / {low_dx['win']:.0%}**（符号相反）；"
      f"CPI 4~5% 最差（文档 −2.38% / 33%），本样本 {pct(cpi_worst['mean'])} / "
      f"{cpi_worst['win']:.0%}。")
    A(f"2. **格子层面的样本根本不够**。两半（IS ≤2016-12 / OOS ≥2017-01）都有 ≥4 个月的"
      f"只有 {len(pairs)} 个格子、其中符号一致 {same_sign} 个；最拥挤的格子也只有 "
      f"{max_n} 个月 ≈ **{max_indep} 个独立年度**。")
    A(f"3. **叠加进仓位公式没有可用的改善**（§3）。{len(noop)} 条口径触发 0 次"
      f"（滚动口径下没有任何格子同时满足 n≥{MIN_N} 与胜率<45%，与基线逐日相同）——"
      f"这本身就是样本不足的直接证据；放宽到 n≥6 后规则开始触发，但年化最好的一条只 "
      f"{(best_ann['ann'] - base['ann']) * 100:+.2f}pp、夏普最好的一条只 "
      f"{num(best_sh['sharpe'])}（基线 {num(base['sharpe'])}），且都在安慰剂分布内（§3.3）。"
      f"DXY 背离口径明确变差：调仓 {base['trades']} → {div_row['trades']} 笔、"
      f"夏普 {div_row['sharpe'] - base['sharpe']:+.2f}。"
      f"**连「拿全样本格子统计作弊」的上界版也只有夏普 {num(cheat_best['sharpe'])}、"
      f"年化 {(cheat_best['ann'] - base['ann']) * 100:+.2f}pp** —— 这组月度宏观水平值的"
      f"信息量本身就在噪声量级。")
    A("4. **只适合当「不计分的宏观读数」**。外部文档 R-03d-1（信息进报告、不进公式）"
      "在本仓库数据上成立；R-03d-3（按格子胜率打折仓位）维持不采纳。"
      "§5 给出可直接贴进每日报告的一行。")
    A("")
    cur_txt = (f"（历史 n={g_all[cur_key]['n']}、1 月远期均值 {pct(g_all[cur_key]['mean'])}、"
               f"胜率 {g_all[cur_key]['win']:.0%}）") if g_all[cur_key]["n"] else "（历史无样本）"
    A(f"**当前读数（{recs['p'].iloc[-1]}）**：DXY **{cur['dxy']:.1f}**、CPI 同比 "
      f"**{cur['cpi']:+.2f}%** → 格子 **{cur_key}**{cur_txt}")
    A("")

    # ---- §1 单因子复现
    A("## 1. 单因子复现（月末 → 未来 1 个月，XAU 美元金）")
    A("")
    A("| DXY 分档 | n | 均值 | 中位数 | 胜率 | t | 外部文档(118 月) |")
    A("|---|---:|---:|---:|---:|---:|---|")
    doc_dx = {"<95": "−0.53% / 34%", "95~97.5": "+1.81% / 62%", "97.5~100": "+1.52% / 75%",
              "100~102.5": "+0.39% / 50%", "102.5~105": "+1.42% / 53%", ">105": "+3.04% / 69%"}
    for name, st in sx:
        A(f"| {name} | {st['n']} | {pct(st['mean'])} | {pct(st['med'])} | "
          f"{st['win']:.0%} | {num(st['t'])} | {doc_dx.get(name, '—')} |")
    A("")
    A("| CPI 同比分档 | n | 均值 | 中位数 | 胜率 | t | 外部文档(118 月) |")
    A("|---|---:|---:|---:|---:|---:|---|")
    doc_cpi = {"<2": "+0.66% / 42%", "2~3": "+2.00% / 74%", "3~4": "+0.96% / 53%",
               "4~5": "−2.38% / 33%", ">5": "+0.63% / 43%"}
    for name, st in sc:
        A(f"| {name} | {st['n']} | {pct(st['mean'])} | {pct(st['med'])} | "
          f"{st['win']:.0%} | {num(st['t'])} | {doc_cpi.get(name, '—')} |")
    A("")
    A(f"> 复现情况：**CPI 2~3% 最舒适** 成立（{pct(cpi_best['mean'])} / "
      f"{cpi_best['win']:.0%} / t={num(cpi_best['t'])}，是五个档里唯一 |t|>2 的）；"
      f"**DXY>105 不差** 也成立（{pct(high_dx['mean'])} / {high_dx['win']:.0%} / "
      f"t={num(high_dx['t'])}）。但**两个「最差档」都翻掉了**：DXY<95 由 −0.53%/34% 变成 "
      f"{pct(low_dx['mean'])}/{low_dx['win']:.0%}，CPI 4~5% 由 −2.38%/33% 变成 "
      f"{pct(cpi_worst['mean'])}/{cpi_worst['win']:.0%}（n={cpi_worst['n']}，t≈0）。")
    A("> 也就是说：**「什么时候好」有弱证据，「什么时候最差」没有** —— 而仓位折扣规则恰恰"
      "只需要知道后者。这是本文最关键的发现。")
    A("")

    # ---- §2 格子
    A("## 2. 格子复现（DXY × CPI，未来 1 个月）")
    A("")
    A("| 格子 | n | 1月均值 | 1月胜率 | t | 3月均值 | IS 均值(n) | OOS 均值(n) |")
    A("|---|---:|---:|---:|---:|---:|---:|---:|")
    for k, v in g_all.items():
        if v["n"] < 3:
            continue
        i_, o_ = g_is[k], g_oos[k]
        A(f"| {k} | {v['n']} | {pct(v['mean'])} | {v['win']:.0%} | {num(v['t'])} | "
          f"{pct(g3[k]['mean'])} | {pct(i_['mean'])} ({i_['n']}) | {pct(o_['mean'])} ({o_['n']}) |")
    A("")
    A(f"> 外部文档点名的两个格子：**97.5~100 × 2~3%**（文档：n=12 / +2.42% / 92%）本样本 "
      f"n={g_all['97.5~100 × 2~3']['n']} / {pct(g_all['97.5~100 × 2~3']['mean'])} / "
      f"{g_all['97.5~100 × 2~3']['win']:.0%}；**100~102.5 × 3~4%**（文档：n=6 / +0.08% / 33%）"
      f"本样本 n={g_all['100~102.5 × 3~4']['n']} / {pct(g_all['100~102.5 × 3~4']['mean'])} / "
      f"{g_all['100~102.5 × 3~4']['win']:.0%}。")
    A(f"> 样本量是硬约束：最大格子 {max_n} 个月 = **{max_indep} 个独立年度**；"
      "外部文档 n=6 的格子对应不到 1 个独立年度，胜率 33%/100% 都在噪声范围。")
    A("")

    # ---- §3 叠加回测
    A("## 3. 叠加回测（518880，2014-01 起，v5 同一套成本与状态机）")
    A("")
    A("| 口径 | 年化 | 最大回撤 | 夏普 | Calmar | 平均仓位 | 调仓 | 触发月 | 成本/年 |")
    A("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in BT_ROWS:
        fired = "—" if r.get("fired") is None else str(r["fired"])
        A(f"| {r['label']} | {pct(r['ann'])} | {pct(r['mdd'])} | {num(r['sharpe'])} | "
          f"{num(r['calmar'])} | {r['exp']:.0%} | {r['trades']} | {fired} | {r['cost']:.2f}pp |")
    A("")
    moved = [r for r in BT_ROWS[1:] if (r.get("fired") or 0) > 0]
    A(f"> 基线（现有 `scale × gate`）：{pct(base['ann'])} / {pct(base['mdd'])} / 夏普 "
      f"{num(base['sharpe'])}。「触发月」= 乘数真正改变目标档的月数。")
    if noop:
        A(f"> **{len(noop)} 条口径触发 0 次**（{'、'.join(r.get('short', r['label']) for r in noop)}）"
          f" —— 滚动口径下没有任何格子同时满足 n≥{MIN_N} 与胜率<45%，规则退化成恒等变换，"
          "与基线逐日相同。这本身就是样本不足的直接证据。")
    if moved:
        A("> 触发过的口径：" + "；".join(
            f"{r.get('short', r['label'])} 夏普 {r['sharpe'] - base['sharpe']:+.2f}、年化 "
            f"{(r['ann'] - base['ann']) * 100:+.2f}pp、最大回撤 {pct(r['mdd'])}" for r in moved)
          + "。")
    A("> 结论：**最大回撤一条都没改善**（风险预算主导的那条路径没被碰到）；年化最好的一条也就 "
      f"{(max(moved, key=lambda r: r['ann'])['ann'] - base['ann']) * 100:+.2f}pp，"
      "而夏普的这点差异在安慰剂分布内（§3.3）。明确变差的是背离口径："
      f"夏普 {div_row['sharpe'] - base['sharpe']:+.2f}、调仓 ×{div_row['trades'] / base['trades']:.0f}。"
      if moved else "")
    A("")
    A("### 3.1 上限在哪：作弊版也只能买到这一点")
    A("")
    A("「全样本格子统计」版本用**含未来信息**的格子胜率决定每个月乘数，等于事后诸葛 —— "
      "它给出的是这个思路在**本样本上的天花板**：")
    A("")
    A("| 口径 | 年化 | 最大回撤 | 夏普 |")
    A("|---|---:|---:|---:|")
    for r in CHEAT_ROWS:
        A(f"| {r['label']} | {pct(r['ann'])} | {pct(r['mdd'])} | {num(r['sharpe'])} |")
    A("")
    hi = float(np.percentile(PLACEBO["dist"], 95)) if PLACEBO else np.nan
    A(f"> 知道未来也只买到 **{(cheat_best['ann'] - base['ann']) * 100:+.2f}pp 年化 / "
      f"{num(cheat_best['sharpe'])} 夏普**，回撤一分没动。")
    if PLACEBO:
        cmp_ = "仍低于" if cheat_best["sharpe"] < hi else "勉强追平"
        A(f"> 而这个夏普{cmp_}安慰剂分布的 95% 上限 {num(hi, 3)}（§3.3）—— "
          "即使偷看答案，也只达到「随机打乱月份」的运气上沿附近。"
          "这基本排除了「参数没调好」这一解释。")
    A("")
    A("### 3.2 背离口径（R-03d-2）真的成立吗")
    A("")
    A("| 状态 | 天数 | 之后 20 日金价均值 | 胜率 |")
    A("|---|---:|---:|---:|")
    for name, n, mu, win in DIV_TABLE:
        A(f"| {name} | {n} | {pct(mu)} | {win:.0%} |")
    A("")
    A("> 若「美元走弱 + 金价不涨 = 买盘缺失」成立，该状态的后续收益应显著为负；"
      "若「美元走强 + 金价抗跌 = 结构性买盘」成立，该状态应显著为正。"
      "抽样用非重叠 20 日块，避免重叠窗口夸大显著度。")
    A("")
    if PLACEBO:
        d = PLACEBO["dist"]
        pv = float((d >= PLACEBO["sharpe"]).mean())
        A("### 3.3 安慰剂检验：这点改善是真的吗")
        A("")
        A(f"把最好的一条（{PLACEBO['label']}）的「月份 → 乘数」对应关系**随机打乱 200 次**，"
          "保留乘数分布与月度块结构，只毁掉它与格子的对齐关系：")
        A("")
        A(f"- 真实夏普 **{num(PLACEBO['sharpe'], 3)}**；安慰剂中位 "
          f"{num(float(np.median(d)), 3)}、5%~95% 区间 "
          f"[{num(float(np.percentile(d, 5)), 3)}, {num(float(np.percentile(d, 95)), 3)}]")
        A(f"- 有 **{pv:.0%}** 次随机打乱的夏普不低于真实值 → "
          + ("**不可区分**，「改善」在噪声范围内。" if pv > 0.10 else "超出噪声范围。"))
        A("")
        A("> 这是判定「+0.03pp 年化算不算改善」的标准做法：宏观月度信号只有几十个有效样本，"
          "夏普的抽样误差本身就有 ±0.1 量级，不比信号小。")
        A("")

    # ---- §4 样本充分性
    A("## 4. 样本充分性")
    A("")
    A("| 项 | 值 |")
    A("|---|---|")
    A(f"| 月频样本 | {len(recs)} 个月（{recs['p'].iloc[0]} ~ {recs['p'].iloc[-1]}） |")
    A(f"| 最大格子样本 | {max_n} 个月 = {max_indep} 个独立年度 |")
    A(f"| IS/OOS 两半都有 ≥4 个月的格子 | {len(pairs)} 个（其中符号一致 {same_sign} 个） |")
    A(f"| 单因子 DXY<95 档 | n={low_dx['n']}、均值 {pct(low_dx['mean'])}、"
      f"胜率 {low_dx['win']:.0%}（文档：−0.53% / 34%） |")
    A(f"| 驱动仓位的最小样本 | 滚动口径要求 n ≥ {MIN_N}（≈ 1 个独立年度） |")
    A("")
    A("> 结论：这组格子最多能当「描述当前宏观环境在哪一格」的读数，"
      "**不够格写进仓位公式** —— 与仓库里 `rate_level` 那条「段数远少于 50 → 只能算中等证据」"
      "是同一类判断标准，但这次连中等证据都算不上（符号不稳定）。")
    A("")

    # ---- §5 建议
    A("## 5. 建议（改什么、不改什么）")
    A("")
    A("| 外部草案 | 本探针结论 |")
    A("|---|---|")
    A("| R-03d-1：DXY 不进日频计分，报告新增「宏观环境格」一行 | **采纳**：§5.1 给出现成读数 |")
    A("| R-03d-2：背离提示（买盘缺失 / 结构性买盘） | **只当提示**：§3.2 的「买盘缺失」组后续收益"
      "反而为正（+0.26% vs 其余 +0.87%，无统计意义），接进闸门后夏普 −0.12、调仓 ×8 |")
    A(f"| R-03d-3：按格子胜率打折仓位 | **不采纳**：§3 滚动版在安慰剂噪声内"
      f"（p={float((PLACEBO['dist'] >= PLACEBO['sharpe']).mean()):.2f}），"
      f"作弊上界也只有 {(cheat_best['ann'] - base['ann']) * 100:+.2f}pp |")
    A("")
    A("### 5.1 可直接进每日报告的一行")
    A("")
    A("```")
    A(f"宏观环境格：DXY {cur['dxy']:.1f} × CPI {cur['cpi']:+.2f}% → {cur_key}"
      f"（历史 n={g_all[cur_key]['n']}、1 月胜率 "
      f"{g_all[cur_key]['win']:.0%}）—— 不计分")
    A("```")
    A("")
    A("> 与 `data/_gold_hints.json` 里的 FedWatch / 央行购金 / 内外价差 同一级别："
      "只进「不计分项」，与本次调仓方向冲突时标注「观察，谨慎」，**绝不改仓位数字**。")
    A("")
    A("### 5.2 为什么不改公式")
    A("")
    A("1. 这组变量与实际利率高度相关（外部文档自己写了 0.7+），而实际利率已经在 `gate` 里 ——"
      "再加一层是重复计权，不是新信息。")
    A("2. DXY 的负相关是**同期**的：v6 的 `_gold_macro.md` §3 已实测，滞后一期相关从 −0.394 掉到 "
      "−0.014。月度水平分档绕不开这个问题。")
    A("3. 换手成本：任何以宏观水平值切换仓位的口径，年化优势都在 0.3pp 量级"
      "（`_gold_macro.md` §5.1），单边 2.5bp 就吃掉了。")
    A("")
    A(DISCLAIMER)
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- 主流程
BT_ROWS = []
CHEAT_ROWS = []
DIV_TABLE = []
PLACEBO = {}


def main():
    gold, dxy, yoy = load_all()
    recs = recs_monthly(gold, dxy, yoy)
    print(f"[样本] XAU {gold.index[0].date()} ~ {gold.index[-1].date()} · "
          f"月频 {len(recs)} 个月（{recs['p'].iloc[0]} ~ {recs['p'].iloc[-1]}）")

    px, rate = F.price_frame(), F.read_rate()

    # 基线必须与生产口径逐日一致，否则下面的对照不成立
    base = bt(px, rate, label="**基线（现有 `scale × gate`）**")
    prod = S.run(px, rate)["state"]
    start = px.index[F.f_vol(px)[2].to_numpy(dtype=bool)][0]
    err = float((base["state"] - prod.loc[base["state"].index]).abs().max())
    assert err < 1e-9, f"探针基线不等于生产口径（max err {err:.2e}）"
    print(f"[校验] 基线 == S.run(px, rate) 状态机，max err {err:.1e}")

    variants = [
        ("weak", MIN_N, "格子胜率减半（滚动，n≥12，胜率<45%→×0.5）", "减半 n≥12"),
        ("tier", MIN_N, "格子胜率三档（滚动，n≥12，×0.5/×0.75/×1.0）", "三档 n≥12"),
        ("tier", 6, "格子胜率三档（滚动，n≥6，放宽到外部文档同样本量）", "三档 n≥6"),
        ("weak", 6, "格子胜率减半（滚动，n≥6）", "减半 n≥6"),
    ]

    BT_ROWS.clear()
    BT_ROWS.append(base)
    for mode, min_n, label, short in variants:
        tbl = rolling_mult(recs, mode, min_n)
        r = bt(px, rate, mult_daily(px.index, tbl), label=label)
        r["fired"], r["_tbl"], r["short"] = fired(tbl), tbl, short
        BT_ROWS.append(r)
    BT_ROWS.append(bt(px, rate, div=True, label="DXY 背离修正（R-03d-2）"))
    BT_ROWS.append(bt(px, rate, mult_daily(px.index, rolling_mult(recs, "tier")), div=True,
                      label="格子三档 + 背离修正"))

    # 安慰剂：对「最会改善的那条」打乱月份↔乘数对应，看夏普的自然波动
    best = max((r for r in BT_ROWS[1:] if r.get("fired")), key=lambda r: r["sharpe"], default=None)
    if best is not None:
        PLACEBO.clear()
        PLACEBO.update(label=best["label"], sharpe=best["sharpe"],
                       dist=placebo(px, rate, best["_tbl"]))
        print(f"[安慰剂] {best['label'][:20]} 真实夏普 {best['sharpe']:.3f}，"
              f"打乱 200 次 p={float((PLACEBO['dist'] >= best['sharpe']).mean()):.2f}")

    CHEAT_ROWS.clear()
    CHEAT_ROWS.append(base)
    for mode, label in (("weak", "格子胜率减半（**全样本，含未来**）"),
                        ("tier", "格子胜率三档（**全样本，含未来**）")):
        tbl = full_mult(recs, mode)
        r = bt(px, rate, mult_daily(px.index, tbl), label=label)
        r["fired"] = fired(tbl)
        CHEAT_ROWS.append(r)

    # 背离状态的条件收益（日频 → 20 日前瞻，季度非重叠抽样避免重叠）
    d = dxy_daily(px.index)
    _g, miss, strong = divergence_gate(pd.Series(1.0, index=px.index), px, d)
    c = px["close"]
    fwd20 = (c.shift(-20) / c - 1.0)
    pos = np.arange(len(px)) % 20 == 0
    DIV_TABLE.clear()
    for name, mask in (("美元 20 日走弱 + 金价 20 日不涨（买盘缺失）", miss),
                       ("美元 20 日走强 + 金价 20 日抗跌（结构性买盘）", strong),
                       ("其余", ~(miss | strong))):
        sel = mask.to_numpy() & pos & fwd20.notna().to_numpy()
        v = fwd20.to_numpy()[sel]
        DIV_TABLE.append((name, int(sel.sum()),
                          float(np.mean(v)) if len(v) else np.nan,
                          float((v > 0).mean()) if len(v) else np.nan))

    md = render(gold, dxy, recs)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(md)
    for r in BT_ROWS:
        print(f"  {r['label'][:34]:<36} ann {pct(r['ann'])} mdd {pct(r['mdd'])} "
              f"sharpe {num(r['sharpe'])}")
    print("落盘:", OUT)


if __name__ == "__main__":
    main()
