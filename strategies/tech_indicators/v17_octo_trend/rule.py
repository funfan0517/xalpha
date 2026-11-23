# -*- coding: utf-8 -*-
"""V17 · 八态趋势（参数权威源）。

**来源**：`strategies/vol_legs/v12_octo_trend/rule.py` 的副本（2026-11-21 按用户要求
整套复制进技术指标家族，编号 V17）。规则本体 = `G:\\tradingagents\\hs300_trend\\八态趋势参数规格.md`
v1.1（参考实现 `hs300_trend/state8_final.py::classify`，本目录 `engine.py::classify` 是它的逐句移植）。

**唯一的改动是标的池与数据口径**（其余参数、映射、时序、成本一律照搬 v12）：

| 项 | v12_octo_trend | 本变种（v17_octo_trend） |
|---|---|---|
| 标的 | 七腿（A500/沪深300/纳指/标普/科创50/恒科/黄金） | **家族 54 只场内标的**（`../_pool.py` = `data/_universe.md` 前四类；含 3 只 LOF，按同一口径） |
| 价格 | 六腿真实指数收盘 + 黄金 `518880` | 全部为**场内 ETF 前复权收盘**（`data/_etf_cv_klines.json`） |
| 量能 | 同源**指数量能**（A股三腿共享 `SH000300`） | **ETF 自身成交量**（家族没有 54 只标的的指数量能库） |
| 成本 | 单边 5bp、空仓计货基 1.40% | 同 v12（**与家族「零成本/零利率」口径不可横比**） |

⚠ **量能口径是本变种最大的已知折扣**：v12 实测把 `510300` 自身量当 `rvol` 分母时，
沪深300 的「弱回调 − 强回调」分离度是 **−0.77pp（符号反了）**，换 `SH000300` 指数量能才是
+1.20pp —— 原因是宽基 ETF 的量由做市与申赎套利主导（`v18_volume_notes/ohlcv.py::EDGE`）。
本变种无指数量能可用，**只能吃这个折扣**：宽基/QDII 的强弱分档会偏噪声，主题/行业 ETF 较贴原意。
回测报告 §3 会把本库数据上的四组配对分离度原样打出来 —— **符号不对就如实说不成立**，
不要解释成「市场变了」。要恢复 v12 的指数口径，就得回到 vol_legs（那边有 `_idxcache`）。

## 与规格的关系（三处必须自己定的东西，v12 已定，本变种照搬）

1. **八态 → 仓位许可上限**（`CAP`）：规格通篇是「状态判定」，没写怎么下仓。映射只按规格
   §3/§4 的后验统计定，不按回测调；**唯一偏离规格的一格是 `慢跌` 20%→0**（2026-11-19，用户指定）。
   「急跌给半仓而不是清仓」是规格 §4② 的直接结论（「急跌 ≠ 危险，慢跌才是」），不按直觉改。
2. **升格确认 2 日（`UP_CONFIRM`）**：规格的迟滞只在 regime 层（多数决 5 日 + 翻转 2 日），
   态层没有迟滞 —— 急涨/慢涨的分界就是 z20 穿 ±1.2，一天内反复穿越是常态。故补：
   **升格要连续 2 日同向、降格立即生效**。这条只影响换手，不参与判定。
3. **v12.1 的按腿优化（`CAP_OPT` / `VOL_LAYER`）原样保留，但只有原来那七只 ETF 有配置**：
   `510300`（强回调 20%）、`588000`（强回调/急跌 20%、强反弹 100%）与七只的波动层。
   ⚠ 这些证据是在**指数口径**上取得的，本变种是 ETF 量能口径、**参数未重标** ——
   只能当「结构保留」，不能当同一条证据；其余 47 只一律走基线 `CAP`、不加波动层。

## 不能改的三件事（规格实测结论，改了就不是八态）

- 结构层是**三票多数决 + 5 日确认**，不是「三票全 AND + 粘滞」：全 AND 覆盖率只有 68%，
  粘滞日在过渡段方向常错（规格 §2 第 1 步的粗体警告）。
- **强回调 / 强反弹的强度必须「关键位 + 量能」双条件**：只看未破 MA60 分离度掉到 +0.26pp、
  只看缩量掉到 +1.00pp（规格 §4③）。反弹侧反过来 —— 量能是主判据，前高只作标注。
- **不要再叠加回撤深度**（规格 §5「回调强度定义」）：破 MA60 + 回撤 −5% 的分离度只有 +0.81，
  比纯净的「破 MA60」+1.83 更差。
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))                 # v17_octo_trend/
_PARENT = os.path.dirname(_HERE)                                   # strategies/tech_indicators
_ROOT = os.path.dirname(os.path.dirname(_PARENT))                  # 仓库根
_DATA = os.path.join(_ROOT, "data")
# 最后插入的 `_HERE` 排最前 —— 仓库里有 5 个同名 `data.py`，且 `data/` 目录也能被
# 当成 namespace package，导入顺序错了会静默换一套标的池。
for _p in (_ROOT, _DATA, _PARENT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import data as vdata  # noqa: E402

if os.path.dirname(os.path.abspath(vdata.__file__)) != _HERE:
    raise RuntimeError(
        f"`import data` 解析到了 {vdata.__file__}，不是 v17_octo_trend/data.py。"
        f"把本目录插到 sys.path[0]（或先清 sys.modules 里的 'data'）再跑。")

# ---------------------------------------------------------------- 标的（家族全池 54 只）
# 池的权威源是 `data/_universe.md` 前四类（经 `../_pool.py` 读取），这里不写死任何代码。
# `LEGS` 保持 v12 的形状 `leg -> (代码, 名称)`，只是 key 现在就是代码本身（一行一个标的）。
CODES = list(vdata.CODES)
NAMES = vdata.names()
LEGS = {c: (c, NAMES.get(c, c)) for c in CODES}
LEG_ORDER = list(CODES)

# 执行范围 = 全池（单标的分档，没有组合层带位叠加问题）；保留 WATCH_LEGS 供将来降级成诊断腿。
CORE_LEGS = list(LEG_ORDER)
WATCH_LEGS = []

# 规格的**标定腿**：八态后验表、状态流水一律用这一条（沪深300 ETF = 规格的标定样本）。
# 与执行范围解耦 —— 执行范围一改，不能让「标定腿」跟着变成池子里第一只。
CALIB_LEG = "510300"

# ---------------------------------------------------------------- 量能口径
# 全池只能用 ETF 自身成交量（家族没有 54 只标的的指数量能库）。见模块 docstring 的 ⚠。
VOL_MODE = "etf"

# ---------------------------------------------------------------- 一 · 结构参数（规格 §1 参数总表）
MA_SHORT = 20            # 短期线 / 当下层
MA_MID = 60              # 中期线 / 回调的关键位
MA_LONG = 250            # 长期牛熊线（规格：横盘样本 250 与 200 打平，长牛 200 更好，保守取 250）
SLOPE_N = 20             # s2：MA60 相对 20 个交易日前
Z_TH = 1.2               # 急 / 慢 分界（1.0→1.2 分离度翻倍，1.5 以上样本锐减）
Z_BURST = 1.5            # 「爆发」标注，不参与判定
RVOL_HI = 1.2            # 放量（强反弹主判据）
RVOL_LO = 0.85           # 缩量（弱回调必要条件）
PH_N = 120               # 逼近前高的窗口
PH_MULT = 0.97           # close ≥ 120 日最高 × 0.97
FLIP_CONFIRM = 2         # 强回调 / 强反弹 翻转 regime 所需连续日数
REGIME_MODE = "vote"     # "vote" = 三票多数决 + 确认（规格 v1.1 默认）；"sticky" = 全 AND + 粘滞（v1.0，仅对照）
VOTE_CONFIRM = 5         # 多数决确认日数（5~10 是平台，1~3 太噪）
UP_MIN = 2               # S >= 2 → 向上票

# ---------------------------------------------------------------- 二 · 八态 → 许可上限
STATE_ORDER = ["急涨", "慢涨", "弱回调", "强回调", "急跌", "慢跌", "弱反弹", "强反弹"]

# 规格 §3 的原始映射，每个数字后面是它依据的那一行 fwd20（2010-2026 / 2006-2026）。
# **只用于对账与报告**（回测 §3 的「许可·规格」列、`daily/_v17_octo_daily.json` 的 `cap_spec`），
# 不参与执行 —— 执行一律走下面的 `CAP`。**不要改这张表**，它必须与规格逐字一致。
CAP_SPEC = {
    "急涨": 1.0,      # +1.72% / +3.69%（钱在「急」里，规格 §4①）
    "慢涨": 0.5,      # +0.04% / +0.87%（和随机没区别 -> 不给满仓）
    "弱回调": 1.0,    # +1.07% / +1.84%（未破 MA60 且缩量 = 真正的回调买点）
    "强回调": 0.5,    # +0.35% / +0.71%（趋势可能已坏，只留半仓）
    "急跌": 0.5,      # +1.53% / +0.89%（急跌会反抽，不是危险态 —— 规格 §4②）
    "慢跌": 0.2,      # −0.73% / −1.00%（钝刀割肉，唯一确定性负期望 -> 观察仓）
    "弱反弹": 0.2,    # −0.28% / −0.09%（缩量反弹没有参与价值）
    "强反弹": 0.5,    # +0.71% / +1.56%（放量反弹是有效的，但仍在向下 regime 里，只给半仓）
}

# **生效基线** = 规格原表 ⊕ 唯一一处偏离：`慢跌` 20% → 0（2026-11-19，用户指定，v12 沿用）。
#   为什么敢偏离：规格按 fwd20 定档，而 `慢跌` 是唯一三档期望全负的态 → 数学最优就是 0，
#   20% 只是「观察仓」的约定值，不是最优点。别的格子不许照此办理
#   （`弱反弹` 本样本 fwd20 反号也故意没改，见 README §5）。
CAP = dict(CAP_SPEC, **{"慢跌": 0.0})

# ---------------------------------------------------------------- 二b · v12.1 优化口径（按代码）
# 来源：`vol_legs/v12_octo_trend/research/opt_ashare/_opt_ashare.md`（2026-11-12，沪深300 + 科创50）。
# **只改两处、且必须由两组独立证据支撑**（详见 v12 的 rule.py docstring）：
#   ① `强回调` 50%→20%（fwd20 定档与实际持有期不匹配）；
#   ② 高波动腿加波动率目标覆盖。
#
# ⚠ 这两层只对**原来那七只 ETF** 有配置，且证据来自 v12 的**指数口径**；
#   本变种是 ETF 量能口径、参数未重标 —— 结构保留，证据不继承。
OPT = True
CAP_OPT = {
    # 沪深300：只降「强回调」（拉到含 2008 的指数样本后，「强反弹 100%」会扩大回撤）
    "510300": dict(CAP, **{"强回调": 0.2}),
    # 科创50：三条都保留（样本里没有 2008 类崩盘）
    "588000": dict(CAP, **{"强回调": 0.2, "强反弹": 1.0, "急跌": 0.2}),
}

# ---------------------------------------------------------------- 二d · 波动层（按代码 · 生效口径）
# 证据 `vol_legs/v12_octo_trend/research/er_vol_layer/_er_vol_layer.md`（同一八态仓位、同一
# `engine.run`、同一 `start`；判据是打穿「等比缩仓」基准）。同样只在原来那七只上有配置：
#   A股两腿 + 恒科 -> C1：ER(20) 切 TV 20%/10% 的自适应层（带 10pp/5日 死区）
#   黄金 -> C4：固定 TV10 + 死区；科创50 -> C3：固定 TV20 + 死区（不要 ER）
#   美股两腿 -> C5：ER20/10 自适应、**无死区**（它们是唯一「加死区更差」的两条）
# 形态恒为 `min(八态许可, 波动层系数)`：只压上限，不改判定、不加杠杆、不放大仓位。
VOL_LAYER = {
    "563360": dict(mode="er", tv=0.20, tv_low=0.10),
    "510300": dict(mode="er", tv=0.20, tv_low=0.10),
    "513180": dict(mode="er", tv=0.20, tv_low=0.10),
    "518880": dict(mode="fixed", tv=0.10),
    "588000": dict(mode="fixed", tv=0.20),
    "513100": dict(mode="er", tv=0.20, tv_low=0.10, dead=0.0, gap=0),
    "513500": dict(mode="er", tv=0.20, tv_low=0.10, dead=0.0, gap=0),
}
VL_ER_N = 20                      # Kaufman 效率比窗口
VL_ER_HI, VL_ER_LO = 0.40, 0.30   # ER 阈值（带滞回：中间维持原档）
VL_VOL_WIN = 20                   # 已实现波动窗口（与 z20 / rvol 同口径）
VL_DEAD = 0.10                    # 幅度死区：|Δ系数| < 10pp 不动手（按腿可被 cfg 覆盖）
VL_DEAD_DAYS = 5                  # 时间死区：距上次调仓 < 5 交易日不动手（同上）

# **历史口径（2026-11-19 前）**：固定 σ 覆盖 `min(w, vt/σ20)`，只有科创50 一条腿。
# 已**不被回测 / 日报读取**（生效口径是上面的 `VOL_LAYER`）；保留它只有一个用途：
# 让 `vol_legs/v12_octo_trend/research` 里的「S1 现状」列可复现（那边比较的是旧口径）。
VOL_TARGET_LEGACY = {"588000": 0.25}
VOL_WIN = 20              # σ 的窗口（与 z20 / rvol 同口径）

# 升格确认日数（降格立即生效）。见模块 docstring 第 2 条。
UP_CONFIRM = 2

# ---------------------------------------------------------------- 二c · 参数变更记录
# 日报会把这些行印在「读法与边界」里。最近一次在前。
CHANGELOG = [
    ("2026-11-21",
     "**新 V17（八态趋势）落地**（用户要求）：整套复制 `vol_legs/v12_octo_trend`，标的由七腿扩到"
     "家族 54 只场内 ETF，价格/量能改用 ETF 日线（`data/_etf_cv_klines.json`）。"
     "原 V17（趋势四象限闸门）同日删除。**量能口径从「同源指数」降为「ETF 自身量」** —— "
     "宽基/QDII 的强弱分档会偏噪声，回测 §3 的分离度会如实体现。成本仍按 v12（单边 5bp、货基 1.40%）。"),
]

# ---------------------------------------------------------------- 三 · 执行与成本
WARMUP = MA_LONG + SLOPE_N        # 270 根：MA250 成形 + 20 日斜率
FEE_SIDE = 0.0005                 # 单边 5bp（ETF 免印花税；与 v12 同）
CASH_ANNUAL = 0.014               # 空仓计货基 1.40%（v12 口径；家族其它变种取 0，勿横比）
TRADING_DAYS = 252

# ---------------------------------------------------------------- 四 · 逐标参数覆盖
# 规格 §7.4 说得很清楚：MA 的时间尺度可移植，但 `z_th` / `rvol` 阈值是**在沪深300 上标的**。
# 这里留一个空表：一旦按标的重做了分离度扫描，把结果写进来（如 {"513100": dict(z_th=1.5)}），
# 状态机自动按标取值，不需要改 engine。**空表 = 全部用规格值**，这是当前口径。
PARAMS_BY_LEG = {}


def params(leg=None):
    """该标的生效的参数（规格值 ⊕ `PARAMS_BY_LEG` 覆盖）。"""
    p = dict(ma_short=MA_SHORT, ma_mid=MA_MID, ma_long=MA_LONG, slope_n=SLOPE_N,
             z_th=Z_TH, z_burst=Z_BURST, rvol_hi=RVOL_HI, rvol_lo=RVOL_LO,
             ph_n=PH_N, ph_mult=PH_MULT, flip_confirm=FLIP_CONFIRM,
             regime_mode=REGIME_MODE, vote_confirm=VOTE_CONFIRM, up_min=UP_MIN)
    p.update(PARAMS_BY_LEG.get(leg) or {})
    return p


def cap_map(leg=None):
    """该标的生效的整张许可表：基线 `CAP`（规格 ⊕ `慢跌` 0）再叠 `CAP_OPT` 的按代码覆盖。"""
    if not OPT or leg is None:
        return CAP
    return CAP_OPT.get(leg, CAP)


def vol_layer_of(leg=None):
    """该标的生效的波动层配置；None = 不加（全池只有原来那七只有配置）。

    返回 `dict(mode="er"|"fixed", tv=..., tv_low=..., dead=..., gap=...)`，交给
    `engine.vol_layer()` 执行；`dead`/`gap` 缺省时取 `VL_DEAD` / `VL_DEAD_DAYS`。
    `OPT=False` 时一律 None（回到基线口径）。
    """
    if not OPT or leg is None:
        return None
    cfg = VOL_LAYER.get(leg)
    return dict(cfg) if cfg else None


def vol_layer_txt(leg=None):
    """波动层的一句话描述（报告/日报/图注用）。"""
    cfg = vol_layer_of(leg)
    if not cfg:
        return "无"
    dead = float(cfg.get("dead", VL_DEAD))
    gap = int(cfg.get("gap", VL_DEAD_DAYS))
    dz = f" + {dead:.0%}/{gap}日死区" if (dead > 0 or gap > 0) else "（无死区）"
    if str(cfg.get("mode")) == "er":
        return f"ER{VL_ER_N} 自适应 {cfg['tv']:.0%}/{cfg.get('tv_low', 0):.0%}{dz}"
    return f"固定 TV{cfg['tv']:.0%}{dz}"


def vol_target_of(leg=None):
    """**历史口径**（2026-11-19 前的固定 σ 覆盖）：None = 不做覆盖。

    ⚠ 回测/日报**已不再调用**它（生效口径是 `vol_layer_of`）。留着只为让
    v12 的 `research/opt_7legs` / `research/er_vol_layer` 的「S1 现状」列能复现旧读数。
    """
    if not OPT or leg is None:
        return None
    return VOL_TARGET_LEGACY.get(leg)


def cap_of(state, leg=None):
    """状态 -> 许可上限（0~1 的带内比例）。未知/暖机状态 -> 0。

    `leg` 不传时给的是**规格原表** `CAP_SPEC`（后验表、研究脚本对账用）；
    传 leg 才是该标的**实际生效**的许可（基线 `CAP` ⊕ `CAP_OPT`）。
    """
    table = CAP_SPEC if leg is None else cap_map(leg)
    return table.get(str(state), 0.0)


def cap_version(leg=None):
    """一句话描述该标的生效的**整条执行口径**（许可表 + 波动层）。"""
    if not OPT or leg is None:
        return "规格 CAP（慢跌 0）"
    diff = {k: v for k, v in CAP_OPT.get(leg, CAP).items() if abs(v - CAP.get(k, v)) > 1e-9}
    txt = ("v12.1(" + "、".join(f"{k} {v:.0%}" for k, v in diff.items()) + ")"
           if diff else "规格 CAP（慢跌 0）")
    cfg = vol_layer_of(leg)
    return txt + (f" + 波动层 {vol_layer_txt(leg)}" if cfg else "")


def fee_of(leg):
    """单边费率：全池统一 5bp（不按标的分档，避免用结果反推参数）。"""
    return FEE_SIDE
