# -*- coding: utf-8 -*-
"""波动资产 · 各标的「策略表现 × 前三名」清单（只读产物，不重跑回测）。

回答两个问题：
1. **七条腿 × 家族里每一支策略的表现各是多少** —— §1 全量表（年化 / 回撤 / 夏普 / 仓位 / 样本）；
2. **每只标的排前三的是哪三支** —— §0 前三名表（排序看夏普，含买入持有基准）。

## 它怎么算「最优」

1. 把家族里**每个变种自己的回测产物**（`backtest/_*_bt.jsonl`）读进来，取各变种的**默认口径那一行**
   （变种名里带「默认」/`gate=是` 等标记；v6 已统一场内 `518880` 单口径，见 `rows_v6()`；
   v5.1 黄金仓位规则见 `rows_v5()`；v12 取 v12.1 生效口径、七腿全部参赛，见 `rows_v12()`）；
2. 每个标的内部，只保留**样本年数 ≥ 该标的最长样本 75%** 的行参与选优
   —— 短样本（如 v13 的科创50 / 恒生科技只有约 5 年）照样列出来，
   但标 `·短样本` 且**不参与选优**，否则「1 年样本 + 夏普 1.5」会挤掉 15 年样本 + 夏普 0.6；
3. 选优规则：**夏普最高**（同分看年化）。买入持有也是候选 —— 长牛里所有时机策略都跑输时，
   「最优策略」就该诚实地是「一直拿着」；
4. 另列「最高年化」标记（`↑`），因为夏普最优与收益最高常常不是同一支（v3 vs v1/v2）；
5. 每只标的列**前三名**（`★1 / ★2 / ★3`）—— 第一名常常是「回撤最小」，第二名/第三名才是
   收益端的选择，只看第一名会漏掉真正想买的那支。

## 为什么不自己重算

各自的 `backtest/*.md` 才是权威产物（参数、费率、窗口都在里面），本脚本只做**横向汇编**，
所以产物一更新，重跑本脚本清单就跟着更新。代价是：**各行的样本窗口与费率口径不统一**，
因此每行都单独标了「样本」与「费率口径」，跨行比较前先看这两列。

产出（本目录 = `strategies/vol_legs/research/best_by_leg/`）：
- `_best_by_leg.md`    人读清单（一页前三名 + 分标的候选明细 + 未纳入原因 + 口径）
- `_best_by_leg.jsonl` 机读结果（每行 = 一个 标的×策略；含 `rank` / `short` 标记）

跑一次：`python strategies/vol_legs/research/best_by_leg/_best_by_leg.py`

> 模拟结果，非投资建议。
"""
import io
import json
import math
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))     # .../research/best_by_leg
_RES = os.path.dirname(_HERE)                          # .../research
_VL = os.path.dirname(_RES)                            # strategies/vol_legs
_ROOT = os.path.dirname(os.path.dirname(_VL))          # 仓库根（推导，不写死）
if _VL not in sys.path:
    sys.path.insert(0, _VL)

import rule  # noqa: E402  （参数权威源：腿名 / 执行代码 / 目标带）

OUT_MD = os.path.join(_HERE, "_best_by_leg.md")
OUT_JSONL = os.path.join(_HERE, "_best_by_leg.jsonl")

LEGS = rule.LEG_ORDER
MAX_SHORT_RATIO = 0.75          # 兜底（缺 start 时用）：样本 < 该腿最长样本的 75% → 短样本
MIN_YEARS = 3.0                 # 绝对下限：不到 3 年的样本一律不选优（连一轮牛熊都盖不住）
MAX_SHORT_GAP = 4.0             # 起点比该腿最早起点晚 4 年以上 → 短样本（缺掉的那段足够改变结论）


# ================================================================ 读产物
def _num(x):
    """-> float 或 None（把 NaN / 非数统一成 None）。"""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _load_jsonl(rel):
    path = os.path.join(_VL, rel.replace("/", os.sep))
    if not os.path.exists(path):
        print(f"  [WARN] 缺产物: {rel}")
        return []
    rows = []
    with io.open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _load_json(rel):
    path = os.path.join(_VL, rel.replace("/", os.sep))
    if not os.path.exists(path):
        print(f"  [WARN] 缺产物: {rel}")
        return {}
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def _years(start, end, fallback=None):
    y = _num(fallback)
    if y is not None and y > 0:
        return y
    try:
        from datetime import date
        a = date.fromisoformat(str(start)[:10])
        b = date.fromisoformat(str(end)[:10])
        return (b - a).days / 365.25
    except Exception:  # noqa: BLE001
        return None


def mk(leg, vx, label, r, src, *, ann=None, mdd=None, sharpe=None, calmar=None,
       vol=None, exposure=None, trades=None, start=None, end=None, years=None,
       fee="", layer="leg", bench=False, note=""):
    """统一成一行。字段缺失就留 None，表格里显示「—」。

    ⚠ **含费优先**：V1 / V2 / V3 的产物把零费当主列、含费放 `*_fee`。
    只要调用方没显式指定 ann/sharpe/mdd，这里自动取含费字段，
    保证清单里所有行是同一把尺子（否则高换手的 MA20 会被系统性高估）。
    """
    use_fee = ann is None and r.get("ann_fee") is not None
    a = _num(ann if ann is not None else (r.get("ann_fee") if use_fee else r.get("ann")))
    m = _num(mdd if mdd is not None else
             (r.get("mdd_fee") if (use_fee and r.get("mdd_fee") is not None) else r.get("mdd")))
    s = _num(sharpe if sharpe is not None else
             (r.get("sharpe_fee") if (use_fee and r.get("sharpe_fee") is not None) else r.get("sharpe")))
    c = _num(calmar if calmar is not None else r.get("calmar"))
    if c is None and a is not None and m is not None and m < -1e-9:
        c = a / abs(m)
    st = start or r.get("start")
    en = end or r.get("end")
    return dict(
        leg=leg, leg_name=rule.LEGS[leg]["name"], vx=vx, label=label,
        ann=a, mdd=m, sharpe=s, calmar=c,
        vol=_num(vol if vol is not None else r.get("vol")),
        exposure=_num(exposure if exposure is not None else
                      (r.get("exposure") if r.get("exposure") is not None else r.get("expo"))),
        trades=(trades if trades is not None else
                (r.get("trades") if r.get("trades") is not None else
                 (r.get("acts") if r.get("acts") is not None else r.get("actions")))),
        start=str(st)[:10] if st else None,
        end=str(en)[:10] if en else None,
        years=_years(st, en, years if years is not None else r.get("years")),
        fee=fee, layer=layer, bench=bench, note=note, src=src,
        rank=None,      # 由 pick() 回填：1/2/3 = 该标的前三名，None = 未进排名
    )


# ---------------------------------------------------------------- 各变种适配器
def rows_ladder():
    """主策略单腿（回撤阶梯 vs 一次性满仓）；只有 a500/hs300/ndx/spx 有独立单腿产物。"""
    d = _load_json("backtest/_vol_legs_bt.json")
    out = []
    for leg, blk in (d.get("singles") or {}).items():
        if leg not in rule.LEGS:
            continue
        band = rule.LEGS[leg]["band"]
        w = blk.get("window") or [None, None]
        L = blk.get("ladder") or {}
        expo = None
        if _num(blk.get("avg_invested")) is not None and band:
            expo = _num(blk["avg_invested"]) / band
        out.append(mk(leg, "ladder", "主策略 · 回撤阶梯（单腿）", L, "backtest/_vol_legs_bt.json",
                      start=w[0], end=w[1], exposure=expo, trades=blk.get("trades"),
                      fee="含费（rule 费率）",
                      note="带内份额按阶梯补，创新高归零；未投按货基计息"))
    return out


def rows_simple(rel, vx, label, fee, note="", min_trades=1, fee_adj=True):
    """v1_1 / v2 / v3 这类「7 腿 × 1 行」的产物。

    ⚠ 这三个产物把**零费**当主列（`ann` / `sharpe`），含费另列 `ann_fee` / `sharpe_fee`。
    本清单统一用**含费**（A 类腿 0.10% 买 + 0.50% 卖，换手高的策略会被吃掉几个 pp），
    否则清单会系统性高估 MA20 这类高换手策略（沪深300 腿上零费 +4.23% → 含费 -4.28%）。
    零费口径仍可在各自产物的 `ann` / `sharpe` 字段里看到。
    """
    out = []
    for r in _load_jsonl(rel):
        leg = r.get("leg")
        if leg not in rule.LEGS:
            continue
        if min_trades and (r.get("trades") or 0) < min_trades:
            continue
        kw = {}
        if fee_adj and r.get("ann_fee") is not None:
            kw = dict(ann=r.get("ann_fee"), sharpe=r.get("sharpe_fee"),
                      mdd=(r.get("mdd_fee") if r.get("mdd_fee") is not None else r.get("mdd")))
        out.append(mk(leg, vx, label, r, rel, fee=fee, note=note, **kw))
    return out


def rows_v4():
    """沪深300 × 国债的 ERP 通道（层次不同：不是这条腿的择时，是股债摆仓）。"""
    rows = _load_jsonl("v4_erp_band/backtest/_v4_erp_band_bt.jsonl")
    # 该产物把 start/end/years 只写在第一行（元信息行），默认行只有 ann/mdd/sharpe
    meta = next((r for r in rows if r.get("years") and r.get("start")), {})
    start, end, years = meta.get("start"), meta.get("end"), meta.get("years")
    out = []
    for r in rows:
        name = str(r.get("name") or "")
        if "默认" not in name or _num(r.get("ann")) is None:
            continue
        out.append(mk("hs300", "v4", "V4 ERP 股债通道（默认）", r,
                      "v4_erp_band/backtest/_v4_erp_band_bt.jsonl",
                      start=start, end=end, years=years,
                      fee="含费（月末回锚）", layer="股债",
                      note="沪深300 vs 国债的 0/100 摆仓，不是这条腿的择时；不参与选优"))
    return out


def rows_v6():
    """黄金三口径（A 长期配置 / B 控回撤 / C 低仓位占坑）+ 恒持满带。

    2026-11-13 起 v6 只剩场内 `518880` 一套口径，产物写 `mode="exchange"`，
    不再有 otc/exchange 分组；只靠 `group` 排除敏感性对照行。
    """
    out = []
    for r in _load_jsonl("v6_gold/backtest/_v6_gold_bt.jsonl"):
        if r.get("group"):
            continue
        key = r.get("key")
        if key not in ("A", "B", "C", "BH"):
            continue
        bench = key == "BH"
        label = f"V6-{key} {r.get('label')}" if not bench else "买入持有（与 V6 同窗口）"
        out.append(mk("gold", f"v6{key}", label, r,
                      "v6_gold/backtest/_v6_gold_bt.jsonl",
                      fee="含费（场内佣金 0.03% + 滑点 0.05%）",
                      bench=bench, layer=("leg" if not bench else "基准对照"),
                      note="黄金专用：没有现金流锚，PE/ERP/股息率都不适用"))
    return out


def rows_v5():
    """黄金仓位规则 V5.1（风险预算 × 实际利率闸门 → 六档目标仓位）。

    2026-11-18 重写：旧 v5.0「三因子计分 → 三档」已整体删除，只剩两件有证据的事
    —— **波动目标的风险预算** + **实际利率水平（FRED DFII10）闸门**。

    与本清单的 V6 各行**同层可比**：它回答的也是「投给黄金的这笔钱该拿几成」
    （连续 0~100%），不是「买不买黄金」，所以 `layer="leg"` 正常参赛。

    ⚠ **费率档不同**：V5 用场内万 2.5 单边（`score.FEE`、`SLIP=0`），V6 用佣金 0.03% + 滑点 0.05%。
    两者都算「含费」，但差额约 0.55bp/次 × 15 次/年 ≈ 0.08pp/年，**不足以解释两行 0.28 的夏普差**；
    真要精确对账就看 §3-② 的费率口径列。
    """
    out = []
    for r in _load_jsonl("v5_gold_score/backtest/_v5_gold_score_bt.jsonl"):
        if r.get("key") != "strategy":      # bh / half / cash / group=ablation_or_sens 都不是策略行
            continue
        out.append(mk("gold", "v5", "V5.1 黄金仓位（风险预算 × 利率闸门）", r,
                      "v5_gold_score/backtest/_v5_gold_score_bt.jsonl",
                      fee="含费（场内万 2.5 单边 · 空仓货基 1.40%）",
                      note="实际利率（FRED DFII10）水平闸门；不预测方向，只做风险归一化"))
    return out


def _default_cfg(name):
    """从产物变种名里抽出「默认口径」的配置描述。

    为什么要抽而不是写死：这几个变种的**默认档换过一次**（v10 2026-11-17 把固定 TV20 换成
    `ER20/10 + 5日死区`、v7 加了日频离场、v9 换成「月末 + 快减 + 快补」）。写死的标签会在
    产物更新后**静默说谎**，而变种名每次回测都会重写 —— 直接跟着它走。
    """
    s = str(name or "").replace("*", "").strip()
    for k in ("默认：", "默认:"):
        if k in s:
            return s.split(k, 1)[1].strip()
    if s.startswith("本变种默认"):
        return s[len("本变种默认"):].strip().strip("（）").strip() or "默认口径"
    s = s.replace("· 默认", "").replace("默认", "").strip(" ·")
    return s.replace(" ）", "）").strip() or "默认口径"


def rows_us(rel, vx, desc, note=""):
    """v7 / v8 / v9 / v10：全七腿（2026-11-17 起由 ndx/spx 两腿扩到 `rule.LEG_ORDER`），
    取每腿变种名里「默认」的那一行，标签里的配置描述从产物名里抽（见 `_default_cfg`）。

    产物里常有「上一版默认」「旧默认」「… 对照」这类行，不能靠 `"默认" in variant` 简单筛 ——
    必须先排除对照行，再优先取明确写了「本变种默认 / 默认：」的，其次取带「（… 默认）」的。
    """
    picked = {}
    for r in _load_jsonl(rel):
        leg = r.get("leg")
        if leg not in rule.LEGS:
            continue
        name = str(r.get("variant") or "")
        if "默认" not in name or "上一版" in name or "旧默认" in name or "对照" in name:
            continue
        prev = picked.get(leg)
        rank = 2 if ("本变种默认" in name or "默认：" in name or "默认:" in name) else 1
        if prev is None or rank > prev[0]:
            picked[leg] = (rank, r)
    out = []
    for leg, (_rank, r) in picked.items():
        cfg = _default_cfg(r.get("variant"))
        if desc:
            label = f"{vx.upper()} {desc}"
            if cfg and cfg != desc:
                label += f" · {cfg}"
        else:                       # 没有家族描述时直接用产物配置名
            label = f"{vx.upper()} {cfg}" if cfg else vx.upper()
        out.append(mk(leg, vx, label, r, rel,
                      fee="统一单边 0.10% / 空仓 1.40%", note=note))
    return out


def rows_v13():
    """2026-11-10 取消「文档版 / 回调加仓」两版并列后，jsonl 行不再带 `ver` 字段；
    旧的 `r.get("ver") != "文档版"` 筛选会把全部行丢掉，故已删除。"""
    out = []
    for r in _load_jsonl("v13_quad_9grid/backtest/_v13_quad_9grid_bt.jsonl"):
        leg = r.get("leg")
        if leg not in rule.LEGS:
            continue
        out.append(mk(leg, "v13", "V13 趋势九宫格（回调加仓）", r,
                      "v13_quad_9grid/backtest/_v13_quad_9grid_bt.jsonl",
                      fee="含费（单边 5bp · 空仓货基 1.40%）",
                      note="真实指数收盘（黄金 518880）；许可上限层，不是 0/1 择时"))
    return out


def rows_v12():
    """八态趋势：**七腿全部参与排序**（2026-11-18 执行范围由「只做沪深300」扩到全七腿）。

    产物里 `ann/mdd/sharpe` = v12.1 生效口径（`rule.OPT=True` + `CAP_OPT` + 按腿波动层
    `rule.VOL_LAYER`）, `ann_spec/*_spec` = 规格口径；本清单取**生效口径**（与每日信号同一份代码），
    规格口径见 `v12_octo_trend/backtest/_v12_octo_trend_bt.md` §1b。

    注意两处口径：① `z_th`/`rvol` 仍是规格在沪深300 上的标定值（a500/hs300 有按腿的
    `CAP_OPT`，其余五腿走规格 `CAP`）；② **波动层自 2026-11-19 起按腿不同**
    （a500/hs300/hkt = ER20 自适应 20%/10% + 死区、gold = 固定 TV10 + 死区、
    kc50 = 固定 TV20 + 死区、ndx/spx 不加，见 `v12_octo_trend/research/er_vol_layer/`），
    所以这一行是「同一套八态判定 + 各自的波动层」，不是「同一套完整参数」；
    ③ `expo` 是「平均许可」（带内允许仓位），不是实际成交后的平均仓位 ——
    与 v13 的同名列不完全同义，跨行比仓位时注意。
    """
    out = []
    for r in _load_jsonl("v12_octo_trend/backtest/_v12_octo_trend_bt.jsonl"):
        leg = r.get("leg")
        if leg not in rule.LEGS:
            continue
        out.append(mk(leg, "v12", "V12 八态趋势（许可上限）", r,
                      "v12_octo_trend/backtest/_v12_octo_trend_bt.jsonl",
                      exposure=r.get("expo"), trades=r.get("acts"),
                      fee="含费（单边 5bp · 空仓货基 1.40%）",
                      note="八态 → 带内许可上限；七腿均执行（额度按 Δ夏普排）"))
    return out


def rows_bh():
    """买入持有基准：取 v1_1 产物里的 bh_*（该腿价格的全历史，口径 = 真实指数）。"""
    out = []
    for r in _load_jsonl("v1_price_volume/v1_1_ma20/backtest/_v1_1_ma20_bt.jsonl"):
        leg = r.get("leg")
        if leg not in rule.LEGS:
            continue
        out.append(mk(leg, "BH", "买入持有（基准）", r,
                      "v1_price_volume/v1_1_ma20/backtest/_v1_1_ma20_bt.jsonl",
                      ann=r.get("bh_ann"), mdd=r.get("bh_mdd"), sharpe=r.get("bh_sharpe"),
                      vol=r.get("bh_vol"), exposure=1.0, trades=0,
                      fee="不计费", bench=True,
                      note="这条腿的价格全历史；"
                           "所有时机策略的及格线"))
    return out


def collect():
    rows = []
    for fn in (rows_ladder, rows_bh, rows_v5, rows_v6, rows_v12, rows_v13,
               lambda: rows_simple("v1_price_volume/v1_1_ma20/backtest/_v1_1_ma20_bt.jsonl",
                                   "v1", "V1 MA20 穿越（下车）",
                                   "含费（A 类 0.10/0.50 · C 类 0）",
                                   "走或留：价格站上/跌破 MA20（V1 的默认实现，真实指数口径）"),
               lambda: rows_simple("v2_boll_breakout/backtest/_v2_boll_breakout_bt.jsonl",
                                   "v2", "V2 BOLL(35,2) 上轨突破",
                                   "含费（A 类 0.10/0.50 · C 类 0）",
                                   "上穿买入 / 跌回中轨卖出；换手最低"),
               lambda: rows_simple("v3_macd_boll/backtest/_v3_macd_boll_bt.jsonl",
                                   "v3", "V3 MACD + BOLL 开口突破",
                                   "含费（A 类 0.10/0.50 · C 类 0）",
                                   "MACD 定多空 + 布林开口入场；追涨型"),
               rows_v4,
               lambda: rows_us("v7_us_gate/backtest/_v7_us_gate_bt.jsonl",
                               "v7", "年线闸门",
                               "年线闸门 + 深破 + 波动层，全七腿（标签里的确认期/死区跟着产物名走）"),
               lambda: rows_us("v8_golden_cross/backtest/_v8_golden_cross_bt.jsonl",
                               "v8", "",
                               "金叉 50/200 × 波动层；对照组/基线，全七腿"),
               lambda: rows_us("v9_vol_target/backtest/_v9_vol_target_bt.jsonl",
                               "v9", "波动率目标",
                               "只调风险预算、不判方向；2026-11-18 起接 v10 自适应/v7 年线/v8 金叉"
                               "三个 0-1 开关（标普 010、其余 111）；权重 t+2 生效（比其它变种晚一天）"),
               lambda: rows_us("v10_vol_layer/backtest/_v10_vol_layer_bt.jsonl",
                               "v10", "波动层",
                               "无方向；仓位随 vol20，TV 随 ER20 在 20%/10% 间切换；全七腿")):
        got = fn()
        print(f"  {fn.__name__ if hasattr(fn, '__name__') else 'adapter'}: {len(got)} 行")
        rows.extend(got)
    return rows


# ================================================================ 选优
TOP_N = 3


def pick(rows):
    """-> {leg: dict(rows=..., top=[...], best=..., best_ann=..., max_years=...)}。

    同时给每行回填 `rank`（1 ~ 3 = 该标的前三名，None = 未进排名），
    `_best_by_leg.jsonl` 依赖这个字段做机读筛选。
    """
    out = {}
    for leg in LEGS:
        mine = [r for r in rows if r["leg"] == leg and r["ann"] is not None]
        if not mine:
            continue
        legs_only = [r for r in mine if r["layer"] == "leg"]
        max_years = max((r["years"] or 0.0) for r in legs_only) or 0.0
        starts = [d for d in (r["start"] for r in legs_only) if d]
        earliest = min(starts) if starts else None

        def _gap_years(d):
            if not d or not earliest:
                return None
            try:
                from datetime import date
                return (date.fromisoformat(d[:10]) - date.fromisoformat(earliest[:10])).days / 365.25
            except Exception:  # noqa: BLE001
                return None

        for r in mine:
            gap = _gap_years(r["start"])
            short = (r["years"] or 0.0) < MIN_YEARS
            if gap is not None:
                short = short or gap > MAX_SHORT_GAP
            else:   # 缺 start：退回「样本占最长样本比例」的弱判据
                short = short or (r["years"] or 0.0) < MAX_SHORT_RATIO * max_years
            r["short"] = short
            r["start_gap"] = gap
        eligible = [r for r in mine
                    if r["layer"] == "leg" and not r["short"]
                    and (r["bench"] or (r["trades"] or 0) > 0)]
        if not eligible:
            eligible = [r for r in mine if r["layer"] == "leg" and r["bench"]]
        eligible.sort(key=lambda r: (r["sharpe"] if r["sharpe"] is not None else -99,
                                     r["ann"]), reverse=True)
        for i, r in enumerate(eligible[:TOP_N], 1):
            r["rank"] = i
        ann_sorted = sorted(eligible, key=lambda r: r["ann"], reverse=True)
        out[leg] = dict(rows=sorted(mine, key=lambda r: (r["sharpe"] if r["sharpe"] is not None else -99,
                                                         r["ann"]), reverse=True),
                        top=eligible[:TOP_N],
                        best=eligible[0] if eligible else None,
                        best_ann=ann_sorted[0] if ann_sorted else None,
                        max_years=max_years,
                        bh=next((r for r in mine if r["vx"] == "BH"), None))
    return out


# ================================================================ 渲染
def pct(x, nd=2, sign=False):
    if x is None:
        return "—"
    return (f"{x:+.{nd}%}" if sign else f"{x:.{nd}%}")


def f2(x):
    return "—" if x is None else f"{x:.2f}"


def _vs_bh(r, bh):
    """相对同腿买入持有的年化差（pp/年）文字；基准本身返回「基准」。"""
    if not bh or bh.get("ann") is None or r.get("ann") is None:
        return "—"
    if r.get("bench"):
        return "基准本身"
    return f"{(r['ann'] - bh['ann']) * 100:+.2f}pp/年"


def render(picked, rows):
    gen = None
    try:
        import datetime
        gen = datetime.date.today().isoformat()
    except Exception:  # noqa: BLE001
        gen = "—"

    L = []
    L.append("# 波动资产 · 各标的策略表现与前三名")
    L.append("")
    L.append(f"> 生成 {gen} · 本文件由 `strategies/vol_legs/research/best_by_leg/_best_by_leg.py` "
             "**只读各变种回测产物**汇编，不重跑回测。")
    L.append("> 重跑：`python strategies/vol_legs/research/best_by_leg/_best_by_leg.py`"
             "（任一变种回测产物更新后重跑一次即可）")
    L.append("> 七条腿的定义与执行代码见 `strategies/vol_legs/README.md` §2.1；"
             "本文件 = 该家族**所有变种的横向汇编**（§0 前三名 / §1 全量明细）。模拟结果，非投资建议。")
    L.append("")

    # ---- 一页结论：每标的前三名
    L.append("## 0. 一页结论 · 每只标的的前三名策略")
    L.append("")
    L.append("排序只看**夏普**（风险调整后收益，同分看年化）。进排名的三个条件：样本 ≥ "
             f"{MIN_YEARS:.0f} 年、起点不晚于该腿最早起点 {MAX_SHORT_GAP:.0f} 年、"
             "必须是该腿的仓位规则（`·短样本` / `·股债层` / `·诊断` 一律不参赛）。"
             "**买入持有基准同样参赛** —— 它占一个名次，说明时机策略不值得做。")
    L.append("")
    L.append("> 全部为**含费**口径（各自默认费率，见 §3-②）；「相对买入持有」= 年化差（pp/年）。")
    L.append("")
    L.append("| 标的 | 执行代码 | 名次 | 策略 | 年化 | 最大回撤 | 夏普 | Calmar "
             "| 平均仓位 | 笔数 | 样本 | 相对买入持有 |")
    L.append("|---|---|---|---|---:|---:|---:|---:|---:|---:|---|---:|")
    for leg in LEGS:
        info = picked.get(leg)
        name = rule.LEGS[leg]["name"]
        code = rule.LEGS[leg]["exec"]
        if not info or not info["top"]:
            L.append(f"| **{name}** | `{code}` | — | 无可用产物 | — | — | — | — | — | — | — | — |")
            continue
        bh = info["bh"]
        for i, r in enumerate(info["top"], 1):
            expo = "—" if r["exposure"] is None else f"{r['exposure']:.0%}"
            trades = "—" if r["trades"] is None else str(r["trades"])
            yrs = "—" if r["years"] is None else f"{r['years']:.1f}y"
            L.append(f"| **{name}** | `{code}` | **★{i}** | {r['label']} | {pct(r['ann'])} "
                     f"| {pct(r['mdd'])} | **{f2(r['sharpe'])}** | {f2(r['calmar'])} | {expo} "
                     f"| {trades} | {yrs} | {_vs_bh(r, bh)} |")
    L.append("")
    L.append("**及格线：买入持有（同腿价格全历史，不计费）**")
    L.append("")
    L.append("| 标的 | 年化 | 最大回撤 | 夏普 | Calmar | 样本 |")
    L.append("|---|---:|---:|---:|---:|---|")
    for leg in LEGS:
        bh = (picked.get(leg) or {}).get("bh")
        if not bh:
            continue
        yrs = "—" if bh["years"] is None else f"{bh['years']:.1f}y"
        L.append(f"| {rule.LEGS[leg]['name']} | {pct(bh['ann'])} | {pct(bh['mdd'])} "
                 f"| {f2(bh['sharpe'])} | {f2(bh['calmar'])} | {yrs} |")
    L.append("")
    L.append("> 落在及格线以下的择时策略（相对买入持有为负）**不如一直拿着**。")
    L.append(">")
    L.append("> 怎么用：只做一支 → 取 ★1；要收益 → 往后翻，年化最高的常常在 ★2/★3（代价是回撤翻倍）；"
             "要控回撤 → 直接挑回撤最浅的那支，名次不重要。")
    L.append("")

    # ---- 各腿最高年化与夏普最优不一致的提示
    diff = [leg for leg in LEGS
            if picked.get(leg) and picked[leg]["best"] and picked[leg]["best_ann"]
            and picked[leg]["best"]["vx"] != picked[leg]["best_ann"]["vx"]]
    if diff:
        L.append("**夏普最优 ≠ 收益最高**（换收益要付回撤代价，按自己的目标取）：")
        for leg in diff:
            i = picked[leg]
            top = i["best_ann"]
            rk = f"，前三第 {top['rank']}" if top.get("rank") else "，未进前三"
            L.append(f"- {rule.LEGS[leg]['name']}：夏普最优 **{i['best']['label']}**"
                     f"（{pct(i['best']['ann'])} / {pct(i['best']['mdd'])} / {f2(i['best']['sharpe'])}）"
                     f" vs 收益最高 **{top['label']}**"
                     f"（{pct(top['ann'])} / {pct(top['mdd'])} / {f2(top['sharpe'])}{rk}）")
        L.append("")

    # ---- 分标的明细
    L.append("## 1. 分标的候选明细")
    L.append("")
    L.append(f"`名次` = 该标的前三名（夏普，且样本 ≥ {MIN_YEARS:.0f} 年、起点不晚于该腿最早起点 "
             f"{MAX_SHORT_GAP:.0f} 年）；`↑` = 年化最高；`·短样本` = 样本不够只列不选；"
             "`·诊断` = 该变种没在这条腿上标定阈值（不参赛）；`·股债层` / `·同窗口基准` 同理。"
             "**本表全部为含费口径**（零费版见各产物 `ann` / `sharpe` 字段）。")
    L.append("")
    for leg in LEGS:
        info = picked.get(leg)
        if not info:
            continue
        L.append(f"### {rule.LEGS[leg]['name']}（`{rule.LEGS[leg]['exec']}` · "
                 f"{rule.LEGS[leg]['role']} · 带 {rule.LEGS[leg]['band']:g}pp）")
        L.append("")
        L.append("| 策略 | 名次 | 年化 | 最大回撤 | 夏普 | Calmar | 波动 | 平均仓位 | 笔数 | 样本 "
                 "| 费率口径 | 出处 |")
        L.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|")
        for r in info["rows"]:
            mark = ""
            if info["best_ann"] is not None and r is info["best_ann"]:
                mark += " ↑"
            if r["short"]:
                mark += " ·短样本"
            if r["layer"] == "股债":
                mark += " ·股债层"
            elif r["layer"] == "诊断":
                mark += " ·诊断"
            elif r["layer"] == "基准对照":
                mark += " ·同窗口基准"
            rank = f"**{r['rank']}**" if r.get("rank") else "—"
            trades = "—" if r["trades"] is None else str(r["trades"])
            yrs = "—" if r["years"] is None else f"{r['years']:.1f}y"
            expo = "—" if r["exposure"] is None else f"{r['exposure']:.0%}"
            L.append(f"| {r['label']}{mark} | {rank} | {pct(r['ann'])} | {pct(r['mdd'])} "
                     f"| {f2(r['sharpe'])} "
                     f"| {f2(r['calmar'])} | {pct(r['vol'])} | {expo} "
                     f"| {trades} | {yrs} | {r['fee']} | `{r['src']}` |")
        L.append("")

    # ---- 未纳入
    L.append("## 2. 没有进清单的策略及原因")
    L.append("")
    L.append("| 变种 | 为什么不参与排序 |")
    L.append("|---|---|")
    L.append("| `v11_support_pressure` 支撑/压力位 | **事件研究，不是仓位规则**：按文档 §4.3 只报信号日后 "
             "20/60/120 日前向收益，刻意不报年化/回撤/夏普（不是漏写） |")
    L.append("| `v11_support_pressure/gate/` 支撑位闸门 | 回测后**判定不上线**（2026-11-16 真实指数长库复算）："
             "撤掉支撑层（纯趋势 MA200 月频）年化 +10.54% / 回撤 -12.39%，装上后 +4.83% / -10.50%（-5.71pp）"
             "且动作 8.9 → 66.8 次/年 —— 支撑层是纯负贡献，不构成候选 |")
    L.append("| `v4_erp_band` ERP 股债通道 | **层次不同**：它管「沪深300 与国债之间摆 0/100」，"
             "不是沪深300 这条腿的择时，故列在明细表里但不参与选优（带 `·股债层` 标记） |")
    L.append("| `v13` 的部分腿 | 真实指数可用数据起点晚：科创50 / 恒生科技的 V13 只有约 5 年"
             "（A500 / 沪深300 自 2006-03 起为长样本）。短样本一律标 `·短样本`，只列不选 |")
    L.append("| `v12_octo_trend` 的阈值出处 | 2026-11-18 起**七腿全部参与排序**（执行范围由「只做沪深300」扩到"
             "全七腿，价格与量能都是同源真实指数）。但 `z_th` / `rvol` 仍是规格在沪深300 上的标定值 —— "
             "两折样本外检验里七腿无一能稳定赢过原值（`v12_octo_trend/research/opt_7legs`），"
             "所以**八态判定**是「同一套规则铺到七腿」，不是各自标定的规则。"
             "2026-11-19 起**波动层按腿分开**（`rule.VOL_LAYER`，见 `research/er_vol_layer/`），"
             "所以 V12 这一行已经不是「完整一套参数」—— 它是「同一套八态 + 各自的波动层」。"
             "七腿 Δ夏普全为正但差距大（科创50 最高、纳指最低 ±0.05 量级），"
             "排序每次重跑都可能微调，拿不准就看 v12 回测 §1c 的当期排序 |")
    L.append("| `v6_gold/research/b_gate_ma` B 加均线闸门 | 实测**更差**（2026-11-09）：跌破 MA250 减到 20% "
             "= +8.75% / -23.50% / 夏普 0.62、76 次调仓（B 本身 +11.60% / -22.00% / 0.77、10 次），"
             "回撤反而深 1.5pp —— 研究结论，不进候选 |")
    L.append("| `v4_erp_band/research/stop_overlay` V4 加止损 | 研究结论：`ma` 解冻 10 档全部不过卡玛基准，"
             "`erp` 解冻能把回撤 -38.96% → -21.41% 但年化 -4.28pp（等于换成更保守的产品），"
             "**建议保持关闭**（`atr_stop.CFG['enabled']=False`），不进候选 |")
    L.append("| `v21_livermore_keypoint` 利弗莫尔关键点 | **按用户 2026-11-19 指令整支不进本清单**："
             "规则未稳定（沪深300 只有 2 笔建仓、黄金腿 12 年只触发 1 次、科创50/恒生科技为负贡献），"
             "单腿只有纳指 0.75 略高于 V10 的 0.73、回撤却深 8pp。研究与产物留在它自己目录"
             "（`v21_livermore_keypoint/backtest/_v21_7legs.md`），不参与 ★1~★3 排名 |")
    L.append("| 主策略的科创50 / 恒生科技 / 黄金腿 | 主策略只有组合层复权净值，"
             "**没有这三条腿的独立单腿产物**（`_vol_legs_bt.json::singles` 只有 4 条腿），"
             "故主策略在这三条腿上无法与变种同尺比较 |")
    L.append("")

    # ---- 口径
    L.append("## 3. 口径与维护")
    L.append("")
    L.append("1. **价格口径分三组**：V1 / V2 / V3 / V7~V10 / V12 / V13 / 主策略 = 真实指数"
             "（六条指数腿走真实指数，黄金走场内 518880）；V4 / V6 = 各自固定口径"
             "（V4 沪深300 指数 × ERP，V6 场内 518880）。"
             "跨口径比较时先看「费率口径」列，别把两组的年化直接相减。")
    L.append("2. **费率一律取含费**：V1 / V2 / V3 的产物把零费当主列（`ann` / `sharpe`）、"
             "含费放在 `ann_fee` / `sharpe_fee`，本清单**取含费**（A 类腿 0.10% 买 + 0.50% 卖）；"
             "V6 / V7~V12 / V13 / 主策略的默认口径本身含费；V4 含月末回锚费用。"
             "差异极大，别忽略：A 类腿一年 12~15 笔、单笔往返 0.6%，一年就吃掉 7~9pp，"
             "能把跑赢买入持有的腿直接打成负的（逐腿「年化拖累」见 v1 报告的「成本」表）。"
             "**但「都含费」≠「费率相同」**：V13 单边 5bp、V7~V10 单边 10bp、A 类往返 60bp、"
             "黄金场内佣金+滑点约 16bp 往返。2026-11-17 复核（换手线性外推、夏普 ∝（年化−货基）/波动）："
             "**七条腿里只有标普500 的第一名对费率敏感** —— V13 在自身 5bp 下 0.590，按 10bp 重算 0.578，"
             "仍高于 V9 的 0.529 / V7 的 0.529；但按 A 类往返 60bp 外推只剩 ≈0.46，"
             "与 V7 的 0.45~0.49 落在同一档 —— **这一格在真收费口径下不值得挑**（V13 赚的就是低换手）。"
             "其余六条腿的第一名与第二名的差距都 ≥0.05 且第一名本身不是 5bp 口径（黄金的 V13 只排第 2），"
             "所以费率口径改不了它们的结论。")
    L.append("3. **样本必须看**：`规则信号用全历史算、统计窗口另截` 是仓库惯例，"
             "各行 `start`/`end` 差异很大（科创50/恒生科技约 5~6 年，"
             "A500/沪深300/美股约 20~22 年，黄金约 12 年；V12 的科创50/恒生科技自 2021 起、"
             "黄金自 2014-09 起，与 V13 同量级）。年化差 ±3pp 内属抽样噪声；"
             "V13 普遍晚入市约 1 年，避开 2005~2006 段，跨策略夏普不宜当作同窗口精确对账。")
    L.append("4. **A500 与沪深300 不是两份独立证据**：现在是两条独立指数序列"
             "（`SH000510` vs `SH000300`，无桥接段），但成分重叠约八成、结论高度相关，"
             "别当成两个互不相干的市场来加总（见 `README.md` §2.1）。")
    L.append("5. **维护方式**：某变种重跑回测 → 重跑本脚本 → 清单自动更新（含 §0 前三名）。"
             "新增变种时只需在 `_best_by_leg.py` 的 `collect()` 里加一个适配器"
             "（取它的 `backtest/_*_bt.jsonl` 并筛出默认口径那一行；非执行腿给 `layer=\"诊断\"`），"
             "同时在本文件 §2 说明它是否参与排序。")
    L.append("6. **名次会随产物变动**：排名按夏普，样本/费率一改就可能换位（±0.05 夏普内的差别等于噪声）。"
             "要机读就把 `_best_by_leg.jsonl` 的 `rank` 字段当索引用，别在别处硬写策略名。")
    L.append("7. **美股两腿的指数价还要再打一次折**：表里是 `.NDX`/`.INX` 指数收盘，实盘走场外 QDII A 类"
             "（`270042`/`017641`），要再减约 **1.3pp/年**（分红漏损 + 汇率 + 跟踪误差）。"
             "这条对**所有变种同等打折**，所以名次不变；但拿「场内 513100/513500」的实盘去对表时，"
             "别忘了两边差的就是这 1.3pp。")
    L.append("8. **结算口径的唯一差异**：V9 的权重自 **t+2** 起承担收益（决策日收盘 → 次日收盘成交 → 再隔一日），"
             "其余变种是 t+1。同一条规则 V9 会少赚一点、也更保守 —— 0.05 以内的夏普差别当胜负。")
    L.append("9. **每日流程里的位置**：本清单是**横向决策参考**，不是每日信号。"
             "每日动作仍由 `run_daily_vol_legs.ps1` 产出（主策略七腿 + 变种层 v2/v3/v4/v6/v9/v12）。")
    L.append("")
    return "\n".join(L) + "\n"


def main():
    print("读取各变种回测产物 …")
    rows = collect()
    print(f"共 {len(rows)} 行（标的 × 策略）")
    picked = pick(rows)
    md = render(picked, rows)
    with io.open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(md)
    with io.open(OUT_JSONL, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[ok] {OUT_MD}")
    print(f"[ok] {OUT_JSONL}")
    print("")
    print("每标的前三名（夏普）：")
    for leg in LEGS:
        info = picked.get(leg)
        if not info or not info["top"]:
            print(f"  {rule.LEGS[leg]['name']:<5} —")
            continue
        print(f"  {rule.LEGS[leg]['name']}")
        for i, r in enumerate(info["top"], 1):
            yrs = f"{r['years']:.1f}y" if r["years"] is not None else "—"
            print(f"    ★{i} {r['label']:<34} "
                  f"年化 {r['ann']:+.2%} · 回撤 {r['mdd']:.2%} · 夏普 {r['sharpe']:.2f} · {yrs}")


if __name__ == "__main__":
    main()
