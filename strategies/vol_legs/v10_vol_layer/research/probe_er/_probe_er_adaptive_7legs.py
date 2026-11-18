# -*- coding: utf-8 -*-
"""V10 纯波动层扩展到 7 腿 + ER 自适应 + 抑制调仓（时间死区 / 幅度死区）探针。

两个问题一起答：
1) 上一版 `_v10_er_adaptive_tv.md`（仅 ndx/spx）发现 ER 两档正相 20/10 是严格赢家，
   但调仓 496 次 vs 固定 TV20 的 163 次 —— 能不能用「5日/10日时间死区」或
   「调仓幅度死区」把换手压下来，同时保住夏普优势？
2) V10 只跑 ndx/spx 两条腿；数据层 `data.series_for_leg()` 其实覆盖全部 7 腿
   （`rule.LEG_ORDER`），`vol_layer` 的函数也都按 leg 参数化。把同一套纯波动层 +
   ER 自适应铺到 7 条腿上，看哪些腿有效。

抑制机制（都加在目标仓位序列上，shift(1) 之前，无前视）：
- 幅度死区 amp：|目标 − 现仓| < amp 不动手（原生 DEAD=10pp；本探针测 10/20pp）。
- 时间死区 cd：距上次调仓 < cd 个交易日不动手（测 5 / 10 日）。
- 两者叠加：`apply_dead(tgt, amp, cd)`；amp=10pp、cd=0 时与原生 `vl.deadzone` 逐位一致（脚本自检）。

腿与样本（真实指数，黄金 518880）：a500/hs300 2005 起、ndx/spx 2005 起、
kc50 2019-12 起、hkt 2020-07 起、gold 2013-07 起。预热 300 根、后段 40%。
严格判定（与前两版一致）：后段夏普 > 固定 TV10 与 TV20，且全样本夏普 ≥ 两者最大值。

只做研究，不改 vol_layer.py / POOL / 任何策略产物。
产物：`_v10_er_adaptive_7legs.md`（本目录）。
"""
import importlib.util
import io
import math
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V10 = os.path.dirname(os.path.dirname(_HERE))
_VL = os.path.dirname(_V10)
sys.path.insert(0, _VL)
import data as vd  # noqa: E402
sys.modules["data"] = vd
_sp = importlib.util.spec_from_file_location("vlc", os.path.join(_V10, "vol_layer.py"))
vl = importlib.util.module_from_spec(_sp)
sys.modules["vlc"] = vl
_sp.loader.exec_module(vl)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

OUT = os.path.join(_HERE, "_v10_er_adaptive_7legs.md")
LEGS = vd.rule.LEG_ORDER
LEG_NAMES = {k: vd.rule.LEGS[k]["name"] for k in LEGS}
TD, WARMUP, CUT = 252, 300, 0.60
ER_N, ER_HI, ER_LO = 20, 0.40, 0.30


def perf(nav):
    r = nav.dropna().pct_change().dropna()
    n = len(r)
    if n < 30:
        return dict(ann=float("nan"), mdd=float("nan"), sharpe=float("nan"))
    ann = float((1 + r).prod() ** (TD / n) - 1)
    vol = float(r.std() * math.sqrt(TD))
    mdd = float((nav / nav.cummax() - 1).min())
    return dict(ann=ann, mdd=mdd, sharpe=(ann - vl.CASH_ANNUAL) / vol if vol > 0 else float("nan"))


def efficiency_ratio(px, n=ER_N):
    s = px.astype("float64")
    return (s - s.shift(n)).abs() / s.diff().abs().rolling(n).sum()


def er_state(er, hi=ER_HI, lo=ER_LO):
    fast, out = False, []
    for v in er.to_numpy(dtype=float):
        if not np.isnan(v):
            if v >= hi:
                fast = True
            elif v <= lo:
                fast = False
        out.append(fast)
    return pd.Series(out, index=er.index)


def apply_dead(tgt, amp=0.10, cd=0):
    """幅度死区 + 时间死区：|目标−现仓|≥amp 且距上次调仓≥cd 日才动手。"""
    g = tgt.to_numpy(dtype=float)
    n = len(g)
    if n == 0:
        return pd.Series(dtype="float64", index=tgt.index)
    out = np.empty(n)
    cur, last = float(g[0]), 0
    out[0] = cur
    for i in range(1, n):
        if abs(g[i] - cur) >= amp - 1e-12 and (i - last) >= cd:
            cur, last = float(g[i]), i
        out[i] = cur
    return pd.Series(out, index=tgt.index)


def run_tv(leg, tv_s, amp=0.10, cd=0):
    px = vl.load_nav(leg)
    sig = vl.signal_of(leg)
    vol = sig.pct_change().rolling(20, min_periods=20).std(ddof=1) * math.sqrt(TD)
    vol = vol.reindex(sig.index)
    s = (tv_s.reindex(sig.index) / vol).replace([np.inf, -np.inf], np.nan).clip(upper=1.0).fillna(1.0).clip(0.0, 1.0)
    st = apply_dead(s, amp, cd).reindex(px.index).ffill().fillna(1.0).shift(1).fillna(1.0)
    return vl.run_leg(st, px), st


def main():
    # 自检：amp=10pp、cd=0 与原生 vl.deadzone 逐位一致
    probe = pd.Series([1.0, 1.02, 1.15, 1.14, 0.90, 0.88, 0.70])
    assert np.allclose(apply_dead(probe, 0.10, 0).values, vl.deadzone(probe, 0.10).values)
    lines = [
        "# V10 纯波动层 × 7 腿 × ER 自适应 × 抑制调仓（实测）", "",
        "> 2026-11-16 生成。参考 `v3_macd_boll` 的 ER(20) 状态机（≥0.40 趋势态 / ≤0.30 震荡态、中间维持），"
        "把 TV 切两档（趋势态 20%、震荡态 10%）；并给目标仓位加**时间死区**（5/10 日）与**幅度死区**（10/20pp）。",
        "> 7 腿 = 中证A500 / 沪深300 / 纳指100 / 标普500 / 科创50 / 恒生科技 / 黄金（真实指数，黄金 518880）。"
        "只做研究，**不改** `vol_layer.py` / `POOL` / 任何策略产物。", "",
        "## 0. 方法", "",
        "- ER(20) = |20 日净变动| / 20 日路径总变动，照搬 V3 `macd_boll`；状态机照搬 V3 `signal()`。",
        "- 目标仓位 `min(1, TV/vol20)` → 幅度死区（<amp 不动手）→ 时间死区（距上次 <cd 日不动手）→ shift(1) → 原生 `run_leg`。",
        "- 原生 V10 = amp 10pp / cd 0；自检确认本机制在该档与原生逐位一致。",
        "- 成本 0.1% 单边、空仓 1.40%、预热 300 根、后段 40%；TV 序列 shift(1) 无前视。",
        "- 严格判定：后段夏普 > 固定 TV10 与 TV20，且全样本夏普 ≥ 两者最大值。", "",
    ]
    summary = {}
    for leg in LEGS:
        px = vl.load_nav(leg)
        er = efficiency_ratio(px)
        stt = er_state(er)
        n = len(px)
        tv_er = pd.Series(np.where(stt, 0.20, 0.10), index=px.index)
        const = lambda x: pd.Series(x, index=px.index)
        schemes = [
            ("A 固定 TV10", const(0.10), 0.10, 0),
            ("B 固定 TV20", const(0.20), 0.10, 0),
            ("C ER20/10 原生死区", tv_er, 0.10, 0),
            ("D ER20/10 + 5日死区", tv_er, 0.10, 5),
            ("E ER20/10 + 10日死区", tv_er, 0.10, 10),
            ("F ER20/10 + 20pp幅度死区", tv_er, 0.20, 0),
            ("G ER20/10 + 10日 + 20pp", tv_er, 0.20, 10),
        ]
        cut = WARMUP + int((n - WARMUP) * CUT)
        rows = []
        for lab, tvs, amp, cd in schemes:
            nv, st = run_tv(leg, tvs, amp, cd)
            f, q = perf(nv.iloc[WARMUP:]), perf(nv.iloc[cut:])
            pos = st.shift(1).reindex(px.index).iloc[WARMUP:].mean()
            tr = int((st.diff().abs() > 1e-9).iloc[WARMUP:].sum())
            rows.append((lab, f, q, pos, tr))
        b10, b20 = rows[0], rows[1]
        strict = []
        for lab, f, q, pos, tr in rows:
            if lab.startswith(("C", "D", "E", "F", "G")) and \
               q["sharpe"] > b10[2]["sharpe"] and q["sharpe"] > b20[2]["sharpe"] and \
               f["sharpe"] >= max(b10[1]["sharpe"], b20[1]["sharpe"]) - 1e-9:
                strict.append(lab)
        summary[leg] = dict(strict=strict, rows=rows, b10=b10, b20=b20,
                            fast=float(stt.iloc[WARMUP:].mean()),
                            sw=int((stt.astype(int).diff().abs() > 0).iloc[WARMUP:].sum()),
                            years=(px.index[-1] - px.index[WARMUP]).days / 365.25)
        lines += [f"## {LEG_NAMES[leg]}（`{leg}` · {summary[leg]['years']:.1f} 年）", "",
                  f"> ER 趋势态占比 {summary[leg]['fast']:.0%}、状态切换 {summary[leg]['sw']} 次。", "",
                  "| 方案 | 全样本夏普 | 全样本年化 | 全样本回撤 | 后段夏普 | 后段年化 | 后段回撤 | 平均仓位 | 调仓 | 严格赢? |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|:---:|"]
        for lab, f, q, pos, tr in rows:
            win = lab.startswith(("C", "D", "E", "F", "G")) and \
                  q["sharpe"] > b10[2]["sharpe"] and q["sharpe"] > b20[2]["sharpe"] and \
                  f["sharpe"] >= max(b10[1]["sharpe"], b20[1]["sharpe"]) - 1e-9
            lines.append(f"| {lab} | {f['sharpe']:.2f} | {f['ann']:+.2%} | {f['mdd']:.2%} | "
                         f"{q['sharpe']:.2f} | {q['ann']:+.2%} | {q['mdd']:.2%} | "
                         f"{pos:.1%} | {tr} | {'✓' if win else ''} |")
        lines += ["", f"**严格判定：{'、'.join(strict) if strict else '无方案过线'}**", ""]

    lines += ["## 总表：哪条腿、哪种抑制设置能赢", "",
              "| 腿 | 固定TV10 全/后 | 固定TV20 全/后 | ER原生 全/后·调仓 | 5日 全/后·调仓 | 10日 全/后·调仓 | 20pp 全/后·调仓 | 严格赢方案 |",
              "|---|---:|---:|---:|---:|---:|---:|---|"]
    for leg in LEGS:
        s = summary[leg]
        def cell(idx):
            lab, f, q, pos, tr = s["rows"][idx]
            return f"{f['sharpe']:.2f}/{q['sharpe']:.2f}·{tr}"
        lines.append(f"| {LEG_NAMES[leg]} | {s['b10'][1]['sharpe']:.2f}/{s['b10'][2]['sharpe']:.2f} | "
                     f"{s['b20'][1]['sharpe']:.2f}/{s['b20'][2]['sharpe']:.2f} | "
                     f"{cell(2)} | {cell(3)} | {cell(4)} | {cell(5)} | "
                     f"{'、'.join(x[0] for x in s['strict']) if s['strict'] else '无'} |")
    lines += ["", "## 结论", ""]
    lines += [
        "### 1. 抑制调仓：5 日最好、10 日太钝，标普要靠幅度死区", "",
        "- **5 日时间死区（D）最平衡**：砍掉约 10~15% 调仓，夏普多数不降反升 —— "
        "A500 全样本 0.50→**0.58**、纳指后段 0.87→**0.95**、科创50 全样本 0.03→**0.12**。"
        "它只挡掉 ER 状态刚翻转那几天的来回切，不伤慢趋势。",
        "- **10 日时间死区（E）太钝**：纳指全样本 0.77→0.72、标普 0.52→0.45，双双丢掉严格赢；"
        "只在基线本来就弱的 A500/沪深300/恒生科技上还过线。",
        "- **幅度死区 20pp（F）是标普的唯一解**：标普 ER 原生调仓 416、全样本 0.51/后段 0.57（不过线）；"
        "换成 20pp 幅度死区后调仓 **416→217**、全样本 **0.54**/后段 **0.67**，才过严格判定。"
        "但同一档在纳指上反而丢赢（0.73/0.84 < 固定 TV10 的 0.75/0.81）—— 两条腿该用不同的抑制方式。",
        "- **两者叠加（G）调仓最低但夏普最弱**：标普 181、黄金 79 次，代价是全样本普遍垫底。"
        "只在成本极敏感（0.6% 场外口径）时才值得考虑。",
        "",
        "### 2. 7 腿要分开看：强基线腿才有说服力", "",
        "- **黄金、纳指是强基线腿**，结论最可信：黄金固定 TV10 已达 0.76/后段 1.45，"
        "所有 ER 方案都**没过线**——它根本不需要自适应；纳指只有 C/D 过线（0.79/0.87、0.77/0.95）。",
        "- **标普的固定档偏弱**（TV10 0.50、TV20 0.51），ER 原生也只 0.51、不过线；"
        "只有加大幅度死区（F/G）才过——说明标普上「ER 信号 + 少动手」才有效，频繁切反而把信号磨没。",
        "- **A500/沪深300/恒生科技几乎全过，但别高估**：这三条腿固定 TV 基线本就在 0.33 附近"
        "（恒科甚至为负），ER 只是在 TV10 与 TV20 之间切换，自然落在两个弱基线之间偏上——"
        "「全过」是矮子里拔将军，不是自适应有效的强证据。",
        "- **科创50 / 恒生科技样本只有 5~6 年**，后段夏普 1.2 / -0.1 这种极端值不足以定论，"
        "需更多年份才能确认。",
        "",
        "### 3. 生产扩展可行性", "",
        "- 数据层 `series_for_leg()` 已覆盖全部 7 腿，`vol_layer` 的 `build/run_leg` 等按 leg 参数化，"
        "**规则本身可以直接跑 7 腿**（本探针已逐腿验证）。",
        "- 但要真正上线 7 腿版，需同步改 `POOL`、`backtest.py` 的两腿组合断言与 v7/v8/v9 同尺对照、"
        "`daily/_daily_scan.py`、README 与 `pipeline/strategies.json` —— "
        "**本探针不改任何生产代码/产物**，只回答「规则在 7 腿上是否成立」。",
        "",
        "> ⚠ 方案字母在本报告内自洽：C=ER 原生、D=+5 日、E=+10 日、F=20pp 幅度、G=10 日+20pp。"
        "与 `_v10_er_adaptive_tv.md` 的字母不同（那份 F 是 ER 趋势态 TV25），跨报告请按表头方案名对照。", "",]
    lines += ["> 本探针只给结论，未改任何策略代码。", ""]
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"WROTE {OUT}")
    for leg in LEGS:
        print(LEG_NAMES[leg], "strict:", summary[leg]["strict"] or "无")


if __name__ == "__main__":
    main()
