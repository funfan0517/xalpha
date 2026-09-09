# -*- coding: utf-8 -*-
"""核心轮动(六类资产动态配置) · 规则化定义 —— 唯一权威源

方法论文档: doc/核心轮动投资策略手册.md
执行口径: 六类资产「一类一标的」, 全部取自 data/_universe.md 场外行:
  中长债 003377 / 中证红利 012644 / 纳指100 270042 / 中证A500 023299 / 科创50 011609 / 黄金 000216
每个标的配一个「场内代理」(同一行 _universe.md 的场内对应列)只用于日频行情/动量/拥挤度观察,
实际申赎一律走场外代码。

数据可达性(2026-09-09 实测):
  [auto] 场内代理日线  xa.get_daily(SH/SZ+proxy)             —— 动量/超买/趋势
  [auto] 场外净值历史   xa.fundinfo(off)                     —— 净值确认/月度收益(QDII 滞后约1个工作日)
  [auto] 10Y 国债收益率 xalpha.universal.get_bond_rates('N') —— 中长债信号 + 股债收益比分母
  [auto] 中证红利股息率/PE分位、科创50 PE分位与 PE 比         —— 蛋卷基金估值 djapi/index_eva/dj
  [auto] A500 发布以来 PE 累计分位(官方日频 peg)             —— 中证官网 index-perf, 本地日频库 _a500_pe.py
  [手动] 纳指 Forward PE 分位 / DXY / 实际利率              —— 可选补录 data/_core_valuation.json(东财仅 TTM)

情景判定(手册第二章)与总配置矩阵(手册第一章)在此唯一实现, 供 _signal.py 与后续回测共用。
"""
import os
import sys

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ---- 六类资产唯一映射(off=执行 / proxy=信号), 从唯一池 _universe.md 派生 ----
_ASSET_DEF = [  # key, 场外代码, 中文短名(用于从 _universe.md 复核, 不参与策略判定)
    ("bond", "003377"),
    ("dividend", "012644"),
    ("ndx", "270042"),
    ("a500", "023299"),
    ("kc", "011609"),
    ("gold", "000216"),
]
_ASSET_NAME = {
    "bond": "中长债", "dividend": "中证红利", "ndx": "纳指100",
    "a500": "中证A500", "kc": "科创50", "gold": "黄金",
}
# 场内代理前缀(proxy 以 5/6/9 开头=SH, 否则 SZ), 与 pipeline/universe.py 保持一致
def _sh(code):
    return ("SH" if code.startswith(("5", "6", "9")) else "SZ") + code


def pool():
    """从唯一池 _universe.md 中按场外代码解析六类标的行, 保证单一维护入口。

    返回: [{key, name, off_code, off_name, proxy, proxy_name, theme, num}]
    """
    from pipeline import universe
    rows = universe.parse()
    by_off = {r["off_code"]: r for r in rows}
    out = []
    for key, off in _ASSET_DEF:
        r = by_off.get(off)
        if r is None:
            raise RuntimeError(f"场外代码 {off} 未在 {universe.UNIVERSE} 中找到, 请先维护唯一池")
        if not r["inner_code"]:
            raise RuntimeError(f"标的 {off}({r['theme']}) 无场内对应列, 无法作信号代理")
        out.append(dict(
            key=key, name=_ASSET_NAME[key], off_code=off,
            off_name=r["off_name"] or off, proxy=r["inner_code"],
            proxy_name=r["inner_name"] or r["inner_code"],
            theme=r["theme"], num=r["num"],
        ))
    return out


# ---- 手册第二章 情景阈值 ----
RATIO_DEF_LOW, RATIO_EQ_LO, RATIO_EQ_HI = 1.5, 1.5, 2.5   # 股债收益比(红利股息率%/10Y%)
PE_DEF_HI = 0.30       # 红利 PE 分位 <30% 视为低估
PE_EQ_LO, PE_EQ_HI = 0.30, 0.70
Y10_LOW = 1.5          # 10Y<1.5% 极度脆弱 -> 卖出至 5% 以下

# ---- 手册第一章 情景 x 六类权重矩阵(区间 lo-hi)与建议中枢(样板, 合计=100) ----
BANDS = {
    "bond": dict(防御=(15, 25), 均衡=(5, 15), 成长=(0, 10), 避险=(25, 35)),
    "dividend": dict(防御=(35, 45), 均衡=(20, 30), 成长=(10, 20), 避险=(15, 20)),
    "ndx": dict(防御=(0, 5), 均衡=(10, 20), 成长=(15, 25), 避险=(0, 10)),
    "a500": dict(防御=(15, 20), 均衡=(22, 28), 成长=(25, 35), 避险=(15, 20)),
    "kc": dict(防御=(0, 5), 均衡=(5, 15), 成长=(20, 30), 避险=(0, 5)),
    "gold": dict(防御=(20, 30), 均衡=(10, 20), 成长=(5, 15), 避险=(20, 30)),
}
# 每类情景的「建议中枢」(人工定锚样板, 合计=100); 偏离 ±5pp 内不调仓
TARGET = {
    "bond": dict(防御=20, 均衡=10, 成长=5, 避险=30),
    "dividend": dict(防御=40, 均衡=25, 成长=15, 避险=17.5),
    "ndx": dict(防御=0, 均衡=15, 成长=20, 避险=5),
    "a500": dict(防御=15, 均衡=25, 成长=30, 避险=17.5),
    "kc": dict(防御=0, 均衡=5, 成长=20, 避险=2.5),
    "gold": dict(防御=25, 均衡=20, 成长=10, 避险=27.5),
}
SCENARIOS = ("防御/低估", "均衡震荡", "成长/牛市", "避险/不确定")
SCENE_KEY = {"防御/低估": "防御", "均衡震荡": "均衡",
             "成长/牛市": "成长", "避险/不确定": "避险"}


def scene_key(sc):
    """情景全名 -> BANDS/TARGET 内部键(防御/均衡/成长/避险)。"""
    return SCENE_KEY.get(sc, sc)

# ---- 手册第三章 中长债利率分级(10Y%) ----
BOND_LEVEL = [  # (lo, hi, 信号, 操作)
    (3.0, None, "票息性价比高", "买入(多配至上限)"),
    (2.5, 3.0, "偏多", "偏多配"),
    (2.0, 2.5, "中性", "持有(情景中枢)"),
    (1.5, 2.0, "票息薄", "压至区间下限, 不操作"),
    (None, 1.5, "极度脆弱", "卖出至5%以下"),
]

# 单类占比硬上限(手册第十章)
CAP_SINGLE = 40.0
CAP_KC = 35.0          # 科创仓 <=35%(手册第七章, 与 4.x 重叠以更严者为准)

# 黄金超买风控(手册第八章 8.3): 单月涨幅 > 10% 暂停加仓
GOLD_MONTHLY_CAP = 10.0
# 调仓阈值(手册第九章月度再平衡): 偏差 > ±5pp 才调
REBAL_TOL = 5.0

# ---- 估值锚文件(data/_core_valuation.json)与当前持仓(data/_core_holdings.json) ----
VALUATION_FILE = os.path.join(_ROOT, "data", "_core_valuation.json")
HOLDINGS_FILE = os.path.join(_ROOT, "data", "_core_holdings.json")


def load_json(path, default):
    if os.path.exists(path):
        try:
            import json
            return json.load(open(path, encoding="utf-8"))
        except Exception:
            pass
    return default


def make_valuation_template():
    return {
        "as_of": "",                      # 录入日期 %Y-%m-%d
        "div_yield_hs_pct": None,         # 中证红利股息率 % (TTM)
        "hs_pe_pct": None,                # 中证红利 10 年 PE 分位 0-1
        "a500_pe_pct": None,              # 中证A500 PE-TTM 分位 0-1
        "ndx_fwd_pe_pct": None,           # 纳指 Forward PE 10 年分位 0-1
        "kc_pe_over_hs": None,            # 科创50 PE / 中证红利 PE 比
        "dxy": None,                      # 美元指数
        "us_real_yield": None,            # 美债10Y 实际利率(TIPS) %
        "growth_yes": False,              # 创业板连续5日跑赢红利 且 两市成交>2.3万亿
        "risk_hedge": False,              # 系统性风险/科创暴雷/地缘冲突
        "risk_red_component": False,      # 红利前十大>=2暴雷 或 >=3下调分红
        "note": "",
    }


def decide_scenario(v, y10):
    """三维联合情景判定(手册第二章) -> (情景, 依据)。

    - 三个指标同时支持同一情景 -> 切换; 任何矛盾 -> 留在更防御的一档;
    - 股债收益比边际突破 2.5 但红利 PE 分位仍高 -> 仍判均衡震荡(防假突破);
    - 估值锚缺失 -> 判"未知", 依手册留在均衡/防御中性档观察。
    """
    if v.get("risk_hedge"):
        return "避险/不确定", "风险触发: 系统性风险/科创暴雷/地缘冲突"
    ratio = None
    if v.get("div_yield_hs_pct") and y10:
        ratio = v["div_yield_hs_pct"] / y10
    pe = v.get("hs_pe_pct")
    if ratio is None or pe is None:
        return "均衡震荡", "估值锚缺失(股息率/红利PE分位未录入), 默认中性观察"
    if ratio > RATIO_EQ_HI and pe < PE_DEF_HI:
        return "防御/低估", f"股债收益比 {ratio:.2f}>2.5 且 红利PE分位 {pe:.0%}<30%"
    if ratio < RATIO_DEF_LOW or v.get("growth_yes"):
        return "成长/牛市", f"股债收益比 {ratio:.2f}<1.5 或 成长占优信号"
    return "均衡震荡", f"股债收益比 {ratio:.2f}∈1.5-2.5 或边际假突破(PE分位 {pe:.0%} 仍高)"


def bond_level(y10):
    """10Y% -> (信号, 操作) (手册第三章 3.2)。"""
    for lo, hi, sig, act in BOND_LEVEL:
        if (lo is None or y10 >= lo) and (hi is None or y10 < hi):
            return sig, act
    return "N/A", "N/A"
