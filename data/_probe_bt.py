# -*- coding: utf-8 -*-
"""一次性: 回测数据盘点 —— csindex 指数(peg/close/H), 场内腿起点。"""
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import requests
import xalpha as xa

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}

def csi(code, start, end="20260909"):
    try:
        r = requests.get(
            f"https://www.csindex.com.cn/csindex-home/perf/index-perf?indexCode={code}"
            f"&startDate={start}&endDate={end}", headers=UA, timeout=20)
        d = r.json()
        data = d.get("data") or []
        if not data:
            return f"{code}: EMPTY(api说 {d.get('code')}/{d.get('msg')})"
        cols = list(data[0].keys())
        return (f"{code}: rows={len(data)} {data[0]['tradeDate']}..{data[-1]['tradeDate']} "
                f"peg_in={('peg' in cols)} close_in={('close' in cols)}")
    except Exception as e:
        return f"{code}: FAIL {type(e).__name__}: {str(e)[:120]}"

print("== csindex ==")
print(csi("000922", "20150601"))   # 中证红利
print(csi("000688", "20190701"))   # 科创50
print(csi("000300", "20150601"))   # 沪深300(中枢代理/基准)
print(csi("H00922", "20150601"))   # 中证红利 全收益?
print(csi("H00300", "20150601"))   # 沪深300 全收益?

print("== xueqiu 场内腿起点(start=2015-06-01) ==")
for c in ("SH511260", "SZ518880", "SH513100"):
    try:
        df = xa.get_daily(c, start="2015-06-01").dropna(subset=["close"]).sort_values("date")
        print(c, "rows", len(df), "first", str(df["date"].iloc[0].date()), "last", str(df["date"].iloc[-1].date()))
    except Exception as e:
        print(c, "FAIL", type(e).__name__, str(e)[:120])
