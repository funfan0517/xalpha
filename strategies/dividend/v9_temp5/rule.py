#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V9 · 红利五因子估值温度 —— 参数权威源（`RULES.md` 是文字版，本文件是代码版）

与 V2（`../v2_temp`）的关系
---------------------------
V2 是「四维等权、PB 不可得、股债收益差用股息率代理」的版本；V9 是**外部规则原件**
（`implementation_rules.md`，公众号《红利类指数温度个人记录》体系）在 `xalpha` 池上的落地：

    温度 = 35%·股债收益差 + 20%·PE + 16%·PB + 15%·年线乖离 + 14%·价格位置（各取近 2 年日频分位）

三处与 V2 不同的口径：

1. **股债收益差用「股息率 − 10Y」**（`SPREAD_KIND = "dy"`）：股息率取 `data/_div_yield_m.csv`
   月度序列，000922 / H30269 是真实值，其余 5 只按 000922 变化率代理；无股息率历史的 930914
   退回 `100 / PE − 10Y`（`SPREAD_KIND = "ey"` 可整体切回盈利收益率口径做对照）。
2. **PB 参与**：`PB = PE × ROE 中枢`（中证官网无 PB 历史）。⚠ 常数 ROE 意味着
   **合成 PB 的分位 ≡ PE 分位**，所以「五因子」在数学上等于把 36% 权重压在 PE 上 ——
   `backtest.py` 会把这个等式实测出来，并给出「去掉 PB、四维归一」的对照。
   ⚠ 但真实 PB 不等价（ROE 时有 9%~11% 变异且系统性下行），该差异见
   `research/_pb_real_check.md`。
3. **权重按外部规则原样保留** 35/20/16/15/14（V2 主口径是四维等权）。

方向：分位一律折算成「越高越热」。股债收益差反号（利差宽 = 便宜 = 低分位 = 冷）。
"""
from __future__ import annotations

import os

_DIR = os.path.dirname(os.path.abspath(__file__))
_STRAT = os.path.dirname(_DIR)                      # strategies/dividend
ROOT = os.path.dirname(os.path.dirname(_STRAT))     # 仓库根（推导，不写死）

SIGNAL = "temp5"
TITLE = "V9 · 红利五因子估值温度（35/20/16/15/14 · PB 合成）"

# ------------------------------------------------------------------ 因子
FACTORS = ("spread", "pe", "pb", "dev", "pos")
LABEL = {"spread": "股债收益差", "pe": "PE", "pb": "PB", "dev": "年线乖离", "pos": "价格位置"}

# 外部规则原样权重（R0-1：禁止修改）
W = {"spread": 0.35, "pe": 0.20, "pb": 0.16, "dev": 0.15, "pos": 0.14}
# 对照口径：去掉 PB 后按剩余四维归一（回答「PB 到底提供了什么信息」）
_W4_SUM = sum(v for k, v in W.items() if k != "pb")
W_NO_PB = {k: W[k] / _W4_SUM for k in W if k != "pb"}
# 方向：+1 = 分位越高越热；-1 = 分位越高越冷（利差宽 = 便宜 = 冷）
SIGN = {"spread": -1, "pe": +1, "pb": +1, "dev": +1, "pos": +1}

# 分位窗口：近 2 年（730 日历日）的全部日频观测；最少 300 个观测才给分位
PCT_WINDOW_DAYS = 730
PCT_MIN_N = 300
MA_WIN = 250            # 年线
POS_WIN = 250           # 价格位置（52 周 = 250 根 high/low）

# ------------------------------------------------------------------ 档位（规则 §4.7，禁改阈值）
BANDS = [
    (85, "危险", "大幅减仓"),
    (75, "过热", "分批减仓"),
    (65, "偏热", "可开始部分止盈"),
    (55, "中性偏热", "不再新增，持有观望"),
    (40, "中性", "持有 / 正常定投"),
    (20, "偏冷", "便宜区域，可分批加仓"),
    (0, "冰点", "便宜区域，可分批加仓"),
]

# ------------------------------------------------------------------ 时间维度
# 板块温度用**自身历史分位**（绝对刻度不可移植：四维合成温度的经验区间只有 10~66）。
# 阈值由 backtest.py 的三档敏感性确认（与 V2 同构，便于横比）。
CUT_Q, OUT_Q = 0.80, 0.90
Q_MIN_N = 24            # 分位最少样本（月）
# 对照：直接拿规则 §4.7 的绝对档当择时（≥65 减半 / ≥75 清仓），回测后由数据判定
ABS_CUT, ABS_OUT = 65.0, 75.0

# 横截面 topK（只在有估值口径的标的里选，† 标的价格温度不参与）
TOP_K = 2
# 两个维度是否参与每日推荐 —— 由 `backtest/_v9_report.md` 的结论决定，不凭印象开
XS_ENABLED = False      # 横截面选品
TIME_ENABLED = False    # 时间维度降暴露

# ------------------------------------------------------------------ 标的池
# 10 只基金 = 10 个跟踪指数（来源 `../pool.py::POOL`，README §1）。
# 指数键用中证代码；两个标普系列在中证官网无条目，用自定键 + 价格代理。
FUNDS = [
    # fund, 短名, 指数键, 跟踪指数全称, ROE 中枢(PB 合成), 是否可得估值, 价格代理(仅无估值标的)
    dict(code="008163", short="标普红利低波50", index="SPCALVHD50",
         index_name="标普中国A股大盘红利低波动50", roe=0.110, has_valuation=False,
         price_proxy="sh515450", note="515450 ETF 价格代理；标普系列三源皆无估值"),
    dict(code="008115", short="红利低波100", index="930955",
         index_name="中证红利低波动100", roe=0.098, has_valuation=True),
    dict(code="007467", short="红利低波", index="H30269",
         index_name="中证红利低波动", roe=0.105, has_valuation=True),
    dict(code="007760", short="沪港深红利低波", index="931157",
         index_name="沪港深红利成长低波", roe=0.120, has_valuation=True),
    dict(code="090010", short="中证红利", index="000922",
         index_name="中证红利", roe=0.0993, has_valuation=True,
         note="ROE 经东财 PB 0.853 / PE 8.706 校准（隐含 0.098）"),
    dict(code="005125", short="红利机会", index="CSPSADRP",
         index_name="标普中国A股红利机会", roe=0.120, has_valuation=False,
         price_proxy=["sh501029", "sh562060"],
         note="501029 LOF（历史更长 2017→，作主代理）+ 562060 标普红利ETF华宝（场内、流动性更好，"
              "作交叉校验/回退）；标普系列三源皆无估值"),
    dict(code="016441", short="红利质量", index="931468",
         index_name="中证红利质量", roe=0.150, has_valuation=True),
    dict(code="561060", short="国企红利", index="000824",
         index_name="中证国有企业红利", roe=0.105, has_valuation=True),
    dict(code="561580", short="央企红利", index="000825",
         index_name="中证中央企业红利", roe=0.100, has_valuation=True),
    dict(code="018388", short="港股通高股息", index="930914",
         index_name="中证港股通高股息投资", roe=0.120, has_valuation=True,
         bond_anchor="US", note="港股用 10Y 美债锚，其余用 10Y 中债"),
]
CODES = [f["code"] for f in FUNDS]
NAME = {f["code"]: f["short"] for f in FUNDS}
BY_CODE = {f["code"]: f for f in FUNDS}
VALUED = [f["code"] for f in FUNDS if f["has_valuation"]]
INDEX_OF = {f["code"]: f["index"] for f in FUNDS}

BASE_LABEL = f"基准：{len(CODES)} 只等权"
UNIT = ""


def unit_fmt(v) -> str:
    return f"{v:.1f}"


def band_of(temp: float):
    """温度 -> (状态, 动作)。"""
    for lo, name, action in BANDS:
        if temp >= lo:
            return name, action
    return BANDS[-1][1], BANDS[-1][2]


# ------------------------------------------------------------------ 路径
DATA_DIR = os.path.join(_DIR, "data")
IDX_DIR = os.path.join(DATA_DIR, "_idx")            # 中证指数日频（close/high/low/pe）
SINA_DIR = os.path.join(DATA_DIR, "_sina")          # 标普系价格代理（ETF/LOF）
BT_DIR = os.path.join(_DIR, "backtest")
DAILY_DIR = os.path.join(_DIR, "daily")

BOND_CN = os.path.join(ROOT, "data", "_bt_caches", "bond10y_d.csv")      # 日频（本策略生产）
BOND_CN_M = os.path.join(ROOT, "data", "_bt_caches", "bond10y_m.csv")    # 月频（历史兜底）
BOND_US_M = os.path.join(ROOT, "data", "_bt_caches", "ust10y_m.csv")     # 月频
DY_CSV = os.path.join(ROOT, "data", "_div_yield_m.csv")                  # 指数股息率月度历史（跨策略数据类）

# 蛋卷 index_eva：指数 PE/PB 周频历史（免 token，跨策略数据类）。**只覆盖 3 只**：
#   000922 -> SH000922 · H30269 -> CSIH30269 · 931157 -> CSI931157
# 其余 5 只（930955/931468/000824/000825/930914）蛋卷无条目，PB 仍只能合成。
DJ_CSV = os.path.join(ROOT, "data", "_danjuan_eva.csv")
DJ_CODE = {"000922": "SH000922", "H30269": "CSIH30269", "931157": "CSI931157"}

# 股债收益差口径："dy" = 股息率 − 10Y（红利专用，优先）；"ey" = 100/PE − 10Y（无股息率时的兜底）
SPREAD_KIND = "dy"

OUT_REPORT = os.path.join(BT_DIR, "_v9_report.md")
OUT_PANEL = os.path.join(BT_DIR, "_v9_panel.jsonl")
OUT_BT = os.path.join(BT_DIR, "_v9_bt.jsonl")
OUT_BT_SUM = os.path.join(BT_DIR, "_v9_bt_summary.json")   # 供 daily 引用，避免手抄数字
OUT_MD = os.path.join(DAILY_DIR, "_v9_signal.md")
OUT_JSON = os.path.join(DAILY_DIR, "_v9_signal.json")
OUT_HTML = os.path.join(DAILY_DIR, "_v9_temp.html")

SUBTITLE = [
    "温度 = 35%·股债收益差 + 20%·PE + 16%·PB + 15%·年线乖离 + 14%·价格位置（各取近 2 年分位）",
    "股债收益差 = 股息率 − 10Y 国债（红利专用；930914 港股无股息率历史，退回 100/PE − 10Y）；"
    "PB = PE × ROE 中枢（无公开 PB 历史）",
    "档位阈值固定：<20 冰点 / 20–40 偏冷 / 40–55 中性 / 55–65 中性偏热 / 65–75 偏热 / 75–85 过热 / ≥85 危险",
    f"池 {len(CODES)} 只（{len(VALUED)} 只有估值口径，2 只标普系列只算价格温度、不参与排名）",
]

NOTE = ("V9 是外部《红利族基金估值温度》规则在 xalpha 池上的**原样落地**，与 V2 的差别在口径"
        "（盈利收益率替股息率代理、PB 参与、原版权重）。它的价值不在「又一种温度」，"
        "而在把该体系的两个隐含假设摊开验证：①PB 用 PE×常数合成时，PB 分位与 PE 分位是同一个数，"
        "36% 权重实际压在 PE 上；②绝对档位（20/40/55/65/75/85）能不能当择时阈值。")
