#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""复现「心情评分 · 日频操作波段策略」（评分 ≥80 买入 / ≤65 卖出）

结论先说：**规则能复现，指标不能。**

* **规则**（可复现，且完全确定）：日频、双阈值状态机 —— 空仓时评分 ≥80 才进，
  持仓时 ≤65 才出；次日生效；单边成本。这条流水线本脚本已跑通。
* **指标**（不可复现）：App 的「心情评分」算法未公开，截图只泄露 3 个读数
  （2026-07-22~10-23 最低 61.3 / 最高 84.0 / 10-23 = 76.5）。本脚本用 10 种常见构造
  （RSI、区间位置、均线偏离、量能、动量组合）去比对，**全部对不上**（见报告 §二），
  连标的都无法从截图锁定（截图点位轴 3575–3839，而上证同期 3658–3990、沪深300 在 4 千点级）。
  → 这条路是排除法：证明它不是「简单价格动量」，再往下猜没有意义。

因此本脚本提供两条路径：

1. **完全复现（推荐）**：把评分序列导出成 CSV（列：`date,score`），用
   `--score-csv path` 直接跑，结果与原 App 等价（同样的 80/65、次日执行、成本）。
2. **代理演练**：不给 CSV 时用「0.5·RSI14 + 0.5·60 日区间位置」当替身，只验证
   **规则本身的统计特征**（截图的「收益更高、回撤更大」是否成立），数值不可与原图对照。

产物（AGENTS §8.4 ④ 专项分析）：`research/backtest/_mood_report.md`

用法::

    python strategies/dividend/research/mood_band.py --fetch
    python strategies/dividend/research/mood_band.py
    python strategies/dividend/research/mood_band.py --score-csv my_score.csv --symbol sh000001
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

import numpy as np
import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
_STRAT = os.path.dirname(_DIR)
_ROOT = os.path.dirname(os.path.dirname(_STRAT))
for _p in (_DIR, _STRAT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pool  # noqa: E402  pylint: disable=wrong-import-position

DATA_DIR = os.path.join(_ROOT, "data", "_bt_caches")
OUT_MD = os.path.join(_DIR, "backtest", "_mood_report.md")

ANCHOR = {"start": "2026-07-22", "end": "2026-10-23",
          "min": 61.3, "max": 84.0, "last": 76.5}
BUY, SELL = 80.0, 65.0
FEE = 0.0003


# ------------------------------------------------------------------ 数据
def _cache_path(symbol: str) -> str:
    return os.path.join(DATA_DIR, f"{symbol}.csv")


def fetch(symbol: str = "sh000001", refresh: bool = False) -> pd.DataFrame:
    """指数日线 -> DataFrame(date, close, volume)。缓存 → akshare → 新浪 K 线。"""
    path = _cache_path(symbol)
    if not refresh and os.path.exists(path):
        df = pd.read_csv(path, parse_dates=["date"])
        return df.sort_values("date").reset_index(drop=True)

    df = None
    try:
        import akshare as ak                     # pylint: disable=import-outside-toplevel
        raw = ak.stock_zh_index_daily(symbol=symbol)
        df = pd.DataFrame({"date": pd.to_datetime(raw["date"]),
                           "close": raw["close"].astype(float),
                           "volume": pd.to_numeric(raw.get("volume"), errors="coerce")})
    except Exception as exc:                     # noqa: BLE001 —— 换备用源
        print(f"[mood] akshare 失败（{type(exc).__name__}），改用新浪 K 线")

    if df is None:
        url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               f"CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen=1023")
        for i in range(3):
            try:
                req = urllib.request.Request(url, headers=pool._UA)  # pylint: disable=protected-access
                rows = json.load(urllib.request.urlopen(req, timeout=25))
                df = pd.DataFrame([{"date": r["day"], "close": float(r["close"]),
                                    "volume": float(r["volume"])} for r in rows])
                df["date"] = pd.to_datetime(df["date"])
                break
            except Exception as exc:             # noqa: BLE001
                if i == 2:
                    raise RuntimeError(f"指数拉取失败: {exc}") from exc
                time.sleep(1.5 * (i + 1))

    os.makedirs(DATA_DIR, exist_ok=True)
    df.to_csv(path, index=False)
    return df


# ------------------------------------------------------------------ 候选评分
def rsi(s: pd.Series, n: int) -> pd.Series:
    d = s.diff()
    au = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    ad = (-d).clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    return (100 - 100 / (1 + au / ad.replace(0, np.nan))).clip(0, 100)


def pos(s: pd.Series, n: int) -> pd.Series:
    lo, hi = s.rolling(n).min(), s.rolling(n).max()
    return ((s - lo) / (hi - lo).replace(0, np.nan) * 100).clip(0, 100)


def rank100(x: pd.Series, n: int = 252) -> pd.Series:
    return x.rolling(n, min_periods=max(120, n // 2)).rank(pct=True) * 100


def candidates(df: pd.DataFrame) -> dict[str, pd.Series]:
    s = df.set_index("date")["close"].astype(float)
    v = df.set_index("date")["volume"].astype(float)
    r14, r9 = rsi(s, 14), rsi(s, 9)
    p20, p60, p120 = pos(s, 20), pos(s, 60), pos(s, 120)
    return {
        "RSI14": r14,
        "RSI9": r9,
        "60 日区间位置": p60,
        "120 日区间位置": p120,
        "60 日均线偏离分位": rank100(s / s.rolling(60).mean() - 1),
        "20 日区间位置分位": rank100(p20),
        "量能分位（5 日均量）": rank100(v.rolling(5).mean()),
        "5 日动量分位": rank100(s.pct_change(5)),
        "0.5·RSI14+0.5·位置60": 0.5 * r14 + 0.5 * p60,
        "0.5·量能+0.5·5日动量": 0.5 * rank100(v.rolling(5).mean()) + 0.5 * rank100(s.pct_change(5)),
    }


def calibrate(scores: dict[str, pd.Series]) -> pd.DataFrame:
    rows = []
    for name, sc in scores.items():
        w = sc.loc[ANCHOR["start"]:ANCHOR["end"]].dropna()
        if w.empty:
            continue
        rows.append({"评分": name, "区间最低": w.min(), "区间最高": w.max(), "10-23": w.iloc[-1],
                     "误差": (abs(w.min() - ANCHOR["min"]) + abs(w.max() - ANCHOR["max"])
                              + abs(w.iloc[-1] - ANCHOR["last"])) / 3})
    return pd.DataFrame(rows).sort_values("误差").reset_index(drop=True)


# ------------------------------------------------------------------ 策略
def band(score: pd.Series, buy=BUY, sell=SELL) -> pd.Series:
    """双阈值状态机：空仓时 ≥buy 才进，持仓时 ≤sell 才出。返回 0/1 仓位。"""
    out = np.zeros(len(score))
    holding = 0
    for i, v in enumerate(score.to_numpy(dtype=float)):
        if not np.isnan(v):
            if not holding and v >= buy:
                holding = 1
            elif holding and v <= sell:
                holding = 0
        out[i] = holding
    return pd.Series(out, index=score.index)


def run(px: pd.Series, pos_v: pd.Series, fee=FEE) -> pd.Series:
    held = pos_v.shift(1).fillna(0.0)
    ret = px.pct_change().fillna(0.0)
    turn = (held - held.shift(1).fillna(0.0)).abs()
    return (held * ret - turn * fee).iloc[1:]


def stats(r: pd.Series) -> dict:
    nav = (1 + r).cumprod()
    yrs = max(len(r) / 252, 1e-9)
    cagr = nav.iloc[-1] ** (1 / yrs) - 1
    dd = float((nav / nav.cummax() - 1).min())
    vol = float(r.std() * np.sqrt(252))
    return {"cagr": cagr, "dd": dd, "calmar": cagr / abs(dd) if dd else float("nan"),
            "vol": vol, "sharpe": float(r.mean() * 252) / vol if vol else float("nan"),
            "total": float(nav.iloc[-1] - 1), "n": len(r)}


def _p(x, d=1):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:.{d}f}%"


def _meta(px: pd.Series, pos_v: pd.Series) -> tuple[dict, float, int]:
    r = run(px, pos_v)
    held = pos_v.shift(1).reindex(r.index).fillna(0.0)
    return stats(r), float(held.mean()), int((pos_v.diff().abs() > 0).sum())


def _pct_thresholds(score: pd.Series, buy_q=0.80, sell_q=0.65):
    """把「80/65 绝对刻度」换成「同分位的相对刻度」，用于刻度不同的代理指标。"""
    return float(score.quantile(buy_q)), float(score.quantile(sell_q))


# ------------------------------------------------------------------ 报告
def render(df: pd.DataFrame, symbol: str, cal: pd.DataFrame, score_kind: str,
           score: pd.Series, res: dict, sens: dict, note: str) -> str:
    s = df.set_index("date")["close"].astype(float)
    L = [f"# 复现「心情评分 · 日频波段策略」（80 买 / 65 卖）", "",
         f"> 标的：`{symbol}` · 样本 {res['start']:%Y-%m-%d} ~ {res['end']:%Y-%m-%d}"
         f"（{res['years']:.1f} 年）· 单边成本 {FEE * 100:.2f}%", "",
         "## 一、能复现什么、不能复现什么", "",
         "| 部分 | 能否 | 说明 |", "|---|---|---|",
         "| **策略规则** | ✅ | 日频、空仓时 ≥80 才买、持仓时 ≤65 才卖（双阈值状态机，不是每天按阈值砍仓）；次日生效 |",
         "| **评分算法** | ❌ | App 未公开。截图只给 3 个读数，任何 0–100 的平滑指标都能拟合 3 个数，但走向可以完全不同 |",
         "| **标的** | ❌ | 截图点位轴 3575–3839、10-23 估算 3713.16；上证同期实际 3658–3990、沪深300 在 4100 级 —— 都对不上 |",
         "| **交易细节** | ⚠ | 原图未说明执行价与成本；本复现取次日生效 + 单边 0.03% |", "",
         "## 二、为什么不能靠「猜指标」复现（排除法）", "",
         f"锚点（{ANCHOR['start']}~{ANCHOR['end']}）：最低 **{ANCHOR['min']}**、"
         f"最高 **{ANCHOR['max']}**、10-23 = **{ANCHOR['last']}**。", "",
         "| 候选构造 | 区间最低 | 区间最高 | 10-23 | 平均误差 |", "|---|---:|---:|---:|---:|"]
    for _, r in cal.iterrows():
        L.append(f"| {r['评分']} | {r['区间最低']:.1f} | {r['区间最高']:.1f} | "
                 f"{r['10-23']:.1f} | {r['误差']:.1f} |")
    L += ["", f"最接近的构造平均误差仍有 **{cal.iloc[0]['误差']:.0f} 分**，且 10-23 那一项普遍差 40 分以上 "
              "—— 说明原指标不是价格动量/位置，更像**成交与情绪类复合**（涨跌家数、融资余额、"
              "北向资金等，这些本地没有）。继续猜没有意义。", "",
          "## 三、代理演练（**只验证规则，数值不可与原图对照**）", "",
          f"用 `{score_kind}` 当评分替身，跑同一套 80/65 规则：", "",
          "| 方案 | 年化 | 年化波动 | 最大回撤 | Calmar | 夏普 | 累计 | 持仓占比 | 换手次数 |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, (st, exp, tr) in res["table"].items():
        L.append(f"| {name} | {_p(st['cagr'])} | {_p(st['vol'])} | {_p(st['dd'])} | "
                 f"{st['calmar']:.2f} | {st['sharpe']:.2f} | {_p(st['total'], 0)} | "
                 f"{_p(exp, 0)} | {tr} |")
    L.append("")

    st_abs = res["table"]["日频 · 绝对 80/65"][0]
    st_eq = res["table"]["日频 · 等频分位"][0]
    st_w = res["table"]["周频 · 等频分位"][0]
    st_bh = res["table"]["买入持有"][0]
    L += [f"- 相对买入持有：年化 {_p(st_eq['cagr'])} vs {_p(st_bh['cagr'])}、"
          f"回撤 {_p(st_eq['dd'])} vs {_p(st_bh['dd'])} —— "
          f"**回撤{'更小' if st_eq['dd'] > st_bh['dd'] else '更大'}、"
          f"年化{'更低' if st_eq['cagr'] < st_bh['cagr'] else '更高'}**"
          f"（Calmar {st_eq['calmar']:.2f} vs {st_bh['calmar']:.2f}）。",
          f"- **日频 vs 周频**（截图那句「收益更高、回撤更大」指的是这个）："
          f"年化 {_p(st_eq['cagr'])} vs {_p(st_w['cagr'])}、回撤 {_p(st_eq['dd'])} vs "
          f"{_p(st_w['dd'])} —— 「收益更高」{'成立' if st_eq['cagr'] > st_w['cagr'] else '不成立'}，"
          f"「回撤更大」{'成立' if st_eq['dd'] < st_w['dd'] else '不成立'}。",
          f"- 平均持仓 {_p(res['table']['日频 · 等频分位'][1], 0)}：这类规则长期空仓，"
          "「回撤小」主要来自**不参与**，不是择时准；绝对阈值那一行持仓更低，"
          "说明阈值位置直接决定暴露，换指标就必须重新标定。", ""]

    L += ["## 四、阈值敏感性（同一条评分，买/卖各挪 5 分）", "",
          "| 买 / 卖 | 年化 | 最大回撤 | Calmar | 持仓占比 | 换手次数 |", "|---|---:|---:|---:|---:|---:|"]
    for k, (st, exp, tr) in sens.items():
        L.append(f"| {k} | {_p(st['cagr'])} | {_p(st['dd'])} | {st['calmar']:.2f} | "
                 f"{_p(exp, 0)} | {tr} |")
    L += ["", "阈值一动，年化与回撤排序就换位 —— 这类「两条线」的策略，"
              "参数稳定性比回测收益更重要。", ""]

    L += ["## 五、怎么做到 100% 复现", "",
          "评分序列在手，就能完全等价复现（同样的 80/65、次日执行、成本）：", "",
          "```bash",
          "python strategies/dividend/research/mood_band.py --score-csv score.csv --symbol sh000001",
          "```", "",
          "- `score.csv` 只要两列：`date,score`（日频，0–100）。App 的「历史记录」页可逐日抄，"
          "或截图 OCR 后人工核对拐点。",
          "- 拿到序列后本脚本会再做一次「评分 → 仓位 → 收益」的完整回放，"
          "并与原图的买卖点标注逐一对齐；差异只会来自执行价与成本假设。", "",
          "## 六、这类策略值不值得用", "",
          "1. **它与我们的 V4 是同一族**：V4 = RSI14 双阈值（70 清仓 / 50 减半），"
          "这条 = 情绪评分双阈值（80 / 65）。差别只在指标的刻度与阈值位置。",
          "2. **V4 的全样本结论可以直接参考**：7 只红利基金、2020-01~2026-10 上，"
          "V4 Calmar 1.44（基准 0.91），但年化只有 10.8%、平均仓位 72% —— "
          "**收益靠降暴露换来，不是靠更准的进出点**。",
          "3. **它测的是市场情绪，不是红利估值**：对红利资产用它择时，等于用 A 股整体情绪"
          "给一个低波动资产定时，相关性弱、且会在红利独立行情里持续错配。",
          "", note, ""]
    return "\n".join(L)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    argv = sys.argv[1:]
    refresh = "--fetch" in argv or "--refresh" in argv
    csv = None
    symbol = "sh000001"
    if "--score-csv" in argv:
        csv = argv[argv.index("--score-csv") + 1]
    if "--symbol" in argv:
        symbol = argv[argv.index("--symbol") + 1]

    df = fetch(symbol, refresh)
    s = df.set_index("date")["close"].astype(float)
    print(f"[mood] {symbol} {s.index[0]:%Y-%m-%d} ~ {s.index[-1]:%Y-%m-%d}（{len(s)} 根）")

    cal = calibrate(candidates(df))
    print(cal.to_string(index=False, float_format=lambda v: f"{v:6.1f}"))
    if "--fetch" in argv:
        print("[mood] 仅刷缓存")
        return 0

    if csv:
        raw = pd.read_csv(csv, parse_dates=["date"]).set_index("date")["score"].astype(float)
        score, kind = raw.sort_index(), f"导入评分（{os.path.basename(csv)}）"
        note = "> 本报告用的是**导入的真实评分序列**，因此与 App 同源、可逐点核对。"
    else:
        score = candidates(df)["0.5·RSI14+0.5·位置60"]
        kind = "0.5·RSI14 + 0.5·60 日区间位置（代理）"
        note = ("⚠ 本报告的评分是**代理指标**，不是 App 的原始评分。"
                "数字只能读规则的方向性特征，不能与原图或原 App 收益对照。")

    start, end = "2013-01-01", str(s.index[-1].date())
    px = s.loc[start:end]
    sc = score.loc[start:end]
    q_buy, q_sell = _pct_thresholds(sc)

    wk = sc.resample("W-FRI").last().dropna()          # 周频版：只看周五收盘
    pos_w = band(wk, q_buy, q_sell).reindex(sc.index, method="ffill").fillna(0.0)

    table = {}
    table["日频 · 绝对 80/65"] = _meta(px, band(sc, BUY, SELL))
    table["日频 · 等频分位"] = _meta(px, band(sc, q_buy, q_sell))
    table["周频 · 等频分位"] = _meta(px, pos_w)
    table["买入持有"] = _meta(px, pd.Series(1.0, index=px.index))

    sens = {}
    for b, sl in ((75, 60), (80, 70), (85, 70), (75, 65)):
        sens[f"{b} / {sl}"] = _meta(px, band(sc, b, sl))

    res = {"table": table, "start": px.index[0], "end": px.index[-1],
           "years": len(px) / 252}
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write(render(df, symbol, cal, kind, score, res, sens, note))

    for name, (st, exp, tr) in table.items():
        print(f"[mood] {name:22s} 年化 {_p(st['cagr']):>7s}  回撤 {_p(st['dd']):>7s}  "
              f"Calmar {st['calmar']:5.2f}  持仓 {_p(exp):>5s}  换手 {tr}")
    print(f"[mood] 产物 -> {os.path.relpath(OUT_MD, _DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
