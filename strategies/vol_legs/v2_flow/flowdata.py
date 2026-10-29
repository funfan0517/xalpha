# -*- coding: utf-8 -*-
"""波动资产 · V2 变种：资金渗透率（份额拥挤度）· 数据层。

实现 `head_fund_ratio_rules.md`（头部 N 只 ETF 市值 ÷ 指数流通市值）的**取数与装配**部分，
标的池沿用主策略 `strategies/vol_legs/rule.py::LEGS` 的六腿。

## 与规则的对应（规则编号见 head_fund_ratio_rules.md）
| 规则 | 实现 |
|---|---|
| R-01 头部排序 | 候选池内按**最新季报净资产**降序取前 `HEAD_N=4`（不用实时市值排序，避免名单跳动） |
| R-02 候选池 | `PROFILES[leg]["cands"]`：只放**跟踪同一标的**的场内 ETF，人工维护 |
| R-03 份额锚点 | `gmbd` 期末总份额，≥ `ANCHOR_FLOOR`(2012-01-01)，升序去重（同日取后者） |
| R-05 份额序列 | 锚点间**分段线性内插**，端点精确等于锚点值；锚点之前**不产出**（本变种要算增长率，
常数钳制会伪造「0 增长」，故按 R-09「全头部可得才产出」处理，记 M-01） |
| R-06 市值序列 | `cap_i(d) = shares_i(d) × close_i(d)`（亿元 = 亿份 × 元），价格前向填充 |
| R-07 流通市值 | `float(d) = float_now × I(d) / I_now`（价格等比代理） |
| R-08 占比 | `ratio(d) = 100 × Σcap / float`，仅 A 股腿有指数流通市值，可算 |
| R-10 实时锚校验 | `LIVE_ANCHOR=True` 时用腾讯「市值/价格」补最新份额，`0.5 ≤ s_now/s_季报 ≤ 2.0` 才采用 |
| N-01~N-03 | curl 直连(`--noproxy *`)、2 次重试、utf-8→gbk 回退、失败回落本地缓存不阻断 |

## 数据源（全部免费无 key，与规则 §2 一致）
- DS-1 东财 push2 指数行情：取 `f117` 流通市值 / `f43` 指数点位 —— **本环境常被限流**，
  失败即读 `data/_index_quote_snapshot.json`（脚本落盘的快照，`quote_src` 标注来源）。
- DS-2 新浪日 K：指数收盘（渗透率分母代理）与 ETF 收盘（分子市值），`datalen=2000` ≈ 8 年。
- DS-3 腾讯行情：ETF 实时市值 + 名称（GBK！`decode("utf-8","ignore")` 会把中文名吃掉）。
- DS-4 东财 gmbd：基金季报**期末总份额 / 期末净资产**（第 4 组是份额、第 5 个是净资产，易错点）。

## 缓存
`data/_shares_cache.json` 存**原始锚点 + 日 K + 实时快照**（策略类缓存，随目录移动），
`data/_index_quote_snapshot.json` 存指数流通市值快照。刷新阈值 `REFRESH_DAYS=7`。
一次刷新约 30 个请求，季报节奏（每季 4 次）足够，日常运行只读缓存。
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_VL = os.path.dirname(_HERE)                       # strategies/vol_legs
_ROOT = os.path.dirname(os.path.dirname(_VL))      # 仓库根

if _VL not in sys.path:
    sys.path.insert(0, _VL)

import rule as vrule    # noqa: E402  主策略参数权威源（六腿、费率、货基利率）
import data as vdata    # noqa: E402  主策略数据层（场外复权净值 = 六腿价格轴）

DATA_DIR = os.path.join(_HERE, "data")
CACHE = os.path.join(DATA_DIR, "_shares_cache.json")
FLOAT_SNAP = os.path.join(DATA_DIR, "_index_quote_snapshot.json")

ANCHOR_FLOOR = "2012-01-01"   # R-03
HEAD_N = 4                    # R-01
KLINE_BARS = 2000             # DS-2：≈8 年
REFRESH_DAYS = 7              # 缓存新鲜度阈值
LIVE_ANCHOR = False           # R-04/R-10 实时锚（默认关：季内锚点会破坏可复现性）
NOPROXY = ["--noproxy", "*"]
SOURCE_NOTE = {}

# ---------------------------------------------------------------- 1. 六腿候选池（R-02）
# cands 只放跟踪**同一标的**的场内 ETF（错配会把渗透率算到别的指数上，见规则 §9 已踩坑 5）。
# float_ok=False 的腿没有「标的指数流通市值」口径（NDX/SPX/HSTECH 不在东财指数接口，黄金也没有），
# 只算**头部 ETF 合计份额**（= 渗透率的分子），不做占比换算。
PROFILES = {
    "a500": dict(name="中证A500", n=HEAD_N, float_ok=True,
                 index=dict(secid="1.000510", sina="sh000510", quote_name="中证A500"),
                 cands=["563360", "159352", "159361", "159338", "512050", "563800"]),
    "kc50": dict(name="科创50", n=HEAD_N, float_ok=True,
                 index=dict(secid="1.000688", sina="sh000688", quote_name="科创50"),
                 cands=["588000", "588080", "588050", "588060", "588090"]),
    "ndx": dict(name="纳指100", n=HEAD_N, float_ok=False, index=None,
                cands=["513100", "159941", "513300", "159632", "513110", "159501"]),
    # 标普 500 的场内 QDII 只有 3 只（561300 是「沪深300增强ETF」，曾误入池 —— R-02 明确要求
    # 只放跟踪同一标的的产品，错配会把别的指数的份额算进来，故剔除并按 n=3 取头部）。
    "spx": dict(name="标普500", n=3, float_ok=False, index=None,
                cands=["513500", "159612", "513650"]),
    "hkt": dict(name="恒生科技", n=HEAD_N, float_ok=False, index=None,
                cands=["513180", "513130", "159740", "159742", "513010", "159741"]),
    "gold": dict(name="黄金", n=HEAD_N, float_ok=False, index=None,
                 cands=["518880", "159934", "159937", "518800", "518600"]),
}
LEG_ORDER = list(vrule.LEG_ORDER)


# ---------------------------------------------------------------- 2. 传输层（N-01~N-03）
def curl(url, referer="https://fund.eastmoney.com/", timeout=20):
    """curl 直连；最多 2 次尝试，失败返回空串（由上层降级）。不做无限重试或 sleep 循环。"""
    cmd = ["curl", "-s", "-m", str(timeout), "-H", "User-Agent: Mozilla/5.0",
           "-H", "Referer: " + referer, url] + NOPROXY
    for _ in range(2):
        try:
            raw = subprocess.run(cmd, capture_output=True, timeout=timeout + 10).stdout
            if not raw.strip():
                continue
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                return raw.decode("gbk", "ignore")       # 腾讯是 GBK（N-02）
        except Exception:  # noqa: BLE001
            pass
    return ""


def _sym(code):
    return ("sh" if code[0] in "56" else "sz") + code


def fetch_gmbd(code):
    """DS-4：季报规模表 -> [[date, 期末份额亿份, 期末净资产亿元]]，升序去重。

    正则第 4 个捕获组是**份额**、第 5 个是净资产（规则 §3 已踩坑 3，取错会把净资产当份额）。
    """
    txt = curl("https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=gmbd&code=" + code,
               referer="https://fundf10.eastmoney.com/")
    rows = re.findall(
        r"<tr><td>(\d{4}-\d{2}-\d{2})</td>.*?<td class='tor'>([\d,\.]+)</td>"
        r"<td class='tor'>([\d,\.]+)</td><td class='tor'>([\d,\.]+)</td>"
        r"<td class='tor'>([\d,\.]+)</td>", txt)
    dedup = {}
    for d, _sub, _red, sh, nav in rows:
        if d >= ANCHOR_FLOOR:
            dedup[d] = [float(sh.replace(",", "")), float(nav.replace(",", ""))]   # 同日取后者
    return [[d, v[0], v[1]] for d, v in sorted(dedup.items())]


def fetch_kline(sina_sym, bars=KLINE_BARS):
    """DS-2：新浪日 K -> {dates: [...], close: [...]}。"""
    url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           "CN_MarketData.getKLineData?symbol=%s&scale=240&ma=no&datalen=%d" % (sina_sym, bars))
    txt = curl(url, referer="https://finance.sina.com.cn/")
    try:
        arr = json.loads(txt)
    except (ValueError, TypeError):
        return {}
    if not isinstance(arr, list) or not arr:
        return {}
    return dict(dates=[b["day"][:10] for b in arr],
                close=[float(b["close"]) for b in arr])


def fetch_tencent(code):
    """DS-3：腾讯行情 -> {name, price, cap_yi}（市值单位亿元，[43]/[44] 取较大非零值）。"""
    txt = curl("https://qt.gtimg.cn/q=" + _sym(code), referer="https://gu.qq.com/")
    if "~" not in txt:
        return {}
    try:
        f = txt.split('="', 1)[1].rsplit('"', 1)[0].split("~")
        caps = [x for x in (f[43], f[44]) if x not in ("", "0.00")]
        return dict(name=f[1], price=float(f[3]),
                    cap_yi=float(max(caps, key=float)) if caps else None)
    except (IndexError, ValueError):
        return {}


def fetch_index_quote(secid):
    """DS-1：东财指数行情（点位 + 流通市值）。限流时返回 {}，由 snapshot 兜底。"""
    for host in ("push2", "1.push2", "82.push2", "push2delay"):
        txt = curl("https://%s.eastmoney.com/api/qt/stock/get?secid=%s"
                   "&fields=f43,f57,f58,f116,f117" % (host, secid),
                   referer="https://quote.eastmoney.com/")
        try:
            d = json.loads(txt)["data"]
        except (ValueError, KeyError, TypeError):
            continue
        if d and d.get("f117"):
            return d
    return {}


# ---------------------------------------------------------------- 3. 缓存与刷新
def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def _age_days(meta):
    try:
        return (datetime.now() - datetime.fromisoformat(meta["fetched_at"])).days
    except (KeyError, TypeError, ValueError):
        return 10 ** 6


def refresh(force=False, verbose=True):
    """刷新 gmbd 锚点 / 日 K / 实时行情 -> `data/_shares_cache.json`。返回 (asof, fails)。

    失败的项目保留旧缓存并记入 fails；已有缓存时不阻断流程（N-03 降级链）。
    """
    cache = _read_json(CACHE)
    old = cache.get("gmbd", {})
    fails = {}
    if not force and _age_days(cache.get("_meta", {})) < REFRESH_DAYS:
        if verbose:
            print(f"[缓存] {os.path.basename(CACHE)} 新鲜"
                  f"（{cache['_meta']['fetched_at'][:10]}），跳过刷新")
        return cache.get("_meta", {}).get("asof", ""), fails

    gmbd, klines, quotes = {}, {}, {}
    for leg in LEG_ORDER:
        for code in PROFILES[leg]["cands"]:
            g = fetch_gmbd(code)
            if g:
                gmbd[code] = g
            elif code in old:
                gmbd[code] = old[code]
                fails[code] = "gmbd 取空，沿用缓存"
            else:
                fails[code] = "gmbd 取空且无缓存"
            k = fetch_kline(_sym(code))
            if k:
                klines[_sym(code)] = k
            q = fetch_tencent(code)
            if q:
                quotes[code] = q
            if verbose:
                print(f"  [{leg:5s}] {code:6s} gmbd "
                      f"{('n=%d' % len(g)) if g else '--':8s} K线 "
                      f"{len(k['dates']) if k else 0:4d}  "
                      f"{(quotes.get(code) or {}).get('name', '')}")
        p = PROFILES[leg]["index"]
        if p and p.get("sina"):
            k = fetch_kline(p["sina"])
            if k:
                klines[p["sina"]] = k
            if verbose:
                print(f"  [{leg:5s}] 指数 {p['sina']}  {len(k['dates']) if k else 0:4d}")

    for s, k in cache.get("klines", {}).items():      # 本次没取到的日 K 沿用缓存
        klines.setdefault(s, k)
    asof = max((v["dates"][-1] for v in klines.values() if v.get("dates")), default="")
    _write_json(CACHE, dict(
        _meta=dict(fetched_at=datetime.now().replace(microsecond=0).isoformat(),
                   asof=asof, anchor_floor=ANCHOR_FLOOR, head_n=HEAD_N,
                   note="gmbd=季报期末份额(亿份)/净资产(亿元); klines=新浪日K; quotes=腾讯实时"),
        gmbd=gmbd, klines=klines, quotes=quotes))

    snap = _read_json(FLOAT_SNAP)
    for leg in LEG_ORDER:
        p = PROFILES[leg]["index"]
        if not (p and p.get("secid")):
            continue
        d = fetch_index_quote(p["secid"])
        if d.get("f117"):
            snap[p["secid"]] = dict(f57=d.get("f57"), f58=d.get("f58") or p["quote_name"],
                                    f43=d.get("f43"), f116=d.get("f116"), f117=d["f117"],
                                    asof=asof, src="live")
            if verbose:
                print(f"  [指数] {p['secid']} 实时流通市值 {d['f117'] / 1e8:,.0f} 亿")
    if snap:
        snap["_note"] = ("指数流通市值快照：push2 实时抓取失败时由本文件兜底。"
                         "f117=流通市值(元), f43=指数点位×100。")
        _write_json(FLOAT_SNAP, snap)
    return asof, fails


def cache_ready():
    return bool(_read_json(CACHE).get("gmbd"))


# ---------------------------------------------------------------- 4. 序列装配（R-05~R-08）
def _interp(anchors, dates):
    """锚点间分段线性内插；**锚点之前不产出**（NaN），锚点之后常数钳制（R-05 端点精确）。"""
    ax = [pd.Timestamp(d).toordinal() for d, _ in anchors]
    av = [v for _, v in anchors]
    if len(ax) < 2:
        return pd.Series(float("nan"), index=dates, dtype="float64")
    ords = [d.toordinal() for d in dates]
    out, lo = [], 0
    for x in ords:
        if x < ax[0]:
            out.append(float("nan"))
        elif x >= ax[-1]:
            out.append(av[-1])
        else:
            while lo < len(ax) - 2 and ax[lo + 1] <= x:
                lo += 1
            x0, x1, v0, v1 = ax[lo], ax[lo + 1], av[lo], av[lo + 1]
            out.append(v0 + (v1 - v0) * (x - x0) / (x1 - x0))
    return pd.Series(out, index=dates, dtype="float64")


def head_funds(leg):
    """R-01：按最新季报净资产降序取前 N -> [(code, [[date, shares, nav]])]。"""
    gmbd = _read_json(CACHE).get("gmbd", {})
    items = [(c, gmbd[c]) for c in PROFILES[leg]["cands"] if gmbd.get(c)]
    items.sort(key=lambda kv: -kv[1][-1][2])
    return items[:PROFILES[leg]["n"]]


def leg_axis(leg):
    """该腿价格轴 = 场外复权净值日期（信号、回测、记账同一口径，与主策略一致）。"""
    return vdata.series_for_leg(leg).dropna()


def shares_frame(leg, axis=None):
    """头部 ETF 份额 -> DataFrame(index=交易日, columns=code)，单位亿份。

    只有「该基金首个季报锚点之后」才有值；其余为 NaN（不向前填充，避免伪造 0 增长）。
    """
    axis = leg_axis(leg) if axis is None else axis
    cols = {}
    for code, g in head_funds(leg):
        anchors = [(d, sh) for d, sh, _ in g]
        if len(anchors) >= 2:
            cols[code] = _interp(anchors, axis.index)
    return pd.DataFrame(cols, index=axis.index)


def _kline_series(sina_sym):
    node = _read_json(CACHE).get("klines", {}).get(sina_sym)
    if not node or not node.get("dates"):
        return None
    return pd.Series(node["close"], index=pd.to_datetime(node["dates"]), dtype="float64").sort_index()


def index_close(leg):
    """该腿指数收盘（仅 A 股腿有；其它腿返回 None，图表退回净值）。"""
    p = PROFILES[leg]["index"]
    return _kline_series(p["sina"]) if p and p.get("sina") else None


def index_float(leg):
    """R-07：-> (float_now 亿元, close_now, quote_src)。取不到返回 (None, None, "missing")。"""
    p = PROFILES[leg]["index"]
    if not p:
        return None, None, "n/a"
    node = _read_json(FLOAT_SNAP).get(p["secid"])
    if not node:
        return None, None, "missing"
    return node["f117"] / 1e8, node["f43"] / 100.0, node.get("src", "snapshot")


def ratio_series(leg, axis=None):
    """R-06~R-08 严格口径：头部 ETF 市值 ÷ 指数流通市值（%）。

    返回 DataFrame(index, [cap_yi, idx_close, float_yi, ratio_pct])；
    非 A 股腿 / 缺指数流通市值 / 头部不全 -> None。
    """
    if not PROFILES[leg]["float_ok"]:
        return None
    float_now, close_now, src = index_float(leg)
    idx = index_close(leg)
    if not float_now or not close_now or idx is None:
        return None
    axis = leg_axis(leg) if axis is None else axis
    cap = {}
    for code, g in head_funds(leg):
        px = _kline_series(_sym(code))
        anchors = [(d, sh) for d, sh, _ in g]
        if px is None or len(anchors) < 2:
            return None
        sh = _interp(anchors, axis.index)
        price = px.reindex(axis.index).ffill()       # R-06：停牌前向填充
        cap[code] = sh * price
    if len(cap) < PROFILES[leg]["n"]:
        return None
    df = pd.DataFrame(cap, index=axis.index).dropna()
    if df.empty:
        return None
    idx_r = idx.reindex(df.index).ffill()
    float_yi = float_now * idx_r / close_now          # R-07 价格等比代理
    out = pd.DataFrame({"cap_yi": df.sum(axis=1), "idx_close": idx_r, "float_yi": float_yi})
    out["ratio_pct"] = 100.0 * out["cap_yi"] / out["float_yi"]
    SOURCE_NOTE[leg] = (f"渗透率 = {len(cap)} 只头部 ETF 市值 ÷ 指数流通市值"
                        f"（流通市值源 {src}，价格等比代理）")
    return out


def live_shares(leg):
    """R-04/R-10：腾讯「市值 / 价格」估当前份额 -> {code: (shares, ok, why)}。"""
    quotes = _read_json(CACHE).get("quotes", {})
    out = {}
    for code, g in head_funds(leg):
        q = quotes.get(code) or {}
        base = g[-1][1]
        cap, price = q.get("cap_yi"), q.get("price")
        if not cap or not price or base <= 0:
            out[code] = (None, False, "无实时行情")
            continue
        s_now = cap / price
        r = s_now / base
        out[code] = (s_now, 0.5 <= r <= 2.0,
                     f"实时/季报 = {r:.2f}" + ("" if 0.5 <= r <= 2.0 else "（超 R-10 区间，丢弃）"))
    return out


def leg_meta(leg):
    """报告用元信息：名称 / 角色 / 目标带 / 执行代码 / 头部名单。"""
    cfg = vrule.LEGS[leg]
    heads = head_funds(leg)
    return dict(leg=leg, name=cfg["name"], role=cfg["role"], band=float(cfg["band"]),
                exec_code=cfg["exec"], exec_name=cfg["exec_name"],
                head=[c for c, _ in heads],
                head_names={c: (_read_json(CACHE).get("quotes", {}).get(c) or {}).get("name", c)
                            for c, _ in heads},
                last_anchor=max((g[-1][0] for _, g in heads), default=""))


def main():
    """自检 / 手动刷新：python strategies/vol_legs/v2_flow/flowdata.py [--refresh]"""
    if "--refresh" in sys.argv:
        print("刷新份额/日K/实时行情 ...")
        asof, fails = refresh(force=True)
        print(f"完成 asof={asof} 失败 {len(fails)} 项: {fails}")
    print(f"\n缓存: {CACHE}")
    print(f"      {'存在' if cache_ready() else '缺失（先跑 --refresh）'}")
    for leg in LEG_ORDER:
        m = leg_meta(leg)
        df = shares_frame(leg)
        r = ratio_series(leg)
        first = df.dropna(how="all").index[0].date() if len(df.dropna(how="all")) else "—"
        last = df.dropna(how="all").index[-1].date() if len(df.dropna(how="all")) else "—"
        print(f"  {m['name']:8s} 头部 {m['head']} · 最新季报 {m['last_anchor']} · "
              f"份额序列 {first} ~ {last} · "
              + (f"渗透率 {r['ratio_pct'].iloc[-1]:.3f}%({r.index[-1].date()})" if r is not None
                 else "渗透率 无（该腿无标的指数流通市值口径）"))


if __name__ == "__main__":
    main()
