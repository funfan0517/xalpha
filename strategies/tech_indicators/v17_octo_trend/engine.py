# -*- coding: utf-8 -*-
"""V17 · 八态趋势状态机（指标 → 结构 regime → 八态 → 许可上限 → 仓位 → 净值）。

从 `vol_legs/v12_octo_trend/engine.py` 复制，**判定逻辑一行未改**；相对的改动只有两处，
都写在 `rule.py` 的 docstring 里：标的换成家族 54 只场内 ETF（收盘 + ETF 自身成交量）、
数据来自 `data.frame(code)`。判定流程严格六步，顺序不能变：

  1. 结构（长期层）：三票 `s1 = MA20>MA60` · `s2 = MA60 > MA60[−20]` · `s3 = close > MA250`，
     `S = s1+s2+s3`；
  2. 定 regime：`S ≥ 2` 连续 5 日 → 向上；`S ≤ 1` 连续 5 日 → 向下；确认期内沿用旧 regime，
     暖机段 regime=None（不出状态）；
  3. 向上 regime 内四态（急涨/慢涨/弱回调/强回调）；
  4. 向下 regime 内四态（急跌/慢跌/弱反弹/强反弹）；
  5. 反转确认：强回调连续 2 日 → regime 翻向下；强反弹连续 2 日 → 翻向上（当日状态不变，
     下一日起按新 regime 分态）；
  6. 标注：burst = |z20| ≥ 1.5、near_ph = close ≥ 120 日最高 × 0.97（不参与判定）。

两处**与参考实现逐字对齐**、但读代码容易改错的细节：

- 「弱回调」= `close > MA60` **且** `rvol ≤ 0.85`（双条件缺一不可，规格 §4③）；
- 「强反弹」只认放量 `rvol ≥ 1.2`，**前高不进判定**（规格 §4③：前高单独用样本仅 39~45 天）。

时序：T 日收盘判态、T+1 起承担仓位（`w.shift(1)`），退出用 `w.diff()` 计费 —— 与 v12 同。
"""
import os

import numpy as np
import pandas as pd

import rule


# ---------------------------------------------------------------- 指标
def indicators(df, p=None):
    """-> DataFrame(close / ma_s / ma_m / ma_l / ma_m_ref / z20 / rvol / dd20 / ph /
    near_ph / burst / ready)。`ready` 之前不出状态（暖机 WARMUP 根）。"""
    p = p or rule.params()
    c = df["close"].astype("float64")
    v = df["volume"].astype("float64")
    out = pd.DataFrame(index=df.index)
    out["close"] = c
    out["ma_s"] = c.rolling(int(p["ma_short"])).mean()
    out["ma_m"] = c.rolling(int(p["ma_mid"])).mean()
    out["ma_l"] = c.rolling(int(p["ma_long"])).mean()
    out["ma_m_ref"] = out["ma_m"].shift(int(p["slope_n"]))

    r = c.pct_change()
    sd = r.rolling(20, min_periods=20).std()
    out["z20"] = (c / c.shift(20) - 1.0) / (sd * np.sqrt(20.0))
    out["dd20"] = c / c.rolling(20).max() - 1.0
    vol_ma = v.rolling(20, min_periods=20).mean()
    out["rvol"] = v / vol_ma
    out["ph"] = c.rolling(int(p["ph_n"])).max()
    out["near_ph"] = (c >= out["ph"] * float(p["ph_mult"])) & out["ph"].notna()
    out["burst"] = out["z20"].abs() >= float(p["z_burst"])

    n = len(out)
    out["ready"] = ((np.arange(n) >= rule.WARMUP)
                    & out["ma_l"].notna() & out["ma_m_ref"].notna()
                    & out["z20"].notna() & out["rvol"].notna())
    return out


# ---------------------------------------------------------------- 六步判定
def classify(ind, p=None):
    """-> DataFrame(state / regime / regime_used / vote / z20 / rvol / dd20 / burst / near_ph)。

    `regime`    = 当日收盘后的 regime（翻转日记录的是**新** regime，与参考实现一致）;
    `regime_used` = 当日分态时实际使用的 regime（翻转日 = 旧 regime）。
    两个都保留，日报里能看出「今天状态是旧 regime 给的、明天起按新的算」。
    """
    p = p or rule.params()
    up_min = int(p["up_min"])
    vote_confirm = int(p["vote_confirm"])
    flip_n = int(p["flip_confirm"])
    z_th, rvol_hi, rvol_lo = float(p["z_th"]), float(p["rvol_hi"]), float(p["rvol_lo"])
    sticky = str(p["regime_mode"]) == "sticky"

    c = ind["close"].to_numpy(dtype=float)
    ma_s = ind["ma_s"].to_numpy(dtype=float)
    ma_m = ind["ma_m"].to_numpy(dtype=float)
    ma_l = ind["ma_l"].to_numpy(dtype=float)
    ma_m_ref = ind["ma_m_ref"].to_numpy(dtype=float)
    z20 = ind["z20"].to_numpy(dtype=float)
    rvol = ind["rvol"].to_numpy(dtype=float)
    ready = ind["ready"].to_numpy(dtype=bool)
    n = len(c)

    s1 = ma_s > ma_m
    s2 = ma_m > ma_m_ref
    s3 = c > ma_l
    vote = s1.astype(int) + s2.astype(int) + s3.astype(int)
    up_st = s1 & s2 & s3            # 仅 sticky 模式用
    dn_st = (ma_s < ma_m) & (ma_m < ma_m_ref) & (c < ma_l)

    states = np.array([None] * n, dtype=object)
    regimes = np.array([None] * n, dtype=object)
    used = np.array([None] * n, dtype=object)

    regime = None
    upn = dnn = 0        # 强回调 / 强反弹 的连续计数（第 5 步）
    vup = vdn = 0        # 多数决票的连续计数（第 2 步）

    for i in range(n):
        if not ready[i]:
            used[i] = regime
            regimes[i] = regime
            continue

        # ---- 第 1~2 步：结构 -> regime
        if sticky:
            if regime is None:
                regime = "向上" if up_st[i] else ("向下" if dn_st[i] else None)
            if regime == "向上" and dn_st[i]:
                regime, upn, dnn = "向下", 0, 0
            elif regime == "向下" and up_st[i]:
                regime, upn, dnn = "向上", 0, 0
        else:
            if vote[i] >= up_min:
                vup += 1
                vdn = 0
                if vup >= vote_confirm:
                    regime, upn, dnn = "向上", 0, 0
            else:                                   # S <= up_min-1，即 S<=1
                vdn += 1
                vup = 0
                if vdn >= vote_confirm:
                    regime, upn, dnn = "向下", 0, 0
        if regime is None:
            used[i] = None
            regimes[i] = None
            continue

        # ---- 第 3~4 步：regime 内分四态
        used[i] = regime
        if regime == "向上":
            if c[i] >= ma_s[i]:
                states[i] = "急涨" if z20[i] >= z_th else "慢涨"
                upn = dnn = 0
            else:
                weak = (c[i] > ma_m[i]) and (rvol[i] <= rvol_lo)
                states[i] = "弱回调" if weak else "强回调"
                if weak:
                    dnn = 0
                else:
                    dnn += 1
                    if dnn >= flip_n:               # 第 5 步：强回调连续 N 日 -> 翻向下
                        regime, dnn, upn = "向下", 0, 0
        else:
            if c[i] >= ma_s[i]:
                strong = rvol[i] >= rvol_hi
                states[i] = "强反弹" if strong else "弱反弹"
                if strong:
                    upn += 1
                    if upn >= flip_n:               # 第 5 步：强反弹连续 N 日 -> 翻向上
                        regime, upn, dnn = "向上", 0, 0
                else:
                    upn = 0
            else:
                states[i] = "急跌" if z20[i] <= -z_th else "慢跌"
                upn = dnn = 0

        regimes[i] = regime          # 翻转日记录新 regime（与 state8_final 一致）

    return pd.DataFrame({
        "state": states, "regime": regimes, "regime_used": used, "vote": vote,
        "z20": z20, "rvol": rvol, "dd20": ind["dd20"].to_numpy(dtype=float),
        "burst": ind["burst"].to_numpy(dtype=bool),
        "near_ph": ind["near_ph"].to_numpy(dtype=bool),
    }, index=ind.index)


# ---------------------------------------------------------------- 八态 -> 仓位
def positions(states, up_confirm=None, cap_map=None):
    """八态 -> 仓位序列（0~1 的带内比例）。空仓起步。

    `up_confirm`（默认 `rule.UP_CONFIRM`）：**升格要连续 N 日同向目标**，降格立即生效。
    这是态的边界（z20 穿 ±1.2、量比穿 1.2/0.85）本身的高频抖动与「仓位」之间的缓冲，
    只影响换手，不参与判定。
    """
    nd = rule.UP_CONFIRM if up_confirm is None else int(up_confirm)
    cmap = rule.CAP if cap_map is None else cap_map
    w = np.zeros(len(states))
    cur, pend_val, pend_cnt = 0.0, None, 0
    for i, st in enumerate(states):
        tgt = float(cmap.get(str(st), 0.0)) if st is not None and str(st) in cmap else cur
        if tgt > cur + 1e-9:
            if pend_val is not None and abs(pend_val - tgt) < 1e-9:
                pend_cnt += 1
            else:
                pend_val, pend_cnt = tgt, 1
            if pend_cnt >= max(nd, 1):
                cur, pend_val, pend_cnt = tgt, None, 0
        elif tgt < cur - 1e-9:
            cur, pend_val, pend_cnt = tgt, None, 0       # 降格立即
        else:
            pend_val, pend_cnt = None, 0
        w[i] = cur
    return pd.Series(w, index=states.index if hasattr(states, "index") else None)


def vol_target(df, w, vt):
    """波动率目标覆盖：`w' = min(w, vt / σ20)`。

    `σ20` 用当日收盘可得的日收益标准差（年化），与 `z20`/`rvol` 同窗口；仓位 T+1 才生效，
    所以没有前视。用途是给「年化波动是别人两倍」的腿（科创50）压风险预算，
    **不改判定、只压上限**，证据见 `vol_legs/v12_octo_trend/research/opt_ashare/_opt_ashare.md` §3.2。

    ⚠ 2026-11-19 起这只是**历史口径**（v12 的 `opt_7legs` / `_er_vol_layer` 的 S1 对照）；
    回测/日报走下面的 `vol_layer()`。
    """
    n = int(rule.VOL_WIN)
    sig = df["close"].pct_change().rolling(n, min_periods=n).std() * np.sqrt(rule.TRADING_DAYS)
    # 暖机段没有 σ -> 用 0.05 当下限，使 vt/σ 足够大、不约束仓位（等价于"不覆盖"）
    sig = sig.fillna(0.05).clip(lower=0.05)
    return pd.concat([w, (float(vt) / sig).rename("cap")], axis=1).min(axis=1)


# ---------------------------------------------------------------- 波动层（2026-11-19 · 按腿）
# 实现在这里、配置在 `rule.VOL_LAYER`。两条口径来自 `v10_vol_layer`：
#   "er"    `scale = min(1, TV(ER)/vol20)`，ER(20) 趋势态 TV=20% / 震荡态 10%，带滞回；
#   "fixed" `scale = min(1, TV/vol20)`，TV 常数。
# 两者都再过一道「幅度死区 10pp + 时间死区 5 日」，最后 `w' = min(w, scale)`。
# **不改判定、不加杠杆**：scale ≤ 1，只压上限。
def efficiency_ratio(px, n=None):
    """Kaufman ER(n) = |n 日净变动| / n 日路径总变动。只判趋势纯度，不判涨跌。"""
    n = int(rule.VL_ER_N if n is None else n)
    s = px.astype("float64")
    return (s - s.shift(n)).abs() / s.diff().abs().rolling(n).sum()


def er_state(er, hi=None, lo=None):
    """ER ≥ hi → 趋势态；≤ lo → 震荡态；中间**维持**（滞回，避免阈值附近抖动）。"""
    hi = float(rule.VL_ER_HI if hi is None else hi)
    lo = float(rule.VL_ER_LO if lo is None else lo)
    fast, out = False, []
    for v in er.to_numpy(dtype=float):
        if not np.isnan(v):
            if v >= hi:
                fast = True
            elif v <= lo:
                fast = False
        out.append(fast)
    return pd.Series(out, index=er.index, dtype=bool)


def vol_scale(df, cfg):
    """波动层系数 `min(1, TV(ER)/vol20)`（未过死区）。预热 / vol=0 → 1.0（不约束）。"""
    n = int(rule.VL_VOL_WIN)
    vol = df["close"].pct_change().rolling(n, min_periods=n).std(ddof=1) * np.sqrt(rule.TRADING_DAYS)
    if str(cfg.get("mode")) == "er":
        fast = er_state(efficiency_ratio(df["close"]))
        tv = pd.Series(np.where(fast, float(cfg["tv"]), float(cfg["tv_low"])), index=df.index)
    else:
        tv = pd.Series(float(cfg["tv"]), index=df.index)
    s = (tv / vol).replace([np.inf, -np.inf], np.nan).clip(upper=1.0).fillna(1.0)
    return s.clip(lower=0.0, upper=1.0)


def deadzone(tgt, dead=None, gap=None):
    """幅度死区 + 时间死区：`|新目标−现仓| ≥ dead` 且距上次调仓 ≥ gap 日才动手。

    ⚠ 只能作用在**波动层系数**上，不能作用在合并后的仓位上 —— 八态的许可变化是
    离散跳档，合并后再过死区会把「降格立即生效」也拖慢 gap 日（实测沪深300 0.92→0.83、
    科创50 0.59→0.19）。见 `research/er_vol_layer` §7。
    """
    dead = float(rule.VL_DEAD if dead is None else dead)
    gap = int(rule.VL_DEAD_DAYS if gap is None else gap)
    g = tgt.to_numpy(dtype=float)
    if not len(g):
        return pd.Series(dtype="float64", index=tgt.index)
    out = np.empty(len(g))
    cur, last = float(g[0]), 0
    out[0] = cur
    for i in range(1, len(g)):
        if abs(g[i] - cur) >= dead - 1e-12 and (i - last) >= gap:
            cur, last = float(g[i]), i
        out[i] = cur
    return pd.Series(out, index=tgt.index, dtype="float64")


def vol_coef(df, cfg):
    """波动层系数（**已过死区**），`≤ 1`；不做合并。

    `cfg` = `rule.vol_layer_of(leg)`：`dict(mode="er"|"fixed", tv=..., tv_low=..., dead=..., gap=...)`
    （`dead`/`gap` 可省，默认取 `rule.VL_DEAD` / `VL_DEAD_DAYS`）。
    """
    sc = vol_scale(df, cfg)
    if float(cfg.get("dead", rule.VL_DEAD)) > 0 or int(cfg.get("gap", rule.VL_DEAD_DAYS)) > 0:
        sc = deadzone(sc, cfg.get("dead"), cfg.get("gap"))
    return sc


def vol_layer(df, w, cfg):
    """把波动层装到八态仓位上：`min(八态许可, 死区后的系数)`。"""
    return pd.concat([w, vol_coef(df, cfg).rename("sc")], axis=1).min(axis=1)


# ---------------------------------------------------------------- 净值与指标
def run(df, w):
    px = df["close"]
    ret = px.pct_change().fillna(0.0)
    w_eff = w.shift(1).fillna(0.0)              # T 日收盘判态，T+1 起承担收益
    dw = w.diff().fillna(w).abs()
    cash_d = (1.0 + rule.CASH_ANNUAL) ** (1.0 / rule.TRADING_DAYS) - 1.0
    port = w_eff * ret + (1.0 - w_eff) * cash_d - dw * rule.FEE_SIDE
    return (1.0 + port).cumprod()


def perf(nav):
    nav = nav.dropna()
    ret = nav.pct_change().dropna()
    n = len(ret)
    ann = float((1.0 + ret).prod() ** (rule.TRADING_DAYS / n) - 1.0) if n else np.nan
    vol = float(ret.std() * np.sqrt(rule.TRADING_DAYS)) if n else np.nan
    mdd = float((nav / nav.cummax() - 1.0).min())
    sharpe = float((ann - rule.CASH_ANNUAL) / vol) if vol and vol > 0 else np.nan
    return dict(ann=ann, vol=vol, mdd=mdd, sharpe=sharpe, days=n,
                cum=float(nav.iloc[-1] / nav.iloc[0] - 1.0))


def actions(w):
    """相邻变化 > 1e-9 的调仓点 -> [(date, delta)]。"""
    d = w.diff().fillna(w)
    idx = d.index[d.abs() > 1e-9]
    return [(t, float(d.loc[t])) for t in idx]


# ---------------------------------------------------------------- 后验统计（规格 §3 的复现）
def state_stats(px, cx, horizon=20):
    """逐态后验：占比 / fwd20 / 胜率；外加四组配对分离度。

    用途是**验口径**：把规格 §3 的两张表在本库数据上重算一遍。数字不必逐位相同
    （标的是场内 ETF 前复权、样本长度也不同）。**本变种有一处预期内的偏差**：
    量能用 ETF 自身成交量，而 v12 实测「弱回调 − 强回调」在宽基 ETF 量上会反号
    —— 那一组为负**不是移植错了**，是量能口径的代价（见 `rule.py` 模块 docstring 的 ⚠）；
    其余三组的符号仍应一致。
    """
    fwd = px.shift(-horizon) / px - 1.0
    st = cx["state"]
    m = st.notna()
    rows = []
    for s in rule.STATE_ORDER:
        k = m & (st == s)
        kf = k & fwd.notna()
        if not k.sum():
            continue
        rows.append({
            "state": s, "n": int(k.sum()),
            "share": float(k.sum() / m.sum()),
            "fwd": float(fwd[kf].mean()) if kf.sum() else np.nan,
            "win": float((fwd[kf] > 0).mean()) if kf.sum() else np.nan,
        })
    pairs = []
    for a, b in [("急涨", "慢涨"), ("弱回调", "强回调"), ("强反弹", "弱反弹"), ("急跌", "慢跌")]:
        ka = m & (st == a) & fwd.notna()
        kb = m & (st == b) & fwd.notna()
        if not ka.sum() or not kb.sum():
            continue
        pairs.append({
            "a": a, "b": b,
            "dfwd": float(fwd[ka].mean() - fwd[kb].mean()),
            "dwin": float((fwd[ka] > 0).mean() - (fwd[kb] > 0).mean()),
            "na": int(ka.sum()), "nb": int(kb.sum()),
        })
    return dict(rows=rows, pairs=pairs,
                base=float(fwd[m].mean()), days=int(m.sum()))


# ---------------------------------------------------------------- 单腿回测
def backtest_leg(leg, df=None, up_confirm=None, params=None, opt=None):
    """-> dict(leg, name, df, ind, cx, w, w_spec, w_vol, nav, perf, acts, expo, start, end, bars)。

    `w_vol` = 该腿波动层系数（已过死区，≤1；无波动层则 None），日报里单独印一列，
    便于区分「今天仓位低」是八态压的还是波动层压的。

    `start` = 第一根「出状态」的 bar（暖机之后），净值与统计都从那里起算，
    避免拿暖机段的 0 仓摊薄年化。

    `opt`：None = 跟随 `rule.OPT`；True = 该腿的 `CAP_OPT` + `VOL_LAYER`（v12.1）；
    False = 强制规格口径（`CAP` 原表、无波动层）。`w` 是生效口径，`w_spec` 恒为规格口径（对照用）。
    """
    opt = rule.OPT if opt is None else bool(opt)
    import importlib
    vdata = importlib.import_module("data")
    # 仓库里有 5 个 `data.py`（本策略根 + 各变种）。名字解析到别人身上不会报错，只会**静默换一套行情**，
    # 所以这里 fail-fast：不是本变种的 data.py 就直接抛，别让它跑出"看起来正常"的错误净值。
    if os.path.dirname(os.path.abspath(vdata.__file__)) != os.path.dirname(os.path.abspath(__file__)):
        raise RuntimeError(
            f"`import data` 解析到了 {vdata.__file__}，不是 v17_octo_trend/data.py。"
            f"把本目录插到 sys.path[0]（或先清 sys.modules 里的 'data'）再跑。")
    p = params or rule.params(leg)
    df = vdata.frame(leg) if df is None else df
    ind = indicators(df, p)
    cx = classify(ind, p)
    w_spec = positions(cx["state"], up_confirm=up_confirm, cap_map=rule.CAP)
    w = positions(cx["state"], up_confirm=up_confirm,
                  cap_map=rule.cap_map(leg) if opt else rule.CAP)
    cfg = rule.vol_layer_of(leg) if opt else None
    w_vol = None
    if cfg:
        w_vol = vol_coef(df, cfg)
        w = pd.concat([w, w_vol.rename("sc")], axis=1).min(axis=1)
    first = cx["state"].notna()
    if not first.any():
        return dict(leg=leg, name=rule.NAMES[leg], df=df, ind=ind, cx=cx, w=w, w_spec=w_spec,
                    w_vol=w_vol, nav=None, perf=None, acts=[], expo=np.nan, start=None,
                    end=df.index[-1], bars=0, opt=opt,
                    cap_txt=rule.cap_version(leg) if opt else "规格 CAP")
    st = int(np.argmax(first.to_numpy()))
    nav = run(df, w).loc[df.index[st]:]
    w_st = w.loc[df.index[st]:]
    return dict(leg=leg, name=rule.NAMES[leg], df=df, ind=ind, cx=cx, w=w, w_spec=w_spec, nav=nav,
                w_vol=w_vol, perf=perf(nav), acts=actions(w_st), expo=float(w_st.mean()),
                start=df.index[st], end=df.index[-1], bars=len(nav),
                opt=opt, cap_txt=rule.cap_version(leg) if opt else "规格 CAP")
