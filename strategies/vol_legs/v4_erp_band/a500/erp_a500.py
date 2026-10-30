# -*- coding: utf-8 -*-
"""V4 变种 · A500 版：股债利差（ERP）通道 —— **中证A500** × 10Y 国债。

本文件是 `../erp.py`（沪深300 版）的**平行变种**：规则、通道算法、回测引擎、绘图全部复用
`../erp.py`，只有「标的与数据源」不同。沪深300 版的任何文件都没有被改动。

## 与沪深300 版的三处差异
1. **标的**：信号用中证A500（`000510`）的 PE；指数点位用中证A500 收盘。
2. **数据源**：
   - A500 日频 PE（`peg`，即滚动 PE）：中证官网 `csindex-home/perf/indexCsiDsPe`
     —— ⚠ 2026-10-30 实测：沪深300 用的 `csindex-home/perf/index-perf` 已被站点 WAF
     按路径拦截（403 + 攻击拦截页），`indexCsiDsPe` 未拦且返回**同一口径**的 `peg`
     （沪深300 用它取回 2011-06 起 3809 行，与 `data/_bt_caches/csi_000300.csv` 逐日一致）。
   - A500 日频收盘：新浪 `CN_MarketData.getKLineData`（`sh000510`，2005-01 起，含回溯点位）。
   - 10Y 国债：与沪深300 版**共用** `../data/bond10y_long.csv`（同一 instrument，保证可比）。
3. **样本长度**：这是核心限制。中证只从 A500 指数发布日（2024-09-02）起提供 PE，
   `peg` 序列早期那个孤立的 2005-01-01 哨兵行会被剔除。所以本变种的 ERP 样本只有
   **约 2 年**（沪深300 版是 2011-06 起 15 年），250 日窗口预热后可用于信号的只剩约 1 年。
   **这不是取数没取全，是指数本身没有更长的估值历史。**

## 股票腿「全收益」的近似
沪深300 版用中证全收益 `H00300`（含分红再投）。A500 的全收益指数 `H00510` 在本机不可达，
故本变种用 **价格指数 + 股息率按日计提** 合成：`ret_tr = ret_px + dy/252`，
`dy` 默认 2.5%（2026-10 中证 A500 股息率，取 `indicator` 文件 `D/P1` 的近期值），
可用 `--div-yield` 覆盖。**该近似只影响回测的股票腿收益，不影响任何信号**
（信号只用 PE 与 10Y 收益率）。
"""
import os
import sys
from datetime import date

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../v4_erp_band/a500
_V4 = os.path.dirname(_HERE)                                # .../v4_erp_band（沪深300 版）
_VL = os.path.dirname(_V4)                                  # strategies/vol_legs
_ROOT = os.path.dirname(os.path.dirname(_VL))               # 仓库根
for _p in (_V4, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import erp  # noqa: E402  —— 规则 / 通道 / 引擎 / 绩效 / 绘图全部复用沪深300 版

IDX_CODE = "000510"
IDX_NAME = "中证A500"
LABEL = "V4-A500 股债利差通道"
LABEL_PX = "中证A500"

# 跨策略数据类（AGENTS §8.1）：与 csi_000300.csv 同一目录、同一列名（date,close,peg）
_CACHE = os.path.join(_ROOT, "data", "_bt_caches", f"csi_{IDX_CODE}.csv")
_PE_ALT = os.path.join(_ROOT, "data", "_a500_pe_hist.csv")   # 兜底：同源 PE（无 close）

_SINA = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
         "CN_MarketData.getKLineData")
_CSI_PE = "https://www.csindex.com.cn/csindex-home/perf/indexCsiDsPe"
_CSI_IND = ("https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/"
            f"autofile/indicator/{IDX_CODE}indicator.xls")

DIV_YIELD_DEFAULT = 0.025      # 中证A500 股息率（2026-10 中证 indicator D/P1 ≈ 2.5%）
_GAP_DAYS = 30                 # 剔除 PE 序列头部孤立块（如 2005-01-01 的哨兵行）

SOURCE = {}
_WARN = []


def _warn(msg):
    _WARN.append(msg)
    print(f"[a500:warn] {msg}", file=sys.stderr)


# ---------------------------------------------------------------- 1. 采集
def fetch_close(symbol="sh000510", datalen=6000):
    """新浪日线 -> DataFrame(date, close)。收盘点位，2005-01 起（中证回溯）。"""
    s = erp._SESSION
    if s is None:
        raise RuntimeError("requests 不可用")
    r = s.get(_SINA, params={"symbol": symbol, "scale": "240", "ma": "no",
                             "datalen": str(datalen)},
              headers={"Referer": "https://finance.sina.com.cn"}, timeout=30, verify=False)
    rows = r.json() if r.status_code == 200 else None
    if not rows:
        raise RuntimeError(f"新浪 {symbol} 返回空（HTTP {r.status_code}）")
    out = pd.DataFrame([{"date": x["day"], "close": float(x["close"])} for x in rows])
    out["date"] = pd.to_datetime(out["date"])
    return out.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def fetch_pe(code=IDX_CODE, start="20050101"):
    """中证官网 indexCsiDsPe -> DataFrame(date, peg)。`peg` = 滚动市盈率。

    返回里可能夹带一条早年的孤立哨兵行（A500 是 2005-01-01），按「与后一行间隔 > 30 天
    的孤立头部」整块剔除，只留连续段。
    """
    d = erp._get_json(_CSI_PE, {"indexCode": code, "startDate": start,
                                "endDate": date.today().strftime("%Y%m%d")},
                      referer="https://www.csindex.com.cn/")
    rows = (d.get("data") or []) if isinstance(d, dict) else []
    out = []
    for x in rows:
        dt, v = str(x.get("tradeDate") or ""), x.get("peg")
        if not dt or v in (None, ""):
            continue
        try:
            out.append({"date": dt, "peg": float(v)})
        except (TypeError, ValueError):
            continue
    if not out:
        raise RuntimeError(f"{code} PE 返回空（中证 indexCsiDsPe）")
    df = pd.DataFrame(out)
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df = df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    gap = df["date"].diff().dt.days.fillna(0)
    cut = gap[gap > _GAP_DAYS]
    if len(cut):
        df = df.loc[cut.index[-1]:].reset_index(drop=True)   # 只留最后一段连续块
    return df


def fetch_div_yield():
    """中证 OSS indicator 文件里的 A500 股息率（D/P1）近期均值 -> float。

    只覆盖最近 20 个交易日，取均值当常数用；失败返回默认值并告警。
    """
    try:
        import io

        s = erp._SESSION
        r = s.get(_CSI_IND, headers={"Referer": "https://www.csindex.com.cn/"},
                  timeout=30, verify=False)
        df = pd.read_excel(io.BytesIO(r.content))
        col = [c for c in df.columns if "D/P1" in str(c)]
        if not col:
            raise RuntimeError("indicator 文件无 D/P1 列")
        v = float(pd.to_numeric(df[col[0]], errors="coerce").dropna().mean()) / 100.0
        return v if 0 < v < 0.10 else DIV_YIELD_DEFAULT
    except Exception as exc:  # noqa: BLE001
        _warn(f"A500 股息率取数失败（{type(exc).__name__}: {exc}），用默认 "
              f"{DIV_YIELD_DEFAULT:.1%}")
        return DIV_YIELD_DEFAULT


def _merge_cache(path, new):
    """并集写缓存：重叠日期以 `new` 为准，缺失单元格由旧值补（列各自独立）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        try:
            old = pd.read_csv(path, parse_dates=["date"]).set_index("date")
            out = new.set_index("date").combine_first(old).reset_index()
        except (OSError, ValueError):
            out = new.copy()
    else:
        out = new.copy()
    out = out.sort_values("date")
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    out.to_csv(path, index=False, encoding="utf-8")


def load_a500(refresh=False, days=erp.CACHE_DAYS):
    """中证A500 日频 -> DataFrame(date, close, peg)。缓存优先，抓取失败沿用缓存并告警。"""
    if refresh or not erp._cache_fresh(_CACHE, days):
        got = []
        try:
            px = fetch_close()
            got.append(px)
            SOURCE["close"] = (f"新浪 `sh{IDX_CODE}`（实时抓取）· 至 "
                               f"{px['date'].max():%Y-%m-%d} · 自 {px['date'].min():%Y-%m-%d}")
        except Exception as exc:  # noqa: BLE001
            _warn(f"A500 收盘抓取失败（{type(exc).__name__}: {exc}），沿用缓存")
        try:
            pe = fetch_pe()
            got.append(pe)
            SOURCE["pe"] = (f"中证官网 `indexCsiDsPe`（实时抓取）· 至 "
                            f"{pe['date'].max():%Y-%m-%d} · 自 {pe['date'].min():%Y-%m-%d}")
        except Exception as exc:  # noqa: BLE001
            _warn(f"A500 PE 抓取失败（{type(exc).__name__}: {exc}），沿用缓存")
        if got:
            merged = got[0]
            for extra in got[1:]:
                merged = merged.merge(extra, on="date", how="outer")
            _merge_cache(_CACHE, merged)

    if not os.path.exists(_CACHE):
        raise RuntimeError(f"缺 {os.path.basename(_CACHE)} 且抓取失败")
    df = pd.read_csv(_CACHE, parse_dates=["date"]).sort_values("date")
    df["close"] = pd.to_numeric(df.get("close"), errors="coerce")
    df["peg"] = pd.to_numeric(df.get("peg"), errors="coerce")
    if df["peg"].notna().sum() == 0 and os.path.exists(_PE_ALT):
        alt = pd.read_csv(_PE_ALT, parse_dates=["date"])
        df = df.drop(columns=["peg"]).merge(alt[["date", "peg"]], on="date", how="left")
        _warn(f"缓存里没有 PE，暂用共享的 `{os.path.basename(_PE_ALT)}` 兜底")
    SOURCE.setdefault("close", f"本地缓存 `{os.path.basename(_CACHE)}` · "
                               f"至 {df['date'].max():%Y-%m-%d}")
    SOURCE.setdefault("pe", f"本地缓存 `{os.path.basename(_CACHE)}` · "
                            f"至 {df['date'].max():%Y-%m-%d}")
    return df.reset_index(drop=True)


# ---------------------------------------------------------------- 2. 框架
def erp_frame(window=None, k=None, exit_frac=None, ffill_limit=None, refresh=False,
              start=None, div_yield=None):
    """完整框架，列与 `../erp.py::erp_frame` 一致（close/pe/y10/tr/erp/ma/std/upper/
    lower/ready/state/event/target），只是标的换成中证A500。

    `close` = A500 收盘点位（画图用）；`tr` = 价格指数 + 股息率按日计提的**全收益近似**
    （回测股票腿）。起点由 PE 可用日决定 —— A500 的 PE 只有 2024-09 起。
    """
    lim = int(erp.CFG["ffill_limit"] if ffill_limit is None else ffill_limit)
    dy = DIV_YIELD_DEFAULT if div_yield is None else float(div_yield)
    raw = load_a500(refresh=refresh)
    y = erp.load_bond10y(refresh=refresh)

    df = pd.DataFrame({"date": raw["date"], "close": raw["close"].astype("float64"),
                       "pe": raw["peg"].ffill(limit=lim)})
    df["y10"] = y.reindex(pd.DatetimeIndex(df["date"])).ffill(limit=lim).to_numpy()
    df = df.dropna(subset=["close", "pe", "y10"]).set_index("date").sort_index()
    if start is not None:
        df = df.loc[pd.Timestamp(start):]
    if df.empty:
        raise RuntimeError("A500 序列在给定起点之后为空（PE 只有 2024-09 起）")

    ret = df["close"].pct_change()
    df["tr"] = df["close"].iloc[0] * (1.0 + (ret.fillna(0.0) + dy / 252.0)).cumprod()
    df["dy"] = dy

    df = erp.add_band(df, window=window, k=k)
    sig = erp.signal_series(df, exit_frac=exit_frac)
    df["state"] = sig["state"]
    df["event"] = sig["event"]
    df["target"] = erp.target_weight(df["state"]).to_numpy()
    return df


def bond_leg(duration=None):
    """债券腿：与沪深300 版同一份 10Y 收益率、同一合成口径（可交叉比较）。"""
    return erp.bond_synthetic(erp.load_bond10y(), duration=duration)


def latest(df=None):
    """当前读数（自检 / 控制台摘要用）。"""
    df = erp_frame() if df is None else df
    last = df.iloc[-1]
    ef = float(erp.CFG["exit_frac"])
    st = int(last["state"])
    exit_line = (float(last["ma"] + ef * last["std"]) if st == 1 else
                 (float(last["ma"] - ef * last["std"]) if st == -1 else float(last["ma"])))
    return dict(date=df.index[-1], erp=float(last["erp"]), pe=float(last["pe"]),
                y10=float(last["y10"]), ma=float(last["ma"]), std=float(last["std"]),
                upper=float(last["upper"]), lower=float(last["lower"]), state=st,
                exit_line=exit_line, target=float(last["target"]), df=df)


# ---------------------------------------------------------------- 3. 自检
def _self_check():
    df = erp_frame()
    r = latest(df)
    print(f"{LABEL} · 标的 {IDX_NAME}({IDX_CODE})")
    print(f"样本 {df.index[0]:%Y-%m-%d} ~ {df.index[-1]:%Y-%m-%d} · {len(df)} 个交易日")
    print(f"来源: 收盘 {SOURCE.get('close')} | PE {SOURCE.get('pe')} | "
          f"10Y {erp.SOURCE.get('bond')}")
    ready = df[df["ready"]]
    if ready.empty:
        print("通道尚未就绪（ERP 不足窗口长度）")
        return
    print(f"通道就绪 {ready.index[0]:%Y-%m-%d} 起（{len(ready)} 日）")
    vc = ready["state"].value_counts().to_dict()
    tot = len(ready)
    print("状态占比: " + " / ".join(
        f"{n} {vc.get(k, 0) / tot:.1%}" for k, n in ((1, "低估"), (0, "中性"), (-1, "高估"))))
    print(f"最新 {r['date']:%Y-%m-%d}: PE {r['pe']:.2f} · 10Y {r['y10']:.3f}% · "
          f"ERP {r['erp']:.2f}% · MA {r['ma']:.2f} · 上轨 {r['upper']:.2f} · "
          f"下轨 {r['lower']:.2f}")
    print(f"状态 {r['state']:+d} · 目标{IDX_NAME} {r['target']:.0%} · 退出线 {r['exit_line']:.2f}%")
    if _WARN:
        print("告警:", len(_WARN))
        for w in _WARN[:5]:
            print("  -", w)


if __name__ == "__main__":
    if "--refresh" in sys.argv:
        load_a500(refresh=True)
    if "--div-yield" in sys.argv:                 # 只读一次网络，用于核对 DIV_YIELD_DEFAULT
        print(f"A500 股息率（中证 indicator 近 20 日均值）: {fetch_div_yield():.2%}")
    _self_check()
