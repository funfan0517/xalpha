# -*- coding: utf-8 -*-
"""技术指标家族的**唯一标的池定义**（v1~v15 共用）。

池 = `data/_universe.md` 的**前四类**中标的有场内 ETF 对应的那些：

| 类 | 名称 | 场内只数 |
|---|---|---|
| 一 | 宽基指数 / 另类 | 12 |
| 二 | 全球 / 跨境 QDII | 7 |
| 三 | A 股行业主题 | 31 |
| 四 | 策略因子 / 商品 | 4 |
| 合计 | | **54** |

**不纳入**的第五类「主动/量化」无场内代理；第六类「债券」（`511260 十年国债ETF`）风险属性
与权益择时不同源，混进来会让「择时 vs 买入持有」的对照失真。

实际可回测的标的还要同时满足：该代码在 `data/_indicators_hist.json` 与
`data/_long_klines.json` 里有数据（缺数据的会被 `load()` 跳过）。

需要改池子时**只改 `data/_universe.md`**（增删标的的行），这里不放任何硬编码代码。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 仓库根
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pipeline import universe  # noqa: E402

CATS = (1, 2, 3, 4)          # 一、二、三、四


def rows():
    """场内映射行（只含前四类），字段见 pipeline.universe.inner_rows。"""
    return universe.inner_rows_cats(CATS)


def allowed_codes():
    """允许进入回测/扫描的场内代码集合。"""
    return {r["code"] for r in rows()}
