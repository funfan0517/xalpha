# -*- coding: utf-8 -*-
"""成长/红利风格轮动 v2 · 指标计算（比值 R / 区间分类 / 上行周期标注）

纯函数，不触碰参数（参数仅在 rule.py）；回测与画图共用。
"""
import numpy as np
import pandas as pd

import rule


def ratio_series(growth_close, div_close):
    """风格比值 R_t = 创业板指收盘 / 中证红利收盘（双列非缺失交集对齐后逐日）。"""
    g = growth_close.astype(float)
    d = div_close.astype(float)
    both = pd.concat([g, d], axis=1).dropna()
    both.columns = ["g", "d"]
    return both["g"] / both["d"]


def classify(r, low=None, high=None):
    """R 分段标签：'low'(≤阈值下沿=低估加仓) / 'high'(≥阈值上沿=高估减仓) / 'mid'(不动作)。"""
    low = rule.RATIO_LOW if low is None else low
    high = rule.RATIO_HIGH if high is None else high
    lab = pd.Series("mid", index=r.index, dtype=object)
    lab[r <= low] = "low"
    lab[r >= high] = "high"
    return lab


def rolling_rank(r, window=None):
    """R 在过去 window 日内的分位（当前值 ≤ 窗口内比例），用于无前视对照版。"""
    window = rule.RANK_WINDOW if window is None else window

    def _rank(x):
        x = np.asarray(x, dtype=float)
        return float((x <= x[-1]).mean()) if len(x) else np.nan

    return r.rolling(window).apply(_rank, raw=True)


def zigzag_turning_points(r, rel=None):
    """比值 R 的 zigzag 转折点（事后标注，非实时可得）。

    返回 [(日期, 值, 'trough'/'peak'), ...]，按时间升序。rel=反转阈值（默认 rule.ZIGZAG_REL）。
    只用于复现图 2 的黄色竖带（创业板相对走强的上行周期），**不参与交易信号**。
    """
    rel = rule.ZIGZAG_REL if rel is None else rel
    vals = r.values.astype(float)
    idx = r.index
    n = len(vals)
    if n < 2:
        return []
    piv = []
    direction = 0                       # +1 上行段 / -1 下行段 / 0 未定
    hi_i = lo_i = 0                     # 当前段内的最高/最低点位置
    for i in range(1, n):
        v = vals[i]
        if direction >= 0:              # 找高点（未定时同时跟踪高低两端）
            if v > vals[hi_i]:
                hi_i = i
            if v <= vals[hi_i] * (1 - rel):
                piv.append((idx[hi_i], float(vals[hi_i]), "peak"))
                direction = -1
                lo_i = i
        if direction <= 0:              # 找低点
            if v < vals[lo_i]:
                lo_i = i
            if v >= vals[lo_i] * (1 + rel):
                piv.append((idx[lo_i], float(vals[lo_i]), "trough"))
                direction = 1
                hi_i = i
    # 收尾：最后一段尚未反转，把当前极值也标出来（画图需要末端标记）
    if direction >= 0:
        piv.append((idx[hi_i], float(vals[hi_i]), "peak"))
    else:
        piv.append((idx[lo_i], float(vals[lo_i]), "trough"))
    return piv


def up_cycles(r, start_max=None, end_min=None, drop=None, merge_gap=None):
    """上行周期区间 [(起点, 终点), ...] —— 供图 2 的黄色竖带着色。

    定义（一次完整的风格轮动周期）：
      起点 = 「低位片区」里的最低点（R 回到低估区附近徘徊的那一片，含 0.30×1.15 的上沿）；
             相邻低位段间隔 < merge_gap 个交易日视为同一片，取全片最低点
      终点 = 之后首次 R ≥ end_min（触到高估线）；若整段都没触到，则取「见顶后回撤 drop」的峰位

    ⚠ 与 `zigzag_turning_points` 一样是**事后标注**（终点要等未来数据确认），只用于画图叙事。
    """
    start_max = rule.CYCLE_START_MAX if start_max is None else start_max
    end_min = rule.CYCLE_END_MIN if end_min is None else end_min
    drop = rule.CYCLE_FALLBACK_DROP if drop is None else drop
    merge_gap = rule.CYCLE_MERGE_GAP if merge_gap is None else merge_gap

    vals = r.values.astype(float)
    idx = r.index
    n = len(vals)
    bands = []
    i = 0
    while i < n:
        if vals[i] > start_max:
            i += 1
            continue
        # 1) 低位片区：合并间隔过近的低位段，取全片最低点
        lo = i
        last_low = i
        k = i
        while k < n:
            if vals[k] <= start_max:
                if vals[k] < vals[lo]:
                    lo = k
                last_low = k
                k += 1
                continue
            nxt = k
            while nxt < n and vals[nxt] > start_max:
                nxt += 1
            if nxt < n and (nxt - last_low) < merge_gap:
                k = nxt
            else:
                break
        # 2) 终点：首次触及高估线，或见顶回撤 drop
        peak, peak_pos, end = vals[lo], lo, None
        for k in range(lo + 1, n):
            if vals[k] > peak:
                peak, peak_pos = vals[k], k
            if vals[k] >= end_min:
                end = k
                break
            if vals[k] <= peak * (1 - drop):
                end = peak_pos
                break
        if end is None:
            end = n - 1
        bands.append((idx[lo], idx[end]))
        i = max(end, lo) + 1
    return bands


def extreme_marks(r, low=None, high=None, min_gap=400, tol=None):
    """低估/高估区段内的极值点（复现图 2 的绿圈/红圈）。

    每个连续区段取一次极值（低估取最低、高估取最高）；同标签相邻区段若间隔
    < `min_gap` 个交易日则合并，只保留更极端的那个 —— 否则 2010-2013 年在 0.30
    附近反复穿越会标出十几个圈，把图糊死。

    `tol` 是**标注容差**（默认 rule.MARK_TOL）：原图把「靠近 0.30 但没跌破」的
    2019-01(0.306)、2024-08(0.304) 也标了绿圈。这里按 R ≤ low×tol 放宽，
    **只影响画圈，不影响交易信号**（信号仍用严格阈值）。
    返回 dict(low=[(日期, 值)], high=[(日期, 值)])，各自按时间升序。
    """
    low = rule.RATIO_LOW if low is None else low
    high = rule.RATIO_HIGH if high is None else high
    tol = rule.MARK_TOL if tol is None else tol
    lab = classify(r, low * tol, high / tol).values
    vals = r.values.astype(float)
    idx = r.index
    n = len(vals)

    segs = []
    i = 0
    while i < n:
        if lab[i] == "mid":
            i += 1
            continue
        j = i
        while j + 1 < n and lab[j + 1] == lab[i]:
            j += 1
        ext = (int(np.argmin(vals[i:j + 1])) if lab[i] == "low"
               else int(np.argmax(vals[i:j + 1]))) + i
        segs.append((lab[i], i, j, ext))
        i = j + 1

    merged = {"low": [], "high": []}
    for tag, i, j, ext in segs:
        lst = merged[tag]
        # 与「上一段的**终点**」比间隔：用起点比会在段本身很长时误判为「离得远」
        if lst and (i - lst[-1][2]) < min_gap:
            _t, pi, _pj, pext = lst.pop()
            keep = pext if ((vals[pext] <= vals[ext]) if tag == "low"
                            else (vals[pext] >= vals[ext])) else ext
            lst.append((tag, pi, j, keep))          # 区间合并，极值取更极端者
        else:
            lst.append((tag, i, j, ext))
    return {tag: [(idx[ext], float(vals[ext])) for _t, _i, _j, ext in lst]
            for tag, lst in merged.items()}
