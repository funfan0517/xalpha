#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V11 · 数据生产：把 V9 的逐标的月度温度聚合成「板块温度」

为什么单独一个脚本、还要走子进程
--------------------------------
V9 与 V11 各自都有 `data.py` / `rule.py`，同进程里 `import factors`（V9 的）会让
`sys.modules["data"]` 指向 V9 的数据层，之后 V11 再 `import data` 就拿错模块。
所以这里**只在独立进程里**加 V9 的路径、import 一次、写完 CSV 就退出；
`data.py` 用 `subprocess` 调它，主进程永远不 import V9 的模块。

产物 `data/_board_temp.csv`（月频，跨版本可复用）：
    date, board6_med, board6_mean, board4_med, 以及 6 只标的的逐只温度

* `board6_*` = V9 全池 6 只（都有 etf.run 等权 PE/PB）—— 主口径
* `board4_*` = 只取 V4 池内有温度的那 4 只（008163 无等权估值被排除）—— 敏感性

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
V4 = ("561060", "008163", "090010", "008115", "016441")


def main(refresh: bool = False) -> int:
    panels = F.build(refresh=refresh)
    temp = F.monthly_temp(panels)          # 月末 × 6 只
    temp.index.name = "date"

    # 每行对应的最后一个交易日（当月未走完时 = 最新交易日，供每日读数标注 asof）
    asof = pd.Series({ts: max(p["monthly"].loc[ts, "asof"] for p in panels.values()
                              if ts in p["monthly"].index) for ts in temp.index})

    board = pd.DataFrame(index=temp.index)
    board["asof"] = asof.reindex(temp.index)
    board["board6_med"] = temp.median(axis=1)
    board["board6_mean"] = temp.mean(axis=1)
    in_v4 = [c for c in temp.columns if c in V4]
    board["board4_med"] = temp[in_v4].median(axis=1)
    board = board.join(temp, how="left").dropna(subset=["board6_med"])

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    board.round(2).to_csv(OUT, index_label="date")

    print(f"[v11:board] {len(board)} 月 · {board.index[0]:%Y-%m}~{board.index[-1]:%Y-%m} · "
          f"温度池 {len(temp.columns)} 只（V4 池内 {len(in_v4)} 只）")
    print(f"[v11:board] 最新 {board.index[-1]:%Y-%m} 板块中位 {board['board6_med'].iloc[-1]:.1f} "
          f"| 分位 {(board['board6_med'] <= board['board6_med'].iloc[-1]).mean():.0%}")
    print(f"[v11:board] -> {os.path.relpath(OUT, os.path.dirname(os.path.dirname(_HERE)))}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(refresh="--refresh" in sys.argv))
