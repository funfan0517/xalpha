# -*- coding: utf-8 -*-
"""A 全球相对动量轮动 · 规则定义(六类资产, 2026-09-11 收窄定稿)
标的池: 六类资产「一类一标的」—— 场外主仓执行, 场内代理只作动量信号(xueqiu 十年库)。
  中长债 003377/511260 · 中证红利 012644/515080 · 纳指100 270042/513100
  中证A500 023299/563360 · 科创50 011609/588000 · 黄金 000216/518880
  与 core_rotation 同一套资产(场外代码为准), 映射仍由唯一池 data/_universe.md 派生。
规则:
 1) MTM: 每 10 交易日(≈双周)再平衡, momentum = close_t/close_{t-120} - 1 降序
 2) MA20 过滤: 从动量排名由高到低取前 HOLD_N=2 名中 close ≥ MA20 的标的(等权持有)
 3) 合格不足 2 只按实际只数持有; 一只都不符合 → 空仓现金
 4) 无前视: 当日收益归旧仓, 再平衡日收盘后换仓; 卖出费 ≤7 交易日 1.5% / 7 日外 0%
"""
import json
import os
import sys

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pipeline import universe

# ---- 六类资产唯一映射(执行=场外 off / 信号=场内代理), 从唯一池 _universe.md 派生 ----
# 场外代码与 core_rotation 完全一致, 保证两个策略轮动的资产口径同一套。
_ASSET_DEF = [  # key, 场外代码
    ("bond", "003377"),      # 中长债
    ("dividend", "012644"),  # 中证红利
    ("ndx", "270042"),       # 纳指100
    ("a500", "023299"),      # 中证A500
    ("kc", "011609"),        # 科创50
    ("gold", "000216"),      # 黄金
]
_ASSET_NAME = {"bond": "中长债", "dividend": "中证红利", "ndx": "纳指100",
               "a500": "中证A500", "kc": "科创50", "gold": "黄金"}


def pool():
    """唯一池 _universe.md -> 六类资产行 [{key,name,off_code,off_name,proxy,proxy_name}]。

    以场外代码为准(与 core_rotation 同一套资产), 同行「场内对应列」作动量信号代理;
    标的的增删改只维护 data/_universe.md, 不在本文件硬编码代码。
    """
    rows = {r["off_code"]: r for r in universe.parse()}
    out = []
    for key, off in _ASSET_DEF:
        r = rows.get(off)
        if r is None:
            raise RuntimeError(f"场外代码 {off} 未在 {universe.UNIVERSE} 中找到, 请先维护唯一池")
        if not r["inner_code"]:
            raise RuntimeError(f"标的 {off}({r['theme']}) 无场内对应列, 无法作信号代理")
        out.append(dict(key=key, name=_ASSET_NAME[key], off_code=off,
                        off_name=r["off_name"] or off,
                        proxy=r["inner_code"], proxy_name=r["inner_name"] or r["inner_code"]))
    return out


ASSETS = pool()
POOL = [r["proxy"] for r in ASSETS]        # 场内信号池(6 只, 由唯一池派生)
SCAN34 = POOL                              # 兼容旧引用名
LABEL6 = {r["proxy"]: f"{r['proxy']} {r['name']}" for r in ASSETS}
LONG_CACHE = os.path.join(_ROOT, "data", "_long_klines.json")   # 十年库(2015+)，数据类留在 data/
# REBAL=10(≈双周): 2026-09-11 由 21(≈月) 改为 10 交易日。
# 依据 research/_rebal_report.md: 惩罚性赎回红线是「持有 <7 自然日赎回收 1.5%」，
# 周期 ≥10 交易日时持有期恒 >7 日，惩罚费永不触发(实测 0 次)，成本仍 ≈ 0；
# 红线之外频率对收益无可分辨影响(10/15/21/42 日相位中位年化同为 6.5%~7.8%，
# 全扫描是锯齿)，唯一可靠的是 5 日(掉进红线，5 个相位全为负)。
# 即 REBAL 能伤害策略(<7 日)、不能改善策略(≥10 日) —— 改 10 只是更及时响应，不承诺更高收益。
HOLD_N = 2          # 每期持「动量排名前 HOLD_N 且站上 MA20」的标的，等权
LOOKBACK, MA, REBAL, MIN_HIST = 120, 20, 10, 140
FEE_SHORT_DAYS, FEE_SHORT, FEE_LONG = 7, 0.015, 0.0


def load_wide(path=None, codes=None):
    """xueqiu 十年库 -> ffill 宽表(index=日期, columns=code)

    codes 缺省为六类资产场内代理(POOL)；传入自定义 code 列表可做指定标的池的对照实验
    （仅用于 research 脚本，不改变官方池的派生口径）。
    """
    raw = json.load(open(path or LONG_CACHE, encoding="utf-8"))
    series = {}
    for code in (codes or SCAN34):
        d = raw.get(code)
        if not d or len(d["close"]) < MIN_HIST:
            continue
        s = pd.Series(d["close"], index=pd.to_datetime(d["dates"]), dtype=float)
        s = s[~s.index.duplicated(keep="last")].sort_index()
        series[code] = s
    return pd.DataFrame(series).sort_index().ffill()
