# -*- coding: utf-8 -*-
r"""单标的 RSI14 择时回测（dividend 策略 · 专项分析）

标的: 大成中证红利指数A（090010，跟踪中证红利 000922）
策略: RSI14 < 35 买入（满仓），RSI14 > 65 卖出（空仓）；35~65 之间持仓不变。
       RSI14 用基金**累计净值 totvalue** 计算（与 `rank.py` / `pool.rsi14` 同口径，
       场内 ETF 与场外联接可横向比较）。

无前视: 收盘 t-1 的 RSI 决定第 t 日持仓（信号次日生效），与回测惯例一致。
对比基准: 买入持有（始终满仓）。

产物（AGENTS §8.4 ④ 专项分析）
----------------------------------------------------------------------
    backtest/_rsi14_090010_report.md
    backtest/_rsi14_090010.json

用法: python strategies/dividend/backtest_rsi14.py [--refresh]
"""
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import pool  # noqa: E402

BACKTEST_DIR = os.path.join(_DIR, "backtest")
OUT_MD = os.path.join(BACKTEST_DIR, "_rsi14_090010_report.md")
OUT_JSON = os.path.join(BACKTEST_DIR, "_rsi14_090010.json")

CODE = "090010"          # 大成中证红利指数A
RSI_WINDOW = 14
BUY = 35.0               # RSI < 35 买入
SELL = 65.0              # RSI > 65 卖出


# ----------------------------------------------------------------------
# RSI14 序列（Wilder 平滑，与 pool.rsi14 同口径）
# ----------------------------------------------------------------------
def rsi_series(nav, window=RSI_WINDOW):
    """累计净值 -> RSI14 序列（index 同 nav，首值 NaN）。"""
    s = nav.dropna()
    if len(s) < window + 1:
        return pd.Series(np.nan, index=nav.index)
    d = s.diff().dropna()
    au = d.clip(lower=0).ewm(alpha=1 / window, adjust=False).mean()
    ad = (-d).clip(lower=0).ewm(alpha=1 / window, adjust=False).mean()
    rs = au / ad
    rsi = 100 - 100 / (1 + rs)
    return rsi.reindex(s.index)


# ----------------------------------------------------------------------
# 信号与组合
# ----------------------------------------------------------------------
def signals(rsi, buy=BUY, sell=SELL):
    """RSI 序列 -> 持仓向量（0/1，无前视：t-1 收盘信号决定 t 日持仓）。"""
    pos = pd.Series(0.0, index=rsi.index)
    target = 0.0
    idx = rsi.index
    for i in range(1, len(idx)):
        prev = rsi.iloc[i - 1]
        if pd.isna(prev):
            pos.iloc[i] = target
            continue
        if target == 0 and prev < buy:
            target = 1.0
        elif target == 1 and prev > sell:
            target = 0.0
        pos.iloc[i] = target
    return pos


def nav_from_pos(nav, pos):
    rets = nav.pct_change(fill_method=None).fillna(0.0)
    port_ret = pos * rets
    return (1 + port_ret).cumprod().rename("rsi14"), port_ret


def stats(nav):
    nav = nav.dropna()
    if len(nav) < 2:
        return None
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    tot = float(nav.iloc[-1] / nav.iloc[0] - 1)
    ann = (1 + tot) ** (1 / years) - 1 if years > 0 else None
    mdd = float((nav / nav.cummax() - 1).min())
    r = nav.pct_change().dropna()
    sharpe = float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else 0.0
    return dict(years=round(years, 2), cum=round(tot, 4),
                ann=round(float(ann), 4), mdd=round(mdd, 4),
                sharpe=round(sharpe, 2),
                calmar=round(float(ann) / abs(mdd), 2) if mdd < 0 else None)


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def main(argv=None):
    argv = argv or sys.argv[1:]
    refresh = "--refresh" in argv
    navs = pool.load_navs(codes=[CODE], refresh=refresh)
    if CODE not in navs.columns:
        print(f"[error] 池中无 {CODE}", file=sys.stderr)
        return 1
    nav = navs[CODE].dropna()
    info = pool.BY_CODE[CODE]
    print(f"[info] {CODE} {info['name']} · 样本 {nav.index[0].date()} ~ {nav.index[-1].date()} · "
          f"{len(nav)} 个交易日")

    rsi = rsi_series(nav)
    pos = signals(rsi)
    port_nav, port_ret = nav_from_pos(nav, pos)

    bh_nav = (nav / nav.iloc[0]).rename("buyhold")

    s_p = stats(port_nav)
    s_b = stats(bh_nav)

    # 交易次数与在市时间
    n_entries = int((pos.diff().fillna(0) == 1).sum())
    n_exits = int((pos.diff().fillna(0) == -1).sum())
    time_in_mkt = float(pos.mean())

    # 最近状态
    last_rsi = rsi.dropna().iloc[-1]
    last_pos = pos.iloc[-1]
    last_state = ("持仓" if last_pos == 1 else "空仓")
    if last_rsi < BUY:
        rec = f"买入信号(<{BUY:.0f})"
    elif last_rsi > SELL:
        rec = f"卖出信号(>{SELL:.0f})"
    else:
        rec = f"持有不动({BUY:.0f}~{SELL:.0f})"

    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "code": CODE, "name": info["name"], "idx": info["idx_name"],
        "params": {"rsi_window": RSI_WINDOW, "buy": BUY, "sell": SELL,
                   "rsi_on": "totvalue(累计净值)"},
        "sample": [str(nav.index[0].date()), str(nav.index[-1].date())],
        "rsi_strategy": s_p, "buy_hold": s_b,
        "trades": {"entries": n_entries, "exits": n_exits,
                   "round_trips": min(n_entries, n_exits),
                   "time_in_market": round(time_in_mkt, 4)},
        "last": {"date": str(nav.index[-1].date()), "rsi14": round(float(last_rsi), 2),
                 "position": last_state, "recommendation": rec},
    }
    os.makedirs(BACKTEST_DIR, exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)

    txt = render(payload, s_p, s_b, nav)
    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write(txt)
    print(txt)
    print(f"产物: {OUT_MD}\n      {OUT_JSON}")
    return 0


def _p(v, d=1):
    return "—" if v is None else f"{v * 100:.{d}f}%"


def render(p, s_p, s_b, nav):
    L = ["# 单标的 RSI14 择时回测 · 大成中证红利指数A（090010）", "",
         f"> 生成 {p['generated_at']} · 标的 {p['code']} {p['name']}（{p['idx']}）",
         f"> 样本 {p['sample'][0]} ~ {p['sample'][1]} · RSI14=Wilder(window={RSI_WINDOW})，"
         f"基于**累计净值 totvalue** 计算（与 dividend 策略同口径）",
         f"> 规则: RSI14 < {BUY:.0f} 满仓买入，RSI14 > {SELL:.0f} 清仓，35~65 持仓不变；"
         "信号次日生效（无前视）。", ""]

    L += ["## 一、RSI14 择时 vs 买入持有", "",
          "| 指标 | 累计 | 年化 | 最大回撤 | 夏普 | Calmar |",
          "|---|---:|---:|---:|---:|---:|"]
    L.append(f"| **RSI14 择时** | {_p(s_p['cum'], 0)} | {_p(s_p['ann'])} | {_p(s_p['mdd'])} | "
             f"{s_p['sharpe']:.2f} | {'—' if s_p['calmar'] is None else format(s_p['calmar'], '.2f')} |")
    L.append(f"| 买入持有(基准) | {_p(s_b['cum'], 0)} | {_p(s_b['ann'])} | {_p(s_b['mdd'])} | "
             f"{s_b['sharpe']:.2f} | {'—' if s_b['calmar'] is None else format(s_b['calmar'], '.2f')} |")
    L += ["",
          f"> 年化提升（择时−持有）: **{(s_p['ann'] - s_b['ann']) * 100:+.1f}pt** · "
          f"回撤改善（择时−持有）: **{(s_p['mdd'] - s_b['mdd']) * 100:+.1f}pt**", ""]

    t = p["trades"]
    L += ["## 二、交易概况", "",
          f"- 建仓次数(买入): **{t['entries']}** · 清仓次数(卖出): **{t['exits']}** · "
          f"完整回合: **{t['round_trips']}**",
          f"- 在市时间占比: **{t['time_in_market'] * 100:.1f}%**（其余为空仓避险）", ""]

    last = p["last"]
    L += ["## 三、当前状态", "",
          f"- 截至 {last['date']}：RSI14 = **{last['rsi14']}** · 当前持仓 = **{last['position']}**",
          f"- 最新信号: **{last['recommendation']}**", ""]

    L += ["## 四、口径与边界", "",
          "- RSI14 用**累计净值 totvalue**（含分红再投），与 dividend 策略打分层的 RSI 同源、同口径。",
          "- 无前视：t-1 收盘 RSI 决定 t 日持仓；若当日无 RSI（上市初期）沿用前一日仓位。",
          "- 成本未计（无申赎费/佣金假设）；实际场外 A 类短期赎回费、ETF 佣金会侵蚀高频轮动收益。",
          "- 单标的、单参数（14/35/65）未做参数稳健性检验；RSI 在趋势市易频繁假信号。",
          "- 历史回测不代表未来；机械输出，非投资建议。", ""]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    sys.exit(main())
