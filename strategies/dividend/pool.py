# -*- coding: utf-8 -*-
r"""红利基金池 + 净值/估值数据层（dividend 策略专用）· **全真实数据**

背景
----------------------------------------------------------------------
原 dividend 策略是**单标的**（中证红利 012644/515080）估值择时。本轮把「各类红利
基金」纳入本策略的标的池，用于横向打分 + 择优推荐：

  * 表内 —— 用户《红利低波基金打分详情》指定的 5 只（纳入，但**不采用其数值**）；
  * 对照 —— 补足红利各主要大类（红利低波 / 中证红利 / 上证红利 / 深证红利 / 沪港深红利 /
    红利机会 / 央企红利）的代表。

**一指数一标的（2026-09-12 去重）**：池内**同跟踪指数只保留 1 只**（否则估值完全相同、
业绩近乎重复，打分表会被同一指数刷屏）。保留规则：① 历史更长（覆盖 3/5/10 年窗更多）→
② 场外 C 类 / 联接（与本策略执行通道一致）→ ③ 规模更大。落选者登记在 `RESERVE`，不进评分。

> 2026-09-12 二次修订：用户要求「全按真实数据进行评分」。此前对表内 5 只使用过用户表
> 快照（`TABLE_SNAPSHOT`）——**已删除**，改为全部走公开数据源。表中数据本身存在指数
> 归属错误（020603 被标成「红利低波100」）与口径差异（PE 分位用 10 年窗），不再引用。

数据源（全部可自动获取，缓存后离线可跑）
----------------------------------------------------------------------
  * 净值：xalpha.fundinfo(code).price 的 **totvalue（累计净值）** —— 含分红再投，
    场内 ETF 与场外联接同口径，是红利基金唯一正确的收益口径。
  * PE-TTM：中证指数官网 `index-perf` 的 `peg`（官方静态市盈率），按基金**跟踪指数**取。
  * PE 分位：由中证官网 `peg` 历史自算「过去最多 10 年」累计分位
    （与 `data.py::_daily_percentile` 同口径；中证红利回测用的就是这个 10 年窗口径）。
  * 股息率：蛋卷 `index_eva` 的指数股息率（按跟踪指数取）。

**覆盖缺口（真实源不可得，如实标注，不编造）**：
  * `008164` 跟踪「标普中国A股大盘红利低波动50」—— 中证官网与蛋卷均无此指数条目
    （蛋卷的 `CSPSADRP` 「标普红利」是标普中国A股红利机会指数, 另一个指数），
    故该基金无 PE / PE 分位 / 股息率 → 价值分不可算, 总分退化为**业绩分**。
  * `021551` / `008115` 跟踪 930955（中证红利低波动100）—— 中证官网有 `peg`（PE 可得），
    但蛋卷无该指数条目 → 股息率缺失。

落位（AGENTS §8.4 ② 策略自己的数据）
----------------------------------------------------------------------
  strategies/dividend/data/_fund_nav.json      净值缓存
  strategies/dividend/data/_index_peg.json     中证官网 peg 历史缓存（自算分位用）
  strategies/dividend/data/_valuation_snapshot.json  蛋卷指数估值缓存
  strategies/dividend/data/_dividend_pool.json 池的 JSON 镜像（供外部读取）
"""
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)
_ROOT = os.path.dirname(os.path.dirname(_DIR))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

DATA_DIR = os.path.join(_DIR, "data")
NAV_CACHE = os.path.join(DATA_DIR, "_fund_nav.json")
PEG_CACHE = os.path.join(DATA_DIR, "_index_peg.json")
CSV_CACHE = os.path.join(DATA_DIR, "_csindex_value.json")
VAL_CACHE = os.path.join(DATA_DIR, "_valuation_snapshot.json")
MX_CACHE = os.path.join(DATA_DIR, "_mx_valuation.json")
POOL_JSON = os.path.join(DATA_DIR, "_dividend_pool.json")
# 10Y 国债（中债月末）—— 与 core_rotation / data.py 共用的**数据类**缓存，按 AGENTS §8 留在仓库 data/
BOND_10Y_CACHE = os.path.join(_ROOT, "data", "_bt_caches", "bond10y_m.csv")
Y10_FALLBACK = 1.68           # 读不到缓存时的缺省值（2026-09 实测 ≈1.68%）

_UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")}
_DJ_URL = "https://danjuanfunds.com/djapi/index_eva/dj"
_DJ_UA = dict(_UA, Referer="https://danjuanfunds.com/")
_CSI_URL = "https://www.csindex.com.cn/csindex-home/perf/index-perf"

CSI_START = "2016-01-01"      # peg 抓取起点（为 10 年分位留足历史）
PE_WINDOW_DAYS = 10 * 244     # 10 年 = 10×244（与 data.py 的 PE_WINDOW_DAYS 同口径）

# ----------------------------------------------------------------------
# 基金池（权威源）。字段:
#   code      基金代码（场外 C 类 / 场内 ETF）
#   name      简称
#   csi       中证官网指数代码（PE-TTM 来源；空 = 该指数不在中证官网）
#   dj        蛋卷指数代码（股息率 + 无 csi 时的 PE/分位来源；空 = 蛋卷无此指数）
#   idx_name  跟踪指数名
#   cat       红利大类
#   role      "表内" = 用户表指定纳入；"对照" = 补足历史 / 基准
#   channel   "场外" / "场内"
#
# 指数归属已按「净值区间涨幅 × 中证官网区间涨幅」交叉验证（2026-09）:
#   930955 中证红利低波动100(100只) -> 021551/008115
#   H30269 中证红利低波动(50只)     -> 020603/512890
# ----------------------------------------------------------------------
POOL = [
    # —— 一指数一标的（2026-09-12 去重：同一跟踪指数只保留 1 只；落选者见 RESERVE）——
    # 008164 跟踪的标普指数在中证官网/蛋卷/东财指数库三源皆无 -> 用 515450 持仓穿透估算
    dict(code="008164", name="南方标普红利低波50C", csi=None, dj=None, mx="SPCALVHD50.HOLDINGS",
         idx_name="标普中国A股大盘红利低波动50", cat="红利低波", role="表内", channel="场外",
         inner="515450"),
    dict(code="008115", name="天弘中证红利低波100C", csi="930955", dj=None, mx="930955.CSI",
         idx_name="中证红利低波动100(930955)", cat="红利低波", role="表内", channel="场外"),
    dict(code="007467", name="华泰柏瑞中证红利低波ETF联接C", csi="H30269", dj="CSIH30269",
         mx="H30269.CSI", idx_name="中证红利低波动(H30269)", cat="红利低波", role="对照",
         channel="场外", inner="512890"),
    dict(code="007606", name="嘉实沪深300红利低波C", csi="930740", dj="CSI930740", mx="930740.CSI",
         idx_name="沪深300红利低波动(930740)", cat="红利低波", role="表内", channel="场外"),
    dict(code="007760", name="景顺长城沪港深红利成长低波C", csi="931157", dj="CSI931157", mx="931157.CSI",
         idx_name="沪港深红利成长低波(931157)", cat="沪港深红利", role="对照", channel="场外"),
    dict(code="090010", name="大成中证红利指数A", csi="000922", dj="SH000922", mx="000922.SH",
         idx_name="中证红利(000922)", cat="中证红利", role="对照", channel="场外"),
    dict(code="012762", name="华泰柏瑞上证红利ETF联接C", csi="000015", dj="SH000015", mx="000015.SH",
         idx_name="上证红利(000015)", cat="上证红利", role="对照", channel="场外"),
    dict(code="159905", name="工银深证红利ETF", csi=None, dj="SZ399324", mx="399324.SZ",
         idx_name="深证红利(399324, 深交所指数)", cat="深证红利", role="对照", channel="场内"),
    dict(code="005125", name="华宝标普中国A股红利机会ETF联接C", csi=None, dj="CSPSADRP", mx=None,
         idx_name="标普中国A股红利机会", cat="红利机会", role="对照", channel="场外"),
    dict(code="022720", name="广发中证国新港股通央企红利ETF联接C", csi="931722", dj=None, mx="931722.CSI",
         idx_name="国新港股通央企红利(931722)", cat="央企红利", role="对照", channel="场外",
         inner="520900"),
    dict(code="021562", name="天弘中证央企红利50指数发起C", csi="931231", dj=None, mx="931231.CSI",
         idx_name="央企红利50(931231)", cat="央企红利", role="对照", channel="场外"),
    # —— 第二批扩充：因子类缺口（2026-09-12）——
    dict(code="016441", name="华夏中证红利质量ETF联接C", csi="931468", dj=None, mx="931468.CSI",
         idx_name="中证红利质量(931468)", cat="红利质量", role="对照", channel="场外"),
    dict(code="024565", name="易方达中证红利价值ETF联接C", csi="H30270", dj=None, mx="H30270.CSI",
         idx_name="中证红利价值(H30270)", cat="红利价值", role="对照", channel="场外"),
    dict(code="561060", name="华安中证国有企业红利ETF", csi="000824", dj=None, mx=None,
         idx_name="中证国有企业红利(000824)", cat="国企红利", role="对照", channel="场内"),
    dict(code="561580", name="华泰柏瑞中证中央企业红利ETF", csi="000825", dj=None, mx=None,
         idx_name="中证中央企业红利(000825)", cat="央企红利", role="对照", channel="场内"),
    dict(code="563180", name="银华中证高股息策略ETF", csi="H30366", dj=None, mx="H30366.CSI",
         idx_name="中证高股息策略(H30366)", cat="高股息策略", role="对照", channel="场内"),
    # —— 第二批扩充：港股族缺口（2026-09-12）——
    dict(code="018388", name="华泰柏瑞中证港股通高股息投资ETF联接C", csi="930914", dj=None,
         mx="930914.CSI", idx_name="中证港股通高股息投资(930914)", cat="港股红利",
         role="对照", channel="场外"),
    dict(code="021143", name="华夏港股通央企红利ETF联接C", csi="931233", dj=None, mx="931233.CSI",
         idx_name="中证港股通央企红利(931233)", cat="港股红利", role="对照", channel="场外"),
    dict(code="004533", name="民生加银中证港股通高股息精选C", csi="930839", dj=None, mx="930839.CSI",
         idx_name="中证港股通高股息精选(930839)", cat="港股红利", role="对照", channel="场外"),
]

# ----------------------------------------------------------------------
# RESERVE：与池内某只**跟踪同一指数**、因而未进打分池的标的（备用申赎通道 / A 类替代）。
# 不进评分、不进回测；只登记，便于换通道时取用。
# ----------------------------------------------------------------------
RESERVE = [
    dict(code="021551", name="博时中证红利低波100C", idx_name="中证红利低波动100(930955)",
         peer="008115", reason="同指数；历史仅 2.2 年(2024-07)，短于 008115 的 6.7 年"),
    dict(code="512890", name="华泰柏瑞中证红利低波ETF", idx_name="中证红利低波动(H30269)",
         peer="007467", reason="同指数；场内 ETF —— 007467 即其场外联接 C，保留联接以保证执行通道一致"),
    dict(code="007466", name="华泰柏瑞中证红利低波ETF联接A", idx_name="中证红利低波动(H30269)",
         peer="007467", reason="同指数；A 类（申购费前置），本策略走 C 类"),
    dict(code="020603", name="易方达中证红利低波动ETF联接C", idx_name="中证红利低波动(H30269)",
         peer="007467", reason="同指数；历史仅 2.5 年(2024-03)"),
    dict(code="012644", name="招商中证红利ETF联接C", idx_name="中证红利(000922)",
         peer="090010", reason="同指数；历史仅 4.5 年(2022-02)。注意：rule.py 单标的模式仍以它为场外执行主仓"),
]

CODES = [r["code"] for r in POOL]
BY_CODE = {r["code"]: r for r in POOL}
CSI_CODES = sorted({r["csi"] for r in POOL if r["csi"]})


# ----------------------------------------------------------------------
# 池的 JSON 镜像
# ----------------------------------------------------------------------
def dump_pool(path=POOL_JSON):
    """写池的 JSON 镜像（供外部流程读取）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {"generated_at": datetime.now().strftime("%Y-%m-%d"),
               "rule": "同跟踪指数只保留 1 只（历史更长 > 场外C类 > 规模更大）；落选者见 reserve",
               "count": len(POOL), "funds": POOL, "reserve": RESERVE}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return path


# ----------------------------------------------------------------------
# 净值层：xalpha 取 totvalue（累计净值）, 落缓存
# ----------------------------------------------------------------------
def _fetch_nav(code):
    """单只基金 -> DataFrame(date, totvalue)。"""
    import xalpha as xa
    p = xa.fundinfo(code).price.copy()
    p["date"] = pd.to_datetime(p["date"])
    p = p[["date", "totvalue"]].dropna()
    return p.sort_values("date").reset_index(drop=True)


def load_navs(codes=None, refresh=False, cache=NAV_CACHE):
    """基金池净值宽表 -> DataFrame(index=date, columns=code, values=totvalue)。

    命中缓存直接读（离线可跑）；refresh=True 或缓存缺失时联网抓取。
    """
    codes = codes or CODES
    raw = {}
    if not refresh and os.path.exists(cache):
        raw = json.load(open(cache, encoding="utf-8"))
    missing = [c for c in codes if not raw.get(c)]
    if refresh or missing:
        for c in (codes if refresh else missing):
            try:
                df = _fetch_nav(c)
            except Exception as e:  # noqa: BLE001 - 单只失败不影响全池
                print(f"[warn] {c} 净值抓取失败: {type(e).__name__}: {e}", file=sys.stderr)
                continue
            raw[c] = {"dates": [str(d.date()) for d in df["date"]],
                      "totvalue": [float(x) for x in df["totvalue"]]}
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache, "w", encoding="utf-8") as fh:
            json.dump(raw, fh, ensure_ascii=False)
    ser = {}
    for c in codes:
        d = raw.get(c)
        if not d:
            continue
        s = pd.Series(d["totvalue"], index=pd.to_datetime(d["dates"]), dtype=float)
        ser[c] = s[~s.index.duplicated(keep="last")].sort_index()
    return pd.DataFrame(ser).sort_index()


# ----------------------------------------------------------------------
# 估值层 ①：中证官网 peg（PE-TTM）+ 自算 10 年分位
# ----------------------------------------------------------------------
def _fetch_csi_peg(code, start=CSI_START):
    """中证官网 index-perf -> DataFrame(date, peg)。"""
    import requests
    url = (f"{_CSI_URL}?indexCode={code}&startDate={start.replace('-', '')}"
           f"&endDate={datetime.now():%Y%m%d}")
    raw = requests.get(url, headers=_UA, timeout=25).json().get("data") or []
    if not raw:
        raise RuntimeError(f"中证官网返回空数据: indexCode={code}")
    df = pd.DataFrame(raw)
    df["date"] = pd.to_datetime(df["tradeDate"], format="%Y%m%d")
    df["peg"] = pd.to_numeric(df["peg"], errors="coerce")
    return df[["date", "peg"]].dropna().sort_values("date").reset_index(drop=True)


def load_peg(codes=None, refresh=False, cache=PEG_CACHE):
    """中证官网 peg 宽表 -> DataFrame(index=date, columns=indexCode)。"""
    codes = codes or CSI_CODES
    raw = {}
    if not refresh and os.path.exists(cache):
        raw = json.load(open(cache, encoding="utf-8"))
    missing = [c for c in codes if not raw.get(c)]
    if refresh or missing:
        for c in (codes if refresh else missing):
            try:
                df = _fetch_csi_peg(c)
            except Exception as e:  # noqa: BLE001 - 单指数失败不影响全池
                print(f"[warn] 中证官网 peg 抓取失败 {c}: {type(e).__name__}: {e}", file=sys.stderr)
                continue
            raw[c] = {"dates": [str(d.date()) for d in df["date"]],
                      "peg": [float(x) for x in df["peg"]]}
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache, "w", encoding="utf-8") as fh:
            json.dump(raw, fh, ensure_ascii=False)
    ser = {}
    for c in codes:
        d = raw.get(c)
        if not d:
            continue
        s = pd.Series(d["peg"], index=pd.to_datetime(d["dates"]), dtype=float)
        ser[c] = s[~s.index.duplicated(keep="last")].sort_index()
    return pd.DataFrame(ser).sort_index()


def _percentile(peg, window=PE_WINDOW_DAYS):
    """peg -> 过去最多 window 个观测的累计分位(0-1)。与 data.py::_daily_percentile 同口径。"""
    v = peg.to_numpy(dtype=float)
    n = len(v)
    out = np.full(n, np.nan)
    lo = 0
    for i in range(n):
        if i - window + 1 > lo:
            lo = i - window + 1
        out[i] = float((v[lo:i + 1] < v[i]).mean())
    return pd.Series(out, index=peg.index)


def csi_valuation(peg=None):
    """中证官网 peg -> {indexCode: {pe, pe_pct, as_of}}（pe_pct 为自算 10 年分位）。"""
    peg = load_peg() if peg is None else peg
    out = {}
    for code in peg.columns:
        s = peg[code].dropna()
        if s.empty:
            continue
        pct = _percentile(s)
        out[code] = dict(pe=round(float(s.iloc[-1]), 2),
                         pe_pct=round(float(pct.iloc[-1]), 4),
                         as_of=str(s.index[-1].date()))
    return out


# ----------------------------------------------------------------------
# 估值层 ②：蛋卷指数估值（股息率；无中证官网覆盖时的 PE/分位）
# ----------------------------------------------------------------------
def fetch_danjuan():
    """蛋卷 index_eva -> {index_code: {pe, pe_pct, dy}}（dy 为百分数）。"""
    import requests
    r = requests.get(_DJ_URL, headers=_DJ_UA, timeout=15)
    r.raise_for_status()
    items = (r.json().get("data") or {}).get("items") or []
    out = {}
    for x in items:
        code = x.get("index_code")
        pe, pep, dy = x.get("pe"), x.get("pe_percentile"), x.get("yeild")
        if not code or pe is None or pep is None or dy is None:
            continue
        out[code] = dict(pe=float(pe), pe_pct=float(pep), dy=round(float(dy) * 100, 2))
    return out


def _danjuan(refresh=False):
    cached = {}
    if os.path.exists(VAL_CACHE):
        cached = json.load(open(VAL_CACHE, encoding="utf-8"))
    if not refresh and cached.get("danjuan"):
        return cached["danjuan"]
    try:
        dj = fetch_danjuan()
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 蛋卷估值抓取失败, 用缓存: {type(e).__name__}: {e}", file=sys.stderr)
        return cached.get("danjuan", {})
    if dj:
        os.makedirs(os.path.dirname(VAL_CACHE), exist_ok=True)
        with open(VAL_CACHE, "w", encoding="utf-8") as fh:
            json.dump({"as_of": datetime.now().strftime("%Y-%m-%d"), "danjuan": dj},
                      fh, ensure_ascii=False, indent=2)
    return dj


# ----------------------------------------------------------------------
# 估值层 ③：mx-ds-mcp 回填（东方财富 PE(TTM)/PB/年内分位）
# ----------------------------------------------------------------------
def load_mx(cache=MX_CACHE):
    """读 agent 回填的 MCP 估值文件 -> {index_code: {pe_ttm, pb, pe_pct_ytd}}。

    脚本不联网调 MCP（MCP 是 agent 工具）；agent 刷新后写本文件，这里只读。
    """
    if not os.path.exists(cache):
        return {}
    try:
        d = json.load(open(cache, encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"[warn] MCP 回填文件解析失败: {type(e).__name__}: {e}", file=sys.stderr)
        return {}
    return d.get("index") or {}


# ----------------------------------------------------------------------
# 估值层 ④：中证指数官网「指数估值」（经 akshare）—— 提供官方**股息率**
# ----------------------------------------------------------------------
def _fetch_csindex_value(code):
    """中证官网指数估值（akshare stock_zh_index_value_csindex）-> dict。

    列序: 日期/代码/全称/简称/英文全称/英文简称/市盈率1/市盈率2/股息率1/股息率2。
    实测 **股息率1** 与蛋卷口径一致（000922 4.16 vs 蛋卷 4.22；H30269 4.22 vs 4.28；
    930740 4.28 vs 4.26；931157 4.48 vs 4.46；000015 3.99 vs 3.99），故取股息率1。
    akshare 为**可选依赖**（延迟 import）：未安装时本层跳过, 自动退回蛋卷, 不报错。
    """
    import akshare as ak
    df = ak.stock_zh_index_value_csindex(symbol=code)
    r = df.iloc[0].tolist()
    return dict(name=str(r[3]), as_of=str(r[0])[:10],
                pe=float(r[6]), pe2=float(r[7]), dy=float(r[8]), dy2=float(r[9]),
                n_days=int(len(df)))


def load_csindex_value(codes=None, refresh=False, cache=CSV_CACHE):
    """-> {index_code: {name, as_of, pe, pe2, dy, dy2, n_days}}（只覆盖中证/上证指数）。"""
    codes = codes or CSI_CODES
    raw = {}
    if not refresh and os.path.exists(cache):
        raw = json.load(open(cache, encoding="utf-8"))
    missing = [c for c in codes if not raw.get(c)]
    if refresh or missing:
        for c in (codes if refresh else missing):
            try:
                raw[c] = _fetch_csindex_value(c)
            except Exception as e:  # noqa: BLE001 - 单指数失败不影响全池
                msg = "akshare 未安装" if isinstance(e, ImportError) else f"{type(e).__name__}: {e}"
                print(f"[warn] 中证官网指数估值跳过 {c}: {msg}", file=sys.stderr)
                continue
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache, "w", encoding="utf-8") as fh:
            json.dump(raw, fh, ensure_ascii=False, indent=2)
    return raw


def _src_mx(mv):
    """MCP 条目的来源标签：持仓穿透估算 vs 东财指数 PE(TTM)。"""
    return ("mx-ds-mcp(持仓穿透估算)" if (mv or {}).get("method") == "holdings_penetration"
            else "mx-ds-mcp(东财PE-TTM)")


# ----------------------------------------------------------------------
# 汇总：池内每只基金的真实估值（含逐维来源；缺失即 None, 不编造）
# ----------------------------------------------------------------------
def valuation(refresh=False):
    """-> {code: {pe, pe_cs, pe_ttm_mx, pe_static, pe_pct, pe_pct_ytd, pb, dy, dy_dj, src_*}}。

    **打分口径以中证官网为单一源**（保证 PE 水平与 PE 分位同源、内部一致）:
      同源规则 : PE 分位只可能来自 peg(10年历史) 或 蛋卷(全历史)，二者均有独立 PE 序列；
                故头部 PE **跟随分位源取同一序列**（cv 在 → pe_peg，dv 在 → dv.pe），
                「指数估值·市盈率1」(pe_cs) 仅 20 日、无历史，仅作交叉校验列、不进打分头部，
                以免出现「PE 高却分位低」的两口径错配（实测 931468 两源差 16%）。
      PE 分位   : 官网 peg 自算「过去最多 10 年」累计分位 → 蛋卷
      股息率    : 中证官网指数估值「股息率1」 → 蛋卷
    东财(mx-ds-mcp) 的 PE(TTM)/PB/年内分位作**交叉校验列**，不进打分 —— 因其 TTM 口径
    与本源的静态口径混用会使「PE 分」与「分位分」不同源（实测 931157 两源差 1.5）。
    """
    dj = _danjuan(refresh=refresh)
    csi = csi_valuation(load_peg(refresh=refresh))
    csv_ = load_csindex_value(refresh=refresh)
    mx = load_mx()
    out = {}
    for r in POOL:
        cv = csi.get(r["csi"]) if r["csi"] else None
        sv = csv_.get(r["csi"]) if r["csi"] else None
        dv = dj.get(r["dj"]) if r["dj"] else None
        mv = mx.get(r["mx"]) if r["mx"] else None
        pe_cs = sv["pe"] if sv else None
        pe_peg = cv["pe"] if cv else None
        pe_mx = mv.get("pe_ttm") if mv else None
        # —— 同源原则：头部 PE 必须与 PE 分位来自同一序列（否则打分不公平）——
        # PE 分位只可能来自 peg(中证官网, 10年历史) 或 蛋卷(全历史)。故头部 PE 严格跟随
        # 分位源：分位取 peg → 头部用 pe_peg；分位取蛋卷 → 头部用 dv.pe；
        # 只在「两源皆无分位」时才回退 pe_cs / 东财(此时本就无分位, 不存在同源问题)。
        # 若不跟随, 会出现「PE 高却分位低」的错配(实测 931468 两源差 16%; 159905 东财PE vs 蛋卷分位)。
        if cv and pe_peg is not None:
            pe, src_pe = pe_peg, "中证官网 peg(静态)"
        elif dv and dv.get("pe") is not None:
            pe, src_pe = dv["pe"], "蛋卷"
        elif pe_cs is not None:
            pe, src_pe = pe_cs, "中证官网指数估值"
        elif pe_mx is not None:
            pe, src_pe = pe_mx, _src_mx(mv)
        else:
            pe, src_pe = None, "缺失"
        dy_cs = sv["dy"] if sv else None
        dy_mx = mv.get("dy") if mv else None
        dy = next((x for x in (dy_cs, dy_mx, (dv or {}).get("dy")) if x is not None), None)
        out[r["code"]] = dict(
            pe=pe, pe_cs=pe_cs, pe_peg=pe_peg, pe_ttm_mx=pe_mx,
            pe_pct=(cv["pe_pct"] if cv else (dv["pe_pct"] if dv else None)),
            pe_pct_ytd=(mv.get("pe_pct_ytd") if mv else None),
            pb=(mv.get("pb") if mv else None),
            dy=dy, dy_dj=(dv["dy"] if dv else None),
            method=(mv.get("method") if mv else None),
            src_pe=src_pe,
            src_pct=("中证官网 peg·自算10年分位" if cv else ("蛋卷" if dv else "缺失")),
            src_dy=("中证官网指数估值" if dy_cs is not None
                    else (_src_mx(mv) if dy_mx is not None else ("蛋卷" if dv else "缺失"))),
            as_of=(sv["as_of"] if sv else (cv["as_of"] if cv
                                           else datetime.now().strftime("%Y-%m-%d"))),
        )
    return out


def y10_latest(cache=BOND_10Y_CACHE, fallback=Y10_FALLBACK):
    """10Y 国债收益率(%) —— 复用与 core_rotation/data.py 共用的中债月末缓存（数据类）。

    股债收益比 = 指数股息率(%) ÷ 10Y国债(%)，故需要这个分母；读不到则用 fallback 并在
    stderr 提示（不静默假装成功）。
    """
    try:
        raw = pd.read_csv(cache)
        col = "close" if "close" in raw.columns else raw.columns[-1]
        s = pd.to_numeric(raw[col], errors="coerce").dropna()
        if len(s):
            return round(float(s.iloc[-1]), 3)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 10Y 国债缓存读取失败, 用缺省 {fallback}: {type(e).__name__}: {e}",
              file=sys.stderr)
    return fallback


def mom6(navs, asof=None):
    """近 6 月（126 交易日）区间收益(%) —— 仅作信息列保留, 已不参与打分。"""
    end = pd.Timestamp(asof) if asof is not None else navs.index[-1]
    hist = navs.loc[:end]
    win = hist if len(hist) <= 126 else hist.iloc[-127:]
    out = {}
    for c in hist.columns:
        s = win[c].dropna()
        out[c] = round(float(s.iloc[-1] / s.iloc[0] - 1) * 100, 2) if len(s) >= 60 else None
    return out


def rsi14(navs, asof=None, window=14):
    """RSI14（Wilder 平滑）—— 用基金**累计净值**（与业绩分同源，场内/场外同口径）。

    RSI = 100 − 100/(1+RS)，RS = Wilder 平均涨幅 / Wilder 平均跌幅。
    打分含义：<35 视为超卖（便宜）→ 满分；>65 视为超买（贵）→ 零分。
    """
    end = pd.Timestamp(asof) if asof is not None else navs.index[-1]
    hist = navs.loc[:end]
    out = {}
    for c in hist.columns:
        s = hist[c].dropna()
        if len(s) < window + 1:
            out[c] = None
            continue
        d = s.diff().dropna()
        au = d.clip(lower=0).ewm(alpha=1 / window, adjust=False).mean().iloc[-1]
        ad = (-d).clip(lower=0).ewm(alpha=1 / window, adjust=False).mean().iloc[-1]
        if ad <= 0:
            out[c] = 100.0
        else:
            out[c] = round(100 - 100 / (1 + au / ad), 2)
    return out


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(f"池内 {len(POOL)} 只（表内 {sum(r['role'] == '表内' for r in POOL)}）")
    print(f"JSON 镜像: {dump_pool()}")
    nv = load_navs()
    print(f"净值面板: {nv.index[0].date()} ~ {nv.index[-1].date()} · {nv.shape[1]} 只")
    val = valuation()
    print("\n真实估值来源与数值:")
    for r in POOL:
        v = val[r["code"]]
        print(f"  {r['code']} {r['name'][:20]:<22} PE={v['pe']} (官网{v['pe_cs']}/peg{v['pe_peg']}/"
              f"东财TTM{v['pe_ttm_mx']}) 10年分位={v['pe_pct']} 年内分位={v['pe_pct_ytd']} "
              f"PB={v['pb']} 股息率={v['dy']}(蛋卷{v['dy_dj']}) | PE源={v['src_pe']} 股息源={v['src_dy']}")
