# -*- coding: utf-8 -*-
"""公用行情刷新器（跨策略共享 · 为 data/_long_klines.json 与 _indicators_hist.json 服务）。

字段、覆盖、已知缺陷与使用禁忌见 **`data/_DATA_SOURCES.md`**（该目录的唯一说明书，改数据结构时同步更新）。

任何策略在执行前调用 :func:`ensure_fresh` 即可保证本地行情为最新：已最新则只做一次日期检查（几乎零成本），
非最新才联网拉取。

- 数据源：xa.get_daily（默认**前复权**，历史值可能因分红整体平移）。
- 刷新策略「增量 + 一致性校验」：取近月窗口与库中重叠交易日比对收盘价，
  一致 → 仅追加新交易日；不一致（复权基准变了）→ 对整段历史重取。
- 指标随刷新一并重算：MA(5/10/20/60) / MACD(12,26,9) / KDJ(9,3,3) / BOLL(20,2) / VOL。
- **只写已收盘的 bar**（2026-11-24 起）：`refresh` 落库前按该标的所在市场的收盘时刻
  （`data/_market_clock.py`：A 股 15:05 / 港股 16:05 / 美股北京次日 05:05）丢掉尾部未收盘的那根。
  本库是跨策略共享的十年行情库，半根写进来会让黄金腿收盘价与 `rvol` 分母一起错。
  `ensure_fresh` 的比较基准相应改成「已收盘的最新交易日」（`expected_closed`），15:05 前不会
  把「今天」当基准从而反复触发整库刷新。
- **旧库兜底**：若最后一根 bar 是「当日盘中」写入的（**旧版代码**的行为），仅比日期会误判为
  「已最新」而永不修正 —— 此时 `ensure_fresh` 会在收盘后强制刷新一次（判据见 `_last_bar_intraday`）。
  该兜底自带收敛：修正过一次后 mtime 就晚于 15:00，后续运行不再触发。

用法：
    import _refresh_data                      # 需先把本目录加入 sys.path
    latest, fails = _refresh_data.ensure_fresh()   # 检查并（必要时）刷新
命令行自检：python data/_refresh_data.py
"""
import datetime as dt
import importlib.util
import json
import os
import sys
import time

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))      # data/
_ROOT = os.path.dirname(_HERE)                          # 仓库根
LK = os.path.join(_HERE, "_long_klines.json")
IND = os.path.join(_HERE, "_indicators_hist.json")
_CHECKED = None   # ensure_fresh 的进程内幂等缓存

# 复用 _gen_indicators 的指标公式（只读）
_spec = importlib.util.spec_from_file_location("gen_ind", os.path.join(_ROOT, "_gen_indicators.py"))
gi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gi)

# 半成品 bar 的判据（按市场收盘时刻），与 `strategies/vol_legs/_idxcache.py` 共用同一份
_spec = importlib.util.spec_from_file_location(
    "xalpha_market_clock", os.path.join(_HERE, "_market_clock.py"))
mc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mc)


def expected_latest():
    """日历上的最新交易日（最近的工作日，未剔除节假日、**不扣收盘时刻**）。"""
    d = dt.date.today()
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d.isoformat()


def expected_closed():
    """期望的「**已收盘**」最新交易日 —— `ensure_fresh` 的比较基准。

    15:05 之前跑，今天那根还不算收盘，基准退回上一工作日。否则库里只存已收盘 bar
    （见 `refresh`），基准却要今天，会导致**每个进程都判定「非最新」而联网整库刷新一次**。
    """
    d = dt.date.today()
    if not mc.bar_closed("CN", d):
        d -= dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d.isoformat()


def _fetch(code, start=None, end=None):
    import xalpha as xa
    pref = gi.exchange_prefix(code)
    err = None
    for p in (pref, "SH" if pref == "SZ" else "SZ"):
        try:
            df = xa.get_daily(p + code, start=start, end=end)
            if df is not None and len(df):
                return df
        except Exception as e:  # noqa
            err = e
    raise RuntimeError(str(err) if err else "empty")


def _vol_list(series):
    return [None if pd.isna(v) else int(round(float(v))) for v in series]


def _prep(df):
    df = df.copy()
    df["d"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df


def _recompute(dates, close, vol):
    s = pd.Series(close, dtype="float64")
    dif, dea, hist = gi.macd_series(s)
    k, d, j = gi.kdj_series(s)
    up, mid, lo = gi.boll_series(s)
    return {
        "dates": dates,
        "MA5": gi.s2list(gi.ma_series(s, 5)), "MA10": gi.s2list(gi.ma_series(s, 10)),
        "MA20": gi.s2list(gi.ma_series(s, 20)), "MA60": gi.s2list(gi.ma_series(s, 60)),
        "DIF": gi.s2list(dif), "DEA": gi.s2list(dea), "HIST": gi.s2list(hist),
        "K": gi.s2list(k), "D": gi.s2list(d), "J": gi.s2list(j),
        "BOLL_UPPER": gi.s2list(up), "BOLL_MID": gi.s2list(mid), "BOLL_LOWER": gi.s2list(lo),
        "volume": vol,
    }


def last_dates():
    """只读：{code: 最近日期}。"""
    lk = json.load(open(LK, encoding="utf-8"))
    return {c: v["dates"][-1] for c, v in lk.items()}


def refresh(codes=None):
    """强制刷新（拉取最新）。返回 (latest_map, fail_map)。"""
    lk = json.load(open(LK, encoding="utf-8"))
    ind = json.load(open(IND, encoding="utf-8"))
    meta = ind.pop("_meta", None)
    targets = codes or list(ind)
    latest, fails = {}, {}
    for code in targets:
        try:
            iv = ind[code]
            dates, close = lk[code]["dates"], lk[code]["close"]
            old_v = dict(zip(iv["dates"], iv["volume"]))   # 按日期对齐旧成交量
            last = dates[-1]
            win_start = (pd.Timestamp(last) - pd.Timedelta(days=40)).strftime("%Y%m%d")
            df = _prep(_fetch(code, start=win_start))
            fclose = dict(zip(df["d"], df["close"]))
            fvol = dict(zip(df["d"], df["volume"]))
            stored = dict(zip(dates, close))
            overlap = [d for d in dates[-5:] if d in fclose]
            shifted = (not overlap) or any(
                abs(fclose[d] - stored[d]) > max(1e-6, 1e-4 * abs(stored[d])) for d in overlap)

            if shifted:  # 复权基准变化 → 整段重取
                df2 = _prep(_fetch(code, start=dates[0]))
                nd = list(df2["d"])
                nc = [float(x) for x in df2["close"]]
                nv = _vol_list(df2["volume"])
            else:        # 一致 → 仅追加新交易日（volume 按日期对齐，缺失填 None）
                add = [d for d in df["d"] if d > last]
                nd = dates + add
                nc = close + [float(fclose[d]) for d in add]
                allv = dict(old_v)
                for d in df["d"]:
                    allv[d] = None if pd.isna(fvol[d]) else int(round(float(fvol[d])))
                nv = [allv.get(d) for d in nd]

            # 半成品 bar **不落库**：按该标的所在市场（A 股 15:05）判，未收盘的尾部 bar 丢掉。
            # 旧行为是「先写进去、靠 `_last_bar_intraday` 在收盘后再修」—— 中间任何策略读库
            # 都拿到未定型的收盘价与半日量能（黄金腿的收盘价就取自本库）。
            k = mc.last_closed_index(code, nd)
            if k + 1 < len(nd):
                nd, nc, nv = nd[:k + 1], nc[:k + 1], nv[:k + 1]

            lk[code] = {"dates": nd, "close": nc}
            ind[code] = _recompute(nd, nc, nv)
            latest[code] = nd[-1]
        except Exception as e:  # noqa
            fails[code] = f"{type(e).__name__}: {e}"
            latest[code] = lk.get(code, {}).get("dates", [""])[-1]
        time.sleep(0.05)

    ind["_meta"] = meta or gi.META
    json.dump(lk, open(LK, "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(ind, open(IND, "w", encoding="utf-8"), ensure_ascii=False)
    return latest, fails


def _last_bar_intraday():
    """最后一根 bar 是否为「当日盘中」写入的未收盘快照。

    **这是给历史库留的自愈路径**（2026-11-24 起 `refresh` 不再写未收盘的 bar，见 `expected_closed`），
    旧版代码曾在盘中把半根写进来，仅比日期看不出来（盘中与收盘后是同一天），但收盘价会不同 ——
    判据是**库文件最后写入时刻 < 最后一根 bar 当日 15:00**（写入发生在盘中）
    **且当前已过该 15:00**。后半条是必须的：刚被判定过的旧 bar 不该让后续进程反复整库刷。
    该判据自带收敛：收盘后修正一次，mtime 就 > 15:00，之后不再触发。
    """
    try:
        mtime = dt.datetime.fromtimestamp(os.path.getmtime(LK))
        last = max(last_dates().values())
    except (FileNotFoundError, ValueError):
        return False
    try:
        close_at = dt.datetime.strptime(last, "%Y-%m-%d").replace(hour=15, minute=0)
    except ValueError:
        return False
    return mtime < close_at < dt.datetime.now()


def ensure_fresh(codes=None, verbose=True):
    """检查是否最新；非最新则刷新。返回 (latest_map, fail_map)。

    已最新时只读文件做日期比较，不联网，开销可忽略。
    **基准是「已收盘」的最新交易日**（`expected_closed`）：`refresh` 只写已收盘的 bar，
    所以 15:05 之前跑不该用「今天」当基准，否则每个进程都判非最新、把整库联网刷一遍。
    **盘中快照兜底**：若最后一根 bar 是「当日盘中」写入的（见 `_last_bar_intraday`），
    强制刷新一次 —— 这是给**旧版代码已写进库的半根**留的自愈路径，新写入不再产生这种情况。
    **进程内幂等**：同一次执行里多次调用只检查/刷新一次（策略入口与底层 load 都会调它）。
    """
    global _CHECKED
    if _CHECKED is not None:
        return _CHECKED
    try:
        latest = last_dates()
    except FileNotFoundError:
        if verbose:
            print("[行情] 未找到本地行情库，跳过刷新检查")
        _CHECKED = ({}, {})
        return _CHECKED
    if not latest:
        _CHECKED = ({}, {})
        return _CHECKED
    exp = expected_closed()
    mx = max(latest.values())
    if mx >= exp:
        if _last_bar_intraday():
            if verbose:
                print(f"[行情] 已最新（{mx}）但该 bar 为盘中写入，收盘价可能未定型 → 强制刷新")
            latest, fails = refresh(codes)
            if verbose:
                mx2 = max(latest.values()) if latest else mx
                print(f"[行情] 刷新完成：最新 {mx2} · 失败 {len(fails)}")
            _CHECKED = (latest, fails)
            return _CHECKED
        if verbose:
            print(f"[行情] 已最新：全池最新 {mx}（基准 {exp}）")
        _CHECKED = (latest, {})
        return _CHECKED
    if verbose:
        print(f"[行情] 非最新（全池最新 {mx} < 基准 {exp}），联网刷新中…")
    latest, fails = refresh(codes)
    if verbose:
        mx2 = max(latest.values()) if latest else mx
        state = "已到最新" if mx2 >= exp else "源暂无更新（可能休市）"
        print(f"[行情] 刷新完成：最新 {mx2} · {state} · 失败 {len(fails)}")
    _CHECKED = (latest, fails)
    return _CHECKED


def main():
    latest, fails = ensure_fresh()
    exp = expected_closed()
    stale = {c: d for c, d in latest.items() if d < exp}
    print(f"基准(最近**已收盘**交易日)={exp} · 共 {len(latest)} 只 · 未达基准 {len(stale)} 只 · 失败 {len(fails)} 只")
    if fails:
        print("刷新失败：", fails)


if __name__ == "__main__":
    main()
