# -*- coding: utf-8 -*-
"""核心轮动(六类资产动态配置)每日监控报告。

自动抓取:
  - 六类场内代理日线(信号): xa.get_daily(SH/SZ+proxy)
  - 六只场外主仓净值(执行确认): xa.fundinfo(off).price
  - 10Y 国债收益率(含近6周趋势): xalpha.universal.get_bond_rates('N')
  - 估值锚(蛋卷基金 djapi/index_eva/dj, 每日): 中证红利股息率/PE分位、
    科创50 PE分位与 PE(自动算 科创PE比=科创PE/红利PE)
人工录入(可选, data/_core_valuation.json, 每日自动覆盖 蛋卷已覆盖字段):
  - A500 PE分位, 纳指 Forward PE 分位(蛋卷不覆盖);
  - DXY / 美债10Y实际利率(可选微调), 成长占优/风险开关
持仓(可选, data/_core_holdings.json): weights -> 月度再平衡偏差

用法: python strategies/core_rotation/_signal.py
输出: strategies/core_rotation/_daily_report.md + data/_core_daily.json
"""
import io
import json
import os
import sys
from datetime import datetime, timedelta

import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import xalpha as xa  # noqa: E402
from xalpha.universal import get_bond_rates, get_bond_rates_range  # noqa: E402

import rule  # noqa: E402

_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_daily_report.md")
_JSON_OUT = os.path.join(_ROOT, "data", "_core_daily.json")
_TEMPLATE_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "valuation_template.json")
# 东财-mx-ds-mcp 估值快照(由 agent 每日查询后回填; A500/纳指等蛋卷未覆盖项)
_MX_FILE = os.path.join(_ROOT, "data", "_mx_valuation_latest.json")
_Y10_TERM = 10.0


def _sh(code):
    return ("SH" if code.startswith(("5", "6", "9")) else "SZ") + code


def fetch_proxy_bars(proxy, start="2025-06-01"):
    df = xa.get_daily(_sh(proxy), start=start)
    df = df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"])
    return df


def fetch_nav(code):
    f = xa.fundinfo(code)
    df = f.price[["date", "netvalue"]].dropna()
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def _last_ret(ser, bars):
    return (ser.iloc[-1] / ser.iloc[-1 - bars] - 1) * 100 if len(ser) > bars else None


def bars_metrics(df):
    """场内代理日线 -> 动量指标(自动)。"""
    c = df["close"].astype(float)
    ma28 = c.rolling(28).mean().iloc[-1] if len(c) >= 28 else None
    return dict(
        date=str(df["date"].iloc[-1].date()),
        close=round(float(c.iloc[-1]), 4),
        d1=_last_ret(c, 1), d5=_last_ret(c, 5), d20=_last_ret(c, 20),
        ma28=round(float(ma28), 4) if ma28 is not None else None,
        above_ma28=bool(c.iloc[-1] > ma28) if ma28 is not None else None,
    )


def nav_metrics(df):
    """场外净值(执行确认)。"""
    c = df["netvalue"].astype(float)
    return dict(
        nav_date=str(df["date"].iloc[-1].date()),
        nav=round(float(c.iloc[-1]), 4),
        nav_d20=_last_ret(c, 20),
    )


def fetch_yield_now():
    df = get_bond_rates("N", datetime.now().strftime("%Y-%m-%d"))
    df = df[df["rate"].notna()]
    row = df.iloc[(df["year"] - _Y10_TERM).abs().argsort()[:1]]
    return float(row["rate"].iloc[0]), float(row["year"].iloc[0])


def fetch_yield_series(weeks=6):
    """近 weeks 周(周五)10Y 收益率序列, 用于利率方向判断。"""
    end = datetime.now()
    start = end - timedelta(days=weeks * 7 + 7)
    df = get_bond_rates_range("N", duration=_Y10_TERM, freq="W-FRI",
                              start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"))
    return df.dropna()


# 蛋卷基金 指数估值(公开, 每日更新, 无需登录): 覆盖中证红利/科创50 等 63 个指数
_DJ_URL = "https://danjuanfunds.com/djapi/index_eva/dj"
_DJ_UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"),
          "Referer": "https://danjuanfunds.com/"}


def fetch_danjuan_eva():
    """蛋卷指数估值 -> dict(中证红利 000922 + 科创50 000688)。

    返回: {as_of, red_yeild(小数, 0.042=4.2%), red_pe, red_pe_pct,
           kc_pe, kc_pe_pct, kc_pe_over_hs=科创PE/红利PE}
    不可用时 raise, 由调用方回退到上一次文件。
    """
    import requests
    r = requests.get(_DJ_URL, headers=_DJ_UA, timeout=12)
    r.raise_for_status()
    rows = ((r.json().get("data") or {}).get("items")) or []
    red = kc = None
    for x in rows:
        if x.get("index_code") == "SH000922":
            red = x
        elif x.get("index_code") == "SH000688":
            kc = x
    if red is None or red.get("yeild") is None or red.get("pe_percentile") is None:
        raise RuntimeError("蛋卷接口中未找到 SH000922(中证红利)")
    d = dict(
        as_of=datetime.fromtimestamp(int(red.get("ts", 0)) / 1000).strftime("%Y-%m-%d"),
        red_yeild=float(red["yeild"]),
        red_pe=float(red["pe"]),
        red_pe_pct=float(red["pe_percentile"]),
        kc_pe=float(kc["pe"]) if kc else None,
        kc_pe_pct=float(kc["pe_percentile"]) if kc else None,
    )
    d["kc_pe_over_hs"] = (d["kc_pe"] / d["red_pe"]) if d.get("kc_pe") else None
    return d


def _fmt(v, nd=2, signed=True):
    if v is None:
        return "—"
    return f"{v:+.1f}%" if signed else f"{v:.1f}%"


def asset_advice(a, sc, v, y10):
    """单类操作要点(情景中枢 + 已录估值锚/利率分级)。"""
    sk = rule.scene_key(sc)
    band = rule.BANDS[a["key"]][sk]
    tgt = rule.TARGET[a["key"]][sk]
    core = f"{tgt:g}%(区间 {band[0]}–{band[1]}%)"
    if a["key"] == "bond":
        sig, act = rule.bond_level(y10)
        return f"10Y={y10:.2f}% {sig} → {act} · 中枢 {core}"
    if a["key"] == "dividend":
        if v.get("div_yield_hs_pct") is not None and v.get("hs_pe_pct") is not None:
            return (f"股息率 {v['div_yield_hs_pct']}% · PE分位 {v['hs_pe_pct']:.0%} · 中枢 {core}"
                    + (" → **停加/减**" if v["hs_pe_pct"] > 0.70 else ""))
        return f"缺股息率/PE分位(人工录) · 中枢 {core}"
    if a["key"] == "ndx":
        pct = v.get("ndx_fwd_pe_pct")
        if pct is not None:
            act = "卖出" if pct > 0.85 else "微降" if pct > 0.70 else "持有" if pct >= 0.30 else "买入"
            return f"Forward PE 分位 {pct:.0%} → {act} · 中枢 {core}"
        ttm = (v.get("_mx") or {}).get("ndx_pe_ttm")
        suffix = f" · 参考(东财) PE-TTM={ttm}" if ttm else ""
        return f"缺 Forward PE 分位(人工){suffix} · 中枢 {core}"
    if a["key"] == "a500":
        pct = v.get("a500_pe_pct")
        if pct is not None:
            act = ("卖出(停加)" if pct > 0.85 else
                   "不操作/微降" if pct > 0.70 else
                   "持有" if pct >= 0.50 else
                   "偏多配" if pct >= 0.30 else "买入(历史级别低估)")
            src = "(官方日频估算)" if v.get("_a500") else "(人工录入)"
            return f"A500 PE 分位 {pct:.0%}{src} → {act} · 中枢 {core}"
        pe = (v.get("_mx") or {}).get("a500_pe")
        suffix = f"A500 PE(TTM)={pe} (发布以来分位未建)" if pe else "缺 A500 PE 分位(人工录)"
        return f"{suffix} · 中枢 {core}"
    if a["key"] == "kc":
        pr = v.get("kc_pe_over_hs")
        if pr is not None:
            act = ("**完全规避(降至0%)**" if pr > 7 else
                   "禁超配(≤35%)" if pr > 6 else
                   "减/不操作" if pr > 5 else
                   "持有" if pr >= 3 else "偏多")
            return f"科创PE比 {pr:.2f} → {act} · 中枢 {core}"
        return f"缺 科创PE比(人工录) · 中枢 {core}"
    # gold
    return f"中枢 {core}"


def build_report(assets, snap, nav_l, y10, y10_term, yseries, v, sc, sc_note):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    ys = " | ".join(f"{d.date()}={r:.2f}%" for d, r in zip(yseries["date"], yseries["close"]))
    ydir = "—"
    if len(yseries) >= 2:
        ydir = "下行" if yseries["close"].iloc[-1] < yseries["close"].iloc[0] else \
               "上行" if yseries["close"].iloc[-1] > yseries["close"].iloc[0] else "持平"
    ysig, yact = rule.bond_level(y10)
    ratio_txt = "**未录**"
    if v.get("div_yield_hs_pct") and y10:
        ratio_txt = "**%.2f**" % (v["div_yield_hs_pct"] / y10)
    stale = ""
    if v.get("as_of"):
        age = (datetime.now() - datetime.strptime(v["as_of"], "%Y-%m-%d")).days
        stale = " ⚠️超过7天, 建议更新" if age > 7 else ""

    L = ["# 核心轮动(六类资产动态配置) · 每日监控报告", "",
         f"> 生成 {now} · 标的一一取自 `data/_universe.md` 场外行(一类一标的, 无第7只); "
         "场内代理仅作行情/动量观察, 申赎一律走场外。机械规则输出, 非投资建议。", ""]

    L += ["## 一、行情快照(场内代理 · 自动)", "",
          "| 资产 | 代理 | 日期 | 收盘 | 日涨 | 5日 | 20日 | 站上MA28 |",
          "|---|---|---|---|---|---|---|---|"]
    for s in snap:
        if s.get("err"):
            L.append(f"| {s['name']} `{s['off']}` | `{s['proxy']}` | **抓取失败** {s['err']} | | | | |")
        else:
            L.append(f"| {s['name']} `{s['off']}` | `{s['proxy']}` | {s['date']} | {s['close']} "
                     f"| {_fmt(s['d1'])} | {_fmt(s['d5'])} | {_fmt(s['d20'])} "
                     f"| {'是' if s['above_ma28'] else '否' if s['above_ma28'] is False else '—'} |")

    L += ["", "## 二、净值快照(场外主仓 · 执行确认)", "",
          "| 资产 | 主仓 | 最新净值日 | 单位净值 | 近20交易日 |",
          "|---|---|---|---|---|"]
    for s in snap:
        nm = s.get("nav") or {}
        if nm.get("err"):
            L.append(f"| {s['name']} | `{s['off']}` | **FAIL** {nm['err'][:80]} | | |")
        else:
            L.append(f"| {s['name']} | `{s['off']}` | {nm['nav_date']} | {nm['nav']} "
                     f"| {_fmt(nm['nav_d20'])} |")
    L.append("")
    L.append("> QDII(`270042`/代理`513100`)净值/行情自然滞后约 1 个交易日, 属正常; 场外按 T 日净值申赎的口径以基金公司为准。")

    auto = v.get("_auto")
    L += ["", "## 三、10Y 国债收益率与估值锚(自动+人工)", "",
          f"- 10Y 国债到期收益率(中债): **{y10:.2f}%**(期限 {y10_term:g}Y) · 近6周方向 **{ydir}** · "
          f"本级: {ysig}({yact})",
          f"- 近6周(周五)序列: {ys if ys else '—'}",
          f"- 估值锚数据日期(蛋卷每日自动 / 人工): {v.get('as_of') or '**未录入**'}{stale}"]
    if auto:
        kcp = auto.get("kc_pe_pct")
        L.append(f"- 估值自动源[{auto.get('source')}]: 中证红利 股息率 "
                 f"{v.get('div_yield_hs_pct')}% · 红利PE分位 {v.get('hs_pe_pct'):.0%} · "
                 f"科创50 PE分位 {f'{kcp:.0%}' if kcp is not None else '—'}"
                 f"(原始PE: 红利 {auto.get('red_pe')} / 科创 {auto.get('kc_pe') or '—'}) → "
                 f"科创PE比 {v.get('kc_pe_over_hs'):.2f}")
    L.append(f"- 股债收益比 = 红利股息率 ÷ 10Y = {ratio_txt} "
             f"(分子红利股息率 {v.get('div_yield_hs_pct') or '未录'}%)")
    kv = [("纳指Forward PE分位(手动)", "ndx_fwd_pe_pct"),
          ("DXY(手动)", "dxy"), ("美债10Y实际利率(手动)", "us_real_yield")]
    segs = [f"{k}={v[j] if v.get(j) is not None else '—'}" for k, j in kv]
    L.append(f"- 人工项: {' · '.join(segs)}")
    a5 = v.get("_a500")
    if a5:
        L.append(f"- A500 本地日频PE库(官方日频, 2024-09 发布起): 最新 PE={a5['pe']} @ {a5['as_of']} · "
                 f"发布以来累计分位 {a5['pe_pct']:.1%} · 区间 [{a5['pe_min']}, {a5['pe_max']}] n={a5['n']}")
    mx = v.get("_mx") or {}
    if mx.get("a500_pe"):
        L.append(f"- 东财MCP快照({mx.get('as_of')}): A500 PE(TTM)={mx.get('a500_pe')}"
                 f" · 纳指 PE(TTM)={mx.get('ndx_pe_ttm')} — 东财无纳指 Forward PE;"
                 f" A500 发布以来分位以本地日频库为准")
    L.append(f"- 成长占优(创业板连续5日跑赢红利&两市>2.3万亿): "
             f"{'是' if v.get('growth_yes') else '否/未录'}")

    L += ["", "## 四、情景判定与六类目标配置", "",
          f"- **情景**: {sc} — {sc_note}",
          "", "| 资产 | 场外主仓 | 本情景区间 | 建议中枢 | 操作要点 |",
          "|---|---|---|---|---|"]
    for a in assets:
        sk = rule.scene_key(sc)
        band = rule.BANDS[a["key"]][sk]
        L.append(f"| {a['name']} | `{a['off_code']}` | {band[0]}–{band[1]}% "
                 f"| {rule.TARGET[a['key']][sk]:g}% | {asset_advice(a, sc, v, y10)} |")
    L += ["",
          "> 区间/中枢见《核心轮动投资策略手册》第一、二章; 类内 ±5–10pp 微调见第三~八章; "
          f"单类 ≤{rule.CAP_SINGLE:g}%、科创 ≤{rule.CAP_KC:g}%。",
          "> 每类独立 0–100%, 六类合计=100%, 之外保留 6–12 个月生活费的现金。", ""]

    # 组合净值监控
    L += ["## 五、组合净值监控(场外六只等权, 手动记录起点对齐)", ""]
    refs = []
    common = None
    for ndf in nav_l.values():
        c = ndf.set_index("date")["netvalue"].astype(float)
        c = c / c.iloc[0]
        refs.append(c)
        if common is None or c.index[0] > common:
            common = c.index[0]
    if refs:
        joined = pd.concat(refs, axis=1, join="outer").sort_index()
        joined = joined.ffill().dropna()
        comp = joined.mean(axis=1)
        L.append(f"- 对齐起点: {common.date()} · 等权组合(无调仓)近1月 "
                 f"{_fmt(_last_ret(comp, 21))} · 近3月 {_fmt(_last_ret(comp, 63))}")
        L.append(f"- 单资产近3月: " + " · ".join(
            f"{a['name']}{_fmt(_last_ret(ref, 63))}"
            for a, ref in zip(assets, refs)))
    else:
        L.append("- 无净值数据")

    # 月度再平衡
    L += ["", "## 六、月度再平衡检查(持仓 vs 中枢, 偏差>±5pp 才调)", ""]
    hold = rule.load_json(rule.HOLDINGS_FILE, None)
    if not hold or "weights" not in hold:
        L.append("- 未配置 `data/_core_holdings.json`, 跳过(示例: "
                 "`{\"as_of\":\"2026-09-01\",\"weights\":{\"003377\":0.10,\"012644\":0.25,"
                 "\"270042\":0.15,\"023299\":0.25,\"011609\":0.05,\"000216\":0.20}}`)")
    else:
        L += ["| 资产 | 当前 | 建议中枢 | 偏差 | 动作 |", "|---|---|---|---|---|"]
        for a in assets:
            w = float(hold["weights"].get(a["off_code"], 0.0))
            t = rule.TARGET[a["key"]][rule.scene_key(sc)]
            d = t - w
            act = (f"加至 {t:g}%" if d > rule.REBAL_TOL else
                   f"减至 {t:g}%" if d < -rule.REBAL_TOL else "不操作(±5pp内)")
            L.append(f"| {a['name']} | {w * 100:.1f}% | {t:g}% | {d * 100:+.1f}pp | {act} |")

    # 风险红线
    gold_m = next((s.get("d20") for s in snap if s["key"] == "gold" and "err" not in s), None)
    L += ["", "## 七、风险红线检查", "",
          f"- 黄金近1月(20交易日): {_fmt(gold_m)} "
          f"{'→ **暂停加仓**(>10%)' if (gold_m is not None and gold_m > rule.GOLD_MONTHLY_CAP) else '→ 未触发'}",
          f"- 红利成分股风险: {'**触发, 暂停加仓**' if v.get('risk_red_component') else '未触发/未录'}",
          "- QDII 溢价: 场内 `513100` 溢价>5% 一律走场外 `270042`(需人工核对溢价)",
          f"- 科创 PE 比>7 规避/TMT 拥挤度>90% 禁加: "
          f"{'**PE比>7, 完全规避**' if (v.get('kc_pe_over_hs') or 0) > 7 else '未触发/未录'}", ""]
    L.append("> 估值锚来源: 红利股息率/PE分位与科创PE比=蛋卷(每日); A500 发布以来分位=本地日频库(中证官网, "
             "每日自动); 东财MCP提供 A500/纳指 PE(TTM) 绝对值快照(agent 回填); "
             "纳指 Forward PE 分位东财无, 仍需人工(每周, `data/_core_valuation.json`); DXY/实际利率可选。"
             "自动拉取失败会自动沿用缓存并在此提醒。")
    L.append("> 免责声明: 机械规则仅供参考, 不构成投资建议; 市场有风险, 投资需谨慎。")
    return "\n".join(L) + "\n", dict(y10=y10, ydir=ydir, sc=sc, sc_note=sc_note)


def main():
    assets = rule.pool()
    v = rule.load_json(rule.VALUATION_FILE, rule.make_valuation_template())
    if not set(v) == set(rule.make_valuation_template()):
        v = {**rule.make_valuation_template(), **v}
    if not os.path.exists(_TEMPLATE_OUT):
        json.dump(rule.make_valuation_template(), open(_TEMPLATE_OUT, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)

    # 估值自动拉取(蛋卷): 覆盖 红利股息率/PE分位/科创PE比; 失败则沿用上次文件并标记
    try:
        auto = fetch_danjuan_eva()
        if auto and auto.get("red_yeild") is not None:
            v["as_of"] = auto["as_of"]
            v["div_yield_hs_pct"] = round(auto["red_yeild"] * 100, 2)
            v["hs_pe_pct"] = auto["red_pe_pct"]
            v["kc_pe_over_hs"] = auto.get("kc_pe_over_hs")
            v["_auto"] = dict(source="蛋卷基金 djapi/index_eva/dj", as_of=auto["as_of"],
                              red_pe=auto["red_pe"], kc_pe=auto.get("kc_pe"),
                              kc_pe_pct=auto.get("kc_pe_pct"))
            json.dump(v, open(rule.VALUATION_FILE, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
            print(f"[i] 估值自动拉取成功(蛋卷 {auto['as_of']}): "
                  f"红利股息率 {v['div_yield_hs_pct']}% / 红利PE分位 {v['hs_pe_pct']:.0%} / "
                  f"科创PE比 {v['kc_pe_over_hs']:.2f}")
    except Exception as e:  # noqa: BLE001
        print(f"[i] 估值自动拉取失败(沿用上次/人工值): {type(e).__name__}: {str(e)[:120]}")

    # 东财-mx-ds-mcp 快照(若有, 只作展示与辅助, 不入 scenario 主判)
    try:
        mxd = rule.load_json(_MX_FILE, None)
        if mxd and mxd.get("as_of"):
            v["_mx"] = mxd
    except Exception:  # noqa: BLE001
        pass

    # A500 本地日频PE库(官方日频, 每日自动增量; 用户手动录了 a500_pe_pct 则不覆盖)
    if v.get("a500_pe_pct") is None:
        try:
            import _a500_pe
            a5 = _a500_pe.daily_pe()
            v["a500_pe_pct"] = a5["pe_pct"]
            v["_a500"] = a5
        except Exception as e:  # noqa: BLE001
            print(f"[i] A500 本地PE分位不可用(沿用人工/缺省): {type(e).__name__}: {str(e)[:120]}")

    snap, nav_l = [], {}
    for a in assets:
        item = dict(key=a["key"], name=a["name"], off=a["off_code"], proxy=a["proxy"])
        try:
            item.update(bars_metrics(fetch_proxy_bars(a["proxy"])))
        except Exception as e:  # noqa: BLE001
            item["err"] = f"{type(e).__name__}: {str(e)[:120]}"
        try:
            ndf = fetch_nav(a["off_code"])
            nav_l[a["off_code"]] = ndf
            item["nav"] = nav_metrics(ndf)
        except Exception as e:  # noqa: BLE001
            item["nav"] = {"err": f"{type(e).__name__}: {str(e)[:120]}"}
        snap.append(item)

    y10, y10_term = fetch_yield_now()
    yseries = fetch_yield_series(weeks=6)
    sc, sc_note = rule.decide_scenario(v, y10)

    txt, meta = build_report(assets, snap, nav_l, y10, y10_term, yseries, v, sc, sc_note)
    with open(_OUT, "w", encoding="utf-8") as fh:
        fh.write(txt)
    print(txt)

    json.dump({
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        **meta,
        "valuation_auto": (v.get("_auto") or {}),
        "valuation_mxds": (v.get("_mx") or {}),
        "a500_local_pe": (v.get("_a500") or {}),
        "valuation_manual": {"a500_pe_pct": v.get("a500_pe_pct"),
                             "ndx_fwd_pe_pct": v.get("ndx_fwd_pe_pct"),
                             "dxy": v.get("dxy"), "us_real_yield": v.get("us_real_yield")},
        "target_weights": {k: rule.TARGET[k][rule.scene_key(sc)] for k in rule.TARGET},
        "assets": [{
            "key": s["key"], "name": s["name"], "off": s["off"], "proxy": s["proxy"],
            "bar_date": s.get("date"), "bar_close": s.get("close"),
            "d1": s.get("d1"), "d5": s.get("d5"), "d20": s.get("d20"),
            "above_ma28": s.get("above_ma28"),
            "nav_date": (s.get("nav") or {}).get("nav_date"),
            "nav": (s.get("nav") or {}).get("nav"),
        } for s in snap],
    }, open(_JSON_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
