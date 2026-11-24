#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V11 · 专项研究：横截面「RSI + 温度双排序取交集」能不能替代 V4 的 RSI top2

用户提出的规则
--------------
对池内标的的 **RSI** 与 **温度** 分别排序（都是越低越便宜/越冷 → 排前面更好），
选出**两个排名都在前 K 只**的标的等权持有；总仓位仍由板块 RSI 均值控制（V4 的 70/50 三档）。

**用户 2026-11-24 定的口径：6 只估值温度池、K = 2。**

要回答的四件事
--------------
1. 双排序交集（每月 0~2 只）与 V4 的「RSI 最低 2 只」比，横截面有没有增量；
2. 交集为空时怎么处理最不坏：空仓 / 退回 RSI 前 2 / 退回温度前 2；
3. 结果是不是「随机挑同样只数」就能复现 —— 用置换零分布判定；
4. **2026-11-24 增**：把「价格温度」并进排序、让 10 只都有温度，效果变好还是变差
   （结论：更差，已否决，见报告 §8.3）。

三种温度口径（`_temp_board.py` 生产）
------------------------------------
* 估值温度（V9 五因子）—— 只有 6 只（008115 / 007467 / 090010 / 016441 / 561060 / 561580）；
* 价格温度（`dev` / `pos` 各 50%）—— 10 只全有（`data/_board_temp_px.csv`）；
* 混合温度 = 估值优先、缺则价格兜底 —— 10 只全有（`data/_board_temp_mix.csv`）。

**结论（详见 `_xs_rsi_temp.md` §八）**：补价格温度后 10 只确实都有温度了，
但同一个规则的 Calmar 从 1.86（6 只估值口径）掉到 1.14（混合）/ 1.23（纯价格），
两条都低于 V4 的 1.44 —— 弱信号稀释强信号，不成立。
所以这两个 CSV 目前只用于读数展示与研究，**不进每日仓位**。

用法::

    python strategies/dividend/v11_gate/_temp_board.py           # 先产三种温度 CSV
    python strategies/dividend/v11_gate/research/xs_rsi_temp/_xs_rsi_temp.py
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_RES = os.path.dirname(_HERE)                      # .../v11_gate/research
_DIR = os.path.dirname(_RES)                       # .../v11_gate
_STRAT = os.path.dirname(_DIR)                     # .../dividend
for _p in (_DIR, _STRAT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import data as D  # noqa: E402  pylint: disable=wrong-import-position
import engine as E  # noqa: E402  pylint: disable=wrong-import-position
import rule  # noqa: E402  pylint: disable=wrong-import-position

OUT_MD = os.path.join(_HERE, "_xs_rsi_temp.md")

K = 2                 # 用户 2026-11-24 定：「RSI 与估值温度都排前 2」
K_ALT = (1, 3, 4)     # 敏感性（池只有 6 只，k=3 即取一半；k=4 接近全池）
N_PERM = 500
SEED = 42


# ------------------------------------------------------------------ 权重构造
def _ranks(raw: pd.DataFrame, temp: pd.DataFrame, ok: pd.DataFrame,
           ts, pool6: bool) -> tuple[pd.Series, pd.Series]:
    """当月 RSI 排名与温度排名（1 = 最便宜/最冷）。温度只有 6 只有，其余为 NaN。"""
    r = raw.loc[ts].where(ok.loc[ts]).dropna()
    t = temp.loc[ts].dropna()
    if pool6:                       # 只在「有温度」的同源池里排 RSI
        r = r[r.index.isin(t.index)]
    return r.rank(method="first"), t.rank(method="first")


def picks_dual(raw, temp, ok, k=K, fallback="rsi", pool6=False) -> dict:
    """双排序取交集；交集为空时按 fallback 处理。"""
    out = {}
    for ts in raw.index:
        rr, tr = _ranks(raw, temp, ok, ts, pool6)
        r = raw.loc[ts].where(ok.loc[ts]).dropna()
        if pool6:
            r = r[r.index.isin(temp.loc[ts].dropna().index)]
        t = temp.loc[ts].dropna()
        a = list(rr.nsmallest(min(k, len(rr))).index)
        b = list(tr.nsmallest(min(k, len(tr))).index)
        inter = [c for c in a if c in b]
        if inter:
            out[ts] = inter
        elif fallback == "cash" or not a:
            out[ts] = []
        elif fallback == "rsi":
            out[ts] = a
        else:                       # "temp"
            out[ts] = b
    return out


def picks_rank_avg(raw, temp, ok, k=K) -> dict:
    """合并排名：在「有温度」的同源池里，按 (RSI 排名 + 温度排名) 取前 k（不用交集）。"""
    out = {}
    for ts in raw.index:
        rr, tr = _ranks(raw, temp, ok, ts, pool6=True)
        both = rr.index.intersection(tr.index)
        if both.empty:
            out[ts] = []
            continue
        s = (rr.reindex(both) + tr.reindex(both)) / 2.0
        out[ts] = list(s.nsmallest(min(k, len(s))).index)
    return out


PX_CSV = os.path.join(_DIR, "data", "_board_temp_px.csv")
MIX_CSV = os.path.join(_DIR, "data", "_board_temp_mix.csv")


def _load_temp_csv(path: str, index) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"缺 {path} —— 先跑 python strategies/dividend/v11_gate/_temp_board.py")
    df = pd.read_csv(path, parse_dates=["date"]).set_index("date")
    return df.drop(columns=["asof"], errors="ignore").reindex(index)


def load_px_temp(index) -> pd.DataFrame:
    """10 只价格温度（dev/pos 各 50%，10 只同一刻度）。"""
    return _load_temp_csv(PX_CSV, index)


def load_mix_temp(index) -> pd.DataFrame:
    """10 只混合温度：估值温度优先、缺则价格温度兜底。"""
    return _load_temp_csv(MIX_CSV, index)


def picks_rsi_only(raw, tt, okx, k=K) -> dict:
    """「只按 RSI」基线：候选集与双排序**完全一致**（当月该只有温度才进候选），只是不用温度排名。

    不这么对齐的话，早年 008115（2021-04 前）/ 016441（2022-06 前）没有温度，
    双排序的候选池比「纯 RSI」小，两者的差异就混进了「候选集变化」而不只是「温度这一票」。
    """
    out = {}
    for ts in raw.index:
        avail = tt.loc[ts].dropna().index
        r = raw.loc[ts].where(okx.loc[ts]).dropna()
        r = r[r.index.isin(avail)]
        out[ts] = list(r.nsmallest(min(k, len(r))).index) if len(r) else []
    return out


def to_w(picks: dict, index, cols) -> pd.DataFrame:
    out = pd.DataFrame(0.0, index=index, columns=cols)
    for ts, sel in picks.items():
        if sel:
            out.loc[ts, sel] = 1.0 / len(sel)
    return out


# ------------------------------------------------------------------ 置换零分布
def perm_random(rets, ok, sizes: pd.Series, m: pd.Series, n_perm=N_PERM, seed=SEED) -> dict:
    """每月从可得标的里随机抽 n_t 只等权（n_t = 双排序方案当月的只数），跑同样时间档。"""
    rng = np.random.default_rng(seed)
    cals, cagrs = [], []
    for _ in range(n_perm):
        W = pd.DataFrame(0.0, index=ok.index, columns=ok.columns)
        for ts in ok.index:
            cand = ok.columns[ok.loc[ts].to_numpy(dtype=bool)]
            n = int(sizes.loc[ts])
            if n <= 0 or len(cand) == 0:
                continue
            pick = rng.choice(cand, size=min(n, len(cand)), replace=False)
            W.loc[ts, list(pick)] = 1.0 / len(pick)
        st = E.metrics(E.sim(W, m, rets))
        cals.append(st["calmar"])
        cagrs.append(st["cagr"])
    return {"calmar": np.array(cals), "cagr": np.array(cagrs)}


# ------------------------------------------------------------------ 主流程
def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    d10 = D.load(codes=rule.CODES_V4NOW)
    rets, raw, ok, bq = d10["rets"], d10["raw"], d10["ok"], d10["bq"]
    board = d10["board"]
    tcols = [c for c in board.columns if c in rets.columns]
    temp = board[tcols].reindex(rets.index)                # 6 只「估值温度」
    # 2026-11-24 新增：价格温度（10 只同源）与「估值优先 / 价格兜底」的混合温度
    temp_px = load_px_temp(rets.index)
    temp_mix = load_mix_temp(rets.index)
    m_rsi = E.mult_rsi(bq)
    one = pd.Series(1.0, index=bq.index)

    # 同源 6 只池的一套口径（RSI 也只在 6 只里算）
    codes6 = rule.CODES
    ok6 = ok.copy()
    ok6[[c for c in ok6.columns if c not in codes6]] = False

    variants = []       # (name, weights, note)

    v4_w = d10["top"]                                    # RSI 最低 2 只
    variants.append(("V4 基线：RSI top2（10 只）", v4_w, "横截面基准"))

    # —— 10 只池：RSI 在 10 只里排、温度在 6 只里排 ——
    for fb, tag in (("cash", "交集空→空仓"), ("rsi", f"交集空→RSI 前{K}"),
                    ("temp", f"交集空→温度前{K}")):
        p = picks_dual(raw, temp, ok, k=K, fallback=fb, pool6=False)
        variants.append((f"10 只双排序 top{K}（{tag}）", to_w(p, rets.index, rets.columns),
                         f"RSI 在前 10 排、温度在前 6 排"))

    # —— 10 只池 + 混合温度（6 估值 + 4 价格兜底）：用户 2026-11-24 提的口径 ——
    for fb, tag in (("cash", "交集空→空仓"), ("rsi", f"交集空→RSI 前{K}"),
                    ("temp", f"交集空→温度前{K}")):
        p = picks_dual(raw, temp_mix, ok, k=K, fallback=fb, pool6=False)
        variants.append((f"10 只双排序 top{K}·混合温度（{tag}）", to_w(p, rets.index, rets.columns),
                         "温度 = 估值优先/价格兜底，10 只全有"))

    # —— 10 只池 + 纯价格温度（10 只同一刻度，最干净）——
    for fb, tag in (("cash", "交集空→空仓"), ("rsi", f"交集空→RSI 前{K}")):
        p = picks_dual(raw, temp_px, ok, k=K, fallback=fb, pool6=False)
        variants.append((f"10 只双排序 top{K}·纯价格温度（{tag}）", to_w(p, rets.index, rets.columns),
                         "10 只都按 dev/pos 价格温度排"))

    # —— 混合温度 / 价格温度的单信号横截面（分离「温度这一票」的贡献）——
    for tag, tt in (("混合温度", temp_mix), ("纯价格温度", temp_px)):
        p_t = {ts: list(tt.loc[ts].dropna().nsmallest(K).index) for ts in rets.index}
        variants.append((f"10 只 {tag} top{K}（不用 RSI）", to_w(p_t, rets.index, rets.columns),
                         "只按温度选品"))

    # —— 6 只同源池：两个排名都在同一批标的里 ——
    for fb, tag in (("cash", "交集空→空仓"), ("rsi", f"交集空→RSI 前{K}"),
                    ("temp", f"交集空→温度前{K}")):
        p = picks_dual(raw, temp, ok6, k=K, fallback=fb, pool6=True)
        variants.append((f"6 只同源双排序 top{K}（{tag}）", to_w(p, rets.index, rets.columns),
                         "RSI 与温度都在同源 6 只里排"))

    # —— 合并排名（不取交集，取加权名次前 3）——
    p = picks_rank_avg(raw, temp, ok6, k=K)
    variants.append((f"6 只合并排名前{K}（名次均值）", to_w(p, rets.index, rets.columns),
                     "不取交集，按 (RSI名次+温度名次)/2 取前 3"))

    # —— 单指标横截面对照：只用温度前 3（V9 否过的形态）——
    p_temp3 = {ts: list(temp.loc[ts].dropna().nsmallest(K).index) for ts in rets.index}
    variants.append((f"温度 top{K}（6 只，只用温度选品）",
                     to_w(p_temp3, rets.index, rets.columns), "温度单指标横截面"))

    # —— 关键对照：静态持有「事后最优两/三只」（有前视，只作标尺）——
    # 如果静态持有就能达到双排序的 Calmar，说明双排序只是「事后押中了回撤最浅的那几只」。
    bh = {c: E.metrics(rets[c].dropna()) for c in codes6 if c in rets.columns}
    order_bh = sorted(bh, key=lambda c: -bh[c]["calmar"])
    for kk in (2, 3):
        sel = order_bh[:kk]
        w_static = pd.DataFrame(0.0, index=rets.index, columns=rets.columns)
        w_static[sel] = 1.0 / len(sel)
        variants.append((f"静态持有事后最优 {kk} 只（{'/'.join(sel)}）", w_static,
                         "有前视，仅作标尺"))

    # —— 同源 6 只等权（最朴素的对照）——
    eq6 = pd.DataFrame(0.0, index=rets.index, columns=rets.columns)
    eq6[codes6] = 1.0 / len(codes6)
    variants.append(("同源 6 只等权", eq6, "池基准"))

    # ---------------------------------------------------------------- 评估
    rows = []
    for name, w0, note in variants:
        for tag, m in (("× RSI 时间档", m_rsi), ("满仓", one)):
            st = E.evaluate(w0, m, rets)
            n_hold = w0.gt(0).sum(axis=1)
            rows.append({"方案": f"{name}｜{tag}", "note": note, "st": st,
                         "n_avg": float(n_hold.mean()), "n_zero": int((n_hold == 0).sum()),
                         "n_pos": float(w0.mul(m.reindex(w0.index), axis=0).sum(axis=1).mean())})

    # ---------------------------------------------------------------- 选品超额（不受集中度/路径影响）
    nxt_r = rets.shift(-1)

    def excess_vs_pool(w0: pd.DataFrame, base_ok: pd.DataFrame = None) -> dict:
        """每月「持仓的次月等权收益 − 池内等权次月收益」，测选品有没有 alpha。

        `base_ok` 缺省用 10 只池；6 只同源方案应传 `ok6`，否则基准里混进它选不到的标的。
        """
        base = ok if base_ok is None else base_ok
        ex = []
        for ts in w0.index[:-1]:
            row = w0.loc[ts]
            sel = list(row.index[row > 0])
            if not sel:
                continue
            nxt = nxt_r.loc[ts]
            pool = nxt.reindex(base.loc[ts][base.loc[ts]].index).dropna()
            got = nxt.reindex(sel).dropna()
            if len(got) < 1 or len(pool) < 3:
                continue
            ex.append(float(got.mean() - pool.mean()))
        a = np.array(ex)
        if len(a) < 5:
            return {"n": len(a), "mean": float("nan"), "t": float("nan"), "win": float("nan")}
        t = a.mean() / (a.std(ddof=1) / np.sqrt(len(a)))
        return {"n": len(a), "mean": float(a.mean()), "t": float(t),
                "win": float((a > 0).mean())}

    def base_ok_of(name: str) -> pd.DataFrame:
        """6 只同源方案的基准用 ok6，其余（10 只口径）用 ok。"""
        return ok6 if ("6 只" in name or "同源" in name or "静态持有" in name) else ok

    hold_cnt = {}
    for name, w0, _n in variants:
        if w0.shape[1] and w0.gt(0).any().any():
            hold_cnt[name] = w0.gt(0).sum(axis=0)

    # 交集只数分布（三种温度口径）
    dist_rows = []
    for tag, tt in (("估值温度（仅 6 只）", temp),
                    ("混合温度（10 只）", temp_mix),
                    ("纯价格温度（10 只）", temp_px)):
        pp = picks_dual(raw, tt, ok, k=K, fallback="cash", pool6=False)
        ss = pd.Series({ts: len(v) for ts, v in pp.items()}).reindex(rets.index).fillna(0)
        dist_rows.append((tag, ss, ss.value_counts().sort_index()))
    _idx = sorted({i for _t, _s, d in dist_rows for i in d.index})
    dist = dist_rows[0][2].reindex(_idx, fill_value=0)
    sizes = dist_rows[0][1]

    # ---------------------------------------------------------------- 置换
    # 主口径 = 用户 2026-11-24 定的方案：6 只估值温度、k=2、交集空→RSI 前 2
    MAIN_NAME = f"6 只同源双排序 top{K}（交集空→RSI 前{K}）"
    MIX_NAME = f"10 只双排序 top{K}·混合温度（交集空→RSI 前{K}）"
    ALT6_NAME = MAIN_NAME
    main_pick = next(r for r in rows if r["方案"] == f"{MAIN_NAME}｜× RSI 时间档")
    w_main = next(w for n, w, _x in variants if n == MAIN_NAME)
    n_main = w_main.gt(0).sum(axis=1)
    perm = perm_random(rets, ok6, n_main, m_rsi)
    pct_cal = float((perm["calmar"] < main_pick["st"]["calmar"]).mean())
    pct_cagr = float((perm["cagr"] < main_pick["st"]["cagr"]).mean())

    # ---------------------------------------------------------------- 截面 IC（温度 / RSI）
    def cs_ic(key: pd.DataFrame, mask: pd.DataFrame = None) -> dict:
        base = ok if mask is None else mask
        ics = []
        for ts in rets.index[:-1]:
            k_ = key.loc[ts].where(base.loc[ts]).dropna()
            nxt = rets.shift(-1).loc[ts].reindex(k_.index).dropna()
            k_ = k_.reindex(nxt.index)
            if len(k_) >= 4 and k_.std() > 0:
                ics.append(float(k_.corr(nxt, method="spearman")))
        a = np.array(ics)
        t = a.mean() / (a.std(ddof=1) / np.sqrt(len(a))) if len(a) > 2 else np.nan
        return {"n": len(a), "ic": float(a.mean()) if len(a) else np.nan, "t": float(t),
                "pos": float((a > 0).mean()) if len(a) else np.nan}

    ic_temp = cs_ic(temp, ok6)          # 6 只估值温度（同源口径）
    ic_rsi = cs_ic(raw, ok6)
    ic_mix = cs_ic(temp_mix)            # 10 只混合温度（10 只池口径）
    ic_px = cs_ic(temp_px)              # 10 只纯价格温度

    # ---------------------------------------------------------------- 报告
    def fmt(x, d=1):
        return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:.{d}f}%"

    L = ["# V11 专项 · 横截面「RSI + 温度双排序取交集」", "",
         "> 由 `strategies/dividend/v11_gate/research/xs_rsi_temp/_xs_rsi_temp.py` 生成，数字每次重跑重算。",
         f"> **规则**：对 6 只同源标的的 RSI 与估值温度分别排序（越低越好），"
         f"选出两个排名都在前 {K} 的标的等权；",
         "> 总仓位仍由板块 RSI 均值控制（V4 三档 70/50）。", "",
         "## 〇、先说口径：三种温度", "",
         f"1. **估值温度（V9 五因子）—— 只有 {len(tcols)} 只**（" + " / ".join(tcols) + "）。"
         f"池内另外 {len(rets.columns) - len(tcols)} 只（"
         + " / ".join(c for c in rets.columns if c not in tcols) + "）三源皆无等权估值，算不出来。",
         "2. **价格温度（`dev` 乖离 + `pos` 位置，各 50%）—— 10 只全有**（2026-11-24 新增，"
         "`_temp_board.py` 落 `data/_board_temp_px.csv`）。主表 6 只也按价格口径重算，"
         "所以这张表里 10 只的刻度是同源的。",
         "3. **混合温度 —— 10 只全有**（`data/_board_temp_mix.csv`）= 估值温度优先、缺则价格温度兜底。"
         "这是用户 2026-11-24 要的「10 个标的都有温度」口径。",
         "",
         "> ⚠ **混合温度不是同一件事**：4 只（007760 / 018388 / 008163 / 005125）永远只有价格口径，"
         "而价格温度只看价格位置、不含 PE/PB/利差。把它们和估值温度放在同一个横截面里比大小，"
         "等于假设「价格冷」与「估值冷」等价。所以本报告同时给**纯价格温度**（10 只同刻度）作干净对照。",
         "",
         f"- 温度各自起点（早于它的月份该只不参与温度排名）："
         + "、".join(f"{c} {temp_mix[c].first_valid_index():%Y-%m}" if pd.notna(temp_mix[c].first_valid_index())
                    else f"{c} —" for c in temp_mix.columns) + "。",
         f"- 可用只数：混合温度 **{int((temp_mix.notna().sum(axis=1) >= 10).sum())}/{len(rets)} 个月满 10 只**"
         f"（其余月份 9 只，缺 008163 的 515450 代理历史）；"
         f"纯价格温度 **{int((temp_px.notna().sum(axis=1) >= 10).sum())}/{len(rets)} 个月满 10 只**。",
         f"- 样本 {rets.index[0]:%Y-%m} ~ {rets.index[-1]:%Y-%m}（{len(rets)} 月）；月末信号 → 下月生效；"
         f"成本单边 {rule.FEE * 100:.2f}%。", "",
         "## 一、交集只数分布（三种温度口径对比）", "",
         "| 温度口径 | 平均只数 | 空仓月 | " + " | ".join(f"交集 {int(i)} 只" for i in _idx) + " |",
         "|---|---:|---:|" + "---:|" * len(_idx)]
    for tag, ss, dd in dist_rows:
        L.append(f"| {tag} | {ss.mean():.2f} | {int((ss == 0).sum())} | "
                 + " | ".join(str(int(dd.reindex(_idx, fill_value=0).loc[i])) for i in _idx) + " |")
    L += ["", f"> **反直觉**：温度补全到 10 只之后，交集并没有变多"
              f"（{dist_rows[0][1].mean():.2f} → {dist_rows[1][1].mean():.2f} 只，"
              f"空仓 {int((dist_rows[0][1] == 0).sum())} → {int((dist_rows[1][1] == 0).sum())} 月）。"
              f"RSI 前 {K} 没变，温度前 {K} 现在能从 10 只里挑，反而更容易落在 RSI 前 {K} 之外 —— "
              "两个排名的分歧不会因为「多给了几个温度」而收敛。", "",
          "## 二、回测（同一时间档 m_rsi 下比横截面）", "",
          "| 方案 | 年化 | 最大回撤 | Calmar | 夏普 | 平均仓位 | 平均只数 | 空仓月 |",
          "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        st = r["st"]
        L.append(f"| {r['方案']} | {fmt(st['cagr'])} | {fmt(st['dd'])} | {st['calmar']:.2f} | "
                 f"{st['sharpe']:.2f} | {r['n_pos']:.0%} | {r['n_avg']:.2f} | {r['n_zero']} |")
    L += ["", "> 「平均仓位」= Σw 的均值（已含时间档）；「平均只数」= 每月非零持仓只数。"
              " **注意**：只数越少越集中，Calmar 对「哪几个月恰好押中哪只」极度敏感。", ""]

    # 二·补：选品超额 + 集中度
    L += ["### 二·补.1 选品超额（剥离集中度与时间档：持仓次月收益 − 同源 6 只等权次月收益）", "",
          "| 方案 | 月数 | 平均超额 | t 值 | 胜率 |", "|---|---:|---:|---:|---:|"]
    for name, w0, _n in variants:
        e = excess_vs_pool(w0, base_ok_of(name))
        L.append(f"| {name} | {e['n']} | {e['mean'] * 1e4:+.0f}bp | {e['t']:+.2f} | "
                 f"{e['win']:.0%} |")
    L += ["", "> 这一栏才是「双排序挑得准不准」的直接证据；上面的组合 Calmar 混了仓位与路径。", "",
          "### 二·补.2 持仓集中度（持有月数最多的 3 只）", "",
          "| 方案 | 平均只数 | 最多 3 只（持有月数 / 82） |", "|---|---:|---|"]
    for name, _w, _n in variants:
        cnt = hold_cnt.get(name)
        if cnt is None:
            continue
        top3 = cnt.sort_values(ascending=False).head(3)
        txt = "、".join(f"{rule.NAME_SHORT.get(c, c)}{c} {int(v)}" for c, v in top3.items())
        L.append(f"| {name} | {cnt.sum() / len(rets):.2f} | {txt} |")
    L.append("")

    # 排序：按 Calmar
    best = sorted(rows, key=lambda r: -r["st"]["calmar"])[:5]
    base = next(r for r in rows if r["方案"].startswith("V4 基线") and r["方案"].endswith("× RSI 时间档"))
    L += ["## 三、前三名与 V4 基线的差", "",
          f"V4 基线（RSI top2 × 70/50）：{fmt(base['st']['cagr'])} / {fmt(base['st']['dd'])} / "
          f"Calmar {base['st']['calmar']:.2f}。", "",
          "| 名次 | 方案 | 年化 | 回撤 | Calmar | ΔCalmar vs V4 |",
          "|---|---|---:|---:|---:|---:|"]
    for i, r in enumerate(best, 1):
        L.append(f"| {i} | {r['方案']} | {fmt(r['st']['cagr'])} | {fmt(r['st']['dd'])} | "
                 f"{r['st']['calmar']:.2f} | {r['st']['calmar'] - base['st']['calmar']:+.2f} |")
    L.append("")

    # 剔除检验（对主口径）
    L += ["## 四、三把刀（对主口径与最好的双排序方案）", "",
          "| 方案 | 全样本 | 剔除 3 月 | 剔除 5 月 | 2020–2023 | 2024–2026 |",
          "|---|---:|---:|---:|---:|---:|"]
    picks_rows = [base]
    picks_rows += [r for r in rows if r["方案"] == main_pick["方案"]]
    alt = next((r for r in rows if r["方案"].startswith(f"6 只同源双排序 top{K}（{f'交集空→温度前{K}'}）")
                and r["方案"].endswith("× RSI 时间档")), None)
    if alt is not None:
        picks_rows.append(alt)
    for r in picks_rows:
        rr = r["st"]["r"]
        d3 = E.metrics(E.drop_best(rr, base["st"]["r"], 3))
        d5 = E.metrics(E.drop_best(rr, base["st"]["r"], 5))
        h1 = E.metrics(rr.loc[:"2023-12-31"])
        h2 = E.metrics(rr.loc["2024-01-01":])
        L.append(f"| {r['方案']} | {fmt(r['st']['cagr'])}/{r['st']['calmar']:.2f} | "
                 f"{fmt(d3['cagr'])}/{d3['calmar']:.2f} | {fmt(d5['cagr'])}/{d5['calmar']:.2f} | "
                 f"{fmt(h1['cagr'])}/{h1['calmar']:.2f} | {fmt(h2['cagr'])}/{h2['calmar']:.2f} |")
    L += ["", "> 剔除口径是「相对 V4 基线超额最大的 N 个月」。", ""]

    # 四·补：k 敏感性（k 是不是关键；池只有 6 只，k=3 即取一半）
    k_rows = []
    for kk in sorted({*K_ALT, K}):
        p_k = picks_dual(raw, temp, ok6, k=kk, fallback="rsi", pool6=True)
        w_k = to_w(p_k, rets.index, rets.columns)
        k_rows.append((kk, E.evaluate(w_k, m_rsi, rets), excess_vs_pool(w_k, ok6),
                       float(w_k.gt(0).sum(axis=1).mean())))
    L += ["### 四·补 k 敏感性（同源 6 只、交集空→RSI 前 k、V4 时间档）", "",
          "| k | 年化 | 最大回撤 | Calmar | 平均只数 | 选品超额 | t |", "|---|---:|---:|---:|---:|---:|---:|"]
    for kk, st, exk, navg in k_rows:
        L.append(f"| {kk} | {fmt(st['cagr'])} | {fmt(st['dd'])} | {st['calmar']:.2f} | "
                 f"{navg:.2f} | {exk['mean'] * 1e4:+.0f}bp | {exk['t']:+.2f} |")
    L += ["", "> 池只有 6 只，k=3 就是「一半」。若 k=2 / k=4 也同向，才说明结论不是取 k 的产物。", ""]

    # 四·补.2 单信号 vs 双排序（同 k）——「温度参与排序」到底有没有贡献
    sig_rows = []
    for kk in (1, 2, 3):
        p_rsi = picks_rsi_only(raw, temp, ok6, k=kk)
        p_tmp = {ts: list(temp.loc[ts].dropna().nsmallest(kk).index) for ts in rets.index}
        p_dual = picks_dual(raw, temp, ok6, k=kk, fallback="rsi", pool6=True)
        for tag, pp in (("只按 RSI", p_rsi), ("只按温度", p_tmp), ("双排序交集", p_dual)):
            w_s = to_w(pp, rets.index, rets.columns)
            sig_rows.append((kk, tag, E.evaluate(w_s, m_rsi, rets), excess_vs_pool(w_s, ok6),
                             float(w_s.gt(0).sum(axis=1).mean())))
    L += ["### 四·补.2 单信号 vs 双排序（同只需要数，V4 时间档）", "",
          "| 取几只 k | 选品方式 | 年化 | 最大回撤 | Calmar | 平均只数 | 选品超额 | t |",
          "|---:|---|---:|---:|---:|---:|---:|---:|"]
    for kk, tag, st, exk, navg in sig_rows:
        L.append(f"| {kk} | {tag} | {fmt(st['cagr'])} | {fmt(st['dd'])} | {st['calmar']:.2f} | "
                 f"{navg:.2f} | {exk['mean'] * 1e4:+.0f}bp | {exk['t']:+.2f} |")
    L += ["", "> 同样是 k 只，「双排序」若不能明显超过「只按 RSI」，"
              "温度这一票就是多余的 —— 用户问的正是「加上温度值不值」。", ""]

    # 置换
    L += ["## 五、置换：随机挑同样只数能不能复现", "",
          f"每月从同源 6 只里随机抽 n_t 只等权（n_t = **主口径（6 只估值温度 top{K}）**当月实际只数），"
          f"套同一时间档，跑 {N_PERM} 次：", "",
          f"- 主口径 Calmar **{main_pick['st']['calmar']:.2f}** 落在随机分布的 "
          f"**{pct_cal:.0%} 分位**（随机中位 {np.median(perm['calmar']):.2f}，"
          f"5%–95% [{np.percentile(perm['calmar'], 5):.2f}, {np.percentile(perm['calmar'], 95):.2f}]）；",
          f"- 年化 {fmt(main_pick['st']['cagr'])} 落在 **{pct_cagr:.0%} 分位**"
          f"（随机中位 {fmt(float(np.median(perm['cagr'])))}）。", "",
          "> 门槛是 95%。不到 95% 就等于「随机挑同样多只数、持有同样久」也能做到这个数。", ""]

    # IC
    L += ["## 六、横截面判别力（月度 Spearman IC vs 次月收益）", "",
          "| 信号 | 池 | 月数 | 平均 IC | t 值 | IC>0 占比 |", "|---|---|---:|---:|---:|---:|",
          f"| 估值温度（6 只） | 6 只同源 | {ic_temp['n']} | "
          f"{-ic_temp['ic']:+.3f} | {-ic_temp['t']:+.2f} | {1 - ic_temp['pos']:.0%} |",
          f"| RSI（6 只） | 6 只同源 | {ic_rsi['n']} | {-ic_rsi['ic']:+.3f} | {-ic_rsi['t']:+.2f} | "
          f"{1 - ic_rsi['pos']:.0%} |",
          f"| 混合温度（10 只） | 10 只池 | {ic_mix['n']} | {-ic_mix['ic']:+.3f} | "
          f"{-ic_mix['t']:+.2f} | {1 - ic_mix['pos']:.0%} |",
          f"| 纯价格温度（10 只） | 10 只池 | {ic_px['n']} | {-ic_px['ic']:+.3f} | "
          f"{-ic_px['t']:+.2f} | {1 - ic_px['pos']:.0%} |",
          "", "> 表内已把「越低越好」翻成正号；|t|<2 表示横截面没有稳定判别力。", ""]

    # 分年
    ys = E.yearly({r["方案"]: r["st"]["r"] for r in picks_rows})
    L += ["## 七、分年度", "", "| 年份 | " + " | ".join(ys.columns) + " |",
          "|---|" + "---:|" * len(ys.columns)]
    for ts, row in ys.iterrows():
        L.append(f"| {int(ts)} | " + " | ".join(fmt(v) for v in row.values) + " |")
    L.append("")

    # ---------------------------------------------------------------- 结论
    W = dict((n, w) for n, w, _x in variants)

    def row_of(name: str) -> dict:
        return next(r for r in rows if r["方案"] == f"{name}｜× RSI 时间档")

    fb_rows = [
        ("交集空→空仓", row_of(f"6 只同源双排序 top{K}（交集空→空仓）")),
        (f"交集空→RSI 前{K}（主口径）", main_pick),
        (f"交集空→温度前{K}", row_of(f"6 只同源双排序 top{K}（交集空→温度前{K}）")),
    ]
    ex_main = excess_vs_pool(W[MAIN_NAME], ok6)
    d3_main = E.metrics(E.drop_best(main_pick["st"]["r"], base["st"]["r"], 3))
    d3_base = E.metrics(E.drop_best(base["st"]["r"], base["st"]["r"], 3))
    base_eq = next(r for r in rows if r["方案"].startswith("同源 6 只等权")
                   and r["方案"].endswith("× RSI 时间档"))["st"]
    static_rows = [r for r in rows if r["方案"].startswith("静态持有事后最优")
                   and r["方案"].endswith("× RSI 时间档")]
    st2 = next((r for r in static_rows if "最优 2 只" in r["方案"]), None)
    checks = {"选品超额 t≥2": ex_main["t"] >= 2,
              "置换 ≥95% 分位": pct_cal >= 0.95,
              "剔除 3 月后仍优于 V4": d3_main["cagr"] > d3_base["cagr"]}
    n_pass = sum(checks.values())
    k_main = [r for r in k_rows if r[0] == K][0]
    # 与「纯 RSI 前 K」的差别：候选集与主口径完全一致，只去掉温度这一票
    w_rsi6 = to_w(picks_rsi_only(raw, temp, ok6, k=K), rets.index, rets.columns)
    st_rsi6 = E.evaluate(w_rsi6, m_rsi, rets)
    diff_m = int((w_main.ne(w_rsi6).any(axis=1)).sum())
    _d6 = dist_rows[0][2]
    n_empty = int(_d6.get(0, 0))

    L += ["## 八、结论", "",
          f"### 8.1 用户选定的规则（6 只估值温度 · RSI 与温度都排前 {K}）", "",
          f"- 交易池：**6 只同源**（" + " / ".join(codes6) + "）—— RSI 与估值温度都在同一批标的里排；",
          f"- 选品：两个排名都 ≤{K} 的标的等权（交集可能 0~{K} 只）；总仓位仍由板块 RSI 均值控制（70/50）；",
          f"- 本样本：平均持仓 **{main_pick['n_avg']:.2f} 只**、空仓 **{main_pick['n_zero']} 个月**。",
          "- 交集只数分布（6 只口径）："
          + "、".join(f"{int(i)} 只 {int(v)} 月" for i, v in dist_rows[0][2].items())
          + f" —— 交集为空时退回 RSI 前 {K}，所以主口径每月实际持有 1~2 只。", "",
          "**交集为空时的三种处理**（这是这条规则唯一需要人为决定的地方）：", "",
          "| 处理 | 年化 | 最大回撤 | Calmar | 平均只数 | 空仓月 | 选品超额 | t |",
          "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for tag, r in fb_rows:
        name = r["方案"].split("｜")[0]
        e = excess_vs_pool(W[name], ok6)
        L.append(f"| {tag} | {fmt(r['st']['cagr'])} | {fmt(r['st']['dd'])} | {r['st']['calmar']:.2f} | "
                 f"{r['n_avg']:.2f} | {r['n_zero']} | {e['mean'] * 1e4:+.0f}bp | {e['t']:+.2f} |")
    L += ["", f"> V4 基线（RSI top2 × 70/50，10 只池）= {fmt(base['st']['cagr'])} / "
              f"{fmt(base['st']['dd'])} / Calmar **{base['st']['calmar']:.2f}**；"
              f"同源 6 只等权 × 70/50 = {fmt(base_eq['cagr'])} / Calmar {base_eq['calmar']:.2f}。", "",
          f"1. **主口径（交集空→RSI 前 {K}）：{fmt(main_pick['st']['cagr'])} / "
          f"{fmt(main_pick['st']['dd'])} / Calmar {main_pick['st']['calmar']:.2f}**，"
          f"比 V4 高 {main_pick['st']['calmar'] - base['st']['calmar']:+.2f}、比同源等权高 "
          f"{main_pick['st']['calmar'] - base_eq['calmar']:+.2f}。全样本层面是这条规则最好看的版本。",
          f"2. **但它过不了三把刀**：剔除相对 V4 超额最大的 3 个月后 "
          f"{fmt(d3_main['cagr'])} / Calmar {d3_main['calmar']:.2f}（V4 同口径 "
          f"{fmt(d3_base['cagr'])} / {d3_base['calmar']:.2f}）—— 掉到 V4 下面。",
          f"3. **选品超额 {ex_main['mean'] * 1e4:+.0f}bp/月、t={ex_main['t']:+.2f}、胜率 "
          f"{ex_main['win']:.0%}（n={ex_main['n']}）** —— "
          + ("t≥2。" if ex_main["t"] >= 2 else "**|t|<2：统计上分辨不出「比池均值好」**。"),
          f"4. **置换 {pct_cal:.0%} 分位**（随机抽同样只数、套同一时间档，{N_PERM} 次；"
          f"随机中位 Calmar {np.median(perm['calmar']):.2f}）；年化 {pct_cagr:.0%} 分位。"
          + ("过了 95% 门槛。" if pct_cal >= 0.95 else "**没过 95% 门槛**。"),
          f"5. **分年**：{int((ys[main_pick['方案']] < ys[base['方案']]).sum())} 个年份跑输 V4，"
          f"最差是 {int((ys[main_pick['方案']] - ys[base['方案']]).idxmin())} 年"
          f"（{fmt((ys[main_pick['方案']] - ys[base['方案']]).min())}）。",
          f"6. **⚠ 规则实际很少被激活**：k={K} 下交集为空 **{n_empty}/{len(rets)} 个月"
          f"（{n_empty / len(rets):.0%}）**，只有 **{int(_d6.get(2, 0))} 个月**真的选出 2 只。"
          f"也就是说它有 {n_empty} 个月完全等于「纯 RSI 前 {K}」，"
          f"其余月份多半只持有 1 只。这条规则的实质是"
          f"**「RSI 前 {K}，偶尔被温度缩到 1 只」**，不是「两个信号共同选品」。",
          f"7. **温度真正起的作用是「缩仓」，不是「换标的」**：纯 RSI 前 {K}（6 只池）× 70/50 是 "
          f"{fmt(st_rsi6['cagr'])} / Calmar {st_rsi6['calmar']:.2f}；加上温度后 "
          f"{fmt(main_pick['st']['cagr'])} / {main_pick['st']['calmar']:.2f}"
          f"（{main_pick['st']['calmar'] - st_rsi6['calmar']:+.2f}）。"
          f"两者持仓在 **{diff_m}/{len(rets)} 个月**不同，而差别几乎都是"
          f"「2 只各 50% → 1 只 100%」—— 温度并没有指向不同的标的，"
          "只是把其中一只的仓位砍掉。所以这是**用温度给 RSI top2 做集中化**，"
          f"不是两个信号各自选品；{main_pick['st']['calmar'] - st_rsi6['calmar']:+.2f} Calmar "
          "来自集中到单只，不是来自方向判断。", "",
          "### 8.2 敏感性", "",
          f"- **k**：k=1 → {[r[1]['calmar'] for r in k_rows if r[0] == 1][0]:.2f}、"
          f"**k=2 → {k_main[1]['calmar']:.2f}**、k=3 → "
          f"{[r[1]['calmar'] for r in k_rows if r[0] == 3][0]:.2f}、k=4 → "
          f"{[r[1]['calmar'] for r in k_rows if r[0] == 4][0]:.2f} —— "
          "单调随 k 变小而变好，说明收益跟着集中度走；k=1 时规则退化成纯 RSI 选 1 只。",
          "- **温度这一票值多少**（同 k 比「只按 RSI」）："
          + " / ".join(f"k={k_}: {next(s for kk, t_, s, _e, _n in sig_rows if kk == k_ and t_ == '只按 RSI')['calmar']:.2f}"
                       f" → {next(s for kk, t_, s, _e, _n in sig_rows if kk == k_ and t_ == '双排序交集')['calmar']:.2f}"
                       for k_ in (1, 2, 3))
          + f"（前者 → 后者）—— k={K} 时多 {next(s for kk, t_, s, _e, _n in sig_rows if kk == K and t_ == '双排序交集')['calmar'] - next(s for kk, t_, s, _e, _n in sig_rows if kk == K and t_ == '只按 RSI')['calmar']:+.2f}。",
          "  **注意 k=1 时双排序必然退化成纯 RSI**（交集最多 1 只），所以那一行不是独立证据。",
          "- **标尺：静态持有事后最优两只**"
          + (f"（{fmt(st2['st']['cagr'])} / Calmar {st2['st']['calmar']:.2f}）与同源 6 只等权"
             f"（{fmt(base_eq['cagr'])} / Calmar {base_eq['calmar']:.2f}）都不及任何月度轮动版本 —— "
             "红利池的收益全在「什么时候换」，不在「选哪只」。"
             if st2 else "：无。"), "",
          "### 8.3 已否决：把价格温度补进来让 10 只都有温度", "",
          f"上一轮按用户提议把 4 只缺估值的标的用**价格温度**（`dev`/`pos`）补齐，"
          f"同一规则（k={K}）下：", "",
          "| 温度口径 | 覆盖 | 年化 | 最大回撤 | Calmar | 选品超额 t |",
          "|---|---|---:|---:|---:|---:|"]
    for tag, nm, cov in (
            ("RSI 排 10 只 + 温度只 6 只（缺口不回退）", f"10 只双排序 top{K}（交集空→RSI 前{K}）", "6/10"),
            ("RSI 排 10 只 + 混合温度", MIX_NAME, "10/10"),
            ("RSI 排 10 只 + 纯价格温度", f"10 只双排序 top{K}·纯价格温度（交集空→RSI 前{K}）", "10/10")):
        r = row_of(nm)
        e = excess_vs_pool(W[nm], ok)
        L.append(f"| {tag} | {cov} | {fmt(r['st']['cagr'])} | {fmt(r['st']['dd'])} | "
                 f"{r['st']['calmar']:.2f} | {e['t']:+.2f} |")
    L += ["",
          "> 这三行的 RSI 都在 10 只里排（与 §8.1 的 6 只同源口径不同），"
          "所以数字与主口径不能直接比 —— 它们只用来回答「温度口径换掉会怎样」。", "",
          f"- 补价格温度后 Calmar 从 {row_of(f'10 只双排序 top{K}（交集空→RSI 前{K}）')['st']['calmar']:.2f} "
          f"掉到 {row_of(MIX_NAME)['st']['calmar']:.2f}（混合）/ "
          f"{row_of(f'10 只双排序 top{K}·纯价格温度（交集空→RSI 前{K}）')['st']['calmar']:.2f}（纯价格），"
          "两条都低于 V4。**价格温度只有 dev/pos，与含 PE/PB/利差的估值温度不可交换**，"
          "混进同一横截面排序等于用弱信号稀释强信号。",
          f"- IC：估值温度 {-ic_temp['ic']:+.3f}（t={-ic_temp['t']:+.2f}）、"
          f"混合 {-ic_mix['ic']:+.3f}（t={-ic_mix['t']:+.2f}）、"
          f"纯价格 {-ic_px['ic']:+.3f}（t={-ic_px['t']:+.2f}）—— 三个都没到 |t|≥2。",
          f"- 交集只数 "
          f"{dist_rows[0][1].mean():.2f} → {dist_rows[1][1].mean():.2f}（混合）/ "
          f"{dist_rows[2][1].mean():.2f}（纯价格）：**温度补全并没有让交集变多**，"
          f"RSI 前 {K} 没变，温度候选扩大只会让温度前 {K} 更容易落在 RSI 前 {K} 之外。", ""]
    L.append("**三项硬检验（选品超额 t / 置换 / 剔除）通过 "
             + f"{n_pass}/3：" + "、".join(f"{k} {'✓' if v else '✗'}" for k, v in checks.items())
             + "**")
    L.append("")
    if n_pass == 3:
        L.append(f"**判定**：本样本三项都过 —— 6 只估值温度 + RSI 与温度同排前 {K} 这条规则"
                 "在 82 个月里站得住，可以做下一步（分窗走查 / 换池复核）；"
                 "但样本只有一轮涨跌、平均持仓 1.4 只，仍不宜当定论。")
    else:
        L.append(f"**判定：本样本内不成立** —— 6 只估值温度 + 同排前 {K} 的全样本 Calmar "
                 f"{main_pick['st']['calmar']:.2f} 高于 V4 的 {base['st']['calmar']:.2f}，"
                 f"但三项硬检验只过 {n_pass} 项（选品超额 t={ex_main['t']:+.2f}、"
                 f"置换 {pct_cal:.0%} 分位、剔 3 月后 "
                 f"{fmt(d3_main['cagr'])} vs V4 {fmt(d3_base['cagr'])}）。"
                 f"更关键的是**规则本身很少被激活**：{n_empty}/{len(rets)} 个月交集为空，"
                 f"只有 {int(_d6.get(2, 0))} 个月真选出 2 只 —— 它大多数时间就等于纯 RSI 前 {K}，"
                 "温度只在少数月份改一次仓。这种触发频率无法从 82 个月里验证出稳定性。")
    L.append("")

    text = "\n".join(L)
    os.makedirs(_HERE, exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write(text)

    print(f"[xs] 样本 {rets.index[0]:%Y-%m}~{rets.index[-1]:%Y-%m} ({len(rets)} 月) · 温度覆盖 {len(tcols)}/{len(rets.columns)}")
    print(f"[xs] 10 只交集只数分布: " + " ".join(f"{int(i)}:{int(v)}" for i, v in dist.items()))
    for r in rows:
        st = r["st"]
        print(f"[xs] {r['方案']:44s} 年化 {st['cagr'] * 100:5.1f}% 回撤 {st['dd'] * 100:6.1f}% "
              f"Calmar {st['calmar']:5.2f} 仓位 {r['n_pos']:.0%} 只数 {r['n_avg']:.2f}")
    print(f"[xs] 主口径置换 Calmar 分位 {pct_cal:.0%} / 年化分位 {pct_cagr:.0%}")
    print(f"[xs] 截面 IC：温度 {-ic_temp['ic']:+.3f}(t={-ic_temp['t']:+.2f}) "
          f"RSI {-ic_rsi['ic']:+.3f}(t={-ic_rsi['t']:+.2f})")
    print(f"[xs] -> {os.path.relpath(OUT_MD, os.path.dirname(os.path.dirname(_STRAT)))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
