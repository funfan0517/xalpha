# -*- coding: utf-8 -*-
"""盘中预演看板 · 把「最新盘中快照」当作收盘价重算 today 的操作，**不写任何数据文件**。

与收盘口径的 `data/_daily_digest.html` 并存（用户 2026-11-06 要求每天出两份）：

    python pipeline/daily_digest.py        # 正式版（收盘口径，产物落盘）
    python pipeline/intraday_preview.py    # 预演版 -> data/_daily_digest_intraday.html

三条铁律：
1. **只读仓库数据**。唯一写出的文件 = `data/_daily_digest_intraday.html`（看板本身）；
   各策略产物一律写进系统临时目录（跑完即删），绝不覆盖 `daily/` 下的 md / json / png。
2. **不碰网络库以外的状态**：所有 `refresh_*` / `ensure_fresh` / `append_latest` 都被替换成
   空操作；进程内另设 `XALPHA_PREVIEW=1`，让会自更新缓存的模块（v5 的 FRED `_dfii10.csv`）也停手。
3. **口径必须写明**：每张卡标注 🟢（盘中价入信号）/ 🟠（盘中价 + 量能按进度折算）/ 🟡（无盘中源）。
   **操作与正式版不一致时，卡片顶部出红色加粗提示条**（`entry["diff"]`）。

用户口径（2026-11-24 定）：**A股/港股取 T+0 盘中数据，美股仍用 T+0 凌晨收盘**。

| 板块 | 预演方式 | 数据 |
|---|---|---|
| 1 永久组合 | 🟡 无盘中源 | 场外累计净值（T 日盘后披露，QDII 滞后 1~2 日） |
| 2 近五年口径 | 🟡 无盘中源 | 研究产物 `_daily_positions_5y.json`（收盘后跑出的排名 + 上一收盘决策），不重算 |
| **2 变种卡（v14~v20）** | 🟠 A股/港股 5 腿重算 · 🟢 美股 2 腿 | 见下「盘中注入」 |
| 3 风格轮动 v0/v5 | 🟢 三个指数用**自己的实时点位**接成今日 bar | `get_rt("SH000688"/"SZ399006"/"SH000922")` |
| 4 红利 V4/V9 | 🟡 无盘中源 | 场外累计净值 + 月度估值分位 |
| 5 技术指标 | 🟢 54 只 ETF 追加今日盘中 bar + 重算全套指标 | `_long_klines.json` + `xa.get_rt` |

**盘中注入（唯一的机制，`strategies/vol_legs/_idxcache.py::frame`）**：环境变量
`XALPHA_INTRADAY_BARS` 携带 `{leg: {date, close, volume}}`，`frame()` 在出口把今日 bar
接到已收盘序列末尾 —— 五个 `_idxcache` 实例同时生效，七个单腿策略与 v12 **一行都不用改**。
- 价格：A股三腿 + 恒生科技用 `get_rt` 的**指数点位**（指数自己就有实时源，不再用 ETF 外推）；
  黄金用 `518880` 实时价；美股两腿**不注入**（库里就是今天凌晨收盘那根）。
- 量能：`rvol` 属强弱档判据，而盘中只有累计量 → 按「已过交易分钟占比」折算当日量
  （实测 14:19 沪深300 累计量占昨日全天 0.81 vs 时间进度 0.83）。含 V12 成分的腿标 🟠。
- 美股两腿：正式版产物若已是今天凌晨那根收盘就直接沿用（用户要求不重跑），
  若产物停在更早（上一次正式流程跑在盘中）则改为重算。

> v2/v3/v6 的预演重算仍在跑（写临时树），但**不进看板**：板块2 自 2026-11-18 起只列 v14~v20。
>
> ⚠ 2026-11-24 起行情库只存**已收盘**的 bar（`data/_market_clock.py`），所以盘中序列末根是
> **T-1**。预演必须把实时价**追加**成一根日期为今天的合成 bar（`_intraday_series` /
> `_intraday_wide`），不能沿用旧的 `px.iloc[-1] = 实时价` —— 那会把今天的价写到昨天的日期上，
> 让整条信号安静地错一天。

机械规则输出，非投资建议。预演**不是**信号：收盘价一翻，结论可能整条变。
"""
from __future__ import annotations

import contextlib
import copy
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import traceback
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import daily_digest as dd  # noqa: E402

CFG = dd.CFG
OUT_HTML = os.path.join(_ROOT, "data", "_daily_digest_intraday.html")
_TMP = None            # 临时树根（镜像仓库布局，供各 parse_* 用 _ROOT 拼接）
_LOG = []

# 盘中快照的日期标签（合成 bar 用；跨零点运行的预演不存在，脚本一次跑完就退出）
_TODAY = datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------- 基础设施
def _mp(name, path):
    """按路径 import 一个模块（策略脚本多以“脚本”形式存在，不是包内模块）。"""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tpath(rel):
    """临时树里与仓库同构的路径（自动建目录）。"""
    p = os.path.join(_TMP, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


@contextlib.contextmanager
def _rooted(root):
    """临时把 daily_digest 的 _ROOT 指到临时树 —— 各 parse_* 的 rel 路径原样复用。"""
    old = dd._ROOT
    dd._ROOT = root
    try:
        yield
    finally:
        dd._ROOT = old


@contextlib.contextmanager
def _isolated(directory, names):
    """隔离导入：仓库里有 5 个 `data.py` / 多个 `rule.py`，全局 sys.modules 会串味。

    进入时把 ``names`` 从 sys.modules 摘掉、把 ``directory`` 顶到 sys.path[0]，
    退出时原样还原 —— 各策略脚本按「脚本所在目录」解析自己的 `import data`。
    """
    old_path = sys.path[:]
    saved = {n: sys.modules.pop(n) for n in names if n in sys.modules}
    sys.path.insert(0, directory)
    try:
        yield
    finally:
        sys.path[:] = old_path
        for n in names:
            sys.modules.pop(n, None)
        sys.modules.update(saved)


@contextlib.contextmanager
def _argv(args):
    old = sys.argv[:]
    sys.argv = args
    try:
        yield
    finally:
        sys.argv = old


def _note(msg):
    _LOG.append(msg)
    print(f"[预演] {msg}")


def _pct(rt):
    """get_rt 的 percent 字段 -> 百分数浮点（'1.23%' / 1.23 / None 都吃得下）。"""
    v = rt.get("percent")
    if v is None:
        return None
    try:
        return float(str(v).replace("%", "").strip())
    except ValueError:
        return None


_RT_RAW = {}           # code -> get_rt 原始返回（含 volume），折算盘中量能用


def _bare(code):
    """`SH000300` -> `000300`（`_idxcache` 用带前缀的代码，`get_rt` 用不带前缀的）。"""
    return code[2:] if len(code) > 6 and code[:2] in ("SH", "SZ") else code


def rt_prices(codes):
    """{code: (price, pct)} —— 取不到就跳过（返回的 dict 里没有该 code）。

    原始返回同时存进 `_RT_RAW`：`get_rt` 对指数也给 `volume`（当日累计），强弱档的
    `rvol` 折算要用它，不必为此再请求一次。
    """
    import xalpha as xa

    gi = _mp("gi_intraday", os.path.join(_ROOT, "_gen_indicators.py"))
    out, fails = {}, {}
    for c in dict.fromkeys(codes):
        # 港股/美股代码本身就是 `get_rt` 的入参（不能再拼 SH/SZ 前缀，否则拼出来的是废代码）
        if c.startswith(("HK", ".")):
            cands = (c,)
        else:
            pref = gi.exchange_prefix(c)
            cands = (pref + c, ("SZ" if pref == "SH" else "SH") + c)
        for full in cands:
            try:
                rt = xa.get_rt(full)
                cur = rt.get("current")
                if cur:
                    out[c] = (float(cur), _pct(rt) or 0.0)
                    _RT_RAW[c] = rt
                    _RT_RAW[_bare(c)] = rt
                    break
            except Exception as exc:  # noqa: BLE001
                fails[c] = f"{type(exc).__name__}: {exc}"
    if fails:
        _note(f"实时价缺失 {len(fails)} 只（沿用缓存末根）: "
              + "、".join(list(fails)[:6]))
    return out


# ---------------------------------------------------------------- 盘中量能的折算
# 八态的强弱档要 `rvol = 当日量 / 20 日均量`（阈值 1.20 / 0.85），而 14:35 只能拿到**当日累计量**。
# 折算：`当日量 ≈ 累计量 / f`，`f` = 已过交易分钟 / 当日总交易分钟。
# 实测标定（2026-11-24 14:19 沪深300）：累计 165.5 亿股 ÷ 昨日全天 203.9 亿 = 0.81，
# 同期时间进度 199/240 = 0.83 → 误差约 2%，且量纲同口径（股）。尾盘放量会让折算略低估。
_SESSIONS = {
    "CN": ((9 * 60 + 30, 11 * 60 + 30), (13 * 60, 15 * 60)),    # 240 分钟
    "HK": ((9 * 60 + 30, 12 * 60), (13 * 60, 16 * 60)),         # 330 分钟
}


def session_progress(market, now=None):
    """已过交易分钟占比：收盘后 1.0、开盘前 0.0。"""
    now = now or datetime.now()
    m = now.hour * 60 + now.minute + now.second / 60.0
    segs = _SESSIONS.get(market)
    if not segs:
        return 1.0
    total = sum(b - a for a, b in segs)
    done = sum(max(0.0, min(m, b) - a) for a, b in segs)
    return max(0.0, min(1.0, done / total)) if total else 1.0


def inject_bars():
    """把 A股/港股/黄金的盘中价 + 折算量能做成注入表（走环境变量，不落盘）。

    美股两腿**不注入**：库里那根已经是「T 日 05:00 凌晨收盘」，与正式版同一根 bar。
    """
    ic = _mp("ip_idxcache", os.path.join(_ROOT, "strategies", "vol_legs", "_idxcache.py"))
    legs = ("a500", "hs300", "kc50", "hkt", "gold")
    bars, miss = {}, []
    for leg in legs:
        code = ic.LEGS[leg][1]
        raw = _RT_RAW.get(code) or _RT_RAW.get(_bare(code)) or {}
        cur = raw.get("current")
        if not cur:
            miss.append(f"{leg}({code})")
            continue
        prog = session_progress(ic.clock.market_of(code))
        bar = {"date": _TODAY, "close": float(cur)}
        vol = raw.get("volume")
        if vol and prog > 0.02:                  # 刚开盘时折算因子太小，宁可不给量能
            bar["volume"] = float(vol) / prog
            bar["progress"] = round(prog, 3)
        bars[leg] = bar
    os.environ[ic.INTRADAY_ENV] = json.dumps(bars, ensure_ascii=False)
    _note(f"注入盘中 bar {len(bars)}/{len(legs)} 条 · 量能按交易进度折算"
          f"（A股 {session_progress('CN'):.0%} / 港股 {session_progress('HK'):.0%}）；"
          "美股两腿用今天凌晨收盘，不注入"
          + (f"；缺 {', '.join(miss)}" if miss else ""))
    return bars


# ---------------------------------------------------------------- 盘中 bar 的接法
# ⚠ 2026-11-24 起行情库**只存已收盘的 bar**（`data/_market_clock.py`：A 股 15:05 / 港股 16:05 /
# 美股北京次日 05:05），所以 14:35 跑时序列末根是**上一交易日**。
# 旧的预演写法是 `px.iloc[-1] = 实时价`，它假设末根是「今天的半根」——在只写收盘的库里，
# 这样等于把今天的价写到昨天的日期上，**整条信号系统性错一天**（而且不会报错）。
# 正确做法：拿实时价在末尾**追加**一根日期为今天的合成 bar；旧库（末根已是今天）仍原地替换。
def _intraday_series(px, price=None, pct=None):
    """把「今日盘中」接到已收盘 Series 的末尾 -> 新 Series（末根日期 = 今天）。"""
    import pandas as pd

    if px is None or not len(px):
        return px
    px = px.copy()
    if price is None:
        price = float(px.iloc[-1]) * (1.0 + float(pct or 0.0) / 100.0)
    if str(px.index[-1].date()) == _TODAY:          # 旧库：今天那根已在，原地替换
        px.iloc[-1] = float(price)
    else:                                            # 新库：末根是 T-1，追加今日 bar
        px.loc[pd.Timestamp(_TODAY)] = float(price)
    return px


def _intraday_wide(wide, pct_by_code):
    """宽表版本：按跟踪 ETF 的实时涨幅外推今日一行（没有代理的列沿用上一收盘）。"""
    import pandas as pd

    if wide is None or not len(wide) or not pct_by_code:
        return wide
    cols = [c for c in wide.columns if c in pct_by_code]
    if not cols:
        return wide
    if str(wide.index[-1].date()) == _TODAY:        # 旧库：原地改今天那行
        for c in cols:
            prev = float(wide[c].iloc[-2]) if len(wide) >= 2 else float(wide[c].iloc[-1])
            wide.iloc[-1, wide.columns.get_loc(c)] = prev * (1 + pct_by_code[c] / 100.0)
        return wide
    row = wide.iloc[[-1]].copy()
    row.index = [pd.Timestamp(_TODAY)]
    for c in cols:
        row.at[pd.Timestamp(_TODAY), c] = float(wide[c].iloc[-1]) * (1 + pct_by_code[c] / 100.0)
    return pd.concat([wide, row])


# ---------------------------------------------------------------- 板块5 技术指标
_V17_DAILY = (
    "strategies/tech_indicators/v17_octo_trend/daily/_v17_octo_daily.json",
    "strategies/tech_indicators/v17_octo_trend/daily/_v17_octo_daily.md",
)


def _stage_v17():
    """把 V17 的每日产物搬进临时树（**不重算**）。

    V17 八态不能盘中重算：强弱回调/反弹由 `rvol`（完整日成交量比）判定，盘中量是半成品，
    硬换实时量会把「弱回调 ↔ 强回调」系统性判反。所以只搬上一收盘的产物并在卡片里标 🟡。
    复制是必须的 —— `parse_tech_indicators` 在 `_rooted(_TMP)` 下按临时树找 signal_json，
    不复制就会显示「无 V17 行」。
    """
    hit = 0
    for rel in _V17_DAILY:
        src = os.path.join(_ROOT, rel)
        if os.path.exists(src):
            shutil.copy2(src, _tpath(rel))
            hit += 1
    _note(f"V17 八态：搬上一收盘产物 {hit}/{len(_V17_DAILY)} 个（盘中量能不全，不重算）")
    return hit


def _mark_v17(entry):
    """给技术指标卡里的 V17 行打 🟡（卡片整体是 🟢，这一行不是）。"""
    for r in entry.get("rows") or []:
        if r.get("c") == "V17 八态":
            # 明细本身是分行的（`td.d` 用 white-space:pre-line），标记单独占一行更清楚
            r["d"] = ("🟡 上一收盘口径（盘中量能不全，八态不重算）\n"
                      + str(r.get("d") or ""))


def preview_tech(rt):
    """把 54 只 ETF 的实时价接成「今日 bar」并重算指标，再跑一遍 _daily_recommend.py。

    库末根是上一交易日（只存已收盘），所以是**追加**今日 bar，不是替换末根。
    """
    _stage_v17()
    rec = _mp("ip_tech", os.path.join(_ROOT, "strategies", "tech_indicators",
                                      "daily", "_daily_recommend.py"))
    # 1) 关掉刷新（ensure_fresh 会写 _long_klines.json / _indicators_hist.json）
    #    但「最新日」必须报**注入后的末根**：库里只存已收盘 bar（末根 T-1），若照抄
    #    `last_dates()`，看板就会写「ETF 行情 T-1」而逐行数字是今天的实时价（自相矛盾），
    #    且所有行会被判成「⚠️陈旧」。所以有实时价的代码一律报到今天。
    def _fresh_dates(codes=None, verbose=True):
        latest = rec._refresh_data.last_dates()
        for code in rt:
            if code in latest:                  # 只有落进了 lk（load 里才会被追加今日 bar）
                latest[code] = _TODAY
        return latest, set()

    rec._refresh_data.ensure_fresh = _fresh_dates
    # 2) 末根换成实时价 + 内存重算指标（_recompute 是纯函数）
    orig_load = rec.base.load

    def load():
        ind, lk = orig_load()
        hit = app = 0
        for code, (price, _p) in rt.items():
            if code not in lk or code not in ind:
                continue
            node = lk[code]
            vols = list(ind[code].get("volume") or [])
            if node["dates"] and node["dates"][-1] == _TODAY:      # 旧库：原地替换今天那根
                node["close"][-1] = price
            else:                                                  # 新库：末根是 T-1，追加今日
                node["dates"].append(_TODAY)
                node["close"].append(price)
                vols = vols[:len(node["dates"]) - 1] + [None]      # 盘中量不完整 -> 不编假量
                app += 1
            close = [float("nan") if v is None else float(v) for v in node["close"]]
            ind[code] = rec._refresh_data._recompute(node["dates"], close, vols)
            hit += 1
        _note(f"技术指标：{hit}/{len(ind)} 只 ETF 已换实时价"
              + (f"（其中 {app} 只在 T-1 序列末尾追加了今日盘中 bar）" if app else ""))
        return ind, lk

    rec.base.load = load
    rec.OUT = _tpath("strategies/tech_indicators/daily/_daily_recommend.md")
    with _argv(["daily_recommend", "--no-refresh"]):
        rec.main()
    try:
        rec.base.load = orig_load
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- 板块2 变种
# 原 v5 趋势闸门已于 2026-11-18 删除（状态机下沉为 strategies/vol_legs/_trend_gate.py）；
# 看板不再读它的产物，预演也就不必重算。
_VOL_SCANS = (
    ("v2", "v2_boll_breakout", "boll", "_v2_boll_daily", False),
    ("v3", "v3_macd_boll", "macd_boll", "_v3_macd_boll_daily", True),
)

# 真实指数没有稳定盘中源：v2/v3/v6 的预演用这些跟踪 ETF 的实时涨幅代理指数当日涨跌。
# 代码只在本文件维护（rule.py 里只剩 idx / exec 两套代码，不再保存场内信号标的）。
_VOL_ETF = {
    "a500": "563360", "hs300": "510300", "kc50": "588000",
    "hkt": "513180", "gold": "518880",
}


def _preview_v6_gold(rt, vl):
    """V6 黄金三口径：A/B/C 共用场内 `518880` 同一条序列，实时价接成今日 bar。

    只动尾部、不改历史：`gold.load` 被换成本地闭包，`refresh` 一律当 False，
    避免盘中半成品 bar 落到 `data/_long_klines.json`。
    """
    sub = os.path.join(vl, "v6_gold")
    with _isolated(sub, ("data", "rule", "engine", "gold")):
        mod = _mp("ip_v6", os.path.join(sub, "daily", "_daily_scan.py"))

    g = mod.G
    orig, code = g.load, str(g.SIGNAL_CODE)

    def load(refresh=True):
        px = orig(refresh=False)
        hit = rt.get(code)
        if not hit or px is None or not len(px):
            return px
        # 库末根是上一交易日（只存已收盘）-> 追加今日 bar，别把实时价盖到昨天上
        return _intraday_series(px, price=hit[0])

    g.load = load
    try:
        mod.OUT_MD = _tpath("strategies/vol_legs/v6_gold/daily/_v6_gold_daily.md")
        mod.OUT_JSON = _tpath("strategies/vol_legs/v6_gold/daily/_v6_gold_daily.json")
        with _argv(["daily_scan", "--no-refresh"]):
            mod.main()
    finally:
        g.load = orig


def preview_vol_variants(rt):
    """v2/v3 逐腿换成「场内 ETF 实时价」，再跑各自的 daily 扫描（输出到临时树）。"""
    vl = os.path.join(_ROOT, "strategies", "vol_legs")
    for key, sub, pkg_name, tag, need_norefresh in _VOL_SCANS:
        with _isolated(os.path.join(vl, sub), ("data", "rule", "boll", "macd_boll")):
            mod = _mp(f"ip_{key}", os.path.join(vl, sub, "daily", "_daily_scan.py"))
        pkg = getattr(mod, pkg_name)
        rule = pkg.rule

        def px_for(leg, _pkg=pkg):
            px = _pkg.vdata.series_for_leg(leg)
            etf = _VOL_ETF.get(leg)
            # 指数点位与 ETF 价格量纲不同，只能按实时涨幅外推；库末根是 T-1（只存已收盘），
            # 所以是**追加**今日 bar 而不是替换末根（见 `_intraday_series`）
            if etf and etf in rt and len(px):
                px = _intraday_series(px, pct=rt[etf][1])
            return px

        pkg.load = px_for
        if hasattr(pkg, "vdata") and need_norefresh:
            pkg.vdata.refresh_global_index = lambda *a, **k: ({}, {})
            pkg.vdata.refresh_us = lambda *a, **k: ({}, {})
        mod.OUT_MD = _tpath(f"strategies/vol_legs/{sub}/daily/{tag}.md")
        mod.OUT_JSON = _tpath(f"strategies/vol_legs/{sub}/daily/{tag}.json")
        args = ["scan", "--no-refresh"] if need_norefresh else ["scan"]
        with _argv(args):
            mod.main()
        # v3 的 as_of 取的是「刷新后的净值日」，离线预演下为空 —— 用逐腿日期补上（都是场内口径）
        jp = mod.OUT_JSON
        if os.path.exists(jp):
            dd_ = json.load(open(jp, encoding="utf-8"))
            dates = [str(x.get("date")) for x in (dd_.get("legs") or []) if x.get("date")]
            if dates and not dd_.get("as_of"):
                dd_["as_of"] = max(dates)
                with open(jp, "w", encoding="utf-8") as fh:
                    json.dump(dd_, fh, ensure_ascii=False, indent=2)
        _note(f"{key}: 跟踪 ETF 实时涨幅外推成今日 bar，已重算（代理口径）")

    # v6 黄金三口径：A/B/C 都用场内 518880，末根直接换实时价。
    # 失败也不拖累 v2/v3，退回收盘口径产物。
    try:
        _preview_v6_gold(rt, vl)
        _note("v6: 黄金三口径已重算（518880 实时价替换末根）")
    except Exception as exc:  # noqa: BLE001
        _note(f"v6: 重算失败，沿用上一收盘（{type(exc).__name__}: {exc}）")
        for fn in ("_v6_gold_daily.json", "_v6_gold_daily.md"):
            src = os.path.join(vl, "v6_gold", "daily", fn)
            if os.path.exists(src):
                shutil.copyfile(src, _tpath(f"strategies/vol_legs/v6_gold/daily/{fn}"))

    # v4 / v9 / v12 没有盘中源：原样复制已有产物进临时树，让汇总卡能照常解析。
    # v12 现在**有**注入通道（见 `inject_bars`：A股/港股/黄金的盘中价 + 折算量能都会进
    # `_idxcache.frame`），但它自己不在看板上，预演不对它单独出卡，保留复制仅供 v14~v20 的
    # `bridge.v12` 与卡片的「前三名参考」列读取。
    for rel in ("v4_erp_band/daily/_v4_erp_band_daily.md",
                "v4_erp_band/daily/_v4_erp_band_daily.json",
                "v9_vol_target/daily/_v9_vol_target_daily.md",
                "v9_vol_target/daily/_v9_vol_target_daily.json",
                "v12_octo_trend/daily/_v12_daily.md",
                "v12_octo_trend/daily/_v12_daily.json"):
        src = os.path.join(vl, rel.replace("/", os.sep))
        if os.path.exists(src):
            shutil.copyfile(src, _tpath(f"strategies/vol_legs/{rel}"))
    _note("v4（月度 ERP）/ v9（无盘中增量）/ v12（自己不出卡）沿用上一收盘产物")

    try:
        _stage_vol_variants(vl)
    except Exception as exc:  # noqa: BLE001
        _note(f"v14~v20 预演整体失败，全部沿用上一收盘（{type(exc).__name__}: {exc}）")
        traceback.print_exc()


# v14~v20：`run` = 是否在临时树里用盘中 bar 重算。美股两腿（v16/v17）不重跑 ——
# 它们的行情就是「今天凌晨收盘」，与正式版同一根 bar，重跑只会得到同一组数字。
_VARIANTS = (
    ("v14", "v14_a500_e2", "_v14_daily", True, "🟠"),
    ("v15", "v15_hs300_min2", "_v15_daily", True, "🟠"),
    ("v16", "v16_ndx_sw", "_v16_daily", False, "🟢"),
    ("v17", "v17_spx_v12", "_v17_daily", False, "🟢"),
    ("v18", "v18_kc50_e2tv", "_v18_daily", True, "🟠"),
    ("v19", "v19_hkt_v3", "_v19_daily", True, "🟢"),
    ("v20", "v20_gold_v5", "_v20_daily", True, "🟢"),
)
# 🟠 = 含 V12 八态成分：强弱档要 `rvol`，盘中量只能按交易进度折算
# 🟢 = A股/港股纯价格策略，或美股腿（行情已完整）
_VARIANT_TONE = {v[0]: v[4] for v in _VARIANTS}
_US_LEG = {"v16": "ndx", "v17": "spx"}      # 美股两腿：正常情况下沿用正式版产物


def _us_artifact_stale(key, rel_js):
    """美股腿：正式版产物的数据日若落后于库里最新收盘，返回该日期（否则 None）。

    「不重跑」的前提是**正式版今天已经跑过**（产物就是今天凌晨那根收盘）。若产物停在前几天
    （例如上一次正式流程跑在盘中、只拿到 11-20），照抄会让预演落后好几天 —— 那就重算。
    """
    leg = _US_LEG.get(key)
    if not leg:
        return None
    path = os.path.join(_ROOT, rel_js.replace("/", os.sep))
    if not os.path.exists(path):
        return None
    try:
        d = json.load(open(path, encoding="utf-8"))
        got = str(d.get("as_of") or ((d.get("legs") or [{}])[0].get("date") or ""))
    except (OSError, ValueError):
        return None
    if not got:
        return None
    ic = _mp("ip_idxcache2", os.path.join(_ROOT, "strategies", "vol_legs", "_idxcache.py"))
    want = str(ic.series(leg).index[-1].date())
    return got if got < want else None


def _stage_vol_variants(vl):
    """v14~v20 逐条在临时树里出产物：A股/港股腿用盘中 bar 重算，美股两腿默认照抄正式版。"""
    rerun, copied = [], []
    for key, sub, tag, run, _tone in _VARIANTS:
        rel_md = f"strategies/vol_legs/{sub}/daily/{tag}.md"
        rel_js = f"strategies/vol_legs/{sub}/daily/{tag}.json"
        rels = (rel_md, rel_js)

        def _copy():
            for rel in rels:
                src = os.path.join(_ROOT, rel.replace("/", os.sep))
                if os.path.exists(src):
                    shutil.copyfile(src, _tpath(rel))

        if not run:
            stale = _us_artifact_stale(key, rel_js)
            if stale:
                _note(f"{key}: 正式版产物数据日 {stale} 落后于库末根，改为重算"
                      "（美股腿无盘中替换，只是把收盘补到最新）")
                run = True
            else:
                _copy()
                copied.append(key)
                continue
        try:
            with _isolated(os.path.join(vl, sub),
                           ("engine", "data", "rule", "_top3_bridge")):
                mod = _mp(f"ip_{key}", os.path.join(vl, sub, "daily", "_daily_scan.py"))
            mod.OUT_MD = _tpath(rel_md)
            mod.OUT_JSON = _tpath(rel_js)
            with _argv(["daily_scan"]):
                mod.main()
            rerun.append(key)
        except Exception as exc:  # noqa: BLE001
            _note(f"{key}: 预演重算失败，沿用上一收盘（{type(exc).__name__}: {exc}）")
            _copy()
    _note(f"v14~v20：盘中重算 {len(rerun)} 条（{', '.join(rerun) or '无'}）；"
          f"沿用正式版 {len(copied)} 条（{', '.join(copied) or '无'}，行情与正式版同值）")


# ---------------------------------------------------------------- 板块3 风格轮动
# 2026-11-24：三个指数**直接有实时点位**（`get_rt("SH000688"/"SZ399006"/"SH000922")` 实测可用），
# 不再用跟踪 ETF 外推 —— ETF 会掺进溢价与跟踪误差（同日 恒生科技ETF −2.27% vs 指数 −1.82%）。
_PROXY = ("000688", "399006", "000922")


def preview_growth_div(rt):
    """指数用**自己的实时点位**接成今日 bar（此前是 ETF 涨幅外推，属代理口径）。"""
    gd = os.path.join(_ROOT, "strategies", "growth_div_rotation")
    pct = {idx: rt[idx][1] for idx in _PROXY if idx in rt}

    def patched_loader(orig):
        def load_wide(codes=None, **kw):
            # 指数库末根是 T-1（只存已收盘）-> 追加今日一行，别覆盖昨天那行
            return _intraday_wide(orig(codes=codes, **kw), pct)
        return load_wide

    # v0
    with _isolated(os.path.join(gd, "v0"), ("data", "factors", "engine", "rule")):
        m0 = _mp("ip_gd0", os.path.join(gd, "v0", "scan.py"))
    m0.datamod.append_latest = lambda *a, **k: None       # 增量写 _gd_index_klines.json
    m0.datamod.load_wide = patched_loader(m0.datamod.load_wide)
    m0.OUT_MD = _tpath("strategies/growth_div_rotation/v0/daily/_signal_report.md")
    m0.OUT_JSON = _tpath("strategies/growth_div_rotation/v0/daily/_gd_signal.json")
    with _argv(["scan"]):
        m0.main()

    # v5
    with _isolated(os.path.join(gd, "v5_vol_target"), ("data", "factors", "engine", "rule")):
        m5 = _mp("ip_gd5", os.path.join(gd, "v5_vol_target", "scan.py"))
    m5.data.append_latest = lambda *a, **k: None
    m5.data.load_wide = patched_loader(m5.data.load_wide)
    m5.OUT_MD = _tpath("strategies/growth_div_rotation/v5_vol_target/daily/_gd_v5_daily.md")
    m5.OUT_JSON = _tpath("strategies/growth_div_rotation/v5_vol_target/daily/_gd_v5_signal.json")
    # 预演不重画 PNG：`build()` 里 `os.path.relpath(CHART, 仓库根)` 不能接受跨盘符的临时路径，
    # 所以 CHART 保持原值，只把重画函数短路（否则会真的覆盖 backtest/_v5_alloc.png）。
    m5._redraw = lambda *a, **k: None
    with _argv(["scan"]):
        m5.main()

    miss = [k for k in _PROXY if k not in pct]
    _note("风格轮动：指数用**实时点位**接成今日 bar"
          + (f"（缺 {', '.join(miss)}，沿用缓存末根）" if miss else ""))


# ---------------------------------------------------------------- 组装
def _mark(entry, tone, text):
    """给卡片打口径标记：🟢 盘中价入信号 / 🟡 无盘中源。"""
    entry["data"] = f"{tone} {text}" + (f" · {entry['data']}" if entry.get("data") else "")
    entry["note"] = (entry.get("note") or "") + (
        "\n\n> **盘中预演**：" + text + "。收盘价一翻，本条可能整条变——只用于提前准备，不作为成交依据。")
    return entry


_ACT_TXT = {"buy": "买入", "sell": "卖出", "hold": "持有",
            "watch": "观察", "avoid": "回避", "none": "无动作"}


def _row_key(row):
    """行的身份：优先「代码/策略名」列（板块4/3 那类没有代码的卡退到名称列）。"""
    c = str(row.get("c") or "").strip()
    if c and c not in ("—", "-"):
        return c
    return str(row.get("n") or "").strip()


def _row_txt(row):
    """一行操作的文字表述：动作文案（如「20pp → 0pp」「买入 1」）。"""
    a = str(row.get("a") or "").strip()
    return a or _ACT_TXT.get(str(row.get("act")), "")


def attach_diff(entries, base):
    """逐卡逐行比对「预演 vs 正式版」的操作，不一致就挂到 `entry["diff"]`（前端加粗加红）。"""
    total = 0
    for key, ent in entries.items():
        old = base.get(key) or {}
        prev = {_row_key(r): r for r in (old.get("rows") or []) if not r.get("sep")}
        items = []
        for r in ent.get("rows") or []:
            if r.get("sep"):
                continue
            kb = _row_key(r)
            o = prev.get(kb)
            if not o:
                continue
            if (r.get("act"), _row_txt(r)) == (o.get("act"), _row_txt(o)):
                continue
            ko = _row_key(o)
            was = _row_txt(o) if ko == kb else f"{ko} {_row_txt(o)}"
            now = _row_txt(r) if ko == kb else f"{kb} {_row_txt(r)}"
            items.append(f"{kb[:30]}：正式版 {was} → 预演 {now}")
        if items:
            ent["diff"] = items
            total += len(items)
    if total:
        _note(f"与正式版比对：**{total} 处操作不一致**（卡片顶部红色提示）")
    else:
        _note("与正式版比对：无操作差异")


def main():
    global _TMP
    # 预演**不写任何数据文件**：置位给各策略的数据层看（如 v5 的 FRED 缓存刷新会检查它）
    os.environ["XALPHA_PREVIEW"] = "1"
    # 临时树必须与仓库**同盘符**：好几个脚本会算 os.path.relpath(产物, 仓库根)，
    # 跨盘符会抛 ValueError('path is on mount ...')；放仓库同级目录，跑完即删。
    try:
        _TMP = tempfile.mkdtemp(prefix="xalpha_preview_", dir=os.path.dirname(_ROOT))
    except OSError:
        _TMP = tempfile.mkdtemp(prefix="xalpha_preview_")
    cfg = json.loads(dd._read(CFG))["strategies"]
    entries = {}

    # 先取全部要用的实时价（一次抓完，失败只影响对应卡片）
    vol_codes = list(_VOL_ETF.values())
    tech_codes = []
    lk_path = os.path.join(_ROOT, "data", "_long_klines.json")
    if os.path.exists(lk_path):
        tech_codes = list(json.load(open(lk_path, encoding="utf-8")))
    # 指数腿也要实时价：A股三腿 + 港股 + 黄金（`inject_bars` 用），板块3 的三个指数同批取
    idx_codes = ["000510", "000300", "000688", "HKHSTECH", "518880"]
    rt = rt_prices(vol_codes + tech_codes + list(_PROXY) + idx_codes)
    inject_bars()

    # 各策略重算（全部落在临时树）
    blocks = [
        ("板块5 技术指标", lambda: preview_tech(rt)),
        ("板块2 变种", lambda: preview_vol_variants(rt)),
        ("板块3 风格轮动", lambda: preview_growth_div(rt)),
    ]
    ok = {}
    for name, fn in blocks:
        try:
            fn()
            ok[name] = True
        except Exception as exc:  # noqa: BLE001
            ok[name] = False
            _note(f"{name} 预演失败，退回收盘口径：{type(exc).__name__}: {exc}")
            traceback.print_exc()

    # 收盘口径的卡片（无盘中源的板块直接复用；失败的板块也复用）
    # **过滤规则必须与 dd.collect() 一致**：daily.enabled=false 整卡撤掉、daily.card=false 不单独
    # 出卡。漏了这一步，被停用的策略也会进 runs.strategies，前端发现它不在任何板块的 keys 里，
    # 就归进「其他（未认领板块）」——2026-11-09 用户在看板上看到这块多余的分组。
    for key, parser in dd.PARSERS.items():
        meta = cfg.get(key) or {}
        if not dd._card_on(meta):
            _note(f"{key}: daily.enabled/card 已关，预演不出卡（与收盘口径一致）")
            continue
        try:
            entries[key] = parser(meta)
        except Exception as exc:  # noqa: BLE001
            _note(f"{key} 收盘口径解析失败：{type(exc).__name__}: {exc}")

    # 正式版（收盘口径）快照：重算完逐卡逐行比对，**操作不一致**的要在卡片顶部红色提醒
    base = copy.deepcopy(entries)

    # 用临时树覆盖可预演板块的卡片
    # 板块3 自 2026-11-23 起只出 v5 一张卡（v0 已下板）：v0 产物仍在临时树里重算，
    # 但**不回填 entries** —— 否则已撤掉的卡会被覆盖回来。
    with _rooted(_TMP):
        for key, block in (("tech_indicators", "板块5 技术指标"),
                           ("vol_variants", "板块2 变种"),
                           ("gd_v5", "板块3 风格轮动")):
            if not ok.get(block):
                continue
            if key not in entries:      # 收盘口径没出这张卡（daily.enabled=false）→ 预演也不许加回来
                continue
            try:
                entries[key] = dd.PARSERS[key](cfg[key])
            except Exception as exc:  # noqa: BLE001
                _note(f"{key} 预演解析失败，保留收盘口径：{type(exc).__name__}: {exc}")

    # 口径标注
    t = datetime.now().strftime("%H:%M")
    for key in ("tech_indicators", "gd_v5"):
        if key in entries:
            _mark(entries[key], "🟢", f"盘中价入信号（{t} 快照当收盘）")
    # 板块2 变种卡：2026-11-24 起 A股/港股腿用盘中价重算（🟠 = 含 V12，量能按进度折算），
    # 美股两腿沿用正式版（🟢，行情就是今天凌晨收盘）。逐行标注在「变种策略」列。
    if "vol_variants" in entries:
        ent = entries["vol_variants"]
        _mark(ent, "🟠", f"盘中价入信号（{t} 快照）；A股/港股 5 条腿的量能按交易进度折算")
        for r in ent.get("rows") or []:
            if r.get("sep"):
                continue
            head = str(r.get("c") or "")
            low = head.lower()
            for vx, tone in _VARIANT_TONE.items():
                if low.startswith(vx + " ") or low.startswith(vx + "·"):
                    r["c"] = f"{head} {tone}"
                    break
    if "tech_indicators" in entries:
        entries["tech_indicators"]["note"] += (
            "\n\n> 本卡最后一行的 **V17 八态** 是 🟡 上一收盘口径：强弱回调/反弹依赖完整日成交量 "
            "`rvol`，盘中量是半成品，重算会把「弱回调 ↔ 强回调」系统性判反 —— 所以盘中不重算，"
            "只沿用上一收盘产物（今日加减/持有许可都按上一收盘）。")
        _mark_v17(entries["tech_indicators"])
    # 板块6 只有 V7 上板（2026-11-21 起）；其余变种停用后不会出现在 entries 里，
    # 这里仍一并列出 —— 万一哪天重新上板，口径标注不会漏。
    for key in ("permanent", "vol_legs", "dividend", "dividend_v9", "bond_cash",
                "bond_cash_v1", "bond_cash_v2", "bond_cash_v3", "bond_cash_v4",
                "bond_cash_v5", "bond_cash_v6", "bond_cash_v7", "bond_cash_v8"):
        if key in entries:
            _mark(entries[key], "🟡", "无盘中数据源（场外净值/月度估值/美股未开盘），沿用上一收盘")

    # 预演不重画产物图：卡片里的图是收盘口径画的，硬塞进来会被误读成"盘中图"
    for key, ent in entries.items():
        if ent.get("img") or ent.get("imgs"):
            ent["img"], ent["imgs"] = "", []
            ent["note"] = (ent.get("note") or "") + (
                "\n\n> 预演版不重画产物 PNG（图是收盘口径的，塞进来会被误读）。")

    # 变种卡口径补一段：逐行说明哪条腿用了盘中价、哪条是正式版同值
    if "vol_variants" in entries:
        entries["vol_variants"]["note"] += (
            "\n\n> **本卡口径（2026-11-24 起）**：A股/港股五条腿（v14 A500 / v15 沪深300 / "
            "v18 科创50 / v19 恒生科技 / v20 黄金）用 **T+0 盘中价**重算，标记 🟠/🟢；"
            "美股两条腿（v16 纳指100 / v17 标普500）标记 🟢 且**「数据与正式版同值」** —— "
            "它们的行情就是今天凌晨 05:00 收盘那根，预演与正式版是同一组数字，不重跑。"
            "\n> 🟠 = 策略成分含 **V12 八态**，强弱档要 `rvol = 当日量/20日均量`，而盘中只能拿到"
            "累计量 → 按「已过交易分钟占比」折算成当日量（实测 14:19 沪深300 累计量占昨日全天 "
            "0.81 vs 时间进度 0.83，误差约 2%）。尾盘放量会让折算略低估，落在阈值 ±0.05 带内时"
            "强弱档可能翻转 —— 这类腿的结论按「可能变」读。"
            "\n> 🟢 = 纯价格策略（v19 V3 / v20 V5）或行情已完整（美股两腿），不受量能折算影响。"
            "图墙本次不重画（图是收盘口径的）。收盘价一翻结论可能整条变，只用于提前准备，"
            "不作为成交依据。")
        entries["vol_variants"]["imgs"] = []

    # 板块2 的「近五年口径」对照卡：**不重算**。
    # 它的落地清单 `_beat_best_5y.jsonl` 是收盘后跑的研究产物（~40s，会改写研究产物），
    # 预演只沿用上一收盘的 `_daily_positions_5y.json`；且七条腿口径混杂（A股 15:00 /
    # 港股 16:00 / 美股次日凌晨），本来就不存在统一的盘中快照。
    if "vol_5y" in entries:
        _mark(entries["vol_5y"], "🟡",
              "无盘中源（排名来自收盘后研究产物，七腿含 A股/港股/美股），沿用上一收盘")
        entries["vol_5y"]["note"] += (
            "\n\n> 本卡是**近五年窗口**排出的组合，与上一张 v14~v20（全历史落地封装）冲突时"
            "以 v14~v20 为准。预演版不重跑排名研究（`_beat_best_5y.py` 会改写研究产物），"
            "只沿用上一收盘的仓位快照。")

    # 与正式版逐行比对（用户要求：某个操作不一致时必须额外提醒）
    attach_diff(entries, base)

    _emit(entries)
    shutil.rmtree(_TMP, ignore_errors=True)
    _TMP = None
    os.environ.pop("XALPHA_INTRADAY_BARS", None)
    os.environ.pop("XALPHA_PREVIEW", None)


def _assets(entries):
    """只读内嵌：把卡片引用的图编码成 data URI（**不**往 data/_daily_digest_assets 写副本）。"""
    import base64
    import mimetypes
    out = {}
    for ent in entries.values():
        rels = [ent.get("img") or ""] + [x.get("path") or "" for x in (ent.get("imgs") or [])]
        for rel in rels:
            if not rel or rel in out:
                continue
            full = os.path.join(_ROOT, rel.replace("/", os.sep))
            if not os.path.exists(full):
                continue
            mime = mimetypes.guess_type(full)[0] or "image/png"
            out[rel] = "data:%s;base64,%s" % (
                mime, base64.b64encode(open(full, "rb").read()).decode("ascii"))
    return out


_BANNER = (
    '<div style="position:sticky;top:0;z-index:99;background:#fff4e5;border-bottom:1px solid #f0c48a;'
    'padding:8px 16px;font:13px/1.6 -apple-system,Segoe UI,Microsoft YaHei,sans-serif;color:#8a4b00">'
    '<b>盘中预演</b> · 用最新盘中快照当作收盘价重算，<b>不写任何数据文件</b>；'
    '收盘价一翻结论可能整条变，<b>不作为成交依据</b>。正式版见 <code>data/_daily_digest.html</code>。'
    '<br>🟢=盘中价入信号 · 🟠=盘中价 + 量能按交易进度折算（强弱档可能翻转）'
    ' · 🟡=无盘中源（沿用上一收盘）'
    ' · <span style="color:#c62828;font-weight:800">红色提示条 = 该卡有操作与正式版不一致</span></div>'
)


def _emit(entries):
    doc = json.loads(dd._read(CFG))
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    day = datetime.now().strftime("%Y-%m-%d")
    payload = {
        "generated": now,
        "dates": [day],
        "blocks": dd._blocks(doc),
        "root": os.path.relpath(_ROOT, os.path.dirname(OUT_HTML)).replace("\\", "/"),
        "assets": _assets(entries),
        "runs": {day: {"run_at": now, "strategies": entries}},
        "intraday": True,
    }
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html = dd._HTML_TMPL.replace("__DATA__", data)
    html = html.replace("<body>", "<body>" + _BANNER, 1)
    html = html.replace("每日操作汇总 · XAlpha", "每日操作汇总（盘中预演） · XAlpha", 1)
    with open(OUT_HTML, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"[预演] 看板 -> {dd_rel(OUT_HTML)}")


def dd_rel(path):
    return os.path.relpath(path, _ROOT).replace("\\", "/")


if __name__ == "__main__":
    main()
