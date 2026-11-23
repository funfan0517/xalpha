# -*- coding: utf-8 -*-
"""波动资产 · **近三年窗口**下各标的的「策略表现 × 前三名」清单。

与同目录 `_best_by_leg.py` 的分工：

| | 窗口 | 数据来源 | 用途 |
|---|---|---|---|
| `_best_by_leg.py` | 各自产物全历史 | 只读 `backtest/_*_bt.jsonl` | 长期结论 |
| **本脚本** | **统一近三年** | **调各变种原生引擎重算 nav 再切窗** | 近期有效性 |

为什么要重算而不是读产物：各变种产物只落**汇总指标**（年化/回撤/夏普），
没有净值序列，无法从 20 年汇总里扣掉 3 年。所以本脚本按各变种 `backtest.py`
的主循环口径**重算全历史单腿净值**，再统一截到近三年窗口重算指标。

## 口径（与全历史榜一致，不是「统一费率」）

- **费率沿用各自原生口径**：v1/v2/v3 用逐腿 `FEE`（A 类 0.10/0.50，C 类 0）、
  v5 万 2.5 单边、v6 佣金万 0.3 + 滑点 5bp、v7~v10 单边 10bp、v12/v13 单边 5bp。
  报告每行都带「费率口径」列 —— 跨行比年化前先看它，别把 5bp 与往返 60bp 直接相减。
- **滞后沿用各自原生**：v7/v8/v9/v10 是 T+2（state 内 shift 1 + run_leg 再 shift 1），
  其余 T+1。切窗时按各变种自己的净值曲线截，不额外对齐。
- **信号在全历史算、统计只截窗口**：窗口起点前一日作为净值基点（`nav/nav[base]`），
  否则窗口首日收益会被吞掉；也避免窗口头重放一次冷启动（v5 的 0.40 起手档、
  v12/v13 的预热段都在窗口之前完成）。
- **窗口**：全局最新交易日前推 3 个日历年。各腿实际起止略有差异（休市日不同），逐行标注。

## 不参与排名

- `v4_erp_band`：股债 0/100 摆仓，不是沪深300 的单腿择时（与全历史榜同判）。
- 主策略回撤阶梯：`_vol_legs_bt.json::singles` 只有 4 条腿，且是组合层资产配置口径，
  与「单腿择时」不同层，本清单不列。
- `v21_livermore_keypoint`：按用户 2026-11-19 指令整支不进榜单。

产出（本目录）：
- `_best_by_leg_3y.md`    近三年榜（一页前三名 + 分标的全量明细 + 与全历史榜的差异）
- `_best_by_leg_3y.jsonl` 机读结果（每行 = 一个 标的×策略×窗口）

跑一次：`python strategies/vol_legs/research/best_by_leg/_best_by_leg_3y.py`

> **5 年窗口版**：`research/best_by_leg_5y/_best_by_leg_5y.py` 是薄壳 —— import 本模块后
> 覆盖 `WINDOW_YEARS` / `WINDOW_WORD` / `SE_TXT` / `MARKET_NOTE` / `SELF_PY` / `OUT_*`
> 再调 `main()`。逻辑只有这一份，两个窗口不会各跑偏一套。

> 模拟结果，非投资建议。3 年窗口只覆盖一轮半牛熊，夏普的标准误约 ±0.3，
> **±0.05 的差距等于噪声**，别把排名当精确结论。
"""
import contextlib
import io
import json
import math
import os
import sys
import traceback
from datetime import date

import pandas as pd

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

import rule  # noqa: E402  （腿名 / 执行代码 / 目标带）

OUT_MD = os.path.join(_HERE, "_best_by_leg_3y.md")
OUT_JSONL = os.path.join(_HERE, "_best_by_leg_3y.jsonl")
FULL_JSONL = os.path.join(_HERE, "_best_by_leg.jsonl")   # 全历史榜，用于「★1 是否换人」

LEGS = rule.LEG_ORDER
# ---------------------------------------------------------------- 窗口参数（可被子脚本覆盖）
# `research/best_by_leg_5y/_best_by_leg_5y.py` 是个薄壳：import 本模块后把这些全局量
# 改成 5 年再调 `main()`。逻辑只有这一份，两个窗口不会各跑偏一套。
WINDOW_YEARS = 3
WINDOW_WORD = "近三年"                 # 叙述用（「近三年谁最好」）
SE_TXT = "0.3"                         # 夏普标准误的量级（3 年 ≈ 0.3，5 年 ≈ 0.22）
SELF_PY = "strategies/vol_legs/research/best_by_leg/_best_by_leg_3y.py"
MARKET_NOTE = ("只覆盖 2023 年末以来的一段行情（含 A 股 2024 年初低点与"
               "2024-09 后的反弹、美股连涨、黄金大涨）")
SIBLING_NOTE = ("另一个窗口的同一份榜单：`research/best_by_leg_5y/_best_by_leg_5y.md`"
                "（近五年，含 2022 年熊市）—— 窗口不同结论可能相反，两份对照看。")
TD = 252
CASH_ANNUAL = float(getattr(rule, "CASH_ANNUAL", 0.014))
TOP_N = 3

# 各变种自己的目录（import 隔离用）
_VAR_DIRS = {
    "v1": "v1_price_volume/v1_1_ma20",
    "v2": "v2_boll_breakout",
    "v3": "v3_macd_boll",
    "v5": "v5_gold_score",
    "v6": "v6_gold",
    "v7": "v7_us_gate",
    "v8": "v8_golden_cross",
    "v9": "v9_vol_target",
    "v10": "v10_vol_layer",
    "v12": "v12_octo_trend",
    "v13": "v13_quad_9grid",
}

# v14~v21 自带 engine.py / data.py，会污染 `import engine` / `import data` 的解析
_SHADOW = {os.path.join(_VL, d) for d in (
    "v14_a500_e2", "v15_hs300_min2", "v16_ndx_sw", "v17_spx_v12",
    "v18_kc50_e2tv", "v19_hkt_v3", "v20_gold_v5", "v21_livermore_keypoint")}

_NAMES = ("data", "rule", "engine", "ma20", "boll", "macd_boll", "factors", "score",
          "gold", "us_gate", "golden_cross", "vol_target", "vol_layer")


@contextlib.contextmanager
def _isolated(sub):
    """隔离 sys.path / sys.modules，避免仓库里 5 个 data.py、多个 rule.py 串味。

    与 `_top3_bridge.isolated` 同范式（这里自带一份，避免 import 它时顺带实例化整套桥）。
    """
    d = os.path.join(_VL, sub.replace("/", os.sep))
    old_path = sys.path[:]
    saved = {n: sys.modules.pop(n) for n in _NAMES if n in sys.modules}
    shadow = {os.path.abspath(p) for p in _SHADOW}
    sys.path[:] = [p for p in sys.path
                   if os.path.abspath(p) not in shadow
                   and os.path.abspath(p) != os.path.abspath(d)]
    sys.path.insert(0, d)
    if _VL not in sys.path:          # 兜底：`import rule` 落到 vol_legs/rule.py
        sys.path.append(_VL)
    try:
        yield d
    finally:
        sys.path[:] = old_path
        for n in _NAMES:
            sys.modules.pop(n, None)
        sys.modules.update(saved)


# ================================================================ 指标
def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def perf(nav):
    """窗口内指标：年化 / 波动 / 回撤 / 夏普（超额货基）/ Calmar / 累计。"""
    nav = nav.dropna()
    if len(nav) < 3:
        return dict(ann=None, vol=None, mdd=None, sharpe=None, calmar=None, cum=None)
    r = nav.pct_change().dropna()
    n = len(r)
    ann = float(nav.iloc[-1] ** (TD / n) - 1.0)
    vol = float(r.std() * math.sqrt(TD))
    mdd = float((nav / nav.cummax() - 1.0).min())
    return dict(ann=ann, vol=vol, mdd=mdd, cum=float(nav.iloc[-1] - 1.0),
                sharpe=(ann - CASH_ANNUAL) / vol if vol > 0 else None,
                calmar=ann / abs(mdd) if mdd < 0 else None)


# ================================================================ 各变种重建 nav
def _v1(leg):
    import ma20
    px_full = ma20.load(leg)
    sig_full, ma_full = ma20.signal(px_full)
    start = ma_full.first_valid_index()
    px, sig = px_full.loc[start:], sig_full.loc[start:]
    fee = ma20.FEE[leg]
    return px, sig, ma20.run(px, sig, fee=fee), \
        f"含费（申购 {fee[0]:.2%} / 赎回 {fee[1]:.2%}）"


def _v2(leg):
    import boll
    px_full = boll.load(leg)
    sig_full, mid_full, _up = boll.signal(px_full)
    start = mid_full.first_valid_index()
    px, sig = px_full.loc[start:], sig_full.loc[start:]
    w = boll.weight(px, sig)
    fee = boll.FEE[leg]
    return px, w, boll.run(px, w, fee=fee), \
        f"含费（申购 {fee[0]:.2%} / 赎回 {fee[1]:.2%}）"


def _v3(leg):
    import macd_boll as mb
    px_full = mb.load(leg)
    sig_full = mb.signal(px_full)
    w_full = mb.weight(px_full, sig_full)
    start = px_full.index[min(mb.WARMUP, max(len(px_full) - 2, 0))]
    px, w = px_full.loc[start:], w_full.loc[start:]
    fee = mb.FEE[leg]
    return px, w, mb.run(px, w, fee=fee), \
        f"含费（申购 {fee[0]:.2%} / 赎回 {fee[1]:.2%}）"


def _v5(leg):
    """只有黄金；状态机依赖 FRED 实际利率 / 资金流缓存（读本地，不联网）。"""
    import score as S
    d = S.build(refresh=False)
    px_all, df, p, start = d["px"], d["df"], d["params"], d["start"]
    px = px_all["close"]
    st = df["state"].loc[start:]
    nav = S.run_leg(st, px.loc[start:], fee=p["FEE"], slip=p["SLIP"])
    return px.loc[start:], st, nav, \
        f"含费（场内万 {p['FEE'] * 1e4:.2f} 单边 · 空仓货基 1.40%）"


def _v6(leg):
    """黄金三口径 A/B/C（同一产物里三个起点，逐个返回）。"""
    import gold as G
    d = G.build(refresh=False)
    px_all, p = d["px"], d["params"]
    fee, slip = G.fee_of(p)
    out = {}
    for key in ("A", "B", "C"):
        st_full = d[key]["state"] if key != "C" else d["C"]
        s0 = d["start"][key]
        px, st = px_all.loc[s0:], st_full.loc[s0:]
        out[key] = (px, st, G.run_leg(st, px, fee=fee, slip=slip))
    txt = f"含费（佣金 {fee[0]:.2%}/{fee[1]:.2%} + 滑点 {slip:.2%}）"
    return out, txt


def _v12(leg):
    import data as vdata
    import engine
    r = engine.backtest_leg(leg, df=vdata.frame(leg))
    if r.get("nav") is None or r.get("w") is None:
        return None
    return r["df"]["close"], r["w"], r["nav"], "含费（单边 5bp · 空仓货基 1.40%）"


def _v13(leg):
    import data as vdata
    import engine
    r = engine.backtest_leg(leg, df=vdata.frame(leg), callback_add=None)
    if r.get("nav") is None or r.get("w") is None:
        return None
    return r["df"]["close"], r["w"], r["nav"], "含费（单边 5bp · 空仓货基 1.40%）"


def _collect_one(vx, leg):
    """-> (px, state, nav, fee_txt) 或 None。全部在各自隔离上下文里跑。"""
    if vx == "v5" and leg != "gold":
        return None
    if vx == "v6" and leg != "gold":
        return None
    with _isolated(_VAR_DIRS[vx]):
        if vx == "v1":
            return _v1(leg)
        if vx == "v2":
            return _v2(leg)
        if vx == "v3":
            return _v3(leg)
        if vx == "v5":
            return _v5(leg)
        if vx == "v7":
            import us_gate as ug
            nav, st, _dec = ug.build(leg)
            return nav, st, ug.run_leg(st, nav), "含费（单边 10bp · 空仓货基 1.40%）"
        if vx == "v8":
            import golden_cross as gc
            nav, st, _dec = gc.build(leg)
            return nav, st, gc.run_leg(st, nav), "含费（单边 10bp · 空仓货基 1.40%）"
        if vx == "v9":
            import vol_target as vt
            nav, st, _dec = vt.build(leg)
            return nav, st, vt.run_leg(st, nav), "含费（单边 10bp · 空仓货基 1.40%）"
        if vx == "v10":
            import vol_layer as vl
            nav, st, _dec = vl.build(leg)
            return nav, st, vl.run_leg(st, nav), "含费（单边 10bp · 空仓货基 1.40%）"
        if vx == "v12":
            return _v12(leg)
        if vx == "v13":
            return _v13(leg)
    raise ValueError(vx)


def collect():
    """-> rows（每个 变种×腿 一行，含全历史 nav/state 供后面切窗）+ 失败清单。"""
    rows, fails = [], []
    for vx in ("v1", "v2", "v3", "v5", "v6", "v7", "v8", "v9", "v10", "v12", "v13"):
        for leg in LEGS:
            if vx in ("v5", "v6") and leg != "gold":
                continue
            try:
                if vx == "v6":          # 一个产物三个口径，拆成三行
                    with _isolated(_VAR_DIRS[vx]):
                        got, txt = _v6(leg)
                    for key, (px, st, nv) in got.items():
                        rows.append(dict(
                            vx=f"v6{key}",
                            label=f"V6-{key} " + {"A": "A 长期配置", "B": "B 控回撤",
                                                   "C": "C 占坑"}[key],
                            leg=leg, px=px, state=st, nav=nv, fee=txt))
                    print(f"  v6 × {leg}: A/B/C")
                    continue
                res = _collect_one(vx, leg)
                if res is None:
                    fails.append((vx, leg, "该腿无可用产物（预热不足）"))
                    continue
                px, st, nv, txt = res
                rows.append(dict(vx=vx, label=label_of(vx), leg=leg,
                                 px=px, state=st, nav=nv, fee=txt))
                print(f"  {vx} × {leg}: ok")
            except Exception as exc:  # noqa: BLE001
                fails.append((vx, leg, f"{type(exc).__name__}: {exc}"))
                print(f"  [FAIL] {vx} × {leg}: {type(exc).__name__}: {exc}")
                traceback.print_exc(limit=2)
    return rows, fails


LABELS = {
    "v1": "V1 MA20 穿越（下车）",
    "v2": "V2 BOLL(35,2) 上轨突破",
    "v3": "V3 MACD + BOLL 开口突破",
    "v5": "V5.1 黄金仓位（风险预算 × 利率闸门）",
    "v7": "V7 年线闸门",
    "v8": "V8 金叉 × 波动层",
    "v9": "V9 波动率目标",
    "v10": "V10 波动层",
    "v12": "V12 八态趋势（许可上限）",
    "v13": "V13 趋势九宫格",
    "BH": "买入持有（基准）",
}


def label_of(vx):
    return LABELS.get(vx, vx.upper())


# ================================================================ 切窗 + 选优
def window_rows(rows):
    """把每行 nav/state 截到 `WINDOW_YEARS` 窗口，重算指标并回填。"""
    ends = [r["nav"].index[-1] for r in rows if r["nav"] is not None and len(r["nav"])]
    if not ends:
        raise SystemExit("没有可用的净值序列")
    end = max(ends)
    w0 = (pd.Timestamp(end) - pd.DateOffset(years=WINDOW_YEARS)).normalize()

    out, skipped = [], []
    for r in rows:
        nav, st, px = r["nav"], r["state"], r["px"]
        if nav is None or len(nav) == 0:
            skipped.append((r["vx"], r["leg"], "净值序列为空"))
            continue
        nav = nav.dropna()
        before = nav.index[nav.index <= w0]
        if len(before) == 0:
            skipped.append((r["vx"], r["leg"], f"样本不足 {WINDOW_YEARS} 年"))
            continue
        base = before[-1]
        seg = (nav.loc[base:] / nav.loc[base]).dropna()
        m = perf(seg)

        hold = st.reindex(seg.index).ffill().fillna(0.0).shift(1).fillna(0.0)
        expo = float(hold.mean()) if len(hold) else None
        acts = int((st.reindex(seg.index).ffill().fillna(0.0).diff().abs() > 1e-9).sum())

        # 买入持有同窗口（同一价格序列）
        bh = None
        if px is not None:
            pxs = px.loc[base:] if base in px.index else px.loc[px.index[px.index >= base]]
            if len(pxs) > 1:
                bh = perf(pxs / pxs.iloc[0])

        out.append(dict(
            leg=r["leg"], leg_name=rule.LEGS[r["leg"]]["name"], vx=r["vx"],
            label=r["label"], ann=m["ann"], mdd=m["mdd"], sharpe=m["sharpe"],
            calmar=m["calmar"], vol=m["vol"], exposure=expo, acts=acts,
            start=str(seg.index[0].date()), end=str(seg.index[-1].date()),
            bars=int(len(seg)), fee=r["fee"],
            bh_ann=(bh or {}).get("ann"), bh_sharpe=(bh or {}).get("sharpe"),
            bh_mdd=(bh or {}).get("mdd"), rank=None, short=False,
        ))
    return out, skipped, str(w0.date()), str(end.date())


def add_bh(rows):
    """每条腿补一行买入持有（同窗口、不计费）。"""
    bh = {}
    for r in rows:
        if r["leg"] not in bh and r["bh_ann"] is not None:
            bh[r["leg"]] = dict(
                leg=r["leg"], leg_name=r["leg_name"], vx="BH", label=LABELS["BH"],
                ann=r["bh_ann"], mdd=r["bh_mdd"], sharpe=r["bh_sharpe"],
                calmar=(r["bh_ann"] / abs(r["bh_mdd"])
                        if r["bh_mdd"] and r["bh_mdd"] < 0 else None),
                vol=None, exposure=1.0, acts=0, start=r["start"], end=r["end"],
                bars=r["bars"], fee="不计费", bh_ann=r["bh_ann"],
                bh_sharpe=r["bh_sharpe"], bh_mdd=r["bh_mdd"], rank=None, short=False)
    out = list(rows)
    for leg in LEGS:
        if leg in bh:
            out.append(bh[leg])
    return out


def pick(rows):
    """每腿按夏普排前三；`rank` 回填到行上。"""
    picked = {}
    for leg in LEGS:
        mine = [r for r in rows if r["leg"] == leg and r["ann"] is not None]
        if not mine:
            continue
        elig = [r for r in mine if r["sharpe"] is not None]
        elig.sort(key=lambda r: (r["sharpe"], r["ann"]), reverse=True)
        for i, r in enumerate(elig[:TOP_N], 1):
            r["rank"] = i
        picked[leg] = dict(
            rows=sorted(mine, key=lambda r: (r["sharpe"] if r["sharpe"] is not None else -99,
                                             r["ann"]), reverse=True),
            top=elig[:TOP_N],
            best=elig[0] if elig else None,
            best_ann=max(elig, key=lambda r: r["ann"]) if elig else None)
    return picked


def load_full_ranks():
    """全历史榜的 ★1~★3：`{(leg, vx): dict(rank, label)}`，用于「是否换人」与「双榜稳」."""
    out = {}
    if not os.path.exists(FULL_JSONL):
        print(f"  [WARN] 缺全历史榜: {FULL_JSONL}")
        return out
    with io.open(FULL_JSONL, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            rk = r.get("rank")
            if rk in (1, 2, 3):
                out[(r["leg"], r.get("vx"))] = dict(rank=rk, label=r.get("label"))
    return out


def star1_of(full):
    return {leg: (vx, d["label"]) for (leg, vx), d in full.items() if d["rank"] == 1}


def stable_both(full, picked):
    """两榜都进前三的变种：-> [(vx, [腿…])]，按腿数降序。"""
    cnt = {}
    for leg in LEGS:
        mine = {r["vx"]: r["rank"] for r in (picked.get(leg) or {}).get("rows", [])}
        for (lg, vx), d in full.items():
            if lg != leg or d["rank"] > 3:
                continue
            if mine.get(vx) and mine[vx] <= 3:
                cnt.setdefault(vx, []).append(leg)
    return sorted(cnt.items(), key=lambda kv: (-len(kv[1]), kv[0]))


# ================================================================ 渲染
def pct(x, nd=2, sign=False):
    if x is None:
        return "—"
    return (f"{x:+.{nd}%}" if sign else f"{x:.{nd}%}")


def f2(x):
    return "—" if x is None else f"{x:.2f}"


def render(picked, rows, w0, end, fails, full):
    gen = date.today().isoformat()
    star1 = star1_of(full)
    L = []
    L.append(f"# 波动资产 · {WINDOW_WORD}窗口下各标的的策略表现与前三名")
    L.append("")
    L.append(f"> 生成 {gen} · 窗口 **{w0} ~ {end}**（{WINDOW_YEARS} 个日历年）· 由 "
             f"`{SELF_PY}` 生成。")
    L.append("> 全历史口径的榜单见 `research/best_by_leg/_best_by_leg.md`；本文件只回答"
             f"「**同样这几支策略，{WINDOW_WORD}谁最好**」。")
    L.append(f"> 重跑：`python {SELF_PY}`。模拟结果，非投资建议。")
    if SIBLING_NOTE:
        L.append(f"> {SIBLING_NOTE}")
    L.append("")
    L.append("**怎么读**：排序只看**夏普**（同分看年化）；买入持有同样参赛。"
             f"{WINDOW_YEARS} 年样本的夏普标准误约 ±{SE_TXT}，**±0.05 的差距等于噪声**，"
             f"只把它当「{WINDOW_WORD}谁在赚钱」的方向判断，别当精确名次。")
    L.append("")

    # ---- 一页结论
    L.append(f"## 0. 一页结论 · {WINDOW_WORD}每只标的的前三名")
    L.append("")
    L.append("| 标的 | 执行代码 | 名次 | 策略 | 年化 | 最大回撤 | 夏普 | Calmar "
             "| 平均仓位 | 调仓次数 | 相对买入持有 | 全历史★1 |")
    L.append("|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for leg in LEGS:
        info = picked.get(leg)
        name = rule.LEGS[leg]["name"]
        code = rule.LEGS[leg]["exec"]
        old = star1.get(leg)
        old_txt = f"{old[1]}" if old and old[1] else "—"
        if not info or not info["top"]:
            L.append(f"| **{name}** | `{code}` | — | 无可用结果 | — | — | — | — | — | — | — | {old_txt} |")
            continue
        bh_ann = next((r["bh_ann"] for r in info["rows"] if r["vx"] == "BH"), None)
        for i, r in enumerate(info["top"], 1):
            expo = "—" if r["exposure"] is None else f"{r['exposure']:.0%}"
            rel = ("基准本身" if r["vx"] == "BH" else
                   (f"{(r['ann'] - bh_ann) * 100:+.2f}pp/年" if bh_ann is not None else "—"))
            L.append(f"| **{name}** | `{code}` | **★{i}** | {r['label']} | {pct(r['ann'])} "
                     f"| {pct(r['mdd'])} | **{f2(r['sharpe'])}** | {f2(r['calmar'])} "
                     f"| {expo} | {r['acts']} | {rel} | {old_txt} |")
    L.append("")
    L.append(f"**及格线：{WINDOW_WORD}买入持有（同窗口，不计费）**")
    L.append("")
    L.append("| 标的 | 年化 | 最大回撤 | 夏普 | Calmar | 区间 |")
    L.append("|---|---:|---:|---:|---:|---|")
    for leg in LEGS:
        bh = next((r for r in (picked.get(leg) or {}).get("rows", []) if r["vx"] == "BH"), None)
        if not bh:
            L.append(f"| {rule.LEGS[leg]['name']} | — | — | — | — | — |")
            continue
        L.append(f"| {rule.LEGS[leg]['name']} | {pct(bh['ann'])} | {pct(bh['mdd'])} "
                 f"| {f2(bh['sharpe'])} | {f2(bh['calmar'])} | {bh['start']} ~ {bh['end']} |")
    L.append("")

    # ---- 与全历史榜的对照
    L.append("## 1. 与全历史榜（`_best_by_leg.md`）的差异")
    L.append("")
    L.append(f"`夏普差` = {WINDOW_WORD}★1 − 全历史★1 的**{WINDOW_WORD}**夏普。"
             f"**|差| < 0.10 视为并列**（{WINDOW_YEARS} 年样本的夏普标准误就有 ±{SE_TXT}）。")
    L.append("")
    L.append(f"| 标的 | 全历史 ★1 | {WINDOW_WORD} ★1 | 是否换人 | 夏普差 | 全历史★1 的{WINDOW_WORD}名次 |")
    L.append("|---|---|---|---|---:|---|")
    flips, ties = 0, []
    for leg in LEGS:
        info = picked.get(leg) or {}
        best = info.get("best")
        old = star1.get(leg)
        old_vx = old[0] if old else None
        if not best:
            continue
        same = (old_vx == best["vx"])
        old_row = next((r for r in info["rows"] if r["vx"] == old_vx), None)
        diff = None
        if old_row and old_row["sharpe"] is not None and best["sharpe"] is not None:
            diff = best["sharpe"] - old_row["sharpe"]
        if not same:
            flips += 1
            if diff is not None and abs(diff) < 0.10:
                ties.append(rule.LEGS[leg]["name"])
        now_rank = (old_row or {}).get("rank")
        L.append(f"| {rule.LEGS[leg]['name']} | {old[1] if old else '—'} | {best['label']} "
                 f"| {'否' if same else '**是**'} "
                 f"| {'—（同一支）' if same else (('%+.2f' % diff) if diff is not None else '—')} "
                 f"| {('★' + str(now_rank)) if now_rank else '未进前三'} |")
    L.append("")
    L.append(f"> {len(LEGS)} 条腿里有 **{flips}** 条换人"
             + (f"，其中 **{len(ties)}** 条（{('、'.join(ties))}）的夏普差 < 0.10，"
                "实质是**并列**，别当成结论翻转" if ties else "")
             + f"。真正的换人说明这条腿的最优策略在{WINDOW_WORD}失效或起伏 —— 不等于另一支"
             "「未来更好」，只说明**样本期结论不稳定**。")
    L.append("")
    both = stable_both(full, picked)
    if both:
        L.append("**两榜都进前三（跨腿计数）—— 这才是稳的**：")
        L.append("")
        for vx, legs in both[:8]:
            names = "、".join(rule.LEGS[lg]["name"] for lg in legs)
            L.append(f"- **{label_of(vx)}**：{len(legs)} 条腿（{names}）")
        L.append("")
        L.append(f"> 只在{WINDOW_WORD}榜进前三、全历史榜进不去的，多半是吃到了{WINDOW_WORD}"
                 "这段行情的风格；只有两榜都在榜上的，才谈得上「这支策略本身就靠谱」。")
        L.append("")

    # ---- 全量明细
    L.append(f"## 2. 分标的全量明细（{WINDOW_WORD}窗口）")
    L.append("")
    L.append(f"`名次` = {WINDOW_WORD}夏普前三；`调仓次数` = 窗口内仓位变动次数（部分调仓也算一次，"
             "与各变种自家产物的「成交笔数」口径不同）。**费率沿用各自原生口径**，见最后一列。")
    L.append("")
    for leg in LEGS:
        info = picked.get(leg)
        if not info:
            continue
        L.append(f"### {rule.LEGS[leg]['name']}（`{rule.LEGS[leg]['exec']}` · "
                 f"{rule.LEGS[leg]['role']} · 带 {rule.LEGS[leg]['band']:g}pp）")
        L.append("")
        L.append("| 策略 | 名次 | 年化 | 最大回撤 | 夏普 | Calmar | 平均仓位 | 调仓次数 "
                 "| 区间 | 费率口径 |")
        L.append("|---|---|---:|---:|---:|---:|---:|---:|---|---|")
        for r in info["rows"]:
            rank = f"**{r['rank']}**" if r.get("rank") else "—"
            expo = "—" if r["exposure"] is None else f"{r['exposure']:.0%}"
            L.append(f"| {r['label']} | {rank} | {pct(r['ann'])} | {pct(r['mdd'])} "
                     f"| {f2(r['sharpe'])} | {f2(r['calmar'])} | {expo} | {r['acts']} "
                     f"| {r['start']} ~ {r['end']} | {r['fee']} |")
        L.append("")

    # ---- 口径
    L.append("## 3. 口径与局限")
    L.append("")
    L.append("1. **这不是「统一费率」的比较**：每一行都用**它自己产物的费率与滞后**"
             "（v1/v2/v3 逐腿 A 类 0.10/0.50 或 C 类 0；v5 万 2.5；v6 佣金万 0.3 + 滑点 5bp；"
             "v7~v10 单边 10bp；v12/v13 单边 5bp）。所以同一支策略在两条腿上的净收益"
             "不完全同尺，跨变种比年化前先看「费率口径」列。"
             "与全历史榜保持同一口径 → **两榜的差异只来自窗口**，可以直接对照。")
    L.append("   ⚠ 其中 **A500 / 科创50 / 恒生科技的 V1/V2/V3 是零申赎费**（走 C 类），"
             "只扣了空仓货基收益，**不含 C 类销售服务费**（约 0.4~0.6%/年，真实 C 类净值里已扣）。"
             "这三条腿上的这三支策略，年化要再自己扣掉约 0.5pp（夏普约降 0.05）——"
             "**若某条腿的 ★1 正好落在 V1/V2/V3 上，先扣掉这 0.5pp 再与其他支比，"
             "否则会把「免申赎费」当成「策略更准」**。")
    L.append("2. **滞后不统一**：v7/v8/v9/v10 是 T+2（state 内 shift 1 + run_leg 再 shift 1），"
             f"其余 T+1。{WINDOW_YEARS} 年窗口里这一天的差别约 0.3~0.5pp/年，不足以改排名。")
    L.append("3. **窗口切法**：以窗口起点**前一日**的净值为基点（`nav / nav[base]`），"
             "窗口首日收益计入，且信号预热发生在窗口之前 —— 不存在「窗口头冷启动重放」。"
             "各腿实际起止略有差异（休市日不同），逐行标注。")
    L.append(f"4. **{WINDOW_YEARS} 年样本{'很薄' if WINDOW_YEARS <= 3 else '仍偏薄'}**："
             f"{MARKET_NOTE}，**这段行情的风格偏好会直接决定排名**。"
             f"夏普标准误 ≈ ±{SE_TXT}，名次相差 0.05 以内视为并列。"
             f"判断「该用哪支」时，全历史榜（20 年）与{WINDOW_WORD}榜**两条都要看**："
             "两榜都进前三的才是稳的。")
    L.append("5. **主策略回撤阶梯不参与**：`_vol_legs_bt.json::singles` 只有 4 条腿（a500/hs300/ndx/spx），"
             "且是「组合层资产配置」口径，与单腿择时不同层。")
    L.append("6. **v4 ERP 股债通道不参与**：它管沪深300 与国债之间摆 0/100，不是这条腿的择时"
             "（与全历史榜同判）。")
    L.append("7. **v21 利弗莫尔关键点不参与**：按用户 2026-11-19 指令整支不进榜单。")
    L.append("8. **v14~v20 七个单标的组合不在本榜**：它们是「每条腿固定用一支」的落地方案"
             "（成分来自全历史榜与 `_beat_best_top3.md`），本榜只比较**单支变种**。"
             f"想知道 v14~v20 自己{WINDOW_WORD}怎样，看各自 `backtest/_v1x_bt.jsonl`，"
             "不要拿它们的成分（如 v14 = V3+V12 等权）反推本榜名次。")
    L.append("")
    if fails:
        L.append("## 4. 重建失败的行")
        L.append("")
        L.append("| 变种 | 标的 | 原因 |")
        L.append("|---|---|---|")
        for vx, leg, why in fails:
            L.append(f"| {vx} | {rule.LEGS[leg]['name']} | `{why}` |")
        L.append("")
        L.append("> 失败多为「该腿历史不足预热窗口」（如 A500 的真实指数起点、科创50 的 V13）。")
        L.append("")
    return "\n".join(L) + "\n"


def main():
    print("重建各变种全历史单腿净值 …")
    raw, fails = collect()
    print(f"共 {len(raw)} 条 (变种×腿) 净值序列，失败 {len(fails)} 条")
    rows, skipped, w0, end = window_rows(raw)
    fails = fails + skipped
    print(f"窗口 {w0} ~ {end}，窗口内可评比 {len(rows)} 行（跳过 {len(skipped)} 行）")
    rows = add_bh(rows)
    picked = pick(rows)
    full = load_full_ranks()
    md = render(picked, rows, w0, end, fails, full)
    with io.open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(md)
    with io.open(OUT_JSONL, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({k: v for k, v in r.items()}, ensure_ascii=False) + "\n")
    print(f"[ok] {OUT_MD}")
    print(f"[ok] {OUT_JSONL}")
    print("")
    print(f"{WINDOW_WORD}每标的前三名（夏普）：")
    for leg in LEGS:
        info = picked.get(leg)
        if not info or not info["top"]:
            print(f"  {rule.LEGS[leg]['name']:<5} —")
            continue
        print(f"  {rule.LEGS[leg]['name']}")
        for i, r in enumerate(info["top"], 1):
            print(f"    ★{i} {r['label']:<34} 年化 {r['ann']:+.2%} · "
                  f"回撤 {r['mdd']:.2%} · 夏普 {r['sharpe']:.2f}")


if __name__ == "__main__":
    main()
