# -*- coding: utf-8 -*-
"""V5 · 资金流因子（`f_f`）全史数据源探针。

背景：规格 §1 要求「518880 主力净流入连续 ≥3 日同向」，但东财 `fflow/daykline` 对 ETF
只回 **120 根**（`lmt=0` / `lmt=100000` / `beg-end` 都一样），复现不了 2013 年以来的全史，
所以当时把资金流剔除了计分。**旧 v5.0 口径已整体删除**；本文保留为数据源结论 ——
当前 v5 里资金流只作每日「近期提示」，永不进仓位。

本脚本只做一件事：**把能想到的免费源逐个打一遍，报告「能取到多少天、从哪天起」**，
用来回答「全史到底有没有免费源」。结论写进同目录 `_probe_flow.md`。

候选源（按可信度排）：
1. 东财 `push2his/push2` 的 `fflow/daykline`（不同 `lmt` / `beg-end` / 域名）
2. 东财 `datacenter-web` 的资金流报表（`RPT_*`）
3. 新浪 `MoneyFlow.ssi_ssfx_flzjtj` / `ssl_bkzj_zjlrqs`
4. 雪球 `capital/flow.json`（需 `xq_a_token` cookie）
5. 交易所 ETF 每日份额（上交所 `commonQuery`）—— 主力净流入的**替代口径**：
   `Δ份额 × 净值 = 净申购额`，对 ETF 比「大单分类」更接近真实资金流
6. 天天基金 F10 规模变动（季度，只能当低频对照）

用法：python strategies/vol_legs/v5_gold_score/research/flow_source/_probe_flow.py
只读网络，不写任何数据文件。非投资建议。
"""
import json
import os
import re
import sys

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
S = requests.Session()
S.headers.update({"User-Agent": UA})
CODE_EM = "1.518880"          # 1 = 上交所
SYM_XQ = "SH518880"
SYM_SINA = "sh518880"

F2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"


def _em(r):
    """东财 klines -> [(date, main_net), ...]；非该结构返回 None。"""
    d = (r.json().get("data") or {})
    ks = d.get("klines") or []
    out = []
    for line in ks:
        p = line.split(",")
        out.append((p[0], float(p[1])))
    return out


def _sina(r):
    txt = r.text.strip()
    if txt.startswith("var "):
        txt = txt.split("=", 1)[1].strip().rstrip(";")
    data = json.loads(txt) if txt.startswith("[") else None
    return data


def _xq_token():
    """雪球 token：先访问首页拿 cookie。"""
    try:
        S.get("https://xueqiu.com/", timeout=15)
        return S.cookies.get("xq_a_token")
    except Exception:  # noqa: BLE001
        return None


def show(name, url, parse, note="", headers=None, expect="rows"):
    """打一个源，打印「行数 / 起止日 / 判读」。"""
    try:
        r = S.get(url, timeout=25, headers=headers or {})
        r.raise_for_status()
        obj = parse(r)
    except Exception as exc:  # noqa: BLE001
        print(f"[失败] {name:26s} {type(exc).__name__}: {str(exc)[:90]}")
        return None
    if obj is None or len(obj) == 0:
        print(f"[空]   {name:26s} 无数据/结构不认识：{r.text[:90]!r}")
        return None
    n = len(obj)
    first, last = obj[0], obj[-1]
    print(f"[OK]   {name:26s} {n:5d} 行  {first} ~ {last}  {note}")
    return obj


def main():
    print("=== 1. 东财 fflow/daykline（主力净流入）===")
    base = ("https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
            "?klt=101&secid=" + CODE_EM + "&fields1=f1,f2,f3,f7&fields2=" + F2)
    a = show("EM his lmt=0", base + "&lmt=0", _em)
    show("EM his lmt=100000", base + "&lmt=100000", _em)
    show("EM his beg/end", base + "&lmt=0&beg=20130729&end=20500101", _em)
    show("EM push2 lmt=0", base.replace("push2his", "push2") + "&lmt=0", _em)
    show("EM 5.1 域名", base.replace("push2his.eastmoney.com", "push2his.eastmoney.com")
         .replace("1.518880", "0.518880"), _em, note="（错市场，作对照）")

    print("\n=== 2. 东财 datacenter 报表（试几个可能的 reportName）===")
    dc = ("https://datacenter-web.eastmoney.com/api/data/v1/get"
          "?sortColumns=TRADE_DATE&sortTypes=-1&pageSize=50&pageNumber=1"
          "&columns=ALL&source=WEB&client=WEB&filter=(SECURITY_CODE%3D%22518880%22)")
    for rn in ("RPT_STOCK_FUND_FLOW", "RPT_MULTI_STOCK_FUNDFLOW", "RPT_DAILY_FUNDFLOW"):
        show(f"DC {rn}", dc + "&reportName=" + rn,
             lambda r: (((r.json().get("result") or {}).get("data")) or None),
             note="（看是否有 result.data）")

    print("\n=== 3. 新浪资金流向 ===")
    show("Sina flzjtj", "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
         f"MoneyFlow.ssi_ssfx_flzjtj?daima={SYM_SINA}", _sina)
    show("Sina zjlrqs", "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
         f"MoneyFlow.ssl_bkzj_zjlrqs?daima={SYM_SINA}", _sina)

    print("\n=== 4. 雪球 capital/flow ===")
    tok = _xq_token()
    print(f"  xq_a_token: {'有' if tok else '无'}")
    hdr = {"Referer": "https://xueqiu.com/S/SH518880", "Cookie": f"xq_a_token={tok};" if tok else ""}
    for cnt in ("-2000", "-10000"):
        show(f"XQ flow count={cnt}",
             f"https://stock.xueqiu.com/v5/stock/capital/flow.json?symbol={SYM_XQ}"
             f"&count={cnt}&type=day", lambda r: (r.json().get("data") or {}).get("items"),
             note="（items 里是逐日大中小单净额）", headers=hdr)

    print("\n=== 5. 交易所 ETF 每日份额（净申购替代口径）===")
    sse = "https://query.sse.com.cn/commonQuery.do"
    q = {"sqlId": "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L", "isPagination": "true",
         "pageHelp.pageSize": "20", "pageHelp.pageNo": "1", "pageHelp.beginPage": "1",
         "pageHelp.cacheSize": "1", "pageHelp.endPage": "1"}
    show("SSE ETF 规模(首页20)",
         sse + "?" + "&".join(f"{k}={v}" for k, v in q.items()),
         lambda r: (r.json().get("result") or []),
         note="（每行一天？还是只有最新一天，看 TRADE_DATE 分布）",
         headers={"Referer": "http://www.sse.com.cn/"})

    print("\n=== 6. 天天基金 F10 规模变动（季度）===")
    try:
        r = S.get("https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
                  "?type=gmbd&code=518880&rt=0.1", timeout=25)
        rows = re.findall(r"<td>(\d{4}-\d{2}-\d{2})</td>", r.text)
        print(f"[OK]   F10 规模变动              {len(rows):5d} 期  "
              f"{rows[-1] if rows else '—'} ~ {rows[0] if rows else '—'}（季度/半年度）")
    except Exception as exc:  # noqa: BLE001
        print(f"[失败] F10 规模变动 {type(exc).__name__}: {exc}")

    print("\n=== 结论 ===")
    if a:
        print(f"东财主力净流入可复现上限：{a[0][0]} ~ {a[-1][0]}（{len(a)} 个交易日）"
              "—— 只够当「近期提示」，不够进计分。")


def round2():
    """第二轮：把「上限是接口硬限制」与「份额口径能不能取全史」问清楚。"""
    print("\n=== 7. 120 行上限是「ETF 特例」还是「接口硬限制」？===")
    base = ("https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
            "?lmt=0&klt=101&fields1=f1,f2,f3,f7&fields2=" + F2 + "&secid=")
    for tag, secid in (("个股 600519", "1.600519"), ("个股 000001", "0.000001")):
        show(f"EM {tag}", base + secid, _em)
    show("EM kline(非 daykline)", base.replace("daykline", "kline") + "1.518880", _em)

    print("\n=== 8. 雪球「逐日」资金流（换参数再试）===")
    tok = _xq_token()
    hdr = {"Referer": "https://xueqiu.com/S/SH518880", "Cookie": f"xq_a_token={tok};"}
    for qs in ("count=-2000&type=day", "count=-2000&period=day", "count=-2000"):
        try:
            r = S.get("https://stock.xueqiu.com/v5/stock/capital/flow.json?"
                      f"symbol={SYM_XQ}&{qs}", timeout=20, headers=hdr)
            items = (r.json().get("data") or {}).get("items") or []
            if items:
                t0 = items[0].get("timestamp")
                span = (items[-1]["timestamp"] - t0) / 86400000
                print(f"[OK]   XQ {qs:22s} {len(items):5d} 项  跨度 {span:.1f} 天"
                      f"  {'（分钟级，不是日线）' if span < 3 else ''}")
            else:
                print(f"[空]   XQ {qs:22s} {r.text[:70]!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"[失败] XQ {qs:22s} {type(exc).__name__}: {str(exc)[:70]}")

    print("\n=== 9. 上交所 ETF 每日份额：能不能按代码 / 按日期筛？===")
    sse = "https://query.sse.com.cn/commonQuery.do"
    hdr = {"Referer": "http://www.sse.com.cn/"}
    base = {"sqlId": "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L", "isPagination": "true",
            "pageHelp.pageSize": "2000", "pageHelp.pageNo": "1", "pageHelp.beginPage": "1",
            "pageHelp.cacheSize": "1", "pageHelp.endPage": "1"}
    for tag, extra in (("按代码 SEC_CODE（无效）", {"SEC_CODE": "518880", "STAT_DATE": "2015-06-30"}),
                       ("按日期 2015-06-30", {"STAT_DATE": "2015-06-30"}),
                       ("按日期 2013-07-29", {"STAT_DATE": "2013-07-29"}),
                       ("按日期 2026-11-17", {"STAT_DATE": "2026-11-17"})):
        q = dict(base, **extra)
        try:
            r = S.get(sse, params=q, timeout=20, headers=hdr)
            rows = (r.json().get("result") or [])
            hit = [x for x in rows if str(x.get("SEC_CODE")) == "518880"]
            dates = sorted({x.get("STAT_DATE") for x in rows})
            print(f"[OK]   SSE {tag:28s} {len(rows):4d} 行  日期 {dates[:1]}~{dates[-1:]}"
                  f"  含 518880: {'是 ' + hit[0].get('TOT_VOL', '') + ' 万份' if hit else '否'}")
        except Exception as exc:  # noqa: BLE001
            print(f"[失败] SSE {tag:28s} {type(exc).__name__}: {str(exc)[:70]}")


if __name__ == "__main__":
    main()
    round2()
