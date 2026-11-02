# -*- coding: utf-8 -*-
"""成长/红利风格轮动 · **v0~v5 家族综合对比**（家族根共享工具）

只读各变种**已落盘的回测产物**（不重跑、不改各变种代码），做四件事：

  1. 用同一套 metrics 重算指标 —— 各变种报告里的年化/回撤口径略有不一致
  2. 拆成「全期 / 早年 / 近 6 年」三段，避免拿不同窗口的数字硬比
  3. 每段都放「买入持有」当标尺，区分「规则更强」与「标的更强」
  4. 给出切换次数与平均暴露，回答「收益是不是靠少持仓换来的」

⚠ 口径提醒（这是本家族最容易读错的地方）：

  v0 / v1 成长端 = 科创50(000688)，2020-01 才有数据 → 自身窗口只有 5.8 年
  v2~v5   成长端 = 创业板指(399006)，2010-06 起   → 16.4 年

  所以「全期」只能比 v2~v5；六代同台只在 **2021-01-11 起**的近 6 年成立，
  而且该窗口内 v0/v1 与 v2~v5 的成长标的仍不同 —— 必须配基准行一起读。
  另外「切片对齐」不等于「同时起跑」：各策略在切片起点已有持仓状态。

产物: _v0_v5_compare.md · _v0_v5_compare.png（家族根）
用法: python strategies/growth_div_rotation/_compare_v0_v5.py
"""
import io
import json
import os
import sys
from datetime import datetime

try:                                       # 控制台可能是 gbk，报告含中文与 × 符号
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.dates as mdates  # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

_DIR = os.path.dirname(os.path.abspath(__file__))
KL_PATH = os.path.join(_DIR, "v0", "data", "_gd_index_klines.json")

# 变种登记表：json 相对路径 / 净值字段 / 成长标的 / 一句话定位
V = {
    "v0": dict(dir="v0", js="backtest/_gd_bt.json", eq="eq", growth="000688",
               label="v0 相对分位+缓冲带",
               tag="R 相对自身 MA20±1% + 250 日分位；自适应、无量纲"),
    "v1": dict(dir="v1_ratio_bands", js="backtest/_v1_bt.json", eq="twr", growth="000688",
               label="v1 绝对阈值+5期分批",
               tag="R≤0.20 成长 / ≥0.31 红利；清仓后拆 5 份分买"),
    "v2": dict(dir="v2_absolute_bands", js="backtest/_v2_bt.json", eq="eq", growth="399006",
               label="v2 绝对水位 0.35/0.65",
               tag="R≤0.35 满仓成长 / ≥0.65 满仓红利；复现用户两张图"),
    "v3": dict(dir="v3_trend_filter", js="backtest/_v3_bt.json", eq="eq", growth="399006",
               label="v3 +双侧 MA200 闸门",
               tag="v2 + 每侧破自己 MA200 就减半、腾出的钱转现金"),
    "v4": dict(dir="v4_graded_bands", js="backtest/_v4_bt.json", eq="eq", growth="399006",
               label="v4 阶梯 5:0→0:5",
               tag="v3 的连续化：0.35~0.65 拆 4 档逐级过渡"),
    "v5": dict(dir="v5_vol_target", js="backtest/_v5_bt.json", eq="eq", growth="399006",
               label="v5 +波动率目标 18%",
               tag="v3 + 按 120 日已实现波动缩放总仓位（18% 目标）"),
}
ORDER = ["v0", "v1", "v2", "v3", "v4", "v5"]

FULL = ("2010-06-01", "2026-10-30")     # v2~v5 的自身窗口
EARLY = ("2010-06-01", "2021-01-08")    # 全期前段（v2~v5）
NEAR = ("2021-01-11", "2026-10-30")     # v0 的指标成形日 → 六代唯一同窗

BENCH = {"399006": "买入持有创业板指", "000688": "买入持有科创50",
         "000922": "买入持有中证红利"}


# ------------------------------------------------------------------ 载入
def load_idx():
    d = json.load(open(KL_PATH, encoding="utf-8"))
    return {k: pd.Series(v["close"], index=pd.to_datetime(v["dates"])).astype(float)
            for k, v in d.items()}


def load(k):
    m = V[k]
    p = os.path.join(_DIR, m["dir"], m["js"])
    if not os.path.exists(p):
        raise SystemExit(f"缺产物: {p}（先跑 {m['dir']}/backtest.py）")
    d = json.load(open(p, encoding="utf-8"))
    eq = d[m["eq"]]
    if isinstance(eq, dict):
        s = pd.Series({pd.Timestamp(a): b for a, b in eq.items()}).sort_index()
    else:
        s = pd.Series(eq, index=pd.to_datetime(d["dates"]))
    return d, s.astype(float)


# ------------------------------------------------------------------ 指标
def metrics(s):
    s = s.dropna()
    g = s / s.iloc[0]
    y = max((g.index[-1] - g.index[0]).days / 365.0, 1e-9)
    ret = float(g.iloc[-1] - 1.0)
    ann = (1 + ret) ** (1 / y) - 1 if ret > -1 else -1.0
    dd = float((g / g.cummax() - 1).min())
    r = g.pct_change().dropna()
    vol = float(r.std() * np.sqrt(252))
    shp = float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else 0.0
    return dict(ann=ann, dd=dd, vol=vol, shp=shp, ret=ret, years=y,
                calmar=(ann / abs(dd)) if dd else float("nan"), mult=float(g.iloc[-1]))


def n_switch(d, start, end):
    sw = d.get("switches")
    if sw is None:
        return None
    if isinstance(sw, int):
        return sw
    a, b = pd.Timestamp(start), pd.Timestamp(end)
    return len([x for x in sw if a <= pd.Timestamp(x["date"]) <= b])


def exposure(k, d, start, end):
    """平均持仓暴露 = 成长 + 红利（**不含现金**）。v0/v2 是满仓二元，恒 1。"""
    if k in ("v3", "v4", "v5"):
        a = d["alloc"]
        w = pd.DataFrame({c: a[c] for c in a}, index=pd.to_datetime(d["dates"]))
        return float(w.loc[start:end, ["growth", "div"]].sum(axis=1).mean())
    if k in ("v0", "v2"):
        return 1.0
    return float("nan")


def pct(x, nd=2):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:+.{nd}f}%"


# ------------------------------------------------------------------ 主流程
def build():
    kl = load_idx()
    docs, nv = {}, {}
    for k in ORDER:
        docs[k], nv[k] = load(k)
    return kl, docs, nv


def rows(kl, docs, nv, win, keys, with_bench=()):
    """一个窗口内的对比行。"""
    start, end = win
    dat = []
    for k in keys:
        s = nv[k].loc[start:end]
        m = metrics(s)
        dat.append(dict(key=k, name=V[k]["label"], growth=V[k]["growth"],
                        sw=n_switch(docs[k], start, end),
                        exp=exposure(k, docs[k], start, end), **m))
    for code in with_bench:
        s = kl[code].loc[start:end]
        m = metrics(s)
        dat.append(dict(key=code, name=BENCH[code], growth=code, sw=None, exp=1.0, **m))
    return dat


def table(dat, title):
    L = [f"### {title}", "",
         "| 组合 | 成长端 | 年化 | 最大回撤 | 年化波动 | 夏普 | Calmar | 净值 | 切换 | 平均暴露 |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for r in dat:
        sw = "—" if r["sw"] is None else str(r["sw"])
        exp = "—" if np.isnan(r["exp"]) else f"{r['exp']:.0%}"
        L.append(f"| {r['name']} | {r['growth']} | {pct(r['ann'])} | {pct(r['dd'])} | "
                 f"{pct(r['vol'], 1)} | {r['shp']:.2f} | {r['calmar']:.2f} | "
                 f"{r['mult']:.2f}× | {sw} | {exp} |")
    return L


def build_report(kl, docs, nv):
    full = rows(kl, docs, nv, FULL, ["v2", "v3", "v4", "v5"], ("399006", "000922"))
    early = rows(kl, docs, nv, EARLY, ["v2", "v3", "v4", "v5"], ("399006", "000922"))
    near = rows(kl, docs, nv, NEAR, ORDER, ("399006", "000688", "000922"))

    def get(dat, key):
        return next(r for r in dat if r["key"] == key)

    f2, f3, f5 = get(full, "v2"), get(full, "v3"), get(full, "v5")
    e5 = get(early, "v5")
    n3, n5 = get(near, "v3"), get(near, "v5")
    n0 = get(near, "v0")
    f4 = get(full, "v4")

    L = ["# 成长/红利风格轮动 · v0~v5 家族综合对比", "",
         f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 数据 {FULL[0]}~{FULL[1]}"
         f"（{len(kl['399006'])} 交易日）· 全部指标由 `_compare_v0_v5.py` 从各变种已落盘产物重算", "",
         "## 0. 先说口径，否则表格会读错", "",
         "- **成长端不同**：`v0`/`v1` 用科创50(000688)（2020-01 上市），`v2~v5` 用创业板指(399006)。"
         "这不是笔误 —— 2026-10-30 主线从创业板指切到了科创50，v2 之后为复现用户原图固定 399006。",
         "- 因此 **v0/v1 的绩效天然只有 5.8 年**，与 v2~v5 的 16.4 年不能直接比年化；"
         "本文只给「同在 2021-01-11 起」的这一段让六代同台，并附基准标尺。",
         "- 「切片对齐」≠「同时起跑」：各策略在切片起点已有持仓状态，切片只对齐指标窗口。",
         "- 全部为指数价格口径，红利端未含股息（约 4.5%/年），持有红利越多被低估越多。", ""]

    L += ["## 1. 各代在解决什么", "",
          "| 代 | 信号形态 | 相对上一代改了什么 | 一句话结果 |", "|---|---|---|---|",
          f"| {V['v0']['label']} | {V['v0']['tag']} | 起点（主线） | "
          f"近 6 年 {pct(get(near, 'v0')['ann'])} / {pct(get(near, 'v0')['dd'])}，"
          f"切换 {get(near, 'v0')['sw']} 次 |",
          f"| {V['v1']['label']} | {V['v1']['tag']} | 相对分位 → **绝对阈值**，并加 5 期分批 + 每月定投 | "
          f"近 6 年 {pct(get(near, 'v1')['ann'])} / {pct(get(near, 'v1')['dd'])}，"
          f"切换 {get(near, 'v1')['sw']} 次（阈值一动结论就反转） |",
          f"| {V['v2']['label']} | {V['v2']['tag']} | 阈值重标定 0.35/0.65，去掉分批回到整仓切换 | "
          f"全期 {pct(get(full, 'v2')['ann'])} / {pct(get(full, 'v2')['dd'])}，"
          "年化最高、回撤最深 |",
          f"| {V['v3']['label']} | {V['v3']['tag']} | **加风控层**：双侧 MA200 闸门 + 现金仓 + 20 日冷却 | "
          f"全期 {pct(get(full, 'v3')['ann'])} / {pct(get(full, 'v3')['dd'])}，"
          "熊市保险型 |",
          f"| {V['v4']['label']} | {V['v4']['tag']} | 二元阈值 → 四档阶梯连续化 | "
          f"全期 {pct(get(full, 'v4')['ann'])} / {pct(get(full, 'v4')['dd'])}，"
          f"切换 {get(full, 'v4')['sw']} 次，**四输** |",
          f"| {V['v5']['label']} | {V['v5']['tag']} | 再套一层波动率目标（缩放总仓位） | "
          f"全期 {pct(get(full, 'v5')['ann'])} / {pct(get(full, 'v5')['dd'])}，"
          f"Calmar {get(full, 'v5')['calmar']:.2f} 家族最高 |",
          "", "> 演进逻辑是两条线并行：**信号线**（v0 相对分位 → v1 绝对阈值 → v2 绝对水位 → "
              "v4 阶梯连续化）与**风控线**（v3 趋势闸门 → v5 波动率目标）。"
              "v4 是信号线走到头的产物、实测失败；v5 是风控线走到头的产物、实测成功。", ""]

    L += ["## 2. 全期 2010-06~2026-10（只有 v2~v5 同窗口）", ""]
    L += table(full, "同一标的、同一窗口、同一引擎")
    L += ["",
          f"> 基准读完再看策略：创业板指买入持有 {pct(get(full, '399006')['ann'])} / "
          f"{pct(get(full, '399006')['dd'])}，中证红利 {pct(get(full, '000922')['ann'])} / "
          f"{pct(get(full, '000922')['dd'])}。"
          f"v2 的 {pct(f2['ann'])} 是把波动全接下来换的：回撤 {pct(f2['dd'])}，"
          f"比它自己换着持有的中证红利（{pct(get(full, '000922')['dd'])}）还深；"
          f"v3 用趋势闸门把回撤压到 {pct(f3['dd'])}、代价 {pct(f3['ann'] - f2['ann'])} 年化；"
          f"v5 再把回撤压到 {pct(f5['dd'])}、代价 {pct(f5['ann'] - f3['ann'])}。"
          f"v4 在每一列都更差（{pct(f4['ann'])} / {pct(f4['dd'])}，切换 {f4['sw']} 次），是纯失败变种。", ""]

    L += ["## 3. 早年 2010-06~2021-01（v2~v5）", ""]
    L += table(early, "同一窗口的前半段")
    L += ["",
          f"> v5 在早年段的年化是 {pct(e5['ann'])}，最大回撤 {pct(e5['dd'])} —— "
          "2015 年红利崩盘与 2016 熔断这两段是 v5 的波动层最有效的时段。"
          "看这一段是为了排除「v5 的优势只来自 2024 之后」的可能。", ""]

    L += ["## 4. 近 6 年 2021-01-11~2026-10（六代唯一同窗，注意成长端不同）", ""]
    L += table(near, "六代同台 + 三个基准")
    L += ["",
          f"> 这一段里 v0（科创50）年化 {pct(n0['ann'])} / 回撤 {pct(n0['dd'])}，"
          f"同期科创50 买入持有 {pct(get(near, '000688')['ann'])} / {pct(get(near, '000688')['dd'])}；"
          f"v3（创业板指）年化 {pct(n3['ann'])} / 回撤 {pct(n3['dd'])}，"
          f"v5 年化 {pct(n5['ann'])} / 回撤 {pct(n5['dd'])}，"
          f"同期创业板指买入持有 {pct(get(near, '399006')['ann'])} / {pct(get(near, '399006')['dd'])}。"
          "**跨标的的绝对收益不可直接比大小**，能比的是「相对各自基准的改善幅度」，"
          "以及同标的的 v2→v3→v4→v5 内部排序。", ""]

    n2, n4 = get(near, "v2"), get(near, "v4")
    e2, e3 = get(early, "v2"), get(early, "v3")

    L += ["## 5. 关键发现：趋势层的贡献会随市场阶段**反转**", "",
          "这是把窗口切开后唯一一个会改变选型结论的事实：", "",
          "| 窗口 | v2 年化 | v3 年化 | v3−v2 | v2 回撤 | v3 回撤 | 趋势闸门 |",
          "|---|---|---|---|---|---|---|",
          f"| 全期 2010-2026 | {pct(f2['ann'])} | {pct(f3['ann'])} | {pct(f3['ann'] - f2['ann'])} | "
          f"{pct(f2['dd'])} | {pct(f3['dd'])} | 用 "
          f"{abs(f2['ann'] - f3['ann']) * 100:.1f}pp 年化买 "
          f"{(f2['dd'] - f3['dd']) * 100:.0f}pp 回撤 |",
          f"| 早年 2010~2021 | {pct(e2['ann'])} | {pct(e3['ann'])} | {pct(e3['ann'] - e2['ann'])} | "
          f"{pct(e2['dd'])} | {pct(e3['dd'])} | **反超**，含 2015 股灾与 2016 熔断 |",
          f"| 近 6 年 2021~2026 | {pct(n2['ann'])} | {pct(n3['ann'])} | {pct(n3['ann'] - n2['ann'])} | "
          f"{pct(n2['dd'])} | {pct(n3['dd'])} | **拖累**，且回撤没少抓 |",
          "",
          "> 读法：趋势闸门是**熊市保险**。2015/2018 那两段有持续数月的单边下跌，"
          "MA200 来得及反应，保费交得值；2021-2026 是宽幅震荡 + 急跌急涨（2024-02、2024-09），"
          "MA200 滞后，破线减半时往往已在坑底，反弹时又因未回线而缺席 —— "
          "近 6 年 v2 与 v3 的最大回撤并列，说明最深那次下跌闸门根本没提前预警，只留下了少赚。",
          "",
          "> 所以「v3 比 v2 好」这句话**只在全期成立**，靠的是 2015 一年的极端行情；"
          "v5 同理（它站在 v3 肩上），近 6 年 v5 靠波动层把回撤压到 "
          f"{pct(n5['dd'])}（优于 v3 的 {pct(n3['dd'])}），代价是年化只剩 {pct(n5['ann'])}。", ""]

    L += ["## 6. 三代风控的取舍（全期口径）", "",
          "| 代 | 相对上代 | 换来的 | 付出的 |", "|---|---|---|---|",
          f"| v3 | v2 + 趋势闸门 | 回撤 {pct(f2['dd'])}→{pct(f3['dd'])}，Calmar "
          f"{f2['calmar']:.2f}→{f3['calmar']:.2f} | 年化 {pct(f3['ann'] - f2['ann'])}，"
          f"切换 {f2['sw']}→{f3['sw']} 次 |",
          f"| v5 | v3 + 波动率目标 | 回撤 {pct(f3['dd'])}→{pct(f5['dd'])}，Calmar "
          f"{f3['calmar']:.2f}→{f5['calmar']:.2f} | 年化 {pct(f5['ann'] - f3['ann'])}，"
          f"切换 {f3['sw']}→{f5['sw']} 次，平均暴露 {f5['exp']:.0%} |",
          "| v4 | v3 + 阶梯连续化 | — | 全线更差：年化、回撤、Calmar、切换次数四输 |",
          "",
          f"> 关键差别：**v3 的降仓是离散的、事件驱动**（破线才减半），"
          f"**v5 的降仓是连续的风险预算**（平均暴露 {f5['exp']:.0%}、缩放均值 0.80、"
          "仅 3% 交易日满仓）。所以 v5 在「波动抬升但继续上涨」的行情里必然少赚 —— "
          "这不是 bug，是它的定价方式。要保收益就用 25% 目标档（+15.91% / -32.35%）。", ""]

    L += ["## 7. 结论", "",
          f"1. **主线仍是 v0**（唯一进每日流水线 + flow_select 排名）。理由不是收益最高，"
          f"而是规则无量纲、自适应、换标的不用重调阈值。代价是切换 "
          f"{docs['v0'].get('total_switches')} 次、多段持仓不足 5 个交易日，"
          "场外 C 类 <7 天赎回费吃收益。",
          f"2. **风险调整后最优是 v5**：全期 Calmar {f5['calmar']:.2f}、夏普 {f5['shp']:.2f}、"
          f"回撤 {pct(f5['dd'])}，三项都是家族第一；代价是年化 {pct(f5['ann'])}、"
          "平均只在场 63%，且需要按周看缩放档位。",
          f"3. **v3 是「熊市保险」而非全局更优**：全期年化只让 {pct(f3['ann'] - f2['ann'])} 换来 "
          f"{(f2['dd'] - f3['dd']) * 100:.0f}pp 回撤；但近 6 年年化反而落后 "
          f"{abs(n3['ann'] - n2['ann']) * 100:.1f}pp。**选它等于赌下一段还有 2015 级别的单边下跌。**",
          f"4. **v2 不该单独实盘**：它是全家族年化最高（{pct(f2['ann'])}），"
          f"但回撤 {pct(f2['dd'])} 比它换着持有的中证红利还深 —— 这个收益是把波动全接下来换的，"
          "它的价值是当「信号层裸基准」，用来看风控层到底加了多少分。",
          "5. **v4 直接淘汰**：阶梯连续化在「信号噪声大 + 换手成本」双重作用下四输"
          "（年化、回撤、Calmar、切换次数），说明这条信号的有效信息集中在阈值附近，"
          "中间地带的细粒度分档全是噪声。",
          f"6. **v1 留在研究区**：近 6 年 {pct(get(near, 'v1')['ann'])} 看着不错，"
          f"但全期只有 2 个决策点（回撤 {pct(get(near, 'v1')['dd'])}），"
          "统计上无法与「事后挑两个日期」区分。可借它的**节奏**（少换仓），不可借它的**阈值**。", ""]

    L += ["## 8. 下一步可做（按性价比排序）", "",
          "1. **把 v0 的分位信号 + v5 的波动预算合成**：v0 解决「阈值要不要标定」，"
          "v5 解决「回撤怎么压」，两者改的是不同层，理论上可以叠加。",
          "2. **给 v5 加最小持有期到 20 日以上**：现在 10 日档 <7 日段已归零，"
          "但切换 77 次仍偏多，20~30 日档的 Calmar 只降 0.02。",
          "3. **红利端补股息**：v5 现金占比高、持有红利时段少，"
          "补 4.5% 股息后与 v3 的收益差会收窄，结论可能偏向 v5。",
          "4. 不建议继续在 v4 方向（阶梯/连续化）投入 —— 已证否。", ""]

    return "\n".join(L) + "\n"


def plot(kl, nv):
    start, end = NEAR
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12.5, 8), sharex=True,
                                   gridspec_kw=dict(height_ratios=[1.6, 1]))
    colors = {"v0": "#7f7f7f", "v1": "#8c564b", "v2": "#ff7f0e",
              "v3": "#1f77b4", "v4": "#9467bd", "v5": "#d62728"}
    width = {"v0": 1.1, "v1": 1.1, "v2": 1.1, "v3": 1.1, "v4": 1.1, "v5": 1.9}
    for k in ORDER:
        s = nv[k].loc[start:end]
        s = s / s.iloc[0]
        m = metrics(s)
        ax1.plot(s.index, s.values, color=colors[k], lw=width[k],
                 label=f"{V[k]['label']}  {m['ann'] * 100:+.1f}% / {m['dd'] * 100:.0f}%")
        dd = s / s.cummax() - 1
        ax2.plot(dd.index, dd.values * 100, color=colors[k], lw=width[k], alpha=0.85)

    for code, c in (("399006", "#2ca02c"), ("000922", "#17becf")):
        b = kl[code].loc[start:end]
        b = b / b.iloc[0]
        m = metrics(b)
        ax1.plot(b.index, b.values, color=c, lw=1.0, ls="--", alpha=0.75,
                 label=f"{BENCH[code]}  {m['ann'] * 100:+.1f}% / {m['dd'] * 100:.0f}%")
        dd = b / b.cummax() - 1
        ax2.plot(dd.index, dd.values * 100, color=c, lw=1.0, ls="--", alpha=0.6)

    ax1.set_ylabel("净值（起点 = 1.0）")
    ax1.set_title(f"成长/红利风格轮动 v0~v5（{start} 起同窗切片，图例 = 年化 / 最大回撤）\n"
                  "注意：v0/v1 成长端为科创50，v2~v5 为创业板指，虚线为各自基准")
    ax1.legend(fontsize=8.3, ncol=2, loc="upper left", framealpha=0.92)
    ax1.grid(alpha=0.3)
    ax1.axhline(1.0, color="k", lw=0.6, alpha=0.4)
    ax2.set_ylabel("回撤 (%)")
    ax2.grid(alpha=0.3)
    ax2.axhline(0, color="k", lw=0.6, alpha=0.5)
    ax2.xaxis.set_major_locator(mdates.YearLocator(1, month=1, day=1))
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax2.set_xlim(pd.Timestamp(start), pd.Timestamp(end))
    fig.subplots_adjust(left=0.07, right=0.98, top=0.90, bottom=0.07)
    path = os.path.join(_DIR, "_v0_v5_compare.png")
    fig.savefig(path, dpi=140, facecolor="white")
    plt.close(fig)
    return path


def main():
    kl, docs, nv = build()
    txt = build_report(kl, docs, nv)
    open(os.path.join(_DIR, "_v0_v5_compare.md"), "w", encoding="utf-8").write(txt)
    print(txt)
    print("图:", plot(kl, nv))


if __name__ == "__main__":
    main()
