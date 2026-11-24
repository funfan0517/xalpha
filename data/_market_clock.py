# -*- coding: utf-8 -*-
"""行情「这根 bar 收盘了吗」的唯一判据（跨策略共享 · 只依赖标准库）。

## 为什么单独一个模块

仓库里曾有 4 份 `_trim_partial`（`strategies/vol_legs/_idxcache.py`、`v12_octo_trend/data.py`、
`tech_indicators/v17_octo_trend/data.py`、`v18_volume_notes/ohlcv.py`），2026-11-24 起前两份与
`data/_refresh_data.py`、`vol_legs/data.py::refresh_us` 已改为调用本模块（后两份只服务 A 股 ETF 库，
判据恰好等价于本模块的 `CN` 分支，为免在那两个模块里塞路径加载先原地保留），它们的旧判据都是

    现在早于 15:05  --> 把「日期 == 北京今天」的末根丢掉
    现在晚于 15:05  --> 一根都不过滤

这对 **A 股**成立，对另外两个市场不成立：

- **港股** 16:00 才收盘：15:05–16:00 之间跑刷新，`HKHSTECH` 还是半根，旧判据直接放行，
  半成品被写进 `data/_global_index_klines.json`（而 `run_daily_vol_legs.ps1` 写的正是
  「15:05 之后跑」——这个窗口是流程默认踩进去的）；
- **美股** 的 bar 日期是美东交易日、收盘落在**北京次日 05:00**：22:30–24:00 因为「已过 15:05」
  放行，00:00–05:00 因为「bar 日期是昨天 ≠ 北京今天」也放行，夜里刷一次就写半根。

根因是判据放在了「**库**」和「**北京时间的今天**」两个维度上，而**收盘时刻属于市场**。
本模块只回答一件事：给定 bar 自己的日期，它在北京时间几点收盘，现在能不能写。

## 口径

| 市场 | 代码形态 | 收盘（北京，含 5 分钟缓冲） |
|---|---|---|
| A 股 / 场内 ETF（含 QDII `513100`、黄金 `518880`） | `SH…` / `SZ…` / 6 位数字 | **当日 15:05** |
| 港股 | `HK…` | **当日 16:05** |
| 美股 | `.NDX` / `.INX` 等以 `.` 开头 | **次日 05:05** |

交易日历**不在这里维护**：上游有该日 bar 就说明是交易日，本模块只回答「那一根收盘了没」。

保守偏置（故意如此，方向永远是「晚写」而不是「写半根」）：

- 夏令时美股 16:00 ET = 北京 04:00，这里统一按 05:05 判 → 多等一小时；
- 半日市（美股感恩节次日 13:00 收盘、港股平安夜 12:00 收盘）→ 多等几小时。

## 用法

```python
import _market_clock as mc                      # data/ 在 sys.path 上时
mc.bar_closed("HKHSTECH", "2026-11-24")         # -> False（16:05 之前）
mc.trim_incomplete(lib)                          # 就地丢掉每个节点末尾未收盘的 bar
mc.last_closed_index(".NDX", node["dates"])      # -> 最后一个已收盘 bar 的下标
```
"""

import datetime as dt

try:  # 与仓库其它脚本一致：中文输出在 Windows 控制台不炸
    import sys
    sys.stdout.reconfigure(encoding="utf-8")   # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

# 市场 -> (收盘时, 收盘分)，北京时间，已含 5 分钟缓冲
MARKETS = {"CN": (15, 5), "HK": (16, 5), "US": (5, 5)}

# 节点里按日期对齐的列（`_idxcache` / `_refresh_data` / `_ohlcv.json` 的 schema 并集）
_FIELDS = ("dates", "open", "high", "low", "close", "volume")


def market_of(x):
    """代码或市场名 -> `"CN"` / `"HK"` / `"US"`。

    认不出的**一律按 A 股**：本仓库绝大多数代码是 A 股场内标的，判错的方向是「多等几小时」，
    而不是「把半根写进库」，所以默认值是安全的。
    """
    s = str(x or "").strip()
    if s in MARKETS:
        return s
    if s.startswith("."):                       # .NDX / .INX 等美股指数
        return "US"
    if s.upper().startswith("HK"):              # HKHSTECH 等港股指数
        return "HK"
    return "CN"


def _as_date(day):
    """`"2026-11-24"` / `Timestamp` / `date` / `datetime` -> `date`。"""
    if isinstance(day, dt.datetime):
        return day.date()
    if isinstance(day, dt.date):
        return day
    return dt.datetime.strptime(str(day)[:10], "%Y-%m-%d").date()


def bar_close_at(x, day):
    """该 bar（日期 = `day`，市场由 `x` 决定）在北京时间几点收盘 -> `datetime`。"""
    mkt = market_of(x)
    hh, mm = MARKETS[mkt]
    d = _as_date(day)
    if mkt == "US":                             # 美股 bar 的日期是美东交易日 -> 收盘落在北京次日
        d += dt.timedelta(days=1)
    return dt.datetime.combine(d, dt.time(hh, mm))


def bar_closed(x, day, now=None):
    """这根 bar 到 `now`（默认此刻）为止是否已经收盘。"""
    return (now or dt.datetime.now()) >= bar_close_at(x, day)


def last_closed_index(x, dates, now=None):
    """`dates`（升序）里最后一个**已收盘** bar 的下标；一根都没有 -> `-1`。

    从尾部往前找而不是只看末根：旧版代码可能已经写进去不止一根半成品。
    """
    now = now or dt.datetime.now()
    for i in range(len(dates) - 1, -1, -1):
        if bar_closed(x, dates[i], now):
            return i
    return -1


def trim_incomplete(lib, now=None):
    """就地丢掉每个节点末尾「尚未收盘」的 bar，返回入参（方便链式调用）。

    `lib` 形如 `{code: {"dates": [...], "close": [...], ...}}`。**逐个节点按它自己的代码判市场**
    （不是按库），所以一个库里混装 A 股 / 港股 / 美股（如 `_global_index_klines.json`）也能逐条判对。
    """
    now = now or dt.datetime.now()
    for code, node in lib.items():
        dates = node.get("dates") or []
        if not dates:
            continue
        k = last_closed_index(code, dates, now)
        if k + 1 >= len(dates):
            continue
        for col in _FIELDS:
            if node.get(col):
                node[col] = node[col][:k + 1]
    return lib


def describe(now=None):
    """逐市场打印「现在能不能写今天的 bar」，自检/日志用。"""
    now = now or dt.datetime.now()
    today = now.date().isoformat()
    out = []
    for mkt in MARKETS:
        ok = bar_closed(mkt, today, now)
        close = bar_close_at(mkt, today)
        out.append(f"  {mkt}  今日({today}) bar 收盘于 {close:%Y-%m-%d %H:%M}"
                   f"  ->  {'已收盘，可写入' if ok else '未收盘，不写入'}")
    return "\n".join(out)


if __name__ == "__main__":
    print(f"现在 {dt.datetime.now():%Y-%m-%d %H:%M}")
    print(describe())
    print("\n逐市场收盘时刻（现在 -> 该 bar 可否写入）：")
    for code in ("SH000510", "HKHSTECH", ".NDX", ".INX", "518880", "513100"):
        d = dt.date.today()
        print(f"  {code:9s} 市场 {market_of(code):2s}  "
              f"今日 bar 收盘 {bar_close_at(code, d):%Y-%m-%d %H:%M}  "
              f"已收盘 {bar_closed(code, d)}")
