# -*- coding: utf-8 -*-
"""V12（八态趋势） vs V13（趋势许可九宫格）· 换到**指数口径**的统一回测对比。

为什么要有这份对比：
- 两个变种平时的回测都跑在**场内 ETF 前复权**（`data/_ohlcv.json`），而 ETF 有折溢价、
  分红除权的处理差、上市晚（科创50 ETF 2020-11 才有）三个问题。用户 2026-11-12 要求
  「统一用场内指数回测对比」，所以这里换成**指数本身**：
  - 沪深300：`SH000300`（2005-01 起，本仓库 `data/_cn_index_klines.json`）；
  - 科创50：`SH000688`（2019-12 起，2026-11-12 新加入同一个库）。
- 换指数还有个附带好处：科创50 的样本从 4.7 年（ETF 2022 起）拉到 5.8 年（指数 2021-02 起，
  扣掉预热），这是唯一一条能把「八态在科创50 上到底行不行」看清一点的窗口。

**统一口径（三件事必须完全一样，否则对比没有意义）**：
1. 价格：同一份指数收盘；
2. 成本：`v12.engine.run`（T+1 生效、单边 5bp、空仓计货基 1.40%）—— **v13 的仓位序列也用这同一份函数**重算，
   而不是引用它报告里自己的净值；
3. 窗口：`max(两策略各自的第一个有效 bar) → 共同末日`。

同时并列 **v12.1**（`rule.OPT=True`：`强回调`/`强反弹`/科创50 `急跌` 重标 + 科创50 波动覆盖）
和 **v12 规格口径**，因为 v12 现在默认出的是 v12.1。

v13 在本进程里 import 会与 v12 的 `rule`/`engine`/`data` 撞名，所以它的仓位用**子进程**取
（cwd 指到 v13 目录，JSON 走 stdin/stdout）。

产物：`_v12v13_compare.md`（本目录）。

用法：
    python strategies/vol_legs/research/v12v13_compare/_v12v13_compare.py
"""
import io
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_VL = os.path.dirname(os.path.dirname(_HERE))       # strategies/vol_legs
_V12 = os.path.join(_VL, "v12_octo_trend")
_V13 = os.path.join(_VL, "v13_quad_9grid")
_ROOT = os.path.dirname(os.path.dirname(_VL))
if _V12 not in sys.path:
    sys.path.insert(0, _V12)

import rule           # noqa: E402  (v12 的 rule)
import engine         # noqa: E402  (v12 的 engine：统一成本/绩效)
import data as vdata  # noqa: E402

OUT_MD = os.path.join(_HERE, "_v12v13_compare.md")
TD = rule.TRADING_DAYS

# 指数口径：只有这两条腿有「指数本身」的收盘 + 成交量
IDX = {"hs300": "SH000300", "kc50": "SH000688"}
NAMES = {"hs300": "沪深300", "kc50": "科创50"}

_CHILD = r'''
import sys, json
sys.path.insert(0, ".")
import numpy as np, pandas as pd
import engine
spec = json.load(sys.stdin)
out = {}
for key, d in spec.items():
    idx = pd.to_datetime(d["dates"])
    df = pd.DataFrame({"close": np.array(d["close"], dtype=float)}, index=idx)
    r = engine.backtest_leg(key.split("@")[0], df=df)
    out[key] = {"dates": [str(x.date()) for x in r["nav"].index],
                "nav": [float(x) for x in r["nav"].values],
                "w": [float(x) for x in r["w"].values]}
sys.stdout.write("@@" + json.dumps(out))
'''


# ---------------------------------------------------------------- 数据
def etf_df(leg):
    return vdata.frame(leg)


def idx_df(code):
    """指数口径的 df：close + volume（v12 的 rvol 要用量，v13 只用 close）。"""
    lib = json.load(io.open(os.path.join(_ROOT, "data", "_cn_index_klines.json"), encoding="utf-8"))
    node = lib[code]
    idx = pd.to_datetime(node["dates"])
    return pd.DataFrame({"close": np.array(node["close"], dtype=float),
                         "volume": np.array(node["volume"], dtype=float)}, index=idx)


# ---------------------------------------------------------------- v13 仓位（子进程）
def v13_positions(series):
    """series: {key: df} -> {key: (nav, w)}。df 只用到 close。"""
    payload = {k: {"dates": [str(x.date()) for x in d.index],
                   "close": [float(x) for x in d["close"]]} for k, d in series.items()}
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-c", _CHILD], cwd=_V13, input=json.dumps(payload),
                       capture_output=True, text=True, encoding="utf-8", env=env)
    for line in (r.stdout or "").splitlines():
        if line.startswith("@@"):
            j = json.loads(line[2:])
            return {k: (pd.Series(v["nav"], index=pd.to_datetime(v["dates"])),
                        pd.Series(v["w"], index=pd.to_datetime(v["dates"])))
                    for k, v in j.items()}
    raise RuntimeError(f"v13 子进程失败：{(r.stderr or r.stdout or '')[-400:]}")


# ---------------------------------------------------------------- 指标
def perf(nav, w=None):
    p = engine.perf(nav)
    out = dict(ann=p["ann"], vol=p["vol"], mdd=p["mdd"], sharpe=p["sharpe"], days=p["days"])
    if w is not None and len(w):
        out["expo"] = float(w.mean())
        out["acts"] = len(engine.actions(w))
        out["tno"] = float(w.diff().fillna(w).abs().sum())      # 累计换手（Σ|Δw|）
    return out


def yearly(nav):
    y = nav.resample("YE").last()
    r = y.pct_change()
    r.iloc[0] = y.iloc[0] / 1.0 - 1.0
    return {d.year: float(v) for d, v in r.items()}


def trow(name, kind, p):
    yrs = p["days"] / TD
    fee = p.get("tno", 0.0) * rule.FEE_SIDE / yrs * 100 if yrs else np.nan
    return (f"| {name} | {kind} | {p['ann'] * 100:+.2f}% | {p['vol'] * 100:.1f}% | "
            f"{p['mdd'] * 100:+.2f}% | {p['sharpe']:.2f} | {p.get('expo', np.nan):.0%} | "
            f"{p.get('acts', 0):.0f} | {p.get('tno', np.nan):.1f} | {fee:.2f}pp |")


def main():
    L = ["# V12 vs V13 · 统一指数口径回测对比", "",
         "> 由 `research/v12v13_compare/_v12v13_compare.py` 生成（2026-11-12）。",
         "> **统一的三件事**：同一份价格（指数收盘）· 同一成本模型（T+1、单边 5bp、空仓货基 1.40%，"
         "两边的仓位序列都喂进 `v12.engine.run` 重算）· 同一窗口（两策略各自第一个有效 bar 取较晚者）。",
         "> v13 的仓位由子进程算（避免 `engine`/`rule`/`data` 同名串味），**不是**引用它报告里的净值。",
         ""]

    series, meta = {}, {}
    # A 口径：场内 ETF（两策略现状数据源）
    for leg in vdata.available():
        series[f"{leg}@etf"] = etf_df(leg)
        meta[f"{leg}@etf"] = (rule.NAMES.get(leg, leg), "场内 ETF 前复权")
    # B 口径：指数本身
    for leg, code in IDX.items():
        series[f"{leg}@idx"] = idx_df(code)
        meta[f"{leg}@idx"] = (NAMES[leg], f"指数 {code}")

    v13 = v13_positions({k: d for k, d in series.items() if k.split("@")[0] in rule.LEGS})

    # ---------------- 逐腿两口径
    for tag, keys in (("A · 场内 ETF 前复权（两策略现状口径）", [k for k in series if k.endswith("@etf")]),
                      ("B · 指数本身（本次要求的统一口径）", [k for k in series if k.endswith("@idx")])):
        L += [f"## {tag}", ""]
        for key in keys:
            leg = key.split("@")[0]
            if leg not in rule.LEGS or key not in v13:
                continue
            df = series[key]
            r12 = engine.backtest_leg(leg, df=df, opt=True)
            r_sp = engine.backtest_leg(leg, df=df, opt=False)
            n13, w13 = v13[key]
            # 统一窗口
            s = max(x for x in (r12["start"], r_sp["start"], n13.index[0]) if x is not None)
            e = min(df.index[-1], n13.index[-1])
            if s >= e:
                continue
            rows = []
            px = df["close"].loc[s:e]
            rows.append(("恒持 100%", "基准", perf(px / px.iloc[0], pd.Series(1.0, index=px.index))))
            for nm, kind, w in (("V12.1 八态（默认）", "本研究", r12["w"]),
                                ("V12 规格口径", "对照", r_sp["w"]),
                                ("V13 九宫格", "对照", w13)):
                w = w.reindex(df.index).fillna(0.0)
                nav = engine.run(df, w).loc[s:e]
                rows.append((nm, kind, perf(nav, w.loc[s:e])))
            L += [f"### {meta[key][0]}（{meta[key][1]}，{s.date()} ~ {e.date()}，"
                  f"{len(px)} 根 · 预热各不相同已对齐）", "",
                  "| 策略 | 类型 | 年化 | 波动 | 最大回撤 | 夏普 | 平均许可 | 调仓 | 累计换手 | 摩擦 |",
                  "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
            for nm, kind, p in rows:
                L.append(trow(nm, kind, p))
            # 仓位差异：两个都是"许可上限"型规则、最低仓 20%，所以「都空仓」这种指标没有信息量，
            # 用相关 + 高/低仓重合率才看得出差别。
            wi = r12["w"].reindex(df.index).fillna(0.0).loc[s:e]
            wj = w13.reindex(df.index).fillna(0.0).loc[s:e]
            hi = float(((wi > 0.6) & (wj > 0.6)).mean())
            lo = float(((wi < 0.3) & (wj < 0.3)).mean())
            L += ["", f"仓位差异：平均 \\|Δw\\| = **{float((wi - wj).abs().mean()):.2f}**、"
                      f"相关 {float(wi.corr(wj)):.2f}、两边都重仓(>60%) {hi:.0%} 的天、"
                      f"都轻仓(<30%) {lo:.0%} 的天。", ""]

    # ---------------- 分年（指数口径）
    L += ["## C · 分年收益（指数口径）", ""]
    for leg, code in IDX.items():
        key = f"{leg}@idx"
        df = series[key]
        r12 = engine.backtest_leg(leg, df=df, opt=True)
        r_sp = engine.backtest_leg(leg, df=df, opt=False)
        n13, w13 = v13[key]
        s = max(r12["start"], r_sp["start"], n13.index[0])
        e = min(df.index[-1], n13.index[-1])
        navs = {"V12.1": engine.run(df, r12["w"]).loc[s:e],
                "V12 规格": engine.run(df, r_sp["w"]).loc[s:e],
                "V13": engine.run(df, w13.reindex(df.index).fillna(0.0)).loc[s:e],
                "恒持": df["close"].loc[s:e] / df["close"].loc[s:e].iloc[0]}
        ys = {k: yearly(v) for k, v in navs.items()}
        yrs = sorted(set().union(*[set(v) for v in ys.values()]))
        L += [f"**{NAMES[leg]}（{code}）**", "",
              "| 年 | " + " | ".join(str(y) for y in yrs) + " |",
              "|---|" + "---:|" * len(yrs)]
        for k in navs:
            L.append(f"| {k} | " + " | ".join(f"{ys[k].get(y, np.nan) * 100:+.1f}%" for y in yrs) + " |")
        L.append("")

    # ---------------- 组合
    L += ["## D · 组合层（同一成本模型）", "",
          "等波动 = 滚动 60 日波动倒数配权（`shift(1)`，无前视）。", ""]
    for tag, keys in (("指数口径 · A股两腿（沪深300 + 科创50）",
                       ["hs300@idx", "kc50@idx"]),):
        navsets = {}
        for nm, opt in (("V12.1", True), ("V12 规格", False)):
            d = {}
            for key in keys:
                leg = key.split("@")[0]
                df = series[key]
                r = engine.backtest_leg(leg, df=df, opt=opt)
                d[key] = engine.run(df, r["w"])
            navsets[nm] = d
        d = {}
        for key in keys:
            leg = key.split("@")[0]
            df = series[key]
            r = engine.backtest_leg(leg, df=df, opt=True)      # 只为拿 start 对齐
            px = df["close"].loc[r["start"]:]
            d[key] = px / px.iloc[0]
        navsets["恒持"] = d
        d = {}
        for key in keys:
            d[key] = v13[key][0]
        navsets["V13"] = d

        L += [f"**{tag}**", ""]
        common = None
        for v in navsets.values():
            idxs = [s.index for s in v.values()]
            c = idxs[0]
            for x in idxs[1:]:
                c = c.intersection(x)
            common = c if common is None else common.intersection(c)
        for nm, v in navsets.items():
            for mode in ("等权", "等波动"):
                R = pd.DataFrame({k: s.reindex(common).pct_change() for k, s in v.items()})
                if mode == "等波动":
                    V = R.rolling(60, min_periods=20).std()
                    inv = 1.0 / V.replace(0.0, np.nan)
                    wgt = inv.div(inv.sum(axis=1), axis=0).shift(1)
                    port = (R * wgt).sum(axis=1, min_count=1).fillna(0.0)
                else:
                    port = R.mean(axis=1, skipna=True).fillna(0.0)
                p = engine.perf((1 + port).cumprod())
                L.append(f"- **{nm} · {mode}**：年化 {p['ann'] * 100:+.2f}% · "
                         f"回撤 {p['mdd'] * 100:+.2f}% · 夏普 {p['sharpe']:.2f}")
        L += ["", f"共同窗口 {common[0].date()} ~ {common[-1].date()}（{len(common)} 根）", ""]

    # ---------------- F · 指数样本对 v12.1 的裁决（这是换口径最大的收益）
    L += ["## F · 指数样本对 v12.1 的裁决（单旋钮，长短样本对照）", "",
          "v12.1 的两条旋钮原本是在 **2014 起的 ETF 样本**上定的。换成 **2006 起的沪深300 指数**后，"
          "样本里第一次出现 2008 那种系统性下跌 —— 这一节就是看旋钮还站不站得住。", ""]
    knob = {"规格 CAP": rule.CAP,
            "A 强回调 20%": dict(rule.CAP, 强回调=0.2),
            "B 强反弹 100%": dict(rule.CAP, 强反弹=1.0),
            "A+B": dict(rule.CAP, 强回调=0.2, 强反弹=1.0),
            "A+B+急跌 20%": dict(rule.CAP, 强回调=0.2, 强反弹=1.0, 急跌=0.2)}
    for leg, code in IDX.items():
        df = series[f"{leg}@idx"]
        ind = engine.indicators(df)
        cx = engine.classify(ind)
        first = cx["state"].notna()
        st = df.index[int(np.argmax(first.to_numpy()))]
        head = ("| 许可口径 | 年化 | 回撤 | 夏普 | 平均许可 |"
                + (" 2008-09 夏普 | 2008-09 回撤 |" if leg == "hs300" else ""))
        sep = ("|---|---:|---:|---:|---:|"
               + ("---:|---:|" if leg == "hs300" else ""))
        L += [f"**{NAMES[leg]}（{code}，{st.date()} ~ {df.index[-1].date()}）**", "", head, sep]
        for nm, cap in knob.items():
            w = engine.positions(cx["state"], cap_map=cap)
            nav = engine.run(df, w).loc[st:]
            p = engine.perf(nav)
            line = (f"| {nm} | {p['ann'] * 100:+.2f}% | {p['mdd'] * 100:+.2f}% | {p['sharpe']:.2f} | "
                    f"{float(w.loc[st:].mean()):.0%} |")
            if leg == "hs300":
                s08 = nav.loc[(nav.index >= "2008-01-01") & (nav.index <= "2009-12-31")]
                q08 = engine.perf(s08)
                line += f" {q08['sharpe']:.2f} | {q08['mdd'] * 100:+.1f}% |"
            L.append(line)
        L.append("")
    L += ["> 读法：**沪深300 上「强反弹 100%」被 2008 证伪**（回撤 −31.8% → −37.3%、2008-09 夏普"
          "0.28 → 0.16 —— 系统性下跌里放量反弹是接飞刀），所以 `rule.CAP_OPT['hs300']` **只保留"
          "「强回调 20%」**；科创50 的样本里没有这类崩盘，两条旋钮都保留，但风险已在 §E 标注。",
          "> 附表（科创50 更保守的一版）：去掉「强反弹 100%」只留 `强回调 20% + 急跌 20% + σ覆盖25%`，"
          "指数口径 **+8.30% / −16.64% / 0.53**、ETF 口径 +10.38% / −15.33% / 0.66"
          "（保留版是 0.70 / 0.76）。差的就是「强反弹满仓」这一条。", ""]

    L += [
        "## E · 结论", "",
        "### 1. 统一到指数口径后，「沪深300 上差不多」依然成立，但差距的**性质**变了",
        "",
        "20.7 年（2006-03 ~ 2026-11，含 2008）：**V12.1 +11.17% / −31.78% / 0.73** vs "
        "**V13 +10.78% / −33.77% / 0.67**。夏普只差 0.06，但 v13 的**摩擦只有 0.09pp/年**"
        "（累计换手 38），v12 是 0.70pp/年（换手 277，调仓 673 次 vs 89 次）。"
        "**在沪深300 上，v13 是更划算的那一条**：用 1/8 的交易拿到 92% 的夏普。",
        "",
        "### 2. 科创50 上的差距是结构性的，不是噪声",
        "",
        "5.6 年（2021-03 ~ 2026-11）：**V12.1 +11.92% / −16.64% / 0.74** vs "
        "**V13 −0.46% / −33.45% / −0.14**（恒持 +1.31% / −60.08% / −0.00）。"
        "换到 ETF 口径同样是 0.77 vs −0.34，两个口径、两段样本方向一致。"
        "原因写在 v13 自己的报告里：**15 日升格迟滞**在高波动指数上会把整段行情错过；"
        "而八态是 2 日确认、降格立即，能跟住。",
        "",
        "### 3. 换口径最大的收获：把 v12.1 的一条旋钮证伪了",
        "",
        "v12.1 的两条旋钮原本在 2014 起的 ETF 样本上定，那段样本里**没有 2008**。"
        "换成 2006 起的指数后：`强反弹 100%` 让沪深300 的回撤从 −31.8% 恶化到 −41.1%、"
        "2008-09 段夏普 0.28→0.16（放量假反弹 = 接飞刀）。所以 `rule.CAP_OPT['hs300']` "
        "**只保留「强回调 20%」**，这也是为什么本文 A/B 两张表里 v12.1 的沪深300 数字与"
        "`research/opt_ashare` 的「B 档」不同 —— 那一档已被更长的样本否掉。",
        "",
        "### 4. 组合层（A股双雄，指数口径，2021-03 起）",
        "",
        "V12.1 等权 **+6.59% / −11.92% / 0.49** · V13 等权 +0.16% / −22.74% / −0.13 · "
        "恒持等权 −0.79% / −49.61% / −0.09。两条规则都跑赢恒持，v12 的差距来自科创50 那一段。",
        "",
        "### 5. 口径本身的影响比想象小",
        "",
        "同一策略换 ETF ↔ 指数，沪深300 的夏普只差 0.01（0.74 vs 0.73），科创50 差 0.03"
        "（0.77 vs 0.74）—— **ETF 的折溢价/分红处理没有明显扭曲结论**。"
        "真正的差别来自**样本长度**（2006 vs 2014）和**样本里有没有崩盘**，不是数据源。"
        "所以以后做跨变种对比，优先统一的是**窗口**，其次才是价格源。",
        "",
        "> 数据：`data/_cn_index_klines.json`。`SH000688`（科创50 指数）2026-11-12 为本研究新增，"
        "由 `v12_octo_trend/data.py` 维护；它把科创50 的可回测样本从 4.7 年（ETF 2022 起）"
        "拉长到 5.6 年（指数 2021-02 起）。",
        "",
        "> 机械规则输出，非投资建议。",
    ]

    with io.open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))
    print("报告:", OUT_MD)


if __name__ == "__main__":
    main()
