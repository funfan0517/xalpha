"""在当前最优（已含波动层）之上，还能不能「再往前推一步」—— 真实指数口径版。

与 `_beat_best.py`（2026-11-13 旧场外净值口径）的区别：

1. **价格唯一口径**：`data.series_for_leg(leg)`（六腿真实指数收盘 + 黄金场内 518880），
   不再用 `data/_otc_nav.json` 场外复权净值，也没有任何基金桥接段。A500 指数 2024-09
   才发布，2005~2024 是指数公司回溯模拟值 —— 报告里显式标注这一点。
2. **基线升级为当前最优且已含波动层**：A500=V3、沪深300=V2、纳指100=V9(月中补丁)、
   标普500=V13（回调加仓）、科创50=V2、恒生科技=V3、黄金=V6-B。
   全部按旧研究的方法论**照规则重写**（复用 `_beat_best.py` 的同尺函数：
   `c_v2_tv` / `c_v3_tv` / `v9_tv_refit` / `v6_b`），参数与当前默认一致；
   V13 用其原生 `engine.state_machine()`（无重写版）。
   —— 因此旧研究的 C8「V3×波动上限20%」/ C11「V2×波动上限20%」**就是基线本身**，
   已从候选集删除；本课题问的是「在已经含波动层的基线之上，再加一层能不能赢」。
3. 同尺：同一份价格、同一成本（申 0.10% / 赎 0.50%）、同一现金（1.40%）、
   同一执行（T 日收盘信号 → T+1 收益）。候选参数事先固定，
   后段（后 40%）夏普也高于基线才标 ★。

产物：`_beat_best_index.md`（本目录），不覆盖历史报告。
用法：`python strategies/vol_legs/research/beat_best/_beat_best_index.py`
"""
import importlib.util
import io
import math
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_RES = os.path.dirname(_HERE)
_VL = os.path.dirname(_RES)
_ROOT = os.path.dirname(os.path.dirname(_VL))
sys.path.insert(0, _VL)

# 复用旧研究的同尺重写函数（纯函数，不加载任何变种模块，无串味风险）
_spec = importlib.util.spec_from_file_location("_bb_legacy", os.path.join(_HERE, "_beat_best.py"))
old = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(old)

import data as vdata  # noqa: E402

OUT_MD = os.path.join(_HERE, "_beat_best_index.md")
TD, WARMUP, CUT = old.TD, old.WARMUP, old.CUT


# ---------------------------------------------------------------- 绩效（与旧研究完全相同）
def ev(hold, px):
    h = hold.reindex(px.index).fillna(0.0)
    return old.perf(old.run(h, px), h)


def post_ev(hold, px, cut):
    h = hold.loc[cut:].reindex(px.loc[cut:].index).fillna(0.0)
    return old.perf(old.run(h, px.loc[cut:]), h)


# ---------------------------------------------------------------- 基线（照规则重写，参数=当前默认）
def baseline(leg, px):
    k = LEGS[leg]["base"]
    if k == "v2tv":
        return old.c_v2_tv(px, target=0.20)          # boll 默认 TV=20%
    if k == "v3tv":
        return old.c_v3_tv(px, target=0.20)          # macd_boll 默认 TV=20%
    if k == "v9":
        return old.v9_tv_refit(px, tv=0.15, refit=10)  # V9 默认 TV15 + 10 日定仓补丁
    if k == "v6b":
        return old.v6_b(px, target=0.10, win=120, dead=0.15, gap=10, floor=0.20)
    if k == "v13":
        return _v13_hold(px, leg)
    raise KeyError(k)


def _v13_hold(px, leg):
    """V13 无重写版，用原生 state_machine；w 是当日决策，需 shift(1) 才是 T+1 持仓。"""
    v13dir = os.path.join(_VL, "v13_quad_9grid")
    for name in ("rule", "engine", "data"):
        sys.modules.pop(name, None)
    if v13dir in sys.path:
        sys.path.remove(v13dir)
    sys.path.insert(0, v13dir)
    eng = importlib.import_module("engine")
    dtamod = importlib.import_module("data")
    df = dtamod.frame(leg)
    sm = eng.state_machine(df, callback_add=True)   # 当前最优 = 回调加仓版
    return sm["w"].shift(1).fillna(0.0)


# ---------------------------------------------------------------- 候选（基线之上再加一层）
def gate_ma(px, n=200):
    return (px > px.rolling(n).mean()).astype(float).shift(1).fillna(0.0)


def gate_mom(px, look=252, skip=21):
    mom = px.shift(skip) / px.shift(skip + look) - 1.0
    return (mom > 0).astype(float).shift(1).fillna(0.0)


def gate_golden(px, fast=50, slow=200):
    return (px.rolling(fast).mean() > px.rolling(slow).mean()).astype(float).shift(1).fillna(0.0)


def cands(leg, px, base):
    """旧版 15 档独立策略 + 当前基线 + 叠加层，全部同一把尺子横向比较。

    与上一版的关键区别：候选**不再只是「基线再叠一层」**，而是把旧版 `_beat_best.py`
    的独立规则全集拿进来 —— 每条腿都在问「有没有任何一个别的策略能超过当前最优」。
    """
    k = LEGS[leg]["base"]
    out = [("基线（当前最优）", base)]

    # ---- A. 旧版独立策略候选（照搬旧报告的 C1~C15，不依赖基线） ----
    indep = [
        ("C1 年线闸门 MA200", old.c_ma(px, 200)),
        # C2 与 V10 是同一机制（纯波动、无方向、win20），只是 TV15% + 死区5pp/限速5日
        # （V10 默认 TV20% / 死区10pp / 逐日）。纳指腿上它就是「V10 调参」，不是新策略。
        ("C2 纯波动 TV15（=V10 调参）", old.c_tv(px, 0.15, 20)),
        ("C3 波动目标×年线", old.c_tv_ma(px, 0.15, 20, 200)),
        ("C4 波动目标×12-1 动量", old.c_tv_mom(px, 0.15, 20)),
        ("C5 BOLL 突破+年线出场", old.c_boll_ma(px)),
        ("C6 V9×年线", old.c_v9_ma(px)),
        ("C7 V3+30% 底仓", old.c_v3_floor(px, 0.30)),
        ("C8 V3×波动上限 20%", old.c_v3_tv(px, 0.20)),
        ("C9 金叉 50/200", old.c_golden(px)),
        ("C10 V2∪V3", old.c_v2v3(px)),
        ("C11 V2×波动上限 20%", old.c_v2_tv(px, 0.20)),
        ("C12 EWM 波动目标", old.c_tv_ewm(px, 0.15)),
        ("C13 V9 定仓 10 日", old.v9_tv_refit(px, 0.15, 10)),
        ("C14 BOLL 压缩突破", old.c_v2_squeeze(px)),
        ("C15 BOLL+10% 跟踪止损", old.c_v2_trail(px)),
    ]
    out.extend(indep)

    # ---- B. 当前最优基线的叠加/邻域（作为候选，与独立策略同台） ----
    for lab, g in (("+年线闸门 MA200", gate_ma(px)),
                   ("+12-1 动量闸门", gate_mom(px)),
                   ("+金叉闸门 50/200", gate_golden(px))):
        out.append((lab, (base * g).clip(0.0, 1.0)))
    if k in ("v2tv", "v3tv"):
        for tv in (0.15, 0.25):
            out.append((f"TV={tv:.0%}（波动层邻域）",
                        old.c_v2_tv(px, target=tv) if k == "v2tv" else old.c_v3_tv(px, target=tv)))
        for win in (10, 60):
            cap = old.tv_hold(px, target=0.20, win=win)
            sig = old.v2_boll(px) if k == "v2tv" else old.v3_macdboll(px)
            out.append((f"波动窗口 {win} 日", (sig * cap).clip(0.0, 1.0)))
    if k == "v9":
        for rf in (5, 20):
            out.append((f"V9 定仓 {rf} 日", old.v9_tv_refit(px, tv=0.15, refit=rf)))
    if k == "v6b":
        for tv in (0.08, 0.12):
            out.append((f"V6-B 波动目标 {tv:.0%}",
                        old.v6_b(px, target=tv, win=120, dead=0.15, gap=10, floor=0.20)))
    return out


LEGS = {
    "a500": dict(name="中证A500", base="v3tv", base_name="V3 MACD+BOLL 开口突破 + TV20",
                 ref=0.88, note="含 2005~2024 指数公司回溯段"),
    "hs300": dict(name="沪深300", base="v2tv", base_name="V2 BOLL(35,2) 上轨突破 + TV20",
                  ref=0.75, note=""),
    "ndx": dict(name="纳指100", base="v9", base_name="V9 波动率目标（TV15 + 月中补丁）",
                ref=0.74, note=""),
    "spx": dict(name="标普500", base="v13", base_name="V13 趋势九宫格（回调加仓）",
                ref=0.59, note=""),
    "kc50": dict(name="科创50", base="v2tv", base_name="V2 BOLL(35,2) 上轨突破 + TV20",
                 ref=0.70, note="样本约 6 年"),
    "hkt": dict(name="恒生科技", base="v3tv", base_name="V3 MACD+BOLL 开口突破 + TV20",
                ref=0.34, note="样本约 6 年"),
    "gold": dict(name="黄金", base="v6b", base_name="V6-B B 控回撤（TV10%/120 日）",
                 ref=0.74, note="场内 518880"),
}


def main():
    lines = [
        "# 能否在「当前最优」之上再往前推一步？—— 真实指数口径",
        "",
        "> 2026-11-16 重做。`_beat_best.md` 是 2026-11-13 口径切换前的历史研究（场外净值、"
        "纯 0/1 基线），其 C8/C11「V2/V3 × 波动上限 20%」**已被吸收为 v2/v3 的默认**。"
        "本报告把**旧版 15 档独立策略候选 + 当前最优基线 + 叠加层**放到同一把尺子上横向比较，"
        "回答「有没有任何一个别的策略能超过当前最优」，价格全部走真实指数收盘（黄金 518880）。",
        "",
        "## 0. 口径与硬约束",
        "",
        "- 价格：`data.series_for_leg()`，六腿真实指数 + 黄金 `518880`；样本至 2026-11-13 / 美股 11-12。",
        "- 成本：统一申 0.10% / 赎 0.50%，空仓货基 1.40%，T 日收盘 → T+1 收益。",
        "- 基线：七腿当前最优，照规则重写（参数与默认一致）；统一预热 300 根。",
        "- 判定：候选必须**全样本夏普 ≥ 基线 +0.02**且**后段（后 40%）夏普 > 基线**才标 ★"
        "（0.02 门槛排除舍入级伪超越）。",
        "- **基线夏普是同尺复刻值，不是 best_by_leg 清单的原口径数字**：本报告对所有腿统一按"
        "「申 0.10% / 赎 0.50%」计成本，而清单里 V13 用单边 5bp、V7~V10 用单边 10bp、黄金用场内 16bp。"
        "A 类往返 60bp 一年吃掉数个百分点，所以同尺下的 0.60 / 0.66 / 0.63 低于清单的 0.88 / 0.74 / 0.74 —— "
        "**这是费率差，不是策略退步**；同一把尺子内基线 vs 候选的比较仍成立。",
        "- ⚠ A500 的 2005~2024 是指数公司 2024-09 发布时回算的**回溯模拟值**，不是当时可成交的行情；"
        "其 A500 行只能当作「有前视偏差的上界」读。",
        "- **C2「纯波动 TV15」不是独立新策略**：它与 V10 同为『无方向、win20 的纯波动层』，"
        "只是 TV 20%→15%、死区 10pp→5pp。纳指腿上标 ★ 的本质是 **V10 调参**，不是发现了新机制。",
        "- **标普500 的 V9 原口径夏普也是 0.55**（`_v9_vol_target_bt.md:32`，空仓 2% + 单边 3bp），"
        "与本报告 V13 同尺复刻的 0.55 数值巧合、口径不同。同尺下 V9 的近似版 C13 是 0.50；"
        "标普500 的『最优』对费率极敏感（见 best_by_leg §3 费率归一化），不要当成 V13 明显胜出。",
        "",
        "## 1. 逐腿结果",
        "",
        "| 标的 | 基线（当前最优） | 基线夏普 | 超越项 | 新夏普 | 后段 新 vs 旧 |",
        "|---|---|---:|---|---:|---|",
    ]
    detail = {}
    for leg, cfg in LEGS.items():
        px = vdata.series_for_leg(leg).dropna()
        if len(px) <= WARMUP + 60:
            continue
        base = baseline(leg, px)
        common = base.index.intersection(px.index)
        px, base = px.loc[common], base.loc[common]
        cut = px.index[int(len(px) * CUT)]
        rows = []
        for lab, h in cands(leg, px, base):
            h = h.reindex(px.index).fillna(0.0)
            rows.append((lab, ev(h, px), post_ev(h, px, cut)))
        b_full, b_post = rows[0][1], rows[0][2]
        best = None
        # 全样本需高出 ≥0.02 夏普且后段也赢，避免 0.75 vs 0.75 这种舍入级「伪超越」
        for lab, full, post in rows[1:]:
            if (full["sharpe"] - b_full["sharpe"] >= 0.02
                    and post["sharpe"] > b_post["sharpe"]):
                if best is None or full["sharpe"] > best[1]["sharpe"]:
                    best = (lab, full, post)
        detail[leg] = dict(name=cfg["name"], base_name=cfg["base_name"], base=b_full,
                           best=best, base_post=b_post, rows=rows, note=cfg["note"])
        if best:
            lines.append(f"| {cfg['name']} | {cfg['base_name']} | {b_full['sharpe']:.2f} | "
                         f"{best[0]} | {best[1]['sharpe']:.2f} | "
                         f"{best[2]['sharpe']:.2f} vs {b_post['sharpe']:.2f} |")
        else:
            lines.append(f"| {cfg['name']} | {cfg['base_name']} | {b_full['sharpe']:.2f} | "
                         "— 无超越项 — | — | — |")

    lines += ["", "## 2. 候选逐条明细（全样本 / 后段夏普）", ""]
    for leg, d in detail.items():
        lines += [f"### {d['name']} —— 基线 {d['base_name']}" + (f"（{d['note']}）" if d["note"] else ""), "",
                  "| 候选 | 年化 | 回撤 | 夏普 | Calmar | 换手/年 | 后段夏普 | ★ |",
                  "|---|---:|---:|---:|---:|---:|---:|:---:|"]
        for lab, full, post in d["rows"]:
            star = "★" if d["best"] and lab == d["best"][0] else ""
            lines.append(f"| {lab} | {full['ann']:+.2%} | {full['mdd']:.2%} | "
                         f"{full['sharpe']:.2f} | {full['calmar']:.2f} | "
                         f"{full['trades'] / full['years']:.1f} | {post['sharpe']:.2f} | {star} |")
        lines.append("")

    wins = [d for d in detail.values() if d["best"]]
    lines += ["## 3. 汇总", "",
              f"- 七条腿中 **{len(wins)} 条**能在「已含波动层的最优基线」之上再往前推一步"
              f"（{'、'.join(d['name'] for d in wins) if wins else '无'}）。",
              "- **注意归属性质**：纳指100 的超越项 C2 就是 V10 的 TV15 调参；科创50 是 V2 的 TV15 调参；"
              "A500 的 C10 是旧版独立策略；恒生科技是叠加年线门。调参类不算「发现了新机制」。",
              "- 旧报告「只有科创50 能再推、超越项就是 V2×波动上限 20%」对应的是**纯 0/1 基线时代**；"
              "那一层现在已是 v2/v3 默认，本报告检验的是再往上一层的增量。",
              "- 候选集包含旧版全部 15 档独立策略（年线、纯波动、波动×动量、V2∪V3、压缩突破、跟踪止损…）"
              "+ 当前最优基线 + 叠加邻域，全部同尺横向比较；不是只在当前基线上做加法。",
              "- 无超越项意味着这些**事先固定的候选**没能同时赢全样本和后段 —— 不等于「已经最优」，"
              "想再推进需要换信息源（估值分位、量能、宏观），而不是继续调均线/波动参数。",
              ""]
    with io.open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"WROTE {OUT_MD}")
    for leg, d in detail.items():
        b = d["base"]
        if d["best"]:
            print(f"  {d['name']:8s} base {b['sharpe']:.2f} -> {d['best'][0]} "
                  f"{d['best'][1]['sharpe']:.2f} (post {d['best'][2]['sharpe']:.2f} vs {d['base_post']['sharpe']:.2f})")
        else:
            print(f"  {d['name']:8s} base {b['sharpe']:.2f} | no beat")


if __name__ == "__main__":
    main()
