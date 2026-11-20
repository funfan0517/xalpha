# -*- coding: utf-8 -*-
"""盘中预演看板 · 把「最新盘中快照」当作收盘价重算 today 的操作，**不写任何数据文件**。

与收盘口径的 `data/_daily_digest.html` 并存（用户 2026-11-06 要求每天出两份）：

    python pipeline/daily_digest.py        # 正式版（收盘口径，产物落盘）
    python pipeline/intraday_preview.py    # 预演版 -> data/_daily_digest_intraday.html

三条铁律：
1. **只读仓库数据**。唯一写出的文件 = `data/_daily_digest_intraday.html`（看板本身）；
   各策略产物一律写进系统临时目录（跑完即删），绝不覆盖 `daily/` 下的 md / json / png。
2. **不碰网络库以外的状态**：所有 `refresh_*` / `ensure_fresh` / `append_latest` 都被替换成
   空操作，避免盘中半成品 bar 污染 `_long_klines.json`、`_global_index_klines.json`、`_gd_index_klines.json`。
3. **口径必须写明**：每张卡标注 🟢（盘中价入信号）/ 🟡（无盘中源，沿用上一收盘）。

接入口径与各板块的可预演性：

| 板块 | 预演方式 | 数据 |
|---|---|---|
| 1 永久组合 | 🟡 无盘中源 | 场外累计净值（T 日盘后披露，QDII 滞后 1~2 日） |
| 2 波动类主策略 | 🟡 无盘中源 | 真实指数收盘（黄金 `518880`），指数没有稳定盘中源 |
| 2 变种 v2/v3 | 🟢 用跟踪 ETF 的实时涨幅外推指数末根（代理口径） | 真实指数库 + `xa.get_rt` |
| 2 变种 v6 | 🟢 三条口径共用 `518880`，末根直接换实时价 | 黄金 `518880` 行情 + `xa.get_rt` |
| 2 变种 v4 | 🟡 无盘中源 | 沪深300×10Y 国债 ERP（月度序列） |
| 2 变种 v9 | 🟡 美股未开盘 | `.NDX/.INX` 收盘（美东 11-05） |
| 3 风格轮动 v0/v5 | 🟢 指数按跟踪 ETF 的实时涨幅外推末根 | 科创50/创业板指/中证红利 |
| 4 红利 V4/V9 | 🟡 无盘中源 | 场外累计净值 + 月度估值分位 |
| 5 技术指标 | 🟢 54 只 ETF 末根换成实时价 + 重算全套指标 | `_long_klines.json` + `xa.get_rt` |

机械规则输出，非投资建议。预演**不是**信号：收盘价一翻，结论可能整条变。
"""
from __future__ import annotations

import contextlib
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


def rt_prices(codes):
    """{code: (price, pct)} —— 取不到就跳过（返回的 dict 里没有该 code）。"""
    import xalpha as xa

    gi = _mp("gi_intraday", os.path.join(_ROOT, "_gen_indicators.py"))
    out, fails = {}, {}
    for c in dict.fromkeys(codes):
        pref = gi.exchange_prefix(c)
        for p in (pref, "SZ" if pref == "SH" else "SH"):
            try:
                rt = xa.get_rt(p + c)
                cur = rt.get("current")
                if cur:
                    out[c] = (float(cur), _pct(rt) or 0.0)
                    break
            except Exception as exc:  # noqa: BLE001
                fails[c] = f"{type(exc).__name__}: {exc}"
    if fails:
        _note(f"实时价缺失 {len(fails)} 只（沿用缓存末根）: "
              + "、".join(list(fails)[:6]))
    return out


# ---------------------------------------------------------------- 板块5 技术指标
def preview_tech(rt):
    """用实时价替换 54 只 ETF 的末根并重算指标，再跑一遍 _daily_recommend.py。"""
    rec = _mp("ip_tech", os.path.join(_ROOT, "strategies", "tech_indicators",
                                      "daily", "_daily_recommend.py"))
    # 1) 关掉刷新（ensure_fresh 会写 _long_klines.json / _indicators_hist.json）
    rec._refresh_data.ensure_fresh = lambda codes=None, verbose=True: (
        rec._refresh_data.last_dates(), set())
    # 2) 末根换成实时价 + 内存重算指标（_recompute 是纯函数）
    orig_load = rec.base.load

    def load():
        ind, lk = orig_load()
        hit = 0
        for code, (price, _p) in rt.items():
            if code not in lk or code not in ind:
                continue
            lk[code]["close"][-1] = price
            close = [float("nan") if v is None else float(v) for v in lk[code]["close"]]
            ind[code] = rec._refresh_data._recompute(
                lk[code]["dates"], close, ind[code].get("volume"))
            hit += 1
        _note(f"技术指标：{hit}/{len(ind)} 只 ETF 末根已换实时价")
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
    """V6 黄金三口径：A/B/C 共用场内 `518880` 同一条序列，末根直接换实时价。

    只替换末根、不改历史：`gold.load` 被换成本地闭包，`refresh` 一律当 False，
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
        px = px.copy()
        px.iloc[-1] = hit[0]
        return px

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
            # 指数点位与 ETF 价格量纲不同，只能按实时涨幅外推末根（美股腿无 A 股盘中源）
            if etf and etf in rt and len(px) >= 2:
                px = px.copy()
                px.iloc[-1] = float(px.iloc[-2]) * (1 + rt[etf][1] / 100.0)
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
        _note(f"{key}: 跟踪 ETF 实时涨幅外推指数末根，已重算（代理口径）")

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
    # v12 硬依赖**指数量能**（七腿全要完整成交量；盘中量不完整会把弱回调/强回调判反），不能按实时价重算。
    for rel in ("v4_erp_band/daily/_v4_erp_band_daily.md",
                "v4_erp_band/daily/_v4_erp_band_daily.json",
                "v9_vol_target/daily/_v9_vol_target_daily.md",
                "v9_vol_target/daily/_v9_vol_target_daily.json",
                "v12_octo_trend/daily/_v12_daily.md",
                "v12_octo_trend/daily/_v12_daily.json",
                "v14_a500_e2/daily/_v14_daily.md",
                "v14_a500_e2/daily/_v14_daily.json",
                "v15_hs300_min2/daily/_v15_daily.md",
                "v15_hs300_min2/daily/_v15_daily.json",
                "v16_ndx_sw/daily/_v16_daily.md",
                "v16_ndx_sw/daily/_v16_daily.json",
                "v17_spx_v12/daily/_v17_daily.md",
                "v17_spx_v12/daily/_v17_daily.json",
                "v18_kc50_e2tv/daily/_v18_daily.md",
                "v18_kc50_e2tv/daily/_v18_daily.json",
                "v19_hkt_v3/daily/_v19_daily.md",
                "v19_hkt_v3/daily/_v19_daily.json",
                "v20_gold_v5/daily/_v20_daily.md",
                "v20_gold_v5/daily/_v20_daily.json"):
        src = os.path.join(vl, rel.replace("/", os.sep))
        if os.path.exists(src):
            shutil.copyfile(src, _tpath(f"strategies/vol_legs/{rel}"))
    _note("v4（月度 ERP）/ v9（美股未开盘）/ v12（硬依赖指数量能）/ v14~v20（单腿专用，无独立盘中源）无盘中源，沿用上一次收盘")


# ---------------------------------------------------------------- 板块3 风格轮动
_PROXY = {"000688": "588000", "399006": "159915", "000922": "515080"}


def preview_growth_div(rt):
    """指数没有稳定的实时源 —— 用跟踪 ETF 的实时涨幅外推指数末根。"""
    gd = os.path.join(_ROOT, "strategies", "growth_div_rotation")
    pct = {idx: rt[etf][1] for idx, etf in _PROXY.items() if etf in rt}

    def patched_loader(orig):
        def load_wide(codes=None, **kw):
            wide = orig(codes=codes, **kw)
            for code in list(wide.columns):
                if code in pct and len(wide) >= 2:
                    col = wide.columns.get_loc(code)
                    wide.iloc[-1, col] = float(wide.iloc[-2, col]) * (1 + pct[code] / 100.0)
            return wide
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
    _note("风格轮动：指数按跟踪 ETF 实时涨幅外推"
          + (f"（缺 {', '.join(miss)}，沿用缓存末根）" if miss else ""))


# ---------------------------------------------------------------- 组装
def _mark(entry, tone, text):
    """给卡片打口径标记：🟢 盘中价入信号 / 🟡 无盘中源。"""
    entry["data"] = f"{tone} {text}" + (f" · {entry['data']}" if entry.get("data") else "")
    entry["note"] = (entry.get("note") or "") + (
        "\n\n> **盘中预演**：" + text + "。收盘价一翻，本条可能整条变——只用于提前准备，不作为成交依据。")
    return entry


def main():
    global _TMP
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
    rt = rt_prices(vol_codes + tech_codes + list(_PROXY.values()))

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

    # 用临时树覆盖可预演板块的卡片
    with _rooted(_TMP):
        for key, block in (("tech_indicators", "板块5 技术指标"),
                           ("vol_variants", "板块2 变种"),
                           ("growth_div_rotation", "板块3 风格轮动")):
            if not ok.get(block):
                continue
            try:
                entries[key] = dd.PARSERS[key](cfg[key])
            except Exception as exc:  # noqa: BLE001
                _note(f"{key} 预演解析失败，保留收盘口径：{type(exc).__name__}: {exc}")

    # 口径标注
    t = datetime.now().strftime("%H:%M")
    for key in ("tech_indicators", "vol_variants", "growth_div_rotation", "gd_v5"):
        if key in entries:
            _mark(entries[key], "🟢", f"盘中价入信号（{t} 快照当收盘）")
    for key in ("permanent", "vol_legs", "dividend", "dividend_v9", "bond_cash",
                "bond_cash_v1", "bond_cash_v2", "bond_cash_v3", "bond_cash_v4",
                "bond_cash_v5", "bond_cash_v6"):
        if key in entries:
            _mark(entries[key], "🟡", "无盘中数据源（场外净值/月度估值/美股未开盘），沿用上一收盘")

    # 预演不重画产物图：卡片里的图是收盘口径画的，硬塞进来会被误读成"盘中图"
    for key, ent in entries.items():
        if ent.get("img") or ent.get("imgs"):
            ent["img"], ent["imgs"] = "", []
            ent["note"] = (ent.get("note") or "") + (
                "\n\n> 预演版不重画产物 PNG（图是收盘口径的，塞进来会被误读）。")

    # 变种卡口径再补一句：v2/v3/v6 是盘中代理，不是正式口径
    if "vol_variants" in entries:
        entries["vol_variants"]["note"] += (
            "\n\n> 本卡 2026-11-18 起只列 v14~v20 七个单标的策略，均无独立盘中源，"
            "预演沿用上一次收盘的 daily 产物（图墙本次不重画）。"
            "收盘价一翻结论可能整条变，只用于提前准备，不作为成交依据。")
        entries["vol_variants"]["imgs"] = []

    _emit(entries)
    shutil.rmtree(_TMP, ignore_errors=True)
    _TMP = None


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
    '🟢=盘中价入信号 · 🟡=无盘中源（沿用上一收盘）</div>'
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
