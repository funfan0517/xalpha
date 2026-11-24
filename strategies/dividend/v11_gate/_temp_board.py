#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V11 · 数据生产：把 V9 的逐标的月度温度聚合成「板块温度」

为什么单独一个脚本、还要走子进程
--------------------------------
V9 与 V11 各自都有 `data.py` / `rule.py`，同进程里 `import factors`（V9 的）会让
`sys.modules["data"]` 指向 V9 的数据层，之后 V11 再 `import data` 就拿错模块。
所以这里**只在独立进程里**加 V9 的路径、import 一次、写完 CSV 就退出；
`data.py` 用 `subprocess` 调它，主进程永远不 import V9 的模块。

产物（月频，跨版本可复用）：

1. `data/_board_temp.csv`：`date, board6_med, board6_mean, board4_med` + 6 只标的的**估值温度**
   （V9 五因子）。`board6_*` = V9 全池 6 只（都有 etf.run 等权 PE/PB）—— 主口径；
   `board4_*` = 只取 **V11 当前交易池**内有温度的标的（列名沿用历史，语义是「池内子集中位数」）。
2. `data/_board_temp_px.csv`（2026-11-24 增）：**10 只价格温度**（`dev`/`pos` 各 50% 的自身历史分位，
   口径同 V9 `TEMP_KIND_PX`）。主表 6 只也按价格口径重算，故 10 只刻度同源、可直接横向排序。
3. `data/_board_temp_mix.csv`（2026-11-24 增）：**10 只混合温度 = 估值温度优先、缺则价格温度兜底**。
   这就是「让 10 只都有温度」的口径；但这 10 个里有 4 只（007760 / 018388 / 008163 / 005125）
   永远只有价格口径，混排的口径异质性要在报告里单独评估，不能当成同一件事。

口径继承 V9：五因子 35/20/16/15/14、分位近 730 日历日、最少 300 观测、股息率−10Y。
本脚本不改任何 V9 参数，只做聚合。

用法::

    python strategies/dividend/v11_gate/_temp_board.py            # 用缓存
    python strategies/dividend/v11_gate/_temp_board.py --refresh  # 强制重抓指数/估值
"""
from __future__ import annotations

import os
import sys

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_V9 = os.path.join(os.path.dirname(_HERE), "v9_temp5")
if _V9 not in sys.path:
    sys.path.insert(0, _V9)

import factors as F  # noqa: E402  pylint: disable=wrong-import-position

OUT = os.path.join(_HERE, "data", "_board_temp.csv")
OUT_PX = os.path.join(_HERE, "data", "_board_temp_px.csv")      # 10 只价格温度（同一口径）
OUT_MIX = os.path.join(_HERE, "data", "_board_temp_mix.csv")    # 6 只估值温度 + 4 只价格温度


def pool_codes() -> tuple:
    """V11 当前交易池 —— 从 `rule.CODES` 取，保证「交易池内温度」这个口径永远跟策略同步。

    2026-11-23 起 V11 的交易池就是「既有温度又有 RSI 的 6 只」，所以 `board4_med`
    （池内子集中位数）与 `board6_med` 相同；列名保留是为了不打断 CSV 的既有读取方。
    """
    try:
        import rule as R  # noqa: PLC0415 - 只读常量，不 import V9 的任何模块
        return tuple(R.CODES)
    except Exception as e:  # noqa: BLE001 - 独立运行时退回旧 5 只口径
        print(f"[warn] 读 rule.CODES 失败，退回旧 5 只池: {type(e).__name__}: {e}",
              file=sys.stderr)
        return ("561060", "008163", "090010", "008115", "016441")


def price_temps(panels: dict, side: dict) -> pd.DataFrame:
    """10 只价格温度（`dev`/`pos` 各 50% 的自身历史分位，口径同 V9 的 `TEMP_KIND_PX`）。

    * 主表 6 只：用它们自己的 `daily` 面板重算一遍价格口径（**不用**估值温度）——
      这样 10 只温度刻度同源，可横向排序；
    * 副表 4 只：V9 `build_side` 已按价格温度算好（007760 / 018388 / 008163 / 005125）。
    """
    out = {}
    for code, p in panels.items():
        m = F.temp_at(p["daily"], F.month_ends(p["daily"].index), has_valuation=False)
        if not m.empty:
            out[code] = m["temp"]
    for code, p in side.items():
        s = p["monthly"]["temp"]
        if len(s):
            out[code] = s
    return pd.DataFrame(out).sort_index()


def main(refresh: bool = False) -> int:
    panels = F.build(refresh=refresh)
    temp = F.monthly_temp(panels)          # 月末 × 6 只
    temp.index.name = "date"
    side = F.build_side(refresh=refresh)    # V9 副表 4 只（价格温度）

    # 每行对应的最后一个交易日（当月未走完时 = 最新交易日，供每日读数标注 asof）
    asof = pd.Series({ts: max(p["monthly"].loc[ts, "asof"] for p in panels.values()
                              if ts in p["monthly"].index) for ts in temp.index})

    board = pd.DataFrame(index=temp.index)
    board["asof"] = asof.reindex(temp.index)
    board["board6_med"] = temp.median(axis=1)
    board["board6_mean"] = temp.mean(axis=1)
    in_v4 = [c for c in temp.columns if c in pool_codes()]
    board["board4_med"] = temp[in_v4].median(axis=1)
    board = board.join(temp, how="left").dropna(subset=["board6_med"])

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    board.round(2).to_csv(OUT, index_label="date")

    # ---- 价格温度：10 只同源刻度（用于「10 只都有温度」的干净对照）----
    px = price_temps(panels, side)
    px.index.name = "date"
    px_out = px.copy()
    px_out.insert(0, "asof", board["asof"].reindex(px.index))
    px_out.round(2).to_csv(OUT_PX, index_label="date")

    # ---- 混合温度：估值温度优先、缺则价格温度兜底（用户要的「10 只都有温度」口径）----
    # 注意不是「6 只固定用估值、4 只固定用价格」：那样会白白丢掉 008115(估值 2021-04 起)/
    # 016441(2022-06 起) 早年的价格温度，覆盖月份反而更少。
    mix = temp.reindex(px.index).combine_first(px)
    mix = mix.round(2)
    mix.index.name = "date"
    mix_out = mix.copy()
    mix_out.insert(0, "asof", board["asof"].reindex(mix.index))
    mix_out.to_csv(OUT_MIX, index_label="date")

    print(f"[v11:board] {len(board)} 月 · {board.index[0]:%Y-%m}~{board.index[-1]:%Y-%m} · "
          f"温度池 {len(temp.columns)} 只（V4 池内 {len(in_v4)} 只）")
    print(f"[v11:board] 最新 {board.index[-1]:%Y-%m} 板块中位 {board['board6_med'].iloc[-1]:.1f} "
          f"| 分位 {(board['board6_med'] <= board['board6_med'].iloc[-1]).mean():.0%}")
    cover_px = px.notna().sum()
    cover_mix = mix.notna().sum()
    print(f"[v11:board] 价格温度 10 只 → {os.path.relpath(OUT_PX, os.path.dirname(_HERE))} "
          f"（覆盖 " + " ".join(f"{c}:{int(v)}" for c, v in cover_px.items()) + "）")
    print(f"[v11:board] 混合温度（估值优先 + 价格兜底）→ {os.path.relpath(OUT_MIX, os.path.dirname(_HERE))} "
          f"（覆盖 {int((cover_mix > 0).sum())}/{len(cover_mix)} 只）")
    print(f"[v11:board] -> {os.path.relpath(OUT, os.path.dirname(os.path.dirname(_HERE)))}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(refresh="--refresh" in sys.argv))
