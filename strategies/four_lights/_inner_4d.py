"""场内映射四灯扫描：唯一池 _universe.md 中「有场内对应」的内池标的，用场内 OHLCV(现抓) + mx主力/换手快照跑四灯。

评分逻辑与 gen_4d.py 一致（方法论 §5.1-5.4）。
用法: python _inner_4d.py [code1,code2,...]  每行输出一条 JSON。
"""
import io
import json
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import xalpha as xa
from pipeline import universe

MX = "G:/tradingagents/fund_data/data/mx_snapshot_latest.json"
# 唯一池: data/_universe.md 中「有场内对应」的行（_universe.md 为唯一维护入口，勿在此硬编码）
POOL = universe.inner_rows()


def sh(code):
    return ("SH" if code.startswith(("5", "6", "9")) else "SZ") + code


# ---- 指标（与 gen_4d 同款） ----
def sma(s, n):
    return sum(s[-n:]) / n if len(s) >= n else None


def ema_series(s, n):
    k = 2.0 / (n + 1)
    out = []
    prev = s[0]
    for x in s:
        prev = x if not out else x * k + prev * (1 - k)
        out.append(prev)
    return out


def macd(s):
    e12 = ema_series(s, 12)
    e26 = ema_series(s, 26)
    dif = [a - b for a, b in zip(e12, e26)]
    return dif, ema_series(dif, 9)


def adx(high, low, close, n=14):
    if len(close) < n * 2 + 1:
        return None
    tr, pDM, mDM = [], [], []
    for i in range(1, len(close)):
        up = high[i] - high[i - 1]
        dn = low[i - 1] - low[i]
        pDM.append(up if (up > dn and up > 0) else 0)
        mDM.append(dn if (dn > up and dn > 0) else 0)
        tr.append(max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])))
    atr = sum(tr[:n]) / n
    pdm = sum(pDM[:n]) / n
    mdm = sum(mDM[:n]) / n
    pdi, mdi, dx = [], [], []
    for i in range(n, len(tr)):
        atr = (atr * (n - 1) + tr[i]) / n
        pdm = (pdm * (n - 1) + pDM[i]) / n
        mdm = (mdm * (n - 1) + mDM[i]) / n
        pdi.append(100 * pdm / atr if atr else 0)
        mdi.append(100 * mdm / atr if atr else 0)
        dx.append(100 * abs(pdi[-1] - mdi[-1]) / (pdi[-1] + mdi[-1]) if (pdi[-1] + mdi[-1]) else 0)
    a = dx[0]
    for x in dx[1:]:
        a = (a * (n - 1) + x) / n
    return a


def weekly_closes(dates, closes):
    from datetime import date

    wk = {}
    for d, c in zip(dates, closes):
        try:
            y, w, _ = date.fromisoformat(str(d)[:10]).isocalendar()
        except Exception:
            continue
        wk[(y, w)] = c
    return [wk[k] for k in sorted(wk)]


# ---- mx 快照 ----
def load_snapshot():
    snap = json.load(open(MX, encoding="utf-8"))
    fund_sheet = max(
        snap["raw"]["data"],
        key=lambda sh: sum(
            1 for c in sh.get("columns", [])[1:] if re.search(r"\d{6}", str(c))
        ),
    )
    cols = fund_sheet["columns"]
    fund_cols = [c for c in cols[1:] if re.search(r"\d{6}", str(c))]
    n2c = {re.search(r"(\d{6})", c).group(1): c for c in fund_cols}

    def row_val(label):
        for r in fund_sheet["items"]:
            if str(r[0]).strip() == label:
                return r[1:]
        return None

    def num(x):
        if x is None:
            return None
        s = str(x).strip()
        if s in ("-", "", "None", "--", "nan"):
            return None
        m = re.match(r"[-+]?[\d.]+", s.replace(",", ""))
        if not m:
            return None
        v = float(m.group())
        if "亿" in s:
            v *= 1e8
        elif "万" in s:
            v *= 1e4
        return v

    turn_row, volr_row, amt_row = row_val("换手率"), row_val("量比"), row_val("成交额")
    net_row = row_val("主力净流入资金")
    intraday = {}
    for i, c in enumerate(fund_cols):
        code = re.search(r"(\d{6})", c).group(1)
        turn = num(turn_row[i]) if turn_row else None
        volr = num(volr_row[i]) if volr_row else None
        amt = num(amt_row[i]) if amt_row else None
        net = num(net_row[i]) if net_row else None
        pct = (net / amt * 100) if (amt and net is not None) else None
        intraday[code] = dict(turn=turn, volr=volr, amount=amt, main_net=net, main_pct=pct)
    return intraday, snap.get("snapshot_time", "?")


# ---- 评分（方法论 §5.1-5.4，与 gen_4d 一致） ----
def score_trend(kl):
    c, h, l = kl["close"], kl["high"], kl["low"]
    ma5, ma10, ma20 = sma(c, 5), sma(c, 10), sma(c, 20)
    if ma20 is None:
        return 0, "灭", "数据不足"
    prev20 = sum(c[-21:-1]) / 20 if len(c) >= 21 else ma20
    slope_up = ma20 > prev20
    ad = adx(h, l, c, 14)
    adx_s = f"ADX={ad:.1f}" if ad is not None else "ADX=N/A"
    if ma5 > ma10 > ma20 and slope_up and c[-1] > ma20 and (ad is not None and ad > 25):
        return 2, "亮", f"MA多头 & MA20↑ & 收>MA20 & {adx_s}>25"
    if ma5 > ma10 and c[-1] > ma20:
        return 1, "偏多", f"MA5>MA10 & 收>MA20 ({adx_s})"
    return 0, "灭", f"{'MA5<MA10' if ma5 <= ma10 else '收<MA20'}"


def score_capital(kl, it):
    c, o, v = kl["close"], kl["open"], kl["volume"]
    v5 = sma(v, 5)
    vr = (v[-1] / v5) if v5 else None
    yang = c[-1] > o[-1]
    net, pct = it.get("main_net"), it.get("main_pct")
    vr_s = f"量比={vr:.2f}" if vr else "量比=N/A"
    if vr is not None and vr > 1.5 and yang and pct is not None and pct > 5:
        return 2, "亮", f"放量({vr_s}>1.5)&收阳&主力占比{pct:.1f}%>5"
    if net is not None and net > 0 and yang:
        return 1, "偏多", f"主力净流入&收阳({vr_s})"
    if net is not None and net < 0:
        return 0, "灭", f"主力净流出{net / 1e8:+.2f}亿"
    if vr is not None and vr > 1.2 and not yang:
        return 0, "灭", f"放量下跌({vr_s})"
    if pct is None:
        return 1, "偏多", f"量价代理: {'放量上涨' if vr and vr>1.3 and yang else '量能平稳'}({vr_s})"
    return 1, "偏多", f"量能未明显放大({vr_s})"


def score_sustain(kl):
    c = kl["close"]
    dif, dea = macd(c)
    d, e = dif[-1], dea[-1]
    ret3 = (c[-1] / c[-4] - 1) * 100 if len(c) >= 4 else 0
    wc = weekly_closes(kl["dates"], c)
    wk_macd = None
    if len(wc) >= 35:
        wd, we = macd(wc)
        wk_macd = wd[-1] > we[-1]
    wk_s = ("周线多" if wk_macd else "周线空") if wk_macd is not None else "周线N/A"
    if d > e and d > 0 and ret3 > 3 and wk_macd:
        return 2, "亮", f"DIF>DEA&DIF>0&近3日{ret3:+.1f}%&{wk_s}"
    if d > e and d > 0 and 0 <= ret3 <= 3:
        return 1, "偏多", f"DIF>DEA&DIF>0&近3日{ret3:+.1f}%"
    if d > e and d > 0:
        return 1, "偏多", f"DIF>DEA&DIF>0&近3日{ret3:+.1f}%(跌)"
    return 0, "灭", f"{'DIF<DEA' if d <= e else 'DIF<0'}"


def score_heat(kl, it):
    c = kl["close"]
    w5 = (c[-1] / c[-6] - 1) * 100 if len(c) >= 6 else 0
    turn = it.get("turn")
    turn_s = f"换手{turn:.2f}%" if turn is not None else "换手N/A"
    if turn is not None and turn > 20:
        return 0, "灭", f"{turn_s}>20%过热"
    if w5 < 0:
        return 0, "灭", f"近5日{w5:+.1f}%<0走弱"
    if turn is not None and 3 <= turn <= 15 and 5 <= w5 <= 20:
        return 2, "亮", f"{turn_s}适中&近5日{w5:+.1f}%∈5-20"
    if w5 >= 5:
        return 1, "偏多", f"近5日{w5:+.1f}%动量强({turn_s})"
    if turn is None:
        return 1, "偏多", f"量价代理近5日{w5:+.1f}%({turn_s})"
    if w5 > 0:
        return 1, "偏多", f"近5日{w5:+.1f}%动量温和"
    return 1, "偏多", f"{turn_s}动量温和"


def main():
    intraday, snap_time = load_snapshot()
    want = set(sys.argv[1].split(",")) if len(sys.argv) > 1 and sys.argv[1] else None
    for row in POOL:
        idx, code, theme, off_name = row["idx"], row["code"], row["theme"], row["off_name"]
        inner_name = row["inner_name"]
        if want and code not in want:
            continue
        it = intraday.get(code, dict(turn=None, volr=None, amount=None, main_net=None, main_pct=None))
        try:
            df = xa.get_daily(sh(code), start="2023-01-01")
        except Exception as e:
            print(json.dumps({"idx": idx, "cat": row["cat"], "theme": theme, "inner": code, "ok": False,
                              "err": f"{type(e).__name__}: {e}"}, ensure_ascii=False), flush=True)
            continue
        df = df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
        kl = {
            "dates": [str(d.date()) for d in df["date"]],
            "open": df["open"].tolist(), "high": df["high"].tolist(),
            "low": df["low"].tolist(), "close": df["close"].tolist(),
            "volume": df["volume"].tolist(),
        }
        tl, tls, td = score_trend(kl)
        cl, cls, cd = score_capital(kl, it)
        sl, sls, sd = score_sustain(kl)
        hl, hls, hd = score_heat(kl, it)
        total = tl + cl + sl + hl
        c = kl["close"]
        today = (c[-1] / c[-2] - 1) * 100
        w5 = (c[-1] / c[-6] - 1) * 100
        w20 = (c[-1] / c[-21] - 1) * 100 if len(c) >= 21 else 0
        veto = w5 > 25 or (it.get("turn") is not None and it["turn"] > 20)
        if veto:
            dec, note = "观望", "否决(追高/过热)"
        elif total >= 6 and tl >= 1 and cl >= 1:
            dec, note = "买入", "总分≥6 & 趋势/主力≥1"
        elif total == 5 and tl >= 1 and cl >= 1:
            dec, note = "持有", "总分5 边际关注"
        elif total <= 3:
            dec, note = "卖出", "总分≤3"
        else:
            dec, note = "观望", f"总分{total} 无买点"
        print(json.dumps({
            "idx": idx, "cat": row["cat"], "theme": theme, "off_name": off_name, "inner": code,
            "inner_name": inner_name, "ok": True, "date": kl["dates"][-1],
            "today": round(today, 2), "w5": round(w5, 2), "w20": round(w20, 2),
            "tl": tl, "tls": tls, "td": td, "cl": cl, "cls": cls, "cd": cd,
            "sl": sl, "sls": sls, "sd": sd, "hl": hl, "hls": hls, "hd": hd,
            "total": total, "dec": dec, "note": note,
            "snap": snap_time[:10], "has_main": it.get("main_pct") is not None,
        }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
