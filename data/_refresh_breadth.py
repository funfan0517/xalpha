# -*- coding: utf-8 -*-
"""全市场广度库（涨跌家数 / 涨跌成交额）· 跨策略共享的**数据类**产物。

TRIN（阿姆氏指标）之类的情绪指标需要的是「全市场每一天的横截面」——上涨/下跌的家数与成交额，
而不是任何单一标的的行情。这样的数据在仓库里没有先例（`_long_klines.json` 只是 54 只 ETF），
所以由本脚本从东财公开接口一次性回补 + 每日增量维护，落盘成 `data/_breadth_daily.json`。

口径（写死，改口径=改文件名+改 meta，避免静默换口径）
------------------------------------------------------
- 标的：**沪深 A 股**（东财 fs `m:0 t:6 / m:0 t:80 / m:1 t:2 / m:1 t:23`），
  代码前缀只保留 600/601/603/605/688/689/000/001/002/003/300/301；
  **不含北交所、B 股、基金、指数**。
- 方向：个股收盘涨跌幅 > 0 记上涨、< 0 记下跌、== 0 记平盘（前复权收盘自算，不用接口的 f59，
  以免除权日的「假跌」）。
- 成交额/成交量：接口原值（元 / 手），停牌或 0 成交的行直接剔除（自然排除停牌日）。
- 存活偏差：股票列表是**当前**的，已退市股票的历史不在库里 → 早年家数略偏少。
  TRIN 用的是「上涨/下跌」的**比值**，对绝对家数不敏感，偏差可接受；报告里如实标注。

两种维护方式
------------
1. `--full`：并发回补历史（逐只股票一次取全历史，聚合后**不落个股明细**，只存每日合计）。
   可断点续跑 —— 进度写在 `data/_bt_caches/breadth_build_state.json`，`--budget` 控制单次时长。
2. `--update`：只取当日快照（一次翻页请求），收盘后追加一天。日常维护用这个。

产物
----
`data/_breadth_daily.json`：`{_meta, dates, adv, dec, flat, adv_vol, dec_vol, adv_amt, dec_amt, total_amt}`

用法
----
    python data/_refresh_breadth.py --full --budget 240 --workers 12   # 回补（可重复执行续跑）
    python data/_refresh_breadth.py --full --reset                     # 丢弃进度重新回补
    python data/_refresh_breadth.py --update                           # 收盘后追加当日
    python data/_refresh_breadth.py --status                           # 看进度/覆盖区间
"""
import argparse
import datetime as dt
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))          # data/
OUT = os.path.join(_HERE, "_breadth_daily.json")
STATE = os.path.join(_HERE, "_bt_caches", "breadth_build_state.json")

START = "20141201"                     # 抓取起点（比 KEEP_START 早一个月，保证首日有前收可算涨跌幅）
KEEP_START = "2015-01-01"              # 落库起点（2015 年前两融/创业板口径差异大，不追）
PREFIX = ("600", "601", "603", "605", "688", "689",
          "000", "001", "002", "003", "300", "301")
FS = "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23"   # 沪深 A 股（不含北交所/B 股/基金）
UT = "bd1d9ddb04089700cf9c27f6f7426281"    # 东财公开页固定 ut

API_LIST = "https://push2.eastmoney.com/api/qt/clist/get"
API_KLINE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

COLS = ("adv", "dec", "flat", "adv_vol", "dec_vol", "adv_amt", "dec_amt")


def _session():
    """直连会话：绕过系统代理（东财是国内站点，走代理反而常被断开）。"""
    s = requests.Session()
    s.trust_env = False
    s.headers.update({"User-Agent": "Mozilla/5.0"})
    return s


_S = _session()


def _get(url, params, tries=3):
    last = None
    for i in range(tries):
        try:
            r = _S.get(url, params=params, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001 - 单次失败重试
            last = e
            time.sleep(0.4 * (i + 1))
    raise RuntimeError(f"{type(last).__name__}: {last}")


# ------------------------------------------------------------------ 标的列表
def stock_list():
    """沪深 A 股列表 -> [{code, name}]（按代码前缀过滤）。"""
    out, pn, seen = [], 1, 0
    while True:
        d = _get(API_LIST, {"pn": pn, "pz": 100, "po": 1, "np": 1, "ut": UT,
                            "fltt": 2, "invt": 2, "fid": "f12", "fs": FS,
                            "fields": "f12,f14"})
        data = (d or {}).get("data") or {}
        diff = data.get("diff") or []
        if not diff:
            break
        seen += len(diff)
        for it in diff:
            code = str(it.get("f12") or "")
            if code.startswith(PREFIX):
                out.append({"code": code, "name": it.get("f14")})
        if seen >= int(data.get("total") or 0):
            break
        pn += 1
    return out


def _secid(code):
    return ("1." if code.startswith(("6", "9")) else "0.") + code


# ------------------------------------------------------------------ 单只个股
def fetch_kline(code, start=START):
    """前复权日线 -> [(date, close, vol, amt)]（只保留有效行）。"""
    d = _get(API_KLINE, {"secid": _secid(code),
                         "fields1": "f1,f2,f3,f4,f5,f6",
                         "fields2": "f51,f52,f53,f54,f55,f56,f57",
                         "klt": 101, "fqt": 1, "beg": start, "end": "20500101",
                         "lmt": 10000})
    data = (d or {}).get("data") or {}
    rows = []
    for line in data.get("klines") or []:
        p = line.split(",")
        try:
            date, close, vol, amt = p[0], float(p[2]), float(p[5]), float(p[6])
        except (IndexError, ValueError):
            continue
        if close <= 0 or vol <= 0 or amt <= 0:
            continue
        rows.append((date, close, vol, amt))
    return rows


def _contribute(sums, rows):
    """把单只股票的日线并入聚合器（首根无前收，跳过；KEEP_START 之前的行丢弃）。"""
    today = dt.date.today().isoformat()
    intraday = dt.datetime.now().hour < 15
    for i in range(1, len(rows)):
        date, close, vol, amt = rows[i]
        if date < KEEP_START or (intraday and date == today):
            continue
        prev = rows[i - 1][1]
        chg = close / prev - 1.0
        cell = sums.setdefault(date, [0, 0, 0, 0.0, 0.0, 0.0, 0.0])
        if chg > 1e-12:
            cell[0] += 1
            cell[3] += vol
            cell[5] += amt
        elif chg < -1e-12:
            cell[1] += 1
            cell[4] += vol
            cell[6] += amt
        else:
            cell[2] += 1


def _load_state():
    """读回补进度。状态文件丢了但产物在 -> 用产物兜底重建 sums（并标记需重新回补）。

    没有这层兜底，`_bt_caches/` 被清掉后跑一次 `--update` 会把 2800 天的库覆盖成 1 天，
    而且**没有任何报错**。
    """
    if os.path.exists(STATE):
        return json.load(open(STATE, encoding="utf-8"))
    if os.path.exists(OUT):
        d = json.load(open(OUT, encoding="utf-8"))
        sums = {date: [d["adv"][i], d["dec"][i], d["flat"][i],
                       d["adv_vol"][i], d["dec_vol"][i], d["adv_amt"][i], d["dec_amt"][i]]
                for i, date in enumerate(d["dates"])}
        print("[广度] 缺状态文件，已从产物 _breadth_daily.json 兜底恢复；"
              "如要重新回补请用 --full --reset")
        return {"done": [], "sums": sums, "stocks": d["_meta"].get("n_stocks_done", 0),
                "rebuild_required": True,
                "started": dt.datetime.now().isoformat(timespec="seconds")}
    return {"done": [], "sums": {}, "stocks": 0, "started": dt.datetime.now().isoformat(timespec="seconds")}


def _save_state(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    st["updated"] = dt.datetime.now().isoformat(timespec="seconds")
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)


# ------------------------------------------------------------------ 回补 / 增量
def build(budget=240, workers=12, reset=False, verbose=True):
    """并发回补历史；budget 秒内尽量多跑，剩余留到下次（状态文件断点续跑）。"""
    if reset and os.path.exists(STATE):
        os.remove(STATE)
    st = _load_state()
    if st.get("rebuild_required") and not reset:
        sys.exit("[广度] 状态文件曾丢失（sums 由产物兜底恢复），无法判断哪些股票已计入 —— "
                 "请用 --full --reset 重新回补，避免重复计数")
    done = set(st["done"])
    sums = {k: list(v) for k, v in st["sums"].items()}

    universe = stock_list()
    todo = [s["code"] for s in universe if s["code"] not in done]
    st["stocks"] = len(universe)
    if verbose:
        print(f"[广度] 全市场 {len(universe)} 只 · 已完成 {len(done)} · 本次待办 {len(todo)}")

    t0 = time.time()
    fails, added = 0, 0

    def work(code):
        try:
            return code, fetch_kline(code)
        except Exception as e:  # noqa: BLE001 - 单只失败不回滚整批
            return code, e

    # 分批提交而不是一次性 submit 全部：否则 budget 到点后仍会等所有任务跑完。
    for i in range(0, len(todo), workers * 4):
        if time.time() - t0 > budget:
            break
        chunk = todo[i:i + workers * 4]
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for code, res in ex.map(work, chunk):
                if isinstance(res, Exception):
                    fails += 1
                    continue
                _contribute(sums, res)
                done.add(code)
                added += 1
        st.update(done=sorted(done), sums=sums)
        _save_state(st)
        if verbose:
            print(f"[广度] 进度 {len(done)}/{len(universe)} · {time.time() - t0:.0f}s", flush=True)

    st.update(done=sorted(done), sums=sums)
    _save_state(st)
    if verbose:
        print(f"[广度] 本次入库 {added} 只（失败 {fails}）· 累计 {len(done)}/{len(universe)}"
              f" · 耗时 {time.time() - t0:.0f}s")
    finalize(partial=len(done) < len(universe))


def finalize(partial=False, verbose=True):
    st = _load_state()
    sums = st["sums"]
    dates = sorted(sums)
    if not dates:
        print("[广度] 状态为空，无可落盘数据")
        return
    body = {k: [] for k in COLS}
    for d in dates:
        a, dc, f, av, dv, aa, da = sums[d]
        body["adv"].append(int(a))
        body["dec"].append(int(dc))
        body["flat"].append(int(f))
        body["adv_vol"].append(round(av, 0))
        body["dec_vol"].append(round(dv, 0))
        body["adv_amt"].append(round(aa, 0))
        body["dec_amt"].append(round(da, 0))
    out = {
        "_meta": {
            "source": "东方财富 push2 / push2his（沪深A股，不含北交所/B股/基金/指数）",
            "built_at": dt.datetime.now().isoformat(timespec="seconds"),
            "universe": f"沪深A股 {st.get('stocks')} 只（当前列表）",
            "n_stocks_done": len(st.get("done") or []),
            "partial": bool(partial),
            "start": dates[0], "end": dates[-1], "days": len(dates),
            "direction": "前复权收盘自算涨跌幅；停牌/0 成交行剔除",
            "survivorship": "股票列表为当前存续标的，退市股历史缺失 → 早年家数偏少（TRIN 为比值，影响有限）",
            "builder": "data/_refresh_breadth.py",
        },
        "dates": dates,
        **body,
    }
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    if verbose:
        print(f"[广度] 已落盘 {OUT} · {dates[0]} ~ {dates[-1]} · {len(dates)} 个交易日"
              f"{'（部分标的，partial=true）' if partial else ''}")


def update_today(verbose=True, force=False):
    """收盘后取一次全市场快照，追加/覆盖最后一天（日常维护用）。

    三道闸：①盘中不取（bar 未定型）；②周末不取；③快照总成交额与库内最后一天相差 5 倍以上视为
    「非交易日/异常快照」，不落库。缺任何一道，节假日跑一次就会凭空多出一根假 K 线。
    """
    now = dt.datetime.now()
    if now.hour < 15:            # --force 也不越过：半日快照一旦入库就是一根假 K 线
        if verbose:
            print("[广度] 当前未到 15:00，当日 bar 未定型 —— 跳过更新（收盘后再跑）")
        return False
    if dt.date.today().weekday() >= 5 and not force:
        if verbose:
            print("[广度] 周末，跳过更新")
        return False
    cells = {}
    pn, seen = 1, 0
    while True:
        d = _get(API_LIST, {"pn": pn, "pz": 100, "po": 1, "np": 1, "ut": UT,
                            "fltt": 2, "invt": 2, "fid": "f12", "fs": FS,
                            "fields": "f3,f5,f6,f12"})
        data = (d or {}).get("data") or {}
        diff = data.get("diff") or []
        if not diff:
            break
        seen += len(diff)
        for it in diff:
            code = str(it.get("f12") or "")
            if not code.startswith(PREFIX):
                continue
            try:
                chg, vol, amt = float(it["f3"]) / 100.0, float(it["f5"]), float(it["f6"])
            except (KeyError, TypeError, ValueError):
                continue
            if vol <= 0 or amt <= 0:
                continue
            cell = cells.setdefault("d", [0, 0, 0, 0.0, 0.0, 0.0, 0.0])
            if chg > 1e-12:
                cell[0] += 1
                cell[3] += vol
                cell[5] += amt
            elif chg < -1e-12:
                cell[1] += 1
                cell[4] += vol
                cell[6] += amt
            else:
                cell[2] += 1
        if seen >= int(data.get("total") or 0):
            break
        pn += 1
    if not cells:
        print("[广度] 快照为空，未更新")
        return False
    total = float(cells["d"][3])
    st = _load_state()
    last = max(st["sums"]) if st["sums"] else None
    if last:
        prev = sum(st["sums"][last][3:5])
        if prev > 0 and not (0.2 <= total / prev <= 5.0):
            print(f"[广度] 快照成交量({total:.0f}) 与库内最后一天({prev:.0f}) 相差过大 —— 视为非交易日/异常，未更新")
            return False
    date = dt.date.today().isoformat()
    st["sums"][date] = cells["d"]
    _save_state(st)
    finalize(partial=False, verbose=False)
    if verbose:
        c = cells["d"]
        print(f"[广度] 已追加 {date}：涨 {c[0]} / 跌 {c[1]} / 平 {c[2]}")
    return True


# ------------------------------------------------------------------ 读取
def load():
    """-> DataFrame(index=DatetimeIndex, 列见 COLS)。缺失返回空表。"""
    if not os.path.exists(OUT):
        return pd.DataFrame(columns=list(COLS))
    d = json.load(open(OUT, encoding="utf-8"))
    df = pd.DataFrame({k: d[k] for k in COLS}, index=pd.to_datetime(d["dates"]))
    return df.sort_index()


def status():
    if os.path.exists(OUT):
        d = json.load(open(OUT, encoding="utf-8"))
        m = d["_meta"]
        print(f"[广度] 库 {m['start']} ~ {m['end']} · {m['days']} 天 · 标的 {m['n_stocks_done']} 只"
              f" · partial={m['partial']}")
    else:
        print("[广度] 尚无库文件")
    if os.path.exists(STATE):
        st = json.load(open(STATE, encoding="utf-8"))
        print(f"[广度] 进度 {len(st.get('done') or [])}/{st.get('stocks')} 只 · 更新于 {st.get('updated')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="并发回补历史（可续跑）")
    ap.add_argument("--reset", action="store_true", help="丢弃进度重新回补")
    ap.add_argument("--update", action="store_true", help="收盘后追加当日快照")
    ap.add_argument("--force", action="store_true", help="盘中强制更新当日")
    ap.add_argument("--status", action="store_true", help="查看进度")
    ap.add_argument("--budget", type=int, default=240, help="单次回补时长上限（秒）")
    ap.add_argument("--workers", type=int, default=12)
    a = ap.parse_args()
    if a.update:
        update_today(force=a.force)
    elif a.full:
        build(budget=a.budget, workers=a.workers, reset=a.reset)
    else:
        status()
    return 0


if __name__ == "__main__":
    sys.exit(main())
