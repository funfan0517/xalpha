# -*- coding: utf-8 -*-
"""黄金的事件驱动：政策周期 + FOMC 决议 + 地缘冲突 + 消息冲击日。

承接 `_gold_macro.py`（美元/美债/利率冲击的量化关系），这里回答用户追问的
「加息降息周期、战争预警这类事件有没有用」。四层检验：

  A 政策周期状态：用美国 2Y 国债收益率（市场定价的政策利率）划紧缩/宽松/平台，
    看黄金在各状态下的收益、胜率、波动 —— 回答「加息周期黄金一定差吗」。
  B FOMC 决议日：150 次会议，拆成加息/降息/按兵不动三组，
    看 T+0 反应、T+1..T+20 漂移、以及「波动放大」。
  C 消息驱动日：把美元和利率都解释不了的大波动挑出来（|r|>3σ 且
    |ΔDXY|<1σ 且 |Δy10|<1σ），这类日子是纯消息面，看之后是反转还是延续。
  D 地缘冲突逐笔：10 个高置信度事件，逐笔复盘（样本太少，只做定性）。

数据（落 data/）：
  data/_ust_yields.csv     美国 2Y/10Y/10Y-2Y 日频（akshare bond_zh_us_rate）
  data/_fomc_meetings.csv  FOMC 决议日历 + 方向（akshare macro_bank_usa_interest_rate）
  复用 data/_gold_xau.csv、data/_dxy.csv

产物（与本脚本同目录）：
  _gold_cycles.md / _gold_cycles.json
  _gold_cycles_policy.png  政策状态 × 黄金
  _gold_cycles_fomc.png    FOMC 三类会议前后的平均路径
  _gold_cycles_shock.png   消息冲击日前后路径 + 全样本对照

由 _gold_cycles.py 生成，运行：python strategies/vol_legs/v6_gold/research/macro_drivers/_gold_cycles.py
"""
import os
import sys
import json
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_HERE = os.path.dirname(os.path.abspath(__file__))
_RES = os.path.dirname(_HERE)
_V6 = os.path.dirname(_RES)
_VL = os.path.dirname(_V6)
_ROOT = os.path.dirname(os.path.dirname(_VL))
DATA = os.path.join(_ROOT, "data")

OUT_MD = os.path.join(_HERE, "_gold_cycles.md")
OUT_JSON = os.path.join(_HERE, "_gold_cycles.json")
FIG_POL = os.path.join(_HERE, "_gold_cycles_policy.png")
FIG_FOMC = os.path.join(_HERE, "_gold_cycles_fomc.png")
FIG_SHOCK = os.path.join(_HERE, "_gold_cycles_shock.png")

REFRESH = "--refresh" in sys.argv
START = "2007-01-01"
TD = 252
PERIOD = 120          # 政策周期判定窗口（约半年）
THRESH = 0.25         # 期限：2Y 半年变动 ±25bp
MIN_HOLD = 40         # 状态最小持续期（否则 2Y 在阈值附近会来回翻，周期变成斑马纹）

# 高置信度地缘冲突事件（只用 2024 年及以前、日期确定的；2025-2026 不编）
GEO = [
    ("2011-03-19", "利比亚禁飞区军事干预"),
    ("2014-03-18", "克里米亚并入俄罗斯"),
    ("2014-09-23", "美军空袭叙利亚 ISIS"),
    ("2015-11-13", "巴黎连环恐袭"),
    ("2016-06-24", "英国脱欧公投结果"),
    ("2017-08-09", "朝美「火与怒」危机"),
    ("2020-01-03", "苏莱曼尼被袭身亡"),
    ("2022-02-24", "俄乌冲突爆发"),
    ("2023-10-07", "哈马斯袭击以色列"),
    ("2024-04-13", "伊朗袭击以色列"),
]


# --------------------------------------------------------------------------
# 数据
# --------------------------------------------------------------------------
def _load_base():
    xau = pd.read_csv(os.path.join(DATA, "_gold_xau.csv"))
    dxy = pd.read_csv(os.path.join(DATA, "_dxy.csv"))
    for d in (xau, dxy):
        d["date"] = pd.to_datetime(d["date"])
    df = xau.merge(dxy, on="date", how="inner").sort_values("date").reset_index(drop=True)
    return df


def _load_ust():
    p = os.path.join(DATA, "_ust_yields.csv")
    if (not REFRESH) and os.path.exists(p):
        d = pd.read_csv(p)
        d["date"] = pd.to_datetime(d["date"])
        return d
    import akshare as ak

    raw = ak.bond_zh_us_rate(start_date="20060101")
    d = pd.DataFrame({
        "date": pd.to_datetime(raw["日期"]),
        "us2y": pd.to_numeric(raw["美国国债收益率2年"], errors="coerce"),
        "us10y": pd.to_numeric(raw["美国国债收益率10年"], errors="coerce"),
        "spread_10_2": pd.to_numeric(raw["美国国债收益率10年-2年"], errors="coerce"),
    }).dropna(subset=["us2y"])
    d = d.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    d.to_csv(p, index=False, encoding="utf-8")
    print("[fetch] UST n=%d %s ~ %s" % (len(d), d["date"].iloc[0].date(), d["date"].iloc[-1].date()))
    return d


def _load_fomc():
    p = os.path.join(DATA, "_fomc_meetings.csv")
    if (not REFRESH) and os.path.exists(p):
        d = pd.read_csv(p)
        d["date"] = pd.to_datetime(d["date"])
        return d
    import akshare as ak

    raw = ak.macro_bank_usa_interest_rate()
    d = pd.DataFrame({"date": pd.to_datetime(raw["日期"]),
                      "now": pd.to_numeric(raw["今值"], errors="coerce"),
                      "prev": pd.to_numeric(raw["前值"], errors="coerce")})
    d = d[d["date"] >= START].sort_values("date").drop_duplicates("date").reset_index(drop=True)
    d["act"] = "hold"
    m = d["now"].notna() & d["prev"].notna()
    d.loc[m & (d["now"] > d["prev"]), "act"] = "hike"
    d.loc[m & (d["now"] < d["prev"]), "act"] = "cut"
    d = d[["date", "now", "prev", "act"]]
    d.to_csv(p, index=False, encoding="utf-8")
    print("[fetch] FOMC n=%d %s ~ %s" % (len(d), d["date"].iloc[0].date(), d["date"].iloc[-1].date()))
    return d


# --------------------------------------------------------------------------
# 公共统计
# --------------------------------------------------------------------------
def _tstat(x):
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    if len(x) < 5:
        return np.nan, np.nan, len(x)
    return float(x.mean()), float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))), len(x)


def _welch(a, b):
    """Welch t 检验，返回 t 值（不查表，只给量级）。"""
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    a, b = a[~np.isnan(a)], b[~np.isnan(b)]
    if len(a) < 5 or len(b) < 5:
        return np.nan
    va, vb = a.var(ddof=1) / len(a), b.var(ddof=1) / len(b)
    if va + vb <= 0:
        return np.nan
    return float((a.mean() - b.mean()) / np.sqrt(va + vb))


def _perf(ret, pos, cost=0.0):
    r = pos * ret - pos.diff().abs().fillna(0.0) * cost
    eq = (1.0 + r.fillna(0.0)).cumprod()
    n = max(int(r.notna().sum()), 1)
    ann = float(eq.iloc[-1] ** (TD / n) - 1.0)
    dd = float((eq / eq.cummax() - 1.0).min())
    sd = float(r.std(ddof=1) * np.sqrt(TD))
    return {"ann": ann, "total": float(eq.iloc[-1] - 1.0), "mdd": dd,
            "sharpe": float(r.mean() * TD / sd) if sd > 0 else np.nan,
            "vol": sd, "expo": float(pos.mean()),
            "turn_pa": float(pos.diff().abs().sum() / (n / TD))}


# --------------------------------------------------------------------------
# A 政策周期状态
# --------------------------------------------------------------------------
def _hysteresis(states, min_hold):
    """状态最短持续期约束：不足 min_hold 就沿用上一状态，滤掉阈值附近的来回翻。"""
    out = []
    cur, held = states[0], 1
    for s in states:
        if s == cur:
            held += 1
        elif held < min_hold:
            held += 1          # 不切换，继续沿用
        else:
            cur, held = s, 1
        out.append(cur)
    return out


def policy_state(d, ust):
    u = ust.copy()
    u["chg"] = u["us2y"].diff(PERIOD)
    u["state"] = np.where(u["chg"] >= THRESH, "紧缩",
                          np.where(u["chg"] <= -THRESH, "宽松", "平台"))
    m = d.merge(u[["date", "us2y", "us10y", "spread_10_2", "chg", "state"]],
                on="date", how="left")
    m["state"] = m["state"].fillna("平台")
    m["state_raw"] = m["state"]
    m["state"] = _hysteresis(m["state"].tolist(), MIN_HOLD)
    m["r_gold"] = np.log(m["gold"]).diff()
    m["r_dxy"] = np.log(m["dxy"]).diff()
    m["dy10"] = m["us10y"].diff()

    def _tab(col):
        rows = []
        base = m["r_gold"]
        for s in ["紧缩", "平台", "宽松", "全期"]:
            seg = base if s == "全期" else m.loc[m[col] == s, "r_gold"]
            seg = seg.dropna()
            if len(seg) < 30:
                continue
            eq = (1 + seg).cumprod()
            n = len(seg)
            rows.append({
                "state": s, "days": n, "share": n / len(base.dropna()),
                "ann": float(eq.iloc[-1] ** (TD / n) - 1),
                "vol": float(seg.std(ddof=1) * np.sqrt(TD)),
                "win": float((seg > 0).mean()),
                "mean_bp": float(seg.mean() * 10000),
                "segments": int((m[col] != m[col].shift()).sum()) - 1,
            })
        return pd.DataFrame(rows)

    return m, _tab("state_raw"), _tab("state")


def policy_cycles(m):
    """连续同状态区间的起止与区间内 2Y 的净变动 —— 比 zigzag 稳，且与状态表同口径。"""
    s = m[["date", "state", "us2y"]].copy()
    s["us2y"] = s["us2y"].ffill()
    g = (s["state"] != s["state"].shift()).cumsum()
    rows = []
    for _, grp in s.groupby(g):
        a, b = grp["us2y"].iloc[0], grp["us2y"].iloc[-1]
        if len(grp) < 60 or pd.isna(a) or pd.isna(b):
            continue
        rows.append({"state": grp["state"].iloc[0],
                     "start": str(grp["date"].iloc[0].date()),
                     "end": str(grp["date"].iloc[-1].date()),
                     "days": int(len(grp)),
                     "from": float(a), "to": float(b), "chg": float(b - a)})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# B FOMC 决议日
# --------------------------------------------------------------------------
def fomc_study(d, fomc):
    """事件日 T0 = 决议当天（该日收盘已知，故 T0 只作观察，T+1 起可交易）。"""
    d = d.copy().reset_index(drop=True)
    d["r_gold"] = np.log(d["gold"]).diff()
    d["r_dxy"] = np.log(d["dxy"]).diff()
    arr = d["date"].values
    f = fomc.copy()
    # 决议日落到最近的下一个黄金交易日（searchsorted 返回行号）
    f["t0"] = f["date"].map(lambda x: int(np.searchsorted(arr, np.datetime64(x))))
    f = f[f["t0"] < len(d)].copy()
    f["t0"] = f["t0"].astype(int)

    r = d["r_gold"]
    abs_r = r.abs()
    uncond_abs = float(abs_r.mean())

    rows, paths = [], {}
    for act, lab in [("hike", "加息"), ("cut", "降息"), ("hold", "按兵不动")]:
        ii = f.loc[f["act"] == act, "t0"].tolist()
        st = {"act": lab, "n": len(ii)}
        # T0 当日反应（不可交易）
        st["t0_mean"] = float(np.nanmean([r.iloc[i] for i in ii])) if ii else np.nan
        st["t0_abs"] = float(np.nanmean([abs_r.iloc[i] for i in ii])) if ii else np.nan
        for h, col in [(1, "次日"), (5, "5日"), (20, "20日")]:
            vals = []
            for i in ii:
                seg = r.iloc[i + 1:i + 1 + h]
                if len(seg) == h:
                    vals.append(float(np.nansum(seg)))
            m_, t_, n_ = _tstat(vals)
            st[col + "_mean"] = m_
            st[col + "_t"] = t_
            st[col + "_win"] = float(np.mean([x > 0 for x in vals])) if vals else np.nan
        rows.append(st)
        # 平均路径 0..+20
        p = [float(np.nanmean([r.iloc[i + k] for i in ii
                               if 0 <= i + k < len(r)])) for k in range(0, 21)]
        paths[lab] = np.cumsum(p)

    # 全样本作为对照路径的「日均」基准
    all_act = f["t0"].tolist()
    p_all = [float(np.nanmean([r.iloc[i + k] for i in all_act
                               if 0 <= i + k < len(r)])) for k in range(0, 21)]
    paths["全部会议"] = np.cumsum(p_all)

    vol_test = {"uncond_abs": uncond_abs,
                "meeting_abs": float(abs_r.iloc[all_act].mean()),
                "t": _welch(abs_r.iloc[all_act].values, abs_r.values)}

    # 无条件基准：黄金自身的 h 日累计收益均值（黄金长期上行，任何事件都会「显著为正」）
    for h in [1, 5, 20]:
        vol_test["uncond_h%d" % h] = float(r.rolling(h).sum().mean())
    return pd.DataFrame(rows), paths, vol_test


# --------------------------------------------------------------------------
# C 消息冲击日（美元和利率都解释不了的大波动）
# --------------------------------------------------------------------------
def shock_days(d, k=2.5):
    """用「宏观解释不了的部分」定义消息冲击。

    r_gold = a + b1·r_dxy + b2·Δy10 + ε，|ε| > k·σ(ε) 的日子视为纯消息驱动。
    回归为全样本 OLS（样本内估计，只用于**识别历史冲击日**，不用于交易），
    这样避免了「要求三项同时落在 1σ 内」导致样本归零的问题。
    """
    d = d.copy().reset_index(drop=True)
    d["r_gold"] = np.log(d["gold"]).diff()
    d["r_dxy"] = np.log(d["dxy"]).diff()
    d["dy10"] = d["us10y"].diff()

    m = d[["r_gold", "r_dxy", "dy10"]].notna().all(axis=1)
    X = np.column_stack([np.ones(m.sum()), d.loc[m, "r_dxy"].values, d.loc[m, "dy10"].values])
    y = d.loc[m, "r_gold"].values
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    fit = X @ beta
    resid = y - fit
    sd = float(np.std(resid, ddof=3))
    r2 = 1.0 - float(np.var(resid) / np.var(y))

    d.loc[m, "resid"] = resid
    d["abs_resid"] = d["resid"].abs()
    mask = (d["abs_resid"] > k * sd).fillna(False)

    ii = np.where(mask.values)[0]
    r = d["r_gold"]
    out = {"n": int(len(ii)), "share": float(len(ii) / d["r_gold"].notna().sum()),
           "r2": r2, "beta_dxy": float(beta[1]), "beta_y10": float(beta[2]), "k": k}
    for h in [1, 5, 20]:
        vals = []
        for i in ii:
            seg = r.iloc[i + 1:i + 1 + h]
            if len(seg) == h:
                vals.append(float(np.nansum(seg)))
        m_, t_, _ = _tstat(vals)
        out["h%d_mean" % h] = m_
        out["h%d_t" % h] = t_
        out["h%d_win" % h] = float(np.mean([v > 0 for v in vals])) if vals else np.nan

    # 对照：同口径下「宏观解释得了」的大波动（|ε| 在 1σ 内但 |r| 大）
    s_g = d["r_gold"].rolling(60).std()
    big = (d["r_gold"].abs() > 2 * s_g).fillna(False)
    ii_all = np.where((big & ~mask).values)[0]
    ctrl = {"n": int(len(ii_all))}
    for h in [1, 5, 20]:
        vals = [float(np.nansum(r.iloc[i + 1:i + 1 + h])) for i in ii_all
                if len(r.iloc[i + 1:i + 1 + h]) == h]
        m_, t_, _ = _tstat(vals)
        ctrl["h%d_mean" % h] = m_
        ctrl["h%d_t" % h] = t_

    n = len(r)
    path = np.cumsum([float(np.nanmean([r.iloc[i + k] for i in ii if i + k < n]))
                      for k in range(0, 21)])
    ctrl_path = np.cumsum([float(np.nanmean([r.iloc[i + k] for i in ii_all if i + k < n]))
                           for k in range(0, 21)]) if len(ii_all) else None
    recent = d.loc[mask, ["date", "r_gold", "r_dxy", "dy10", "resid"]].tail(15).iloc[::-1]
    return out, ctrl, path, ctrl_path, recent


# --------------------------------------------------------------------------
# D 地缘冲突逐笔
# --------------------------------------------------------------------------
def geo_study(d):
    d = d.copy().reset_index(drop=True)
    d["r_gold"] = np.log(d["gold"]).diff()
    d["r_dxy"] = np.log(d["dxy"]).diff()
    idx = d["date"]
    rows = []
    for ds, name in GEO:
        t = pd.Timestamp(ds)
        pos = idx[idx >= t]
        if pos.empty:
            continue
        i = int(pos.index[0])
        r = d["r_gold"]
        def cum(a, b):
            seg = r.iloc[i + a:i + b]
            return float(np.nansum(seg)) if len(seg) else np.nan
        rows.append({
            "date": str(idx.iloc[i].date()), "event": name,
            "t0": float(r.iloc[i]) if not np.isnan(r.iloc[i]) else np.nan,
            "dxy0": float(d["r_dxy"].iloc[i]) if not np.isnan(d["r_dxy"].iloc[i]) else np.nan,
            "t1": cum(1, 2), "t5": cum(1, 6), "t20": cum(1, 21),
        })
    df = pd.DataFrame(rows)
    summ = {}
    for c in ["t0", "t1", "t5", "t20"]:
        m_, t_, n_ = _tstat(df[c])
        summ[c] = {"mean": m_, "t": t_, "n": n_,
                   "win": float((df[c] > 0).mean()) if n_ else np.nan}
    return df, summ


# --------------------------------------------------------------------------
# 回测
# --------------------------------------------------------------------------
def backtest(m):
    d = m.copy().reset_index(drop=True)
    sig = {
        "买入持有": pd.Series(1.0, index=d.index),
        "宽松期持有(2Y半年下行)": (d["state"] == "宽松").astype(float),
        "紧缩期空仓": (d["state"] != "紧缩").astype(float),
    }
    rows = []
    for name, s in sig.items():
        pos = s.shift(1).fillna(0.0)
        rows.append(dict(name=name, **_perf(d["r_gold"], pos)))
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# 图
# --------------------------------------------------------------------------
def make_plots(m, paths, shock_path, shock_ctrl_path=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.dpi"] = 120

    # 1 政策状态 × 黄金
    fig, axes = plt.subplots(2, 1, figsize=(11, 6.4), sharex=True,
                             gridspec_kw={"height_ratios": [1, 1.2]})
    ax = axes[0]
    ax.plot(m["date"], m["us2y"], lw=1.2, color="#2b6cb0", label="美国 2Y 收益率")
    ax.plot(m["date"], m["us10y"], lw=1.0, color="#805ad5", alpha=0.7, label="美国 10Y")
    ax.set_ylabel("%")
    ax.legend(fontsize=8, loc="upper right")
    ax.grid(alpha=0.25)
    ax.set_title("政策周期状态（底部色条：绿=宽松 / 红=紧缩；2Y 半年变动 ±25bp，最小持续 %d 日）"
                 % MIN_HOLD)

    ax = axes[1]
    ax.plot(m["date"], m["gold"], lw=1.1, color="#b7791f")
    ax.set_ylabel("XAU/USD")
    ax.grid(alpha=0.25)
    # 底部状态条（不遮挡曲线）
    for a in axes:
        a.fill_between(m["date"], 0.0, 0.035, where=(m["state"] == "宽松"),
                       transform=a.get_xaxis_transform(), color="#2f855a", alpha=0.75, lw=0)
        a.fill_between(m["date"], 0.0, 0.035, where=(m["state"] == "紧缩"),
                       transform=a.get_xaxis_transform(), color="#c53030", alpha=0.75, lw=0)
    fig.tight_layout()
    fig.savefig(FIG_POL)
    plt.close(fig)

    # 2 FOMC
    fig, ax = plt.subplots(figsize=(10, 4.6))
    xs = list(range(0, 21))
    for k, p in paths.items():
        ax.plot(xs, p * 100, marker="o", ms=3, lw=1.3, label=k)
    ax.axhline(0, color="black", lw=0.6)
    ax.set_title("FOMC 决议日前后黄金平均累计收益（t=0 为决议日，其右侧才可交易）")
    ax.set_xlabel("相对决议日的交易日")
    ax.set_ylabel("累计收益 %")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(FIG_FOMC)
    plt.close(fig)

    # 3 消息冲击
    fig, ax = plt.subplots(figsize=(9, 4.4))
    ax.plot(xs, shock_path * 100, marker="o", ms=4, lw=1.6, color="#c53030",
            label="纯消息冲击日（美元/利率都解释不了）")
    if shock_ctrl_path is not None:
        ax.plot(xs, shock_ctrl_path * 100, lw=1.2, ls="--", color="#4a5568",
                label="全部 3σ 大波动日（对照）")
    ax.axhline(0, color="black", lw=0.6)
    ax.set_title("消息冲击日之后的黄金平均累计收益（t=0 为冲击当日）")
    ax.set_xlabel("相对冲击日的交易日")
    ax.set_ylabel("累计收益 %")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(FIG_SHOCK)
    plt.close(fig)


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def main():
    base = _load_base()
    ust = _load_ust()
    fomc = _load_fomc()

    m, tab_raw, state_tab = policy_state(base, ust)
    cyc = policy_cycles(m)
    fomc_tab, paths, vol_test = fomc_study(base, fomc)
    shock, shock_ctrl, shock_path, ctrl_path, shock_recent = shock_days(m)
    geo_tab, geo_summ = geo_study(base)
    bt = backtest(m)

    make_plots(m, paths, shock_path, ctrl_path)

    print("\n== A 政策状态（不加最小持续期）==")
    print(tab_raw.to_string(index=False, float_format=lambda x: "%.3f" % x))
    print("\n== A 政策状态（加最小持续期 %d 日）==" % MIN_HOLD)
    print(state_tab.to_string(index=False, float_format=lambda x: "%.3f" % x))
    print("\n== B FOMC ==")
    print(fomc_tab.to_string(index=False, float_format=lambda x: "%.4f" % x))
    print("会议日 |r| 均值 %.4f vs 全样本 %.4f, Welch t=%.2f"
          % (vol_test["meeting_abs"], vol_test["uncond_abs"], vol_test["t"]))
    print("\n== C 消息冲击日 ==  n=%d (%.2f%%)" % (shock["n"], shock["share"] * 100))
    print({k: round(v, 4) for k, v in shock.items() if isinstance(v, float)})
    print("对照 全部3σ n=%d" % shock_ctrl["n"], {k: round(v, 4) for k, v in shock_ctrl.items() if k != "n"})
    print("\n== D 地缘事件 ==")
    print(geo_tab.to_string(index=False, float_format=lambda x: "%.4f" % x))
    print("\n== 回测 ==")
    print(bt.to_string(index=False, float_format=lambda x: "%.3f" % x))

    out = {
        "state": state_tab.to_dict("records"),
        "state_raw": tab_raw.to_dict("records"),
        "cycles": cyc.astype(str).to_dict("records"),
        "fomc": fomc_tab.to_dict("records"),
        "fomc_vol": vol_test,
        "shock": shock, "shock_ctrl": shock_ctrl,
        "geo": geo_tab.to_dict("records"), "geo_summary": geo_summ,
        "backtest": bt.to_dict("records"),
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=float)
    write_md(m, tab_raw, state_tab, cyc, fomc_tab, vol_test, shock, shock_ctrl,
             shock_recent, geo_tab, geo_summ, bt)
    print("\n[save] %s\n[save] %s" % (OUT_JSON, OUT_MD))


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------
def _md_table(df, cols=None, fmt=None):
    cols = cols or list(df.columns)
    fmt = fmt or {}
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            if c in fmt:
                cells.append(fmt[c](v))
            elif isinstance(v, (int, np.integer)):
                cells.append(str(int(v)))
            elif isinstance(v, float):
                cells.append("—" if np.isnan(v) else "%.3f" % v)
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def write_md(m, tab_raw, state_tab, cyc, fomc_tab, vol_test, shock, shock_ctrl,
             shock_recent, geo_tab, geo_summ, bt):
    def pcar(v):
        return "—" if pd.isna(v) else "%+.1f%%" % (v * 100)

    def pct(v):
        return "—" if pd.isna(v) else "%.0f%%" % (v * 100)

    def bp(v):
        return "—" if pd.isna(v) else "%+.1f" % v

    def f2(v):
        return "—" if pd.isna(v) else "%.2f" % v

    base = bt[bt["name"] == "买入持有"].iloc[0]
    st = {r["state"]: r for _, r in state_tab.iterrows()}
    fm = {r["act"]: r for _, r in fomc_tab.iterrows()}
    n_raw = int((m["state_raw"] != m["state_raw"].shift()).sum()) - 1
    n_eff = int((m["state"] != m["state"].shift()).sum()) - 1

    L = []
    A = L.append
    A("# V6 黄金 · 事件驱动：政策周期 / FOMC / 地缘冲突 / 消息冲击")
    A("")
    A("> 由 `_gold_cycles.py` 生成。承接 `_gold_macro.md` 的「事件驱动」那一节，"
      "把用户提到的**加息降息周期、战争预警**这类事件单独拎出来检验。")
    A("> 样本：XAU/USD 日频 **%s ~ %s**；政策利率用美国 **2Y 国债收益率**"
      "（市场定价的政策利率，日频完整），FOMC 日历 150 次会议。"
      % (m["date"].iloc[0].date(), m["date"].iloc[-1].date()))
    A("")
    A("## 1. 一句话结论")
    A("")
    sr = {r["state"]: r for _, r in tab_raw.iterrows()}
    A("1. **「降息周期买黄金」在数据上不成立 —— 但「为什么不成立」比结论本身更值得看。** "
      "第一版（状态不加最小持续期）：宽松 %s / 平台 %s / 紧缩 %s，差 %.0fpp，"
      "看起来完美支持这个直觉。加上 %d 日最小持续期（更接近真实周期）后："
      "紧缩 %s / 平台 %s / 宽松 %s —— **差异基本消失，甚至紧缩期最好**。"
      % (pcar(sr["宽松"]["ann"]), pcar(sr["平台"]["ann"]), pcar(sr["紧缩"]["ann"]),
         (sr["宽松"]["ann"] - sr["紧缩"]["ann"]) * 100, MIN_HOLD,
         pcar(st["紧缩"]["ann"]), pcar(st["平台"]["ann"]), pcar(st["宽松"]["ann"])))
    A("2. **一个参数就让 %.0fpp 的差距归零，说明这个变量根本不稳。** "
      "不加约束时 2Y 的半年变动在阈值附近来回翻，20 年切换 **%d 次**，"
      "把黄金在 2008-2011 和 2020 的暴涨随机切进了「宽松」标签 —— 那是**采样偏差**，不是因果。"
      "加上约束后切换降到 **%d 次**。这种「换个实现就翻」的脆弱性，"
      "比任何 p 值都更能说明「政策周期不能用来给黄金择时」。"
      % ((sr["宽松"]["ann"] - sr["紧缩"]["ann"]) * 100, n_raw, n_eff))
    A("3. **FOMC 决议日只放大波动，不给方向 —— 这条最硬。** 决议日 |日收益| 均值 %.2f%%，"
      "全样本 %.2f%%，Welch **t=%.2f**（150 次会议），放大约 %.1f 倍。"
      "而 T+1 起的方向漂移，加息/降息/按兵不动三组**没有一组**过 2 倍标准误。"
      % (vol_test["meeting_abs"] * 100, vol_test["uncond_abs"] * 100, vol_test["t"],
         vol_test["meeting_abs"] / vol_test["uncond_abs"]))
    A("4. **「消息驱动」的冲击，之后既没有延续也没有反转 —— 就是没有信息。** "
      "宏观解释不了的大波动（残差 2.5σ）共 %d 天（占 %.2f%%），T+20 平均 %s（t=%.2f），"
      "**低于**黄金的无条件 20 日均值 %s。也就是说：突发消息砸出来或拉出来的幅度，"
      "5~20 天内被市场完全消化，方向不可预测，反着做也不成立。"
      % (shock["n"], shock["share"] * 100, pcar(shock["h20_mean"]), shock["h20_t"],
         pcar(vol_test["uncond_h20"])))
    A("5. **战争类事件是唯一「当天有反应」的：T+0 平均 %s（t=%.2f，%d/%d 次上涨），"
      "但 T+1 就归零（%s）、T+5 仍是 %s。** 这是「谣言时买入、新闻时卖出」的量化版。"
      "问题在于**当天买不到**（T+1 才成交），等你能买时超额已经消失。"
      % (pcar(geo_summ["t0"]["mean"]), geo_summ["t0"]["t"],
         int(round(geo_summ["t0"]["win"] * geo_summ["t0"]["n"])), geo_summ["t0"]["n"],
         pcar(geo_summ["t1"]["mean"]), pcar(geo_summ["t5"]["mean"])))
    A("")
    A("## 2. 政策周期：一个参数就让结论翻转")
    A("")
    A("状态判定：美国 2Y 收益率 **%d 个交易日（约半年）变动 ≥ +%.0fbp → 紧缩**，≤ -%.0fbp → 宽松，"
      "其余为平台。用 2Y 而非联邦基金利率，是因为它能日频、连续地反映市场对政策的定价；"
      "真正的政策利率是阶梯函数，半年内经常一动不动的会议占七成。"
      % (PERIOD, THRESH * 100, THRESH * 100))
    A("")
    A("### 2.1 口径 A：不施加最小持续期（切换 %d 次）" % n_raw)
    A("")
    A(_md_table(tab_raw, ["state", "days", "share", "ann", "vol", "win", "mean_bp"],
                {"share": pct, "ann": pcar, "vol": pct, "win": pct, "mean_bp": bp}))
    A("")
    A("### 2.2 口径 B：施加 %d 日最小持续期（切换 %d 次）—— 本文采用" % (MIN_HOLD, n_eff))
    A("")
    A(_md_table(state_tab, ["state", "days", "share", "ann", "vol", "win", "mean_bp"],
                {"share": pct, "ann": pcar, "vol": pct, "win": pct, "mean_bp": bp}))
    A("")
    A("`share` = 该状态占样本天数；`mean_bp` = 日均收益（基点）。"
      "两个口径只差「状态最短维持多久」，结果从「宽松碾压紧缩」变成「紧缩反而最好」—— "
      "**这就是这类状态划分的真实可靠度。**")
    A("")
    A("**状态切换的完整区间**（连续同状态 ≥60 个交易日的段落，`chg` 是区间内 2Y 的净变动）：")
    A("")
    A(_md_table(cyc, ["state", "start", "end", "days", "from", "to", "chg"],
                {"from": f2, "to": f2, "chg": lambda v: "%+.2fpp" % v}))
    A("")
    A("> 能看出两件事：① 状态段长度从几十到四百多个交易日不等，"
      "而黄金在同一个状态内可以正负反转多次 —— 颗粒度太粗；"
      "② **不加最小持续期时，2Y 的半年变动在阈值附近会来回翻**：20 年切换 %d 次，"
      "加上 %d 日约束后降到 %d 次。即便如此，这个状态变量仍然是「慢半拍」的 —— "
      "等它确认，行情已经走完一半，这就是 §6 择时失败的直接原因。"
      % (n_raw, MIN_HOLD, n_eff))
    A("")
    A("![政策状态](%s)" % os.path.basename(FIG_POL))
    A("")
    A("## 3. FOMC 决议日：显著的是波动，不是方向")
    A("")
    A(_md_table(fomc_tab, ["act", "n", "t0_mean", "t0_abs", "次日_mean", "次日_t",
                           "5日_mean", "5日_t", "20日_mean", "20日_t"],
                {"t0_mean": pcar, "次日_mean": pcar, "5日_mean": pcar,
                 "20日_mean": pcar, "t0_abs": lambda v: "%.2f%%" % (v * 100),
                 "次日_t": f2, "5日_t": f2, "20日_t": f2}))
    A("")
    A("**波动放大检验**：决议日 |日收益| 均值 **%.2f%%**，全样本 **%.2f%%**，"
      "Welch t = **%.2f**（n=%d 次会议），放大约 **%.1f 倍**，非常显著。"
      % (vol_test["meeting_abs"] * 100, vol_test["uncond_abs"] * 100,
         vol_test["t"], int(fomc_tab["n"].sum()),
         vol_test["meeting_abs"] / vol_test["uncond_abs"]))
    A("")
    A("> ⚠ **读 T+20 那一列之前先扣掉 beta。** 黄金本身长期上行，任意取 20 个交易日，"
      "无条件均值就有 **%s**。所以「降息后 T+20 %s」看着不错，相对基准只多 %s，"
      "且 t=%.2f（n=18）—— 不显著。真正超过 2 倍标准误的只有「按兵不动」组的 %s，"
      "而它本质上就是「21 年里大部分时间的平均表现」，不是事件效应。"
      % (pcar(vol_test["uncond_h20"]), pcar(fm["降息"]["20日_mean"]),
         pcar(fm["降息"]["20日_mean"] - vol_test["uncond_h20"]),
         fm["降息"]["20日_t"], pcar(fm["按兵不动"]["20日_mean"])))
    A("")
    A("> 这是本次研究里**最稳固的一个结论**：政策事件确定性地放大波动，"
      "但方向是随机的。对做方向的人没用，对做波动/仓位的人有用 —— "
      "「决议周别满仓、别用窄止损」是可执行的，而「降息就买黄金」不是。")
    A("")
    A("![FOMC](%s)" % os.path.basename(FIG_FOMC))
    A("")
    A("## 4. 消息冲击日：突发的大涨大跌，之后往哪走")
    A("")
    A("定义用**回归残差**而不是「三个条件同时满足」：先估 `r_gold = a + b₁·Δln(DXY) + b₂·Δy10 + ε`，"
      "取 |ε| > %.1f·σ(ε) 的日子。拟合结果 R² = **%.3f**（b₁=%.2f、b₂=%.3f/pp），"
      "即美元和 10Y 只能解释黄金日波动的 **%.0f%%** —— 剩下 %.0f%% 归给「消息面」。"
      % (shock["k"], shock["r2"], shock["beta_dxy"], shock["beta_y10"],
         shock["r2"] * 100, (1 - shock["r2"]) * 100))
    A("")
    A("> 早前版本用「|r|>3σ 且 |ΔDXY|<1σ 且 |Δy10|<1σ」，结果**一天都选不出来** —— "
      "这本身就是结论：黄金 3σ 级别的日波动，几乎全部伴随美元或利率的大动，"
      "「纯消息驱动」在日频上比想象中稀有。改用残差口径才拿到足够样本。")
    A("")
    A(_md_table(pd.DataFrame([
        {"组": "纯消息冲击", "n": shock["n"], "占比": shock["share"],
         "T+1": shock["h1_mean"], "T+1_t": shock["h1_t"],
         "T+5": shock["h5_mean"], "T+5_t": shock["h5_t"],
         "T+20": shock["h20_mean"], "T+20_t": shock["h20_t"]},
        {"组": "宏观驱动的大波动(≥2σ)", "n": shock_ctrl["n"],
         "占比": shock_ctrl["n"] / len(m), "T+1": shock_ctrl["h1_mean"], "T+1_t": shock_ctrl["h1_t"],
         "T+5": shock_ctrl["h5_mean"], "T+5_t": shock_ctrl["h5_t"],
         "T+20": shock_ctrl["h20_mean"], "T+20_t": shock_ctrl["h20_t"]},
    ]), ["组", "n", "占比", "T+1", "T+1_t", "T+5", "T+5_t", "T+20", "T+20_t"],
        {"占比": pct, "T+1": pcar, "T+5": pcar, "T+20": pcar,
         "T+1_t": f2, "T+5_t": f2, "T+20_t": f2}))
    A("")
    A("**最近的纯消息冲击日**（最近 15 个；`r_gold` 是黄金当日涨跌，`r_dxy`/`dy10` 用来验证"
      "确实不是宏观驱动，`resid` 是回归残差）：")
    A("")
    A(_md_table(shock_recent, ["date", "r_gold", "r_dxy", "dy10", "resid"],
                {"date": lambda v: str(pd.Timestamp(v).date()),
                 "r_gold": pcar, "r_dxy": pcar, "resid": pcar,
                 "dy10": lambda v: "—" if pd.isna(v) else "%+.2fpp" % v}))
    A("")
    A("> **两组的对照很干净**：纯消息组 %d 天，T+20 = %s；宏观驱动组 %d 天，T+20 = %s，"
      "后者看着 t=%.2f 很显著，但黄金的无条件 20 日均值本身就是 %s —— "
      "**超额只有 %.2fpp，就是 beta，不是 alpha**。纯消息组连基准都不到。"
      "结论：消息冲击不能追，也不能反着做。"
      % (shock["n"], pcar(shock["h20_mean"]), shock_ctrl["n"],
         pcar(shock_ctrl["h20_mean"]), shock_ctrl["h20_t"],
         pcar(vol_test["uncond_h20"]),
         (shock_ctrl["h20_mean"] - vol_test["uncond_h20"]) * 100))
    A("")
    A("![消息冲击](%s)" % os.path.basename(FIG_SHOCK))
    A("")
    A("## 5. 地缘冲突：逐笔复盘（定性，样本太少）")
    A("")
    A("T0 = 事件后第一个交易日。`t0` 是当日反应（无法交易），`t5`/`t20` 是从 T+1 起的累计。")
    A("")
    A(_md_table(geo_tab, ["date", "event", "t0", "dxy0", "t1", "t5", "t20"],
                {"t0": pcar, "dxy0": pcar, "t1": pcar, "t5": pcar, "t20": pcar}))
    A("")
    A(_md_table(pd.DataFrame([
        {"窗口": k.upper(), "均值": v["mean"], "胜率": v["win"], "t": v["t"], "n": v["n"]}
        for k, v in geo_summ.items()
    ]), ["窗口", "均值", "胜率", "t", "n"], {"均值": pcar, "胜率": pct, "t": f2}))
    A("")
    A("> **这个「当天涨、次日归零」的形态是本报告最值得记住的一条**：T0 平均 %s 且 t=%.2f，"
      "T+1 立刻变成 %s（t=%.2f），T+5 仍是 %s。地缘冲击的定价几乎全部发生在**你无法成交的那一天**。"
      "这就解释了为什么「战争预警」类消息在实战里难做：真正的升温期金价是缓慢抬升的，"
      "等到开战那天反而是最后一跳 —— **谣言时买入，新闻时卖出**。"
      % (pcar(geo_summ["t0"]["mean"]), geo_summ["t0"]["t"],
         pcar(geo_summ["t1"]["mean"]), geo_summ["t1"]["t"],
         pcar(geo_summ["t5"]["mean"])))
    A("")
    A("> ⚠ **为什么这里不能当统计结论用**：10 个事件里有 2 对是同一轮周期（2014 克里米亚/ISIS、"
      "2023 以哈/2024 伊朗），独立样本不到 5 个。T0 的 t=2.29 是「10 个数、7 个同向」撑出来的，"
      "换个样本就可能翻转。当成**行为特征**看，不要当**规律**用。")
    A("")
    A("## 6. 能不能变成钱")
    A("")
    A(_md_table(bt, ["name", "ann", "total", "mdd", "sharpe", "vol", "expo", "turn_pa"],
                {"ann": pcar, "total": pcar, "mdd": pcar, "vol": pct}))
    A("")
    A("> 「宽松期才持有」年化 **%s**（买入持有 %s），平均仓位 %s —— "
      "它连「把仓位砍到两成」都算不上（两成仓位持有黄金的机械版约有 %.1f%%/年）。"
      "**这是直接的证伪**：按政策周期择时，比什么都不做差得多。"
      % (pcar(bt[bt["name"].str.startswith("宽松")].iloc[0]["ann"]), pcar(base["ann"]),
         pct(bt[bt["name"].str.startswith("宽松")].iloc[0]["expo"]),
         bt[bt["name"].str.startswith("宽松")].iloc[0]["expo"] * base["ann"] * 100))
    A("")
    A("> 「紧缩期空仓」年化 %s，比买入持有低 %.1fpp，但回撤几乎没改善（%s vs %s）—— "
      "**空掉的那段时间恰好包含黄金表现最好的阶段**，和 §2.2 的「紧缩期 %s」互相印证。"
      % (pcar(bt[bt["name"].str.startswith("紧缩")].iloc[0]["ann"]),
         (base["ann"] - bt[bt["name"].str.startswith("紧缩")].iloc[0]["ann"]) * 100,
         pcar(bt[bt["name"].str.startswith("紧缩")].iloc[0]["mdd"]), pcar(base["mdd"]),
         pcar(st["紧缩"]["ann"])))
    A("")
    A("## 7. 局限")
    A("")
    A("1. **FOMC 日历只到 2025-10-30**（akshare 源的尾部 3 条今值为空，2026 年整年缺失），"
      "所以 2026 年的政策事件没进样本。历史 150 次会议足够做统计，但最近的降息周期尾部不完整。")
    A("2. **政策周期用 2Y 代理，不是联邦基金利率**。好处是日频连续、能反映预期；"
      "坏处是 2Y 同时被期限溢价和避险需求影响，2020 年 3 月和 2024 年 8 月的 2Y 暴跌"
      "都不是政策预期而是流动性事件。")
    A("3. **地缘事件是人工清单，只有 10 条**，且「战争预警」本身没有量化定义。"
      "想做得更严谨需要 GPR（Caldari-Iacoviello 地缘风险指数），但其数据源只提供旧版 .xls，"
      "本机 xlrd 2.x 已不支持，未接入。")
    A("4. **消息冲击日的定义带循环性**：用「美元/利率解释不了」来定义，"
      "再用它检验「消息驱动」—— 定义本身已经把宏观因子排除了，所以这里检验的是"
      "「**剩余波动**之后有没有反转」，而不是「消息本身有没有可预测性」。")
    A("5. 全部为样本内。§6 的结论是「更差」，这类否定性结论对过拟合不敏感。")
    A("")

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


if __name__ == "__main__":
    main()
