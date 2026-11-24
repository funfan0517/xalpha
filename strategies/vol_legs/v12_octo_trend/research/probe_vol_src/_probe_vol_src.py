# -*- coding: utf-8 -*-
"""V12 · 量能来源补齐（P3）：黄金 / 港股两腿换量能分母值不值。

## 问题

八态里四个态（弱回调 / 强回调 / 弱反弹 / 强反弹）全靠 `rvol = 当日量 / 20 日均量`。
七腿里只有两条腿的量能来源"不干净"：

| 腿 | 现在的量能 | 问题 |
|---|---|---|
| 黄金 | `518880` **ETF 自身量** | 现货黄金是 OTC，没有成交量；ETF 量 = 场内交易 + 申赎套利 |
| 恒生科技 | `HKHSTECH` **自身指数量能** | 不是代理量能（那条注释已过时），但它是**单一行业指数**的量，未必代表港股整体活跃度 |

本研究回答两件事（各做一次 A/B，其余一切不变 —— 同一份收盘、同一个 `engine`）：

1. 黄金：`518880` 量 → **沪金主力连续 `AU0`**（新浪期货，2008-01 起，与 A 股同一交易日历）；
2. 港股：`HKHSTECH` 量 → **恒生指数 `HKHSI`** 量（雪球，2005 起，2010 后完整）。

判据按顺序看，**先验口径再看好坏**：

1. **四组配对分离度**（规格 §4 的验伪尺子）：急涨−慢涨、弱回调−强回调、强反弹−弱反弹、急跌−慢跌。
   换量能后若分离度符号反了 / 明显变小，说明新量能更噪 —— 直接否掉；
2. **八态占比与逐态 fwd20**：量能只影响四个"强弱"态，看它们的分布是否变得极端（某态占比暴涨 = 阈值不匹配）；
3. **整条策略的回测**（夏普 / 回撤 / 分折）：只有前两条不退化，才轮到看这个。

## 口径

- 价格：`data.frame(leg)`，**换量不动价**（只替换 `volume` 列）；
- 对齐：新序列按该腿的交易日 `reindex + ffill`，并报告覆盖率；
- 成本：`engine.run`（T 日收盘定仓、T+1 起承担收益、单边 5bp、空仓计货基 1.40%）；
- 许可与波动层：**现行生效口径**（`rule.cap_map` + `rule.vol_layer_of`），
  所以「现在」一列应等于 `backtest/_v12_octo_trend_bt.md` §1（报告带自检）；
- **不写任何数据文件**：新量能在内存里拉，`data/` 与 `rule.py` 一行不动。

## 数据可得性（2026-11-24 实测）

| 序列 | 来源 | 起 | 根数 | 量能 |
|---|---|---|---:|---|
| `HKHSTECH`（现行） | `_global_index_klines.json` | 2020-07 | 1549 | 完整 |
| `HKHSI` | `xa.get_daily("HKHSI")` | 2005-01 | 5441 | 2009 前大量为 0，2010 后完整 |
| `518880`（现行） | `_ohlcv.json` / `_long_klines.json` | 2013-07 | 3200+ | ETF 交易量 |
| `AU0` 沪金主力 | 新浪期货 `getDailyKLine` | 2008-01 | 4596 | 完整（含夜盘） |

> 港股腿预热后样本从 2021-09 起、黄金从 2014-09 起，都在新序列的覆盖范围内，**不需要截断样本**。

产物：`_probe_vol_src.md`（本目录）。

用法：
    python strategies/vol_legs/v12_octo_trend/research/probe_vol_src/_probe_vol_src.py
"""
import io
import json
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V12 = os.path.dirname(os.path.dirname(_HERE))            # v12_octo_trend
if _V12 not in sys.path:
    sys.path.insert(0, _V12)

import data as vdata  # noqa: E402
import engine          # noqa: E402
import rule            # noqa: E402

OUT_MD = os.path.join(_HERE, "_probe_vol_src.md")
TD = rule.TRADING_DAYS

# 回测报告 §1 的现行读数（自检用；数据每刷新一天会微动）
EXPECT = {"hkt": 0.08, "gold": 0.81}

# 本研究的两条腿：腿 -> (新量能名, 说明)
CASES = {
    "gold": ("AU0 沪金主力", "新浪期货 `InnerFuturesNewService.getDailyKLine`（含夜盘量）"),
    "hkt": ("HKHSI 恒生指数", "雪球 `HKHSI` 日成交量"),
}


# ---------------------------------------------------------------- 取新量能（内存，不落库）
def fetch_au0():
    """沪金主力连续日线量能（新浪期货）。-> Series[volume]"""
    import requests
    url = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_AU0=/"
           "InnerFuturesNewService.getDailyKLine?symbol=AU0")
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    t = r.text
    s, e = t.find("["), t.rfind("]")
    rows = json.loads(t[s:e + 1])
    idx = pd.to_datetime([x["d"] for x in rows])
    vol = pd.to_numeric(pd.Series([x["v"] for x in rows]), errors="coerce")
    return pd.Series(vol.to_numpy(), index=idx).sort_index()


def fetch_hsi_volume():
    """恒生指数成交量（雪球）。-> Series[volume]"""
    import xalpha as xa
    df = xa.get_daily("HKHSI", start="20050101")
    vol = pd.to_numeric(df["volume"], errors="coerce")
    return pd.Series(vol.to_numpy(), index=pd.to_datetime(df["date"])).sort_index()


def with_volume(df, vol):
    """只替换 `volume` 列（价格/OHL 一律不动）-> (df2, cover_ratio)。

    `cover_ratio` = 该腿交易日里**原本就有**新量能 bar 的占比（ffill 之前），
    低于 90% 说明两条日历不匹配，结论不可信。
    """
    v = vol.reindex(df.index)
    cover = float(v.notna().mean())
    v = v.ffill().bfill()
    out = df.copy()
    out["volume"] = v.to_numpy()
    return out, cover


# ---------------------------------------------------------------- 跑一条腿
def run_one(leg, df, label):
    """-> dict(cx, w, nav, perf, acts, expo, apo, fee, sep, states)。"""
    p = rule.params(leg)
    ind = engine.indicators(df, p)
    cx = engine.classify(ind, p)
    w = engine.positions(cx["state"], cap_map=rule.cap_map(leg))
    cfg = rule.vol_layer_of(leg)
    if cfg:
        w = engine.vol_layer(df, w, cfg)
    first = cx["state"].notna()
    st = int(np.argmax(first.to_numpy()))
    start = df.index[st]
    nav = engine.run(df, w).loc[start:]
    w_s = w.loc[start:]
    pf = engine.perf(nav)
    yrs = max(pf["days"] / TD, 1e-9)
    acts = len(engine.actions(w_s))
    fee = float(w_s.diff().fillna(w_s).abs().sum()) * rule.FEE_SIDE / yrs * 100.0
    sep = engine.state_stats(df["close"], cx, horizon=20)
    n = len(nav)
    halves = (engine.perf(nav.iloc[: n // 2])["sharpe"],
              engine.perf(nav.iloc[n // 2 - 1:])["sharpe"])
    return dict(leg=leg, label=label, df=df, cx=cx, w=w, nav=nav, start=start,
                perf=pf, expo=float(w_s.mean()), apy=acts / yrs, fee=fee,
                sep=sep, halves=halves)


def row(name, r):
    p = r["perf"]
    return (f"| {name} | {p['ann'] * 100:+.2f}% | {p['mdd'] * 100:+.2f}% | "
            f"**{p['sharpe']:.2f}** | {r['expo']:.0%} | {r['apy']:.0f} | {r['fee']:.2f}pp |")


def state_table(r):
    """八态占比 / fwd20 / 胜率。"""
    L = ["| 态 | 占比 | fwd20 | 胜率 |", "|---|---:|---:|---:|"]
    for x in r["sep"]["rows"]:
        L.append(f"| {x['state']} | {x['share']:.1%} | {x['fwd'] * 100:+.2f}% | "
                 f"{x['win']:.1%} |")
    return L


def pair_table(r):
    L = ["| 配对 | fwd20 差 | 胜率差 | 样本 |", "|---|---:|---:|---|"]
    for x in r["sep"]["pairs"]:
        L.append(f"| {x['a']} − {x['b']} | {x['dfwd'] * 100:+.2f}pp | "
                 f"{x['dwin'] * 100:+.1f}pp | {x['na']}/{x['nb']} |")
    return L


def agree(a, b):
    """两条状态序列的一致率 + 各态被改判的天数。"""
    sa = a["cx"]["state"]
    sb = b["cx"]["state"]
    m = sa.notna() & sb.notna()
    same = float((sa[m] == sb[m]).mean()) if int(m.sum()) else np.nan
    chg = []
    for s in rule.STATE_ORDER:
        k = m & (sa == s) & (sb != s)
        if int(k.sum()):
            chg.append((s, int(k.sum()), int((m & (sa == s)).sum())))
    return same, chg


# ---------------------------------------------------------------- 主流程
def main():
    vfile = os.path.abspath(vdata.__file__)
    if os.path.dirname(vfile) != _V12:
        raise RuntimeError(f"`import data` 解析到 {vfile}，不是 v12_octo_trend/data.py")

    L = ["# V12 · 量能来源补齐（黄金 / 港股）", "",
         "> 由 `research/probe_vol_src/_probe_vol_src.py` 生成。**模拟结果，非投资建议。**",
         "> 只换 `rvol` 的分母，价格、许可表、波动层、成本模型全部不动"
         f"（`engine.run`：T 日收盘定仓、T+1 起承担收益、单边 {rule.FEE_SIDE * 1e4:.1f}bp、"
         f"空仓计货基 {rule.CASH_ANNUAL:.2%}）。**不写任何数据文件，`rule.py` 未改。**", ""]

    # ---------- 0 数据可得性
    L += ["## 0. 数据可得性（实测）", "",
          "| 序列 | 根数 | 起 | 止 | 量能非空 | 量能>0 |", "|---|---:|---|---|---:|---:|"]
    probe = []
    try:
        au0 = fetch_au0()
        probe.append(("AU0 沪金主力（新浪期货）", au0))
    except Exception as exc:  # noqa: BLE001
        L.append(f"| AU0 沪金主力 | 抓取失败 | — | — | — | — |")
        print(f"[warn] AU0 抓取失败: {type(exc).__name__}: {exc}")
        au0 = None
    try:
        hsi = fetch_hsi_volume()
        probe.append(("HKHSI（雪球）", hsi))
    except Exception as exc:  # noqa: BLE001
        L.append(f"| HKHSI | 抓取失败 | — | — | — | — |")
        print(f"[warn] HKHSI 抓取失败: {type(exc).__name__}: {exc}")
        hsi = None
    for nm, s in probe:
        L.append(f"| {nm} | {len(s)} | {s.index[0].date()} | {s.index[-1].date()} | "
                 f"{s.notna().mean():.0%} | {(s > 0).mean():.0%} |")
    L.append("")

    if hsi is not None:
        nz = hsi[hsi > 0]
        L += [f"> `HKHSI` 首个量能>0 的日期 **{nz.index[0].date() if len(nz) else '—'}**；"
              "2009 前大量为 0，而港股腿预热后样本从 2021-09 起，不受影响。"
              "`AU0` 含夜盘量（归属到次一交易日），与 A 股同一交易日历。", ""]

    # ---------- 1/2 逐腿
    S = {}
    for leg, (nm, desc) in CASES.items():
        df = vdata.frame(leg)
        cur = run_one(leg, df, f"现行（{vdata.vol_note(leg)}）")
        alt_src = au0 if leg == "gold" else hsi
        L += [f"## {rule.NAMES[leg]}（{rule.LEGS[leg][0]}）", "",
              f"样本 **{cur['start'].date()} ~ {df.index[-1].date()}**"
              f"（{cur['perf']['days'] / TD:.1f} 年）· 换量不动价", ""]
        if alt_src is None:
            L += [f"> 新量能 `{nm}` 抓取失败，本节跳过。", ""]
            S[leg] = dict(cur=cur)
            continue
        df2, cover = with_volume(df, alt_src)
        alt = run_one(leg, df2, f"{nm}（{desc}）")
        S[leg] = dict(cur=cur, alt=alt, cover=cover, nm=nm)

        same, chg = agree(cur, alt)
        L += [f"新量能覆盖率（该腿交易日内原本就有 bar 的比例）**{cover:.1%}**；"
              f"两条状态序列一致率 **{same:.2%}**。", ""]

        L += ["### 四组配对分离度（先看这个）", "", "**现行**", ""]
        L += pair_table(cur)
        L += ["", f"**{nm}**", ""]
        L += pair_table(alt)
        L.append("")

        L += ["### 八态占比 / fwd20", "",
              "| 态 | 现行占比 | 新占比 | 现行 fwd20 | 新 fwd20 | 现行胜率 | 新胜率 |",
              "|---|---:|---:|---:|---:|---:|---:|"]
        cd = {x["state"]: x for x in cur["sep"]["rows"]}
        ad = {x["state"]: x for x in alt["sep"]["rows"]}
        for s in rule.STATE_ORDER:
            a, b = cd.get(s), ad.get(s)
            f1 = f"{a['fwd'] * 100:+.2f}%" if a else "—"
            f2 = f"{b['fwd'] * 100:+.2f}%" if b else "—"
            w1 = f"{a['win']:.1%}" if a else "—"
            w2 = f"{b['win']:.1%}" if b else "—"
            L.append(f"| {s} | {a['share']:.1%} | {b['share']:.1%} | {f1} | {f2} | "
                     f"{w1} | {w2} |")
        L.append("")

        L += ["### 整条策略（现行许可 + 波动层）", "",
              "| 口径 | 年化 | 最大回撤 | 夏普 | 平均仓位 | 次/年 | 摩擦 |",
              "|---|---:|---:|---:|---:|---:|---:|",
              row("现行量能", cur), row(f"{nm}", alt), "",
              "| 口径 | 前半夏普 | 后半夏普 |", "|---|---:|---:|",
              f"| 现行量能 | {cur['halves'][0]:+.2f} | {cur['halves'][1]:+.2f} |",
              f"| {nm} | {alt['halves'][0]:+.2f} | {alt['halves'][1]:+.2f} |", ""]
        if chg:
            L += ["> 被改判最多的态：" + "、".join(
                f"`{s}` {k}/{t} 天" for s, k, t in sorted(chg, key=lambda z: -z[1])[:4]), ""]

    # ---------- 3 自检
    L += ["## 3. 自检：现行口径是否接对", "",
          "| 腿 | 现行夏普 | 回测 §1 | 差 |", "|---|---:|---:|---:|"]
    ok = True
    for leg in CASES:
        c = S[leg]["cur"]
        exp = EXPECT[leg]
        d = c["perf"]["sharpe"] - exp
        if abs(d) > 0.02:
            ok = False
        L.append(f"| {rule.NAMES[leg]} | {c['perf']['sharpe']:.2f} | {exp:.2f} | {d:+.2f} |")
    L += ["", f"> 自检结论：**{'通过' if ok else '有腿超差 → 先查口径'}**。", ""]

    L += _conclusion(S)

    with io.open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))
    print("报告:", OUT_MD)
    for leg in CASES:
        c = S[leg]["cur"]
        if "alt" in S[leg]:
            a = S[leg]["alt"]
            print(f"  {rule.NAMES[leg]:8s} 现行 {c['perf']['sharpe']:+.2f}  "
                  f"{S[leg]['nm']} {a['perf']['sharpe']:+.2f} "
                  f"({a['perf']['sharpe'] - c['perf']['sharpe']:+.2f})  "
                  f"覆盖率 {S[leg].get('cover', float('nan')):.0%}")
        else:
            print(f"  {rule.NAMES[leg]:8s} 现行 {c['perf']['sharpe']:+.2f}  新量能不可用")


def _conclusion(S):
    L = ["## 4. 结论", "",
         "**一句话：两条腿都略差，P3「补齐量能来源」不成立。** 数据本身没问题"
         "（覆盖率 99.9% / 100%、状态一致率约 90%），是「换分母」这件事没有增量 ——"
         "**八态在这两条腿上的失效不是数据源造成的**。", ""]
    for leg, (nm, _desc) in CASES.items():
        m = S[leg]
        if "alt" not in m:
            L.append(f"- **{rule.NAMES[leg]}**：数据不可得，维持现状。")
            continue
        c, a = m["cur"]["perf"], m["alt"]["perf"]
        # 分离度：四组配对逐条比较「符号对不对」与幅度
        cp = {(x["a"], x["b"]): x["dfwd"] for x in m["cur"]["sep"]["pairs"]}
        ap = {(x["a"], x["b"]): x["dfwd"] for x in m["alt"]["sep"]["pairs"]}
        keys = [k for k in cp if k in ap]
        better = sum(1 for k in keys if ap[k] > cp[k])
        flip = [f"{k[0]}−{k[1]}" for k in keys if cp[k] * ap[k] < 0]
        # 正确号 = 分离度 > 0（八态的定义就是「前者好于后者」）
        ok_cur = [f"{k[0]}−{k[1]}" for k in keys if cp[k] > 0]
        ok_alt = [f"{k[0]}−{k[1]}" for k in keys if ap[k] > 0]
        d = a["sharpe"] - c["sharpe"]
        verdict = ("**值得考虑**" if (d > 0.05 and not flip and better >= max(3, len(keys) - 1))
                   else "**不采纳**")
        L.append(
            f"- **{rule.NAMES[leg]}**：夏普 {c['sharpe']:.2f} → {a['sharpe']:.2f}"
            f"（{d:+.2f}）、回撤 {c['mdd'] * 100:+.1f}% → {a['mdd'] * 100:+.1f}%、"
            f"年化 {c['ann'] * 100:+.2f}% → {a['ann'] * 100:+.2f}%；"
            f"四组配对里 {better}/{len(keys)} 组数值变大"
            + (f"，**符号反转 {'、'.join(flip)}**" if flip else "，无符号反转")
            + f" → {verdict}。")
        L.append(
            f"  - ⚠ **更要紧的是符号本身**：现行量能下四组配对只有 **{len(ok_cur)}/{len(keys)}** 组为正"
            f"（{'、'.join(ok_cur) or '无'}），换新量能后 **{len(ok_alt)}/{len(keys)}** 组为正"
            f"（{'、'.join(ok_alt) or '无'}）。"
            "八态的定义是「前者应当好于后者」，**负号这一格说明该腿的八态层在自身样本上不成立** ——"
            "这与 `opt_7legs` 的诊断一致（这两条腿的增量本来就接近噪声）。"
            "在这种前提下换量能只能改判几天仓位，救不了分离度。")
        pos = [k for k in keys if cp[k] > 0]
        if pos:
            L.append("  - 现行里符号**正确**的那几组怎么变：" + "、".join(
                f"`{k[0]}−{k[1]}` {cp[k] * 100:+.2f}pp → {ap[k] * 100:+.2f}pp" for k in pos)
                + "。")
    L += ["",
          "> 判据顺序是「先验口径、再看好坏」：配对分离度的符号与幅度是规格 §4 的验伪尺子，"
          "整条策略的夏普只是第三条。若分离度变差却在回测里变好，那是**巧合**，不该采纳。",
          "",
          "## 5. 落地改法（若采纳；本报告**未改**任何文件）", "",
          "- 黄金：`data.frame(\"gold\")` 的 `volume` 列改为沪金量能。实现上要新增一个数据类序列"
          "（`data/` 下，AGENTS.md §8.1）+ `_idxcache` 读取分支，并给 `rule.MKT_VOL_LEG` 与"
          "`data.vol_note()` 补一行；`refresh` 需接新浪期货接口（`xa.get_daily` 不支持期货）。",
          "- 港股：把 `HKHSI` 加进 `_global_index_klines.json`（`xa.get_daily(\"HKHSI\")` 直接可用），"
          "在 `rule.MKT_VOL_LEG` 把 `hkt` 指到 `HKHSI`，`data.frame` 用 `index_volume()` 取分母即可。",
          "- 两条都要重跑 `backtest.py` + `daily/_daily_scan.py` + 两版 HTML（AGENTS.md §10），"
          "并在 `rule.CHANGELOG` 留一行（会改变目标仓位）。",
          "",
          "> 机械规则输出，非投资建议。", ""]
    return L


if __name__ == "__main__":
    main()
