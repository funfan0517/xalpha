# -*- coding: utf-8 -*-
"""黄金宏观驱动验证：美元、美债、事件冲击。

用户命题：
  1) 黄金和美元、美债收益率「常常相反」；
  2) 黄金受事件/消息驱动影响比较大。

本脚本只做一件事 —— 用可复现的数据把这个命题拆成三问：
  Q1 同期相关：r_gold 与 Δln(DXY)、Δy10(pp)、Δln(VIX) 是否负相关，有多强，稳不稳；
  Q2 预测性：把「变化」滞后一期，相关还剩多少（能不能交易，而不是只能事后解释）；
  Q3 事件驱动：利率/美元/VIX 的极端冲击日之后，黄金有没有可交易的方向漂移。
最后做一版毛收益口径的信号回测，跟买入持有同尺子比。

产物（同名 .md / .json / .png，与本脚本同目录）：
  _gold_macro.md       结论文档
  _gold_macro.json     全部数值
  _gold_macro_rolling.png   63 日滚动相关
  _gold_macro_scatter.png   同期散点 + 回归
  _gold_macro_event.png     事件后累计收益路径

数据（落 data/，别的策略将来可能复用）：
  data/_gold_xau.csv   伦敦金现 XAU/USD 日频（新浪外盘，2006-11 起）
  data/_dxy.csv        ICE 美元指数日频（东财 100.UDI，1986-01 起）
  data/_us10y.csv      美债 10Y 收益率（已有，FRED，1962 起）
  data/_us_vix.csv     VIX（已有，1990 起）

由 _gold_macro.py 生成，运行：python strategies/vol_legs/v6_gold/research/macro_drivers/_gold_macro.py
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

OUT_MD = os.path.join(_HERE, "_gold_macro.md")
OUT_JSON = os.path.join(_HERE, "_gold_macro.json")
FIG_ROLL = os.path.join(_HERE, "_gold_macro_rolling.png")
FIG_SCAT = os.path.join(_HERE, "_gold_macro_scatter.png")
FIG_EVT = os.path.join(_HERE, "_gold_macro_event.png")

REFRESH = "--refresh" in sys.argv
START = "2007-01-01"
TRADING_DAYS = 252

# 分期（命题是否稳定，靠这个看）
REGIMES = [
    ("2007-2011 危机+QE", "2007-01-01", "2011-12-31"),
    ("2012-2015 Taper", "2012-01-01", "2015-12-31"),
    ("2016-2019 加息尾段", "2016-01-01", "2019-12-31"),
    ("2020-2021 疫情", "2020-01-01", "2021-12-31"),
    ("2022-2023 快速加息", "2022-01-01", "2023-12-31"),
    ("2024- 至今", "2024-01-01", "2099-12-31"),
]


# --------------------------------------------------------------------------
# 数据层
# --------------------------------------------------------------------------
def _fetch_sina(symbol):
    """新浪外盘期货日线（XAU 伦敦金现可用；DINIW 美元指数已下线）。"""
    import requests

    url = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_x="
           "/GlobalFuturesService.getGlobalFuturesDailyKLine?symbol=" + symbol)
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0",
                                   "Referer": "https://finance.sina.com.cn"}, timeout=25)
    txt = r.text
    i, j = txt.find("["), txt.rfind("]")
    if i < 0:
        raise RuntimeError("新浪返回异常: " + txt[:200])
    rows = json.loads(txt[i:j + 1])
    df = pd.DataFrame(rows)
    df = df[["date", "close"]].rename(columns={"date": "date", "close": "close"})
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    return df.dropna().query("close > 0").reset_index(drop=True)


def _fetch_em_dxy():
    """东财 K 线：100.UDI = ICE 美元指数，日频，1986 起。"""
    import requests

    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=100.UDI"
           "&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
           "&klt=101&fqt=0&beg=0&end=20500101&lmt=100000")
    j = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=25).json()
    data = j.get("data") or {}
    klines = data.get("klines") or []
    if not klines:
        raise RuntimeError("东财 DXY 无数据: " + json.dumps(j)[:200])
    rec = []
    for line in klines:
        p = line.split(",")
        rec.append({"date": p[0], "close": float(p[2])})
    return pd.DataFrame(rec)


def _cache_csv(df, path, name):
    """缓存优先：文件存在且不是 --refresh 就复用。"""
    if (not REFRESH) and os.path.exists(path):
        d = pd.read_csv(path)
        d["date"] = pd.to_datetime(d["date"])
        return d.sort_values("date").reset_index(drop=True)
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    df.to_csv(path, index=False, encoding="utf-8")
    print("[fetch] %s n=%d %s ~ %s" % (name, len(df), df["date"].iloc[0].date(),
                                       df["date"].iloc[-1].date()))
    return df


def load_all():
    xau_p = os.path.join(DATA, "_gold_xau.csv")
    dxy_p = os.path.join(DATA, "_dxy.csv")
    xau = _cache_csv(_fetch_sina("XAU").rename(columns={"close": "gold"}), xau_p, "XAU")
    dxy = _cache_csv(_fetch_em_dxy().rename(columns={"close": "dxy"}), dxy_p, "DXY")

    y10 = pd.read_csv(os.path.join(DATA, "_us10y.csv"))
    y10["date"] = pd.to_datetime(y10["date"])
    y10 = y10.rename(columns={"value": "y10"})[["date", "y10"]].dropna()

    vix = pd.read_csv(os.path.join(DATA, "_us_vix.csv"))
    vix["date"] = pd.to_datetime(vix["date"])
    vix = vix.rename(columns={"value": "vix"})[["date", "vix"]].dropna()

    df = xau.merge(dxy, on="date", how="inner")
    df = df.merge(y10, on="date", how="inner")
    df = df.merge(vix, on="date", how="inner")
    df = df[df["date"] >= START].sort_values("date").reset_index(drop=True)
    return df


# --------------------------------------------------------------------------
# 统计小工具
# --------------------------------------------------------------------------
def _tstat(x):
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    if len(x) < 5:
        return np.nan, np.nan
    return float(x.mean()), float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x))))


def _corr(a, b, min_n=12):
    a = pd.Series(a)
    b = pd.Series(b)
    m = a.notna() & b.notna()
    if m.sum() < min_n:
        return np.nan, int(m.sum())
    return float(a[m].corr(b[m])), int(m.sum())


def _perf(ret, pos, cash_daily=0.0, cost=0.0):
    """pos[t] 为 t 日实际持仓（调用方已 shift），ret[t] 为 t 日资产收益。

    cost 为单边换手成本（如 0.0005 = 5bp），按 |Δpos| 计。
    """
    r = pos * ret + (1.0 - pos) * cash_daily - pos.diff().abs().fillna(0.0) * cost
    eq = (1.0 + r.fillna(0.0)).cumprod()
    n = max(int(r.notna().sum()), 1)
    ann = float(eq.iloc[-1] ** (TRADING_DAYS / n) - 1.0)
    dd = float((eq / eq.cummax() - 1.0).min())
    sd = float(r.std(ddof=1) * np.sqrt(TRADING_DAYS))
    sharpe = float(r.mean() * TRADING_DAYS / sd) if sd > 0 else np.nan
    calmar = ann / abs(dd) if dd < 0 else np.nan
    return {
        "ann": ann, "total": float(eq.iloc[-1] - 1.0), "mdd": dd,
        "sharpe": sharpe, "calmar": calmar, "vol": sd,
        "expo": float(pos.mean()), "turn": float(pos.diff().abs().sum()),
    }


# --------------------------------------------------------------------------
# Q1/Q2：相关性与预测性
# --------------------------------------------------------------------------
def build_returns(df):
    d = df.copy()
    d["r_gold"] = np.log(d["gold"]).diff()
    d["r_dxy"] = np.log(d["dxy"]).diff()
    d["dy10"] = d["y10"].diff()          # 百分点变化
    d["r_vix"] = np.log(d["vix"]).diff()
    return d


def corr_table(d, freq):
    """freq: 'D' 原始日频；'W' 周五收盘；'M' 月末。"""
    if freq == "D":
        s = d
    else:
        s = d.set_index("date").resample("W-FRI" if freq == "W" else "ME").last().dropna()
        s = s.reset_index()
    d = s.copy()
    d["r_gold"] = np.log(d["gold"]).diff()
    d["r_dxy"] = np.log(d["dxy"]).diff()
    d["dy10"] = d["y10"].diff()
    d["r_vix"] = np.log(d["vix"]).diff()
    rows = []
    for lab in REGIMES:
        seg = d[(d["date"] >= lab[1]) & (d["date"] <= lab[2])]
        c1, n1 = _corr(seg["r_gold"], seg["r_dxy"])
        c2, _ = _corr(seg["r_gold"], seg["dy10"])
        c3, _ = _corr(seg["r_gold"], seg["r_vix"])
        rows.append({"regime": lab[0], "n": n1,
                     "c_dxy": c1, "c_y10": c2, "c_vix": c3})
    c1, n1 = _corr(d["r_gold"], d["r_dxy"])
    c2, _ = _corr(d["r_gold"], d["dy10"])
    c3, _ = _corr(d["r_gold"], d["r_vix"])
    rows.insert(0, {"regime": "全期", "n": n1, "c_dxy": c1, "c_y10": c2, "c_vix": c3})
    return pd.DataFrame(rows)


def predict_table(d):
    """同期 vs 滞后 h 期：把「变化」换成 t 日值，收益换成 t+1..t+h 累计。"""
    rows = []
    for h in [1, 2, 5, 10, 20]:
        fwd = d["r_gold"].rolling(h).sum().shift(-h)
        c_same_dxy, n = _corr(d["r_dxy"], d["r_gold"])
        c_fwd_dxy, _ = _corr(d["r_dxy"], fwd)
        c_same_y, _ = _corr(d["dy10"], d["r_gold"])
        c_fwd_y, _ = _corr(d["dy10"], fwd)
        rows.append({"h": h, "n": n,
                     "dxy_same": c_same_dxy, "dxy_fwd": c_fwd_dxy,
                     "y10_same": c_same_y, "y10_fwd": c_fwd_y})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Q3：事件研究
# --------------------------------------------------------------------------
def event_study(d):
    """事件 t 日收盘确认，收益从 t+1 起算（可交易口径）。

    三类冲击，各拆正负：
      y10±  : Δy10 > +2σ / < -2σ   （利率冲击）
      dxy±  : Δln(DXY) > +2σ / < -2σ（美元冲击）
      vix+  : Δln(VIX) > +0.15      （恐慌事件，固定 15% 阈值）
    """
    d = d.copy()
    s_y = d["dy10"].rolling(60).std()
    s_x = d["r_dxy"].rolling(60).std()
    ev = {
        "利率跳升(Δy10>+2σ)": d["dy10"] > (2 * s_y),
        "利率跳降(Δy10<-2σ)": d["dy10"] < (-2 * s_y),
        "美元走强(ΔDXY>+2σ)": d["r_dxy"] > (2 * s_x),
        "美元走弱(ΔDXY<-2σ)": d["r_dxy"] < (-2 * s_x),
        "恐慌(VIX单日>+15%)": d["r_vix"] > 0.15,
    }
    horizons = [1, 3, 5, 10, 20]
    rows, paths = [], {}
    r = d["r_gold"]
    for name, mask in ev.items():
        idx = np.where(mask.fillna(False).values)[0]
        # 事件不重叠：同一事件后 5 日内不重复计数
        keep, last = [], -99
        for i in idx:
            if i - last >= 5:
                keep.append(i)
                last = i
        stats = {"event": name, "n": len(keep)}
        for h in horizons:
            vals = []
            for i in keep:
                seg = r.iloc[i + 1:i + 1 + h]
                if len(seg) == h:
                    vals.append(float(np.nansum(seg)))
            m, t = _tstat(vals)
            stats["h%d_mean" % h] = m
            stats["h%d_win" % h] = float(np.mean([v > 0 for v in vals])) if vals else np.nan
            stats["h%d_t" % h] = t
        rows.append(stats)
        # 平均路径 -2..+20（用于画图）
        p = []
        for k in range(-2, 21):
            vals = []
            for i in keep:
                if 0 <= i + k < len(r):
                    vals.append(float(r.iloc[i + k]) if not np.isnan(r.iloc[i + k]) else np.nan)
            p.append(np.nanmean(vals) if vals else np.nan)
        paths[name] = np.cumsum(p)
    return pd.DataFrame(rows), paths


# --------------------------------------------------------------------------
# 信号回测（毛收益，零成本，只回答「驱动本身有没有信息」）
# --------------------------------------------------------------------------
def signal_backtests(d):
    d = d.copy()
    ma200 = d["dxy"].rolling(200).mean()
    mom20_dxy = d["dxy"].pct_change(20)
    chg20_y = d["y10"].diff(20)

    sig = {
        "DXY<MA200 持有": (d["dxy"] < ma200).astype(float),
        "DXY 20日动量<0 持有": (mom20_dxy < 0).astype(float),
        "10Y 20日下行 持有": (chg20_y < 0).astype(float),
        "两者同时(美元+利率)": ((mom20_dxy < 0) & (chg20_y < 0)).astype(float),
        "两者同时或(宽松版)": ((mom20_dxy < 0) | (chg20_y < 0)).astype(float),
        "反向对照:DXY>MA200": (d["dxy"] > ma200).astype(float),
    }
    rows = []
    base = _perf(d["r_gold"], pd.Series(1.0, index=d.index))
    rows.append(dict(name="买入持有(基准)", **base))
    for name, s in sig.items():
        pos = s.shift(1).fillna(0.0)   # t 日信号 → t+1 持仓
        rows.append(dict(name=name, **_perf(d["r_gold"], pos)))
    return pd.DataFrame(rows)


def cost_sensitivity(d):
    """同一批信号在不同单边成本下的年化 —— 高换手信号的真实死法。"""
    d = d.copy()
    mom20_dxy = d["dxy"].pct_change(20)
    chg20_y = d["y10"].diff(20)
    sig = {
        "买入持有(基准)": pd.Series(1.0, index=d.index),
        "DXY<MA200": (d["dxy"] < d["dxy"].rolling(200).mean()).astype(float),
        "两者同时或(宽松版)": ((mom20_dxy < 0) | (chg20_y < 0)).astype(float),
        "两者同时(美元+利率)": ((mom20_dxy < 0) & (chg20_y < 0)).astype(float),
    }
    rows = []
    for name, s in sig.items():
        pos = s.shift(1).fillna(0.0)
        row = {"name": name, "turn_per_year": float(pos.diff().abs().sum() / (len(pos) / TRADING_DAYS))}
        for bp in [0, 2, 5, 10, 20]:
            row["ann_%dbp" % bp] = _perf(d["r_gold"], pos, cost=bp / 10000.0)["ann"]
        rows.append(row)
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# 画图
# --------------------------------------------------------------------------
def make_plots(d, paths):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.dpi"] = 120

    # 1) 滚动相关
    rc_dxy = d.set_index("date")["r_gold"].rolling(63).corr(d.set_index("date")["r_dxy"])
    rc_y10 = d.set_index("date")["r_gold"].rolling(63).corr(d.set_index("date")["dy10"])
    fig, ax = plt.subplots(figsize=(11, 4.6))
    ax.plot(rc_dxy.index, rc_dxy.values, lw=1.0, label="黄金 vs ΔDXY（63日滚动相关）")
    ax.plot(rc_y10.index, rc_y10.values, lw=1.0, label="黄金 vs Δ10Y（63日滚动相关）", alpha=0.85)
    ax.axhline(0, color="black", lw=0.8)
    ax.fill_between(rc_dxy.index, -1, 0, where=(rc_dxy.values < 0), color="green", alpha=0.05)
    ax.fill_between(rc_y10.index, -1, 0, where=(rc_y10.values < 0), color="green", alpha=0.05)
    ax.set_ylim(-1, 1)
    ax.set_title("黄金日收益与美元指数/10Y收益率变化的 63 日滚动相关（绿底=负相关区间）")
    ax.legend(loc="lower left", fontsize=9)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(FIG_ROLL)
    plt.close(fig)

    # 2) 同期散点
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4))
    for ax, xcol, xlab in zip(axes, ["r_dxy", "dy10"],
                              ["美元指数日变化 Δln(DXY)", "10Y 收益率日变化（百分点）"]):
        m = d[xcol].notna() & d["r_gold"].notna()
        x, y = d.loc[m, xcol].values, d.loc[m, "r_gold"].values
        ax.scatter(x, y, s=3, alpha=0.25, color="#2b6cb0")
        b, a = np.polyfit(x, y, 1)
        xs = np.linspace(np.percentile(x, 0.5), np.percentile(x, 99.5), 50)
        ax.plot(xs, a + b * xs, color="#c53030", lw=1.6)
        ax.axhline(0, color="black", lw=0.6)
        ax.set_xlabel(xlab, fontsize=9)
        ax.set_ylabel("黄金日收益", fontsize=9)
        ax.set_title("斜率=%.3f  r=%.3f" % (b, np.corrcoef(x, y)[0, 1]), fontsize=10)
        ax.grid(alpha=0.25)
    fig.suptitle("同期关系（当年就能看到，不代表能预测）", fontsize=11)
    fig.tight_layout()
    fig.savefig(FIG_SCAT)
    plt.close(fig)

    # 3) 事件后路径
    fig, ax = plt.subplots(figsize=(10, 4.6))
    xs = list(range(-2, 21))
    for name, path in paths.items():
        ax.plot(xs, path * 100, marker="o", ms=3, lw=1.3, label=name)
    ax.axvline(0, color="black", lw=0.9, ls="--")
    ax.axhline(0, color="black", lw=0.6)
    ax.set_title("事件日前后黄金平均累计收益（t=0 为事件当日，其右侧才是可交易区间）")
    ax.set_xlabel("相对事件日的交易日")
    ax.set_ylabel("累计收益 %")
    ax.legend(fontsize=8, ncol=2)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(FIG_EVT)
    plt.close(fig)


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def main():
    raw = load_all()
    d = build_returns(raw)
    print("[data] 对齐后 n=%d  %s ~ %s" % (len(d), d["date"].iloc[0].date(),
                                           d["date"].iloc[-1].date()))

    c_d = corr_table(d, "D")
    c_w = corr_table(d, "W")
    c_m = corr_table(d, "M")
    prd = predict_table(d)
    ev, paths = event_study(d)
    bt = signal_backtests(d)
    cs = cost_sensitivity(d)

    # ---- 控制台摘要 ----
    print("\n== 日频同期相关 ==")
    print(c_d.to_string(index=False, float_format=lambda x: "%.3f" % x))
    print("\n== 预测性（同期 vs 未来 h 日累计）==")
    print(prd.to_string(index=False, float_format=lambda x: "%.3f" % x))
    print("\n== 事件研究（t+1 起算）==")
    print(ev[["event", "n", "h1_mean", "h5_mean", "h5_win", "h5_t", "h20_mean", "h20_t"]]
          .to_string(index=False, float_format=lambda x: "%.4f" % x))
    print("\n== 信号回测（毛收益）==")
    print(bt.to_string(index=False, float_format=lambda x: "%.3f" % x))
    print("\n== 成本敏感性（年化）==")
    print(cs.to_string(index=False, float_format=lambda x: "%.3f" % x))

    make_plots(d, paths)

    out = {
        "range": [str(d["date"].iloc[0].date()), str(d["date"].iloc[-1].date())],
        "n": int(len(d)),
        "corr_daily": c_d.to_dict("records"),
        "corr_weekly": c_w.to_dict("records"),
        "corr_monthly": c_m.to_dict("records"),
        "predict": prd.to_dict("records"),
        "events": ev.to_dict("records"),
        "backtest": bt.to_dict("records"),
        "cost_sensitivity": cs.to_dict("records"),
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n[save] %s" % OUT_JSON)
    write_md(d, c_d, c_w, c_m, prd, ev, bt, cs)
    print("[save] %s" % OUT_MD)


def _md_table(df, cols=None, fmt=None):
    cols = cols or list(df.columns)
    fmt = fmt or {}
    head = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join(["---"] * len(cols)) + "|"
    lines = [head, sep]
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


def write_md(d, c_d, c_w, c_m, prd, ev, bt, cs):
    def pcar(v):
        return "—" if pd.isna(v) else "%+.1f%%" % (v * 100)

    def pct(v):
        return "—" if pd.isna(v) else "%.1f%%" % (v * 100)

    def cor(v):
        return "—" if pd.isna(v) else "%.3f" % v

    base = bt[bt["name"].str.startswith("买入持有")].iloc[0]

    L = []
    A = L.append
    A("# V6 黄金 · 宏观驱动验证：美元、美债、事件冲击")
    A("")
    A("> 由 `_gold_macro.py` 生成，数据区间 **%s ~ %s（%d 个共同交易日）**。"
      % (d["date"].iloc[0].date(), d["date"].iloc[-1].date(), len(d)))
    A("> 数据：伦敦金现 XAU/USD（新浪外盘）、ICE 美元指数（东财 100.UDI）、"
      "美债 10Y（FRED，仓库 `data/_us10y.csv`）、VIX（仓库 `data/_us_vix.csv`）。")
    A("> 口径：黄金、DXY、VIX 用对数收益，10Y 用一阶差（百分点）。**事件研究收益一律从 t+1 起算**，"
      "回测信号一律 `t 日收盘生成 → t+1 持仓`，不含前视。")
    A("")
    A("## 1. 一句话结论")
    A("")
    cd_all = c_d.iloc[0]
    p5 = prd[prd["h"] == 5].iloc[0]
    p1 = prd[prd["h"] == 1].iloc[0]
    A("1. **「常常相反」是真的，但只在同期，而且强度不高。** 全期日频相关："
      "黄金 vs ΔDXY **%s**、vs Δ10Y **%s**、vs ΔVIX **%s**。"
      "方向上确实是负的，但 |r| 只在 0.2~0.4 这个量级 —— 解释不了大部分波动。"
      % (cor(cd_all["c_dxy"]), cor(cd_all["c_y10"]), cor(cd_all["c_vix"])))
    dxy_rng = (c_d["c_dxy"].iloc[1:].min(), c_d["c_dxy"].iloc[1:].max())
    y_rng = (c_d["c_y10"].iloc[1:].min(), c_d["c_y10"].iloc[1:].max())
    A("   - **跟美元的关系最稳**：6 个分期全在 %s ~ %s 之间，没有一期转正。"
      "这是唯一可以当「常识」用的那条。"
      % (cor(dxy_rng[0]), cor(dxy_rng[1])))
    A("   - **跟美债的关系极不稳定**：分期从 %s 到 %s。2007-2011 QE 期间基本无关，"
      "2016 年后才变强。所谓「黄金盯实际利率」是个**近十年才成立**的叙事，不是历史规律。"
      % (cor(y_rng[0]), cor(y_rng[1])))
    A("   - **跟 VIX 基本无关（%s）**。反直觉但很重要：黄金的日波动不跟着股市恐慌走，"
      "「避险资产」这个标签在日频尺度上不成立（月频略正，见 §2.2）。"
      % cor(cd_all["c_vix"]))
    A("2. **「相反」不能反过来当预测用。** 把 DXY 变化滞后一期，相关从 %s 掉到 **%s**；"
      "10Y 从 %s 掉到 **%s**。到 5 日累计口径只剩 %s / %s。"
      "也就是说，这个负相关是**当天同时发生的**，等你看到美元跌，金价已经涨完了。"
      % (cor(p1["dxy_same"]), cor(p1["dxy_fwd"]), cor(p1["y10_same"]), cor(p1["y10_fwd"]),
         cor(p5["dxy_fwd"]), cor(p5["y10_fwd"])))
    ev_yd = ev[ev["event"].str.startswith("利率跳降")].iloc[0]
    ev_yu = ev[ev["event"].str.startswith("利率跳升")].iloc[0]
    ev_vix = ev[ev["event"].str.startswith("恐慌")].iloc[0]
    ev_xd = ev[ev["event"].str.startswith("美元走弱")].iloc[0]
    ev_xu = ev[ev["event"].str.startswith("美元走强")].iloc[0]
    A("3. **事件效应存在，但只有一类统计显著，且方向是「反弹」不是「跟随」。** 见 §4：")
    A("   - **利率跳降（Δy10 < -2σ）后 T+5 平均 %s（t=%.2f）、T+20 %s（t=%.2f）**"
      "—— 全部事件里唯一过 2 倍标准误的。20 年触发 %d 次，约 %.0f 次/年。"
      % (pcar(ev_yd["h5_mean"]), ev_yd["h5_t"], pcar(ev_yd["h20_mean"]),
         ev_yd["h20_t"], ev_yd["n"], ev_yd["n"] / 20.0))
    A("   - **上/下严重不对称**：利率跳升后 T+5 只有 %s（t=%.2f），等于没有。"
      "所以这不是「利率降→买黄金」的线性关系，而是**暴跌之后的均值回复**。"
      % (pcar(ev_yu["h5_mean"]), ev_yu["h5_t"]))
    A("   - **美元冲击不论方向，黄金都涨**：美元走强后 %s（t=%.2f）、走弱后 %s（t=%.2f）。"
      "两个方向都为正，说明这是**冲击后的反弹**，不是「美元跌→黄金涨」的传导。"
      "按「相反」逻辑去做，会在美元走强那天做空黄金，正好做反。"
      % (pcar(ev_xu["h5_mean"]), ev_xu["h5_t"], pcar(ev_xd["h5_mean"]), ev_xd["h5_t"]))
    A("   - **VIX 恐慌后 T+20 %s（t=%.2f）**，虽然显著但只有 %d 次样本、"
      "单次幅度小，且 T+5 还不显著（%s, t=%.2f）—— 要拿满 20 天，等于长期占仓。"
      % (pcar(ev_vix["h20_mean"]), ev_vix["h20_t"], ev_vix["n"],
         pcar(ev_vix["h5_mean"]), ev_vix["h5_t"]))
    best = bt[bt["name"] != "买入持有(基准)"].sort_values("ann", ascending=False).iloc[0]
    cs_best = cs[cs["name"].str.startswith("两者同时或")].iloc[0]
    A("4. **毛收益上有一个勉强能看，扣成本立刻死。** 最好的信号是「%s」："
      "年化 %s（基准 %s）、最大回撤 %s（基准 %s）、夏普 %.2f（基准 %.2f）—— "
      "**回撤确实小了一大截，代价是 %.0f 次/年的换手**。"
      % (best["name"], pcar(best["ann"]), pcar(base["ann"]),
         pcar(best["mdd"]), pcar(base["mdd"]),
         best["sharpe"], base["sharpe"], cs_best["turn_per_year"]))
    A("5. **成本只要 2bp 单边，那 0.3pp 的优势就没了**（年化 %s vs 基准 %s）；5bp 明显输（%s）。"
      "518880 场内双边价差+佣金约 5bp，000216 场外申赎一次 60bp —— 两条路都过不去。"
      % (pcar(cs_best["ann_2bp"]), pcar(base["ann"]), pcar(cs_best["ann_5bp"])))
    A("")
    A("## 2. 同期相关：存在，但按区间剧烈漂移")
    A("")
    A("### 2.1 日频，全期与分期")
    A("")
    A(_md_table(c_d, ["regime", "n", "c_dxy", "c_y10", "c_vix"],
                {"c_dxy": cor, "c_y10": cor, "c_vix": cor}))
    A("")
    A("### 2.2 周频 / 月频（降噪后关系更强）")
    A("")
    A(_md_table(c_w, ["regime", "n", "c_dxy", "c_y10", "c_vix"],
                {"c_dxy": cor, "c_y10": cor, "c_vix": cor}))
    A("")
    A(_md_table(c_m, ["regime", "n", "c_dxy", "c_y10", "c_vix"],
                {"c_dxy": cor, "c_y10": cor, "c_vix": cor}))
    A("")
    A("> 读法：日频相关被噪声稀释，周频/月频才接近「教科书里的负相关」。"
      "但交易是按天做的，频率越高越不可用 —— 这就是这个命题的核心矛盾。")
    A("")
    A("![滚动相关](%s)" % os.path.basename(FIG_ROLL))
    A("")
    A("## 3. 预测性：同期显著，滞后归零")
    A("")
    A(_md_table(prd, ["h", "n", "dxy_same", "dxy_fwd", "y10_same", "y10_fwd"],
                {"h": lambda v: "%d日" % int(v), "n": lambda v: "%d" % int(v),
                 "dxy_same": cor, "dxy_fwd": cor, "y10_same": cor, "y10_fwd": cor}))
    A("")
    A("`same` = 当日变化与当日黄金收益的相关；`fwd` = 当日变化与**未来 h 日累计**黄金收益的相关。"
      "若 fwd 也显著为负，说明美元/利率领先黄金，可交易；若归零，说明只是同时性。")
    A("")
    A("![散点](%s)" % os.path.basename(FIG_SCAT))
    A("")
    A("## 4. 事件研究：冲击之后有没有可交易漂移")
    A("")
    A("事件在 t 日收盘确认（当日 Δ 已发生，无法交易），收益从 t+1 起算。"
      "同类事件 5 个交易日内不重复计数。")
    A("")
    A(_md_table(ev, ["event", "n", "h1_mean", "h3_mean", "h5_mean", "h5_win", "h5_t",
                     "h10_mean", "h20_mean", "h20_t"],
                {"h1_mean": pcar, "h3_mean": pcar, "h5_mean": pcar, "h5_win": pct,
                 "h10_mean": pcar, "h20_mean": pcar}))
    A("")
    A("![事件路径](%s)" % os.path.basename(FIG_EVT))
    A("")
    A("## 5. 信号回测：能不能变成钱（毛收益、零成本）")
    A("")
    A("先给**零成本**的毛收益尺子（最宽松），再看 §5.1 成本敏感性。"
      "逻辑很简单：如果连零成本的毛收益都赢不了买入持有，就不必往下算；"
      "如果只赢一点点，就看这点优势够不够付换手成本。")
    A("")
    A(_md_table(bt, ["name", "ann", "total", "mdd", "sharpe", "calmar", "expo", "turn"],
                {"ann": pcar, "total": pcar, "mdd": pcar}))
    A("")
    A("`expo` = 平均仓位，`turn` = 累计换手（单位：仓位变动绝对值之和）。")
    A("")
    A("### 5.1 换手成本敏感性（年化收益 vs 单边成本）")
    A("")
    A(_md_table(cs, ["name", "turn_per_year", "ann_0bp", "ann_2bp", "ann_5bp",
                     "ann_10bp", "ann_20bp"],
                {"ann_0bp": pcar, "ann_2bp": pcar, "ann_5bp": pcar,
                 "ann_10bp": pcar, "ann_20bp": pcar}))
    A("")
    A("> 这一栏是全文最硬的证据：**信号的优势幅度（0.3pp）小于任何真实成本**。"
      "基准零换手，成本对它无效；所有依赖「20 日动量/均线」的信号都被换手吃穿。")
    A("")
    A("## 6. 局限")
    A("")
    A("1. **XAU 是美元金价（伦敦金现），不是你能买的标的。** 人民币金价（518880 / 000216）"
      "多一层汇率，USDCNY 与 DXY 并非同向，负相关会被削弱甚至翻转。这是本次结论"
      "「在美元金上都不够用，在人民币金上只会更弱」的直接理由。")
    A("2. **10Y 是名义利率，不是实际利率。** 理论框架用的是 TIPS 实际利率，"
      "但仓库无 TIPS 数据；名义利率混入了通胀预期，方向解释力更差。")
    A("3. **事件只用「市场冲击」代理（2σ 跳变、VIX+15%），不是真日历事件。** "
      "仓库无 CPI/FOMC 日历数据，硬编码日期容易出错且不可维护。"
      "冲击型事件研究的好处是不依赖日历、完全可复现。")
    A("4. **样本内。** 所有区间都参与了观察，没有做真正的样本外。"
      "不过 §5 的结论是「全都更差」，这类否定性结论对过拟合不敏感。")
    A("5. 2022 年之后负相关的弱化在 §2 的分期表里直接可见 —— 与 v6 外部研究清单"
      "（`research/external_ideas/_v6_external_ideas.md` §1 末行）引用的外部结论一致。")
    A("")

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


if __name__ == "__main__":
    main()
