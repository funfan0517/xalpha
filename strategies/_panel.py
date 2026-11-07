# -*- coding: utf-8 -*-
"""技术指标家族 · 共享行情面板（口径唯一实现）+ 停牌污染自检。

**为什么单独一个模块**：v9_right_side / v16_trin / v10_lights 三处原先各写一份
`load_panels`，前两份逐行同构、第三份多两个字段 —— 「改一处漏两处」的典型温床。
2026-11-06 把 v9 / v16 两处真正同构的实现收口到这里；v10_lights 因为要五个字段
（open/high/low/close/volume）且自带可选 ffill 对照开关，暂不并入。

**口径（fix#2，唯一正确的那个）**：
  每只标的**只保留自身交易日** —— 不 ffill、不补齐、不填 0。
  历史上的「全池并集日期 + ffill」会把停牌日变成 volume=0 的假交易日，
  量比类因子把它读成「缩量」，全池合计混入 64 天假数据。

**数据来源**（数据类，留在 `data/`，跨策略共享）：
  - `data/_long_klines.json`    收盘价（2015-01 起，前复权）
  - `data/_indicators_hist.json` 成交量

对外：
  - `load_pair(codes, ...)`   -> {"close", "volume"} 两个宽表
  - `complete_end(idx, ...)`  -> 最后一个「已收盘」交易日
  - `check_hygiene(panels)`   -> 口径自检，违规项列表
命令行（启动脚本用，只读缓存不联网）：
  `python strategies/_panel.py`
"""
import json
import os
import sys
from datetime import datetime

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))                    # strategies/
_ROOT = os.path.dirname(_HERE)                                        # 仓库根
_DATA = os.path.join(_ROOT, "data")
_TI = os.path.join(_HERE, "tech_indicators")                          # 家族目录（_pool.py 所在）
for _p in (_ROOT, _DATA, _TI, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

LK = os.path.join(_DATA, "_long_klines.json")
IND = os.path.join(_DATA, "_indicators_hist.json")


def ensure_fresh(verbose=True):
    """触发公用行情库刷新检查；失败只告警、沿用本地数据（只读策略不该因网络中断而停）。"""
    try:
        import _refresh_data
        return _refresh_data.ensure_fresh(verbose=verbose)
    except Exception as e:  # noqa: BLE001 - 刷新失败不应中断只读策略
        print(f"[行情] 刷新检查失败，沿用本地数据：{type(e).__name__}: {e}")
        return {}, {}


def _series_with_valid_index(values, idx, name=""):
    """-> Series(index=DatetimeIndex)；索引重复时保留最后一条。"""
    s = pd.Series(values, index=idx, dtype=float)
    return s[~s.index.duplicated(keep="last")].sort_index()


def load_pair(codes, refresh=True, lk=LK, ind=IND, verbose=True):
    """-> {"close": DataFrame(index=日期, columns=code), "volume": 同形}。

    每只标的保留**自身交易日**（不 ffill）：停牌日留 NaN，下游按行丢弃，
    避免补齐后的 0 成交量污染量比类因子（fix#2）。

    `volume` 与 `dates` **长度不一致**时直接抛错，不再静默丢列 ——
    静默丢列的后果是「池子悄悄变小、回测数字变了没人知道」，
    或者拖到下游 `volume[code]` 才 KeyError，报错位置离病根很远。
    """
    if refresh:
        ensure_fresh(verbose=verbose)
    with open(lk, encoding="utf-8") as f:
        lk_d = json.load(f)
    with open(ind, encoding="utf-8") as f:
        ind_d = json.load(f)
    ind_d.pop("_meta", None)

    close, volume = {}, {}
    missing, bad_len = [], []
    for code in codes:
        d = lk_d.get(code)
        if not d:
            missing.append(code)
            continue
        idx = pd.to_datetime(d["dates"])
        close[code] = _series_with_valid_index(d["close"], idx)

        v = (ind_d.get(code) or {}).get("volume")
        if not v:
            bad_len.append((code, len(d["dates"]), 0))
            continue
        if len(v) != len(d["dates"]):
            bad_len.append((code, len(d["dates"]), len(v)))
            continue
        vs = pd.Series([None if x is None else float(x) for x in v], index=idx, dtype=float)
        volume[code] = vs[~vs.index.duplicated(keep="last")].sort_index()

    if bad_len:
        msg = "; ".join(f"{c}: dates={n} volume={m}" for c, n, m in bad_len)
        raise ValueError(f"volume 与 dates 长度不一致（{len(bad_len)} 只）: {msg}")
    if missing:
        print(f"[行情] 十年库缺 {len(missing)} 只: {missing}")

    return {"close": pd.DataFrame(close).sort_index(),
            "volume": pd.DataFrame(volume).sort_index()}


def complete_end(idx, close_hour=15):
    """最后一个「已收盘」交易日（避免盘中半日 bar 污染指标与扫描结论）。

    若最后一根 bar 就是今天且当前未到 15:00，它是盘中未完成 bar（成交量与涨幅都失真），
    回测/扫描应回退一天；实盘「今日盘中」结论另行标注。
    """
    if len(idx) == 0:
        return None
    last = idx[-1]
    now = datetime.now()
    if last.date() == now.date() and now.hour < close_hour:
        return idx[-2] if len(idx) > 1 else last
    return last


def check_hygiene(panels):
    """口径自检 -> 违规项列表 [(级别, 说明)]；空列表 = 通过。

    这里检查的都是「曾经真实发生过」的毛病，不是假想防御：

      fix#2 停牌污染 : 某个标的同时「有收盘价」且「成交量为 0」——
                       标的自有交易日里不该出现 0 成交量，出现即说明面板被 ffill 补齐过。
      有价无量       : 收盘有值但成交量 NaN —— volume 与 dates 错位（长度对上了、日期错位）。
      整列全空/全 0  : 该标的根本没进面板 / 数据源把量写成了 0。
      索引异常       : 日期非单调递增或存在重复（周线锚点、rolling 全都会跟着错）。
    """
    bad = []
    close, volume = panels["close"], panels["volume"]

    for name, df in (("close", close), ("volume", volume)):
        idx = df.index
        if not idx.is_monotonic_increasing:
            bad.append(("ERROR", f"{name} 面板日期非单调递增"))
        if idx.has_duplicates:
            bad.append(("ERROR", f"{name} 面板日期存在重复"))

    for code in close.columns:
        c = close[code]
        if code not in volume.columns:
            bad.append(("ERROR", f"{code} 缺 volume 列"))
            continue
        v = volume[code]

        n_susp = int(((v == 0) & c.notna()).sum())
        if n_susp:
            bad.append(("ERROR", f"{code} 有 {n_susp} 天「有价无量」(volume=0) —— fix#2 停牌污染"))

        n_gap = int((c.notna() & v.isna()).sum())
        if n_gap:
            bad.append(("ERROR", f"{code} 有 {n_gap} 天「有价无量」(volume=NaN) —— volume 与 dates 错位"))

        if int(v.notna().sum()) == 0:
            bad.append(("ERROR", f"{code} volume 整列为空"))
        elif (v.fillna(0) == 0).all():
            bad.append(("ERROR", f"{code} volume 整列为 0 —— 数据源异常"))

    for code in volume.columns:
        if code not in close.columns:
            bad.append(("ERROR", f"{code} 有 volume 无 close"))

    return bad


def _main():
    """命令行入口：只读缓存跑自检，违规即非 0 退出（给启动脚本当闸门）。"""
    import _pool

    codes = [r["code"] for r in _pool.rows()]
    panels = load_pair(codes, refresh=False)
    idx = panels["close"].index
    print(f"标的池 {len(codes)} 只 · 面板 {panels['close'].shape[0]} 个交易日 "
          f"{idx[0].date()} ~ {idx[-1].date()}")

    bad = check_hygiene(panels)
    if not bad:
        print("[OK] 面板口径自检通过: 无停牌污染、无 volume/dates 错位、索引单调")
        return 0
    print(f"[X] 面板口径自检发现 {len(bad)} 项问题:")
    for lv, msg in bad:
        print(f"   [{lv}] {msg}")
    return 1


if __name__ == "__main__":
    sys.exit(_main())
