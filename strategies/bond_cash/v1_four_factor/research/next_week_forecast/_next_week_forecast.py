# -*- coding: utf-8 -*-
"""V1 · 下周债市晴雨表概率预测，以及最近一个月 / 半年的滚动回看。

方法：把 10Y 收益率 / SHIBOR-1W / 511260 累计净值 / 信用利差四个输入，按
「过去 3 年任一历史周的 5 交易日联合变动」bootstrap 推进一周，再用 V1 引擎
（engine.build_temps → build_factors → build_signal）重算下周晴雨表。
不是点预测：输出的是合成温度分位数、主信号概率、目标仓位与触发概率。

产物：`research/next_week_forecast/_next_week_forecast.{md,json}`。
用法：`python strategies/bond_cash/v1_four_factor/research/next_week_forecast/_next_week_forecast.py`
"""
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_RES = os.path.dirname(_HERE)
_STRAT = os.path.dirname(_RES)
for _p in (_STRAT, _RES, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import data  # noqa: E402
import engine  # noqa: E402
import rule  # noqa: E402

CFG = rule.CFG
OUT_MD = os.path.join(_HERE, "_next_week_forecast.md")
OUT_JSON = os.path.join(_HERE, "_next_week_forecast.json")
COLS = ("nav", "y10", "shibor", "spread", "cgb1")
KEEP_ROWS = 850           # ≈ 3.3 年，足够 1095D 滚动窗（约 750 交易日）+ 5 天推进
N_NOW = 800               # 当日预测样本数
N_ROLL = 60               # 逐日回看样本数（半年约 125 天，取小一些）
MONTHS_LONG = 6           # 回看窗口：半年；一个月表从同一批结果里截取
SEED = 20261126


def load_x():
    p = data.panel()
    bench = p["navs"][CFG["bench"]]
    curve = p["curve"]
    idx = pd.DatetimeIndex(sorted(bench.index.union(p["y10"].index)
                                  .union(p["shibor"].index).union(curve.index)))
    return pd.DataFrame({
        "nav": bench.reindex(idx).ffill(),
        "y10": p["y10"].reindex(idx).ffill(),
        "shibor": p["shibor"].reindex(idx).ffill(),
        "spread": (curve["aaa_3y"] - curve["cgb_1y"]).reindex(idx).ffill(),
        "cgb1": curve["cgb_1y"].reindex(idx).ffill(),
    })


def barometer(xs):
    """给定已推进到 T+5 的输入序列，重算当日晴雨表。"""
    curve = pd.DataFrame({"aaa_3y": xs["spread"] + xs["cgb1"], "cgb_1y": xs["cgb1"]})
    temps = engine.build_temps({CFG["bench"]: xs["nav"]}, xs["y10"], xs["shibor"], curve)
    fac = engine.build_factors(temps, xs["nav"])
    sig = engine.build_signal(fac)
    t, s = temps.iloc[-1], sig.iloc[-1]
    return dict(
        composite=float(t["composite"]), band=rule.temp_band(t["composite"]),
        valuation=float(t["valuation"]), funding=float(t["funding"]),
        momentum=float(t["momentum"]), credit=float(t["credit"]),
        score=int(s["score"]), side=str(s["side"]), conf=str(s["conf"]),
        target=float(rule.target_long(int(s["score"]), str(s["conf"])) * 100.0),
    )


def summarize(rows):
    df = pd.DataFrame(rows)
    side = df["side"].value_counts(normalize=True)
    modal, prob = str(side.index[0]), float(side.iloc[0])
    return dict(
        composite=round(float(df["composite"].median()), 1),
        c05=round(float(df["composite"].quantile(0.05)), 1),
        c95=round(float(df["composite"].quantile(0.95)), 1),
        valuation=round(float(df["valuation"].median()), 1),
        funding=round(float(df["funding"].median()), 1),
        momentum=round(float(df["momentum"].median()), 1),
        credit=round(float(df["credit"].median()), 1),
        modal_side=modal, modal_prob=round(prob * 100, 1),
        sides={k: round(float(v * 100), 1) for k, v in side.items()},
        target=round(float(df["target"].median()), 1),
        target05=round(float(df["target"].quantile(0.05)), 1),
        target95=round(float(df["target"].quantile(0.95)), 1),
        p_move=round(float((abs(df["target"] - CFG["long_base"] * 100)
                            >= CFG["hysteresis_pp"]).mean()) * 100, 1),
    )


def forecast_at(x, asof, d5, pool, n, rng):
    xd = x.loc[:asof].iloc[-KEEP_ROWS:]
    base = xd.iloc[-1]
    fut = pd.bdate_range(asof + pd.Timedelta(days=1), periods=5)
    local = pool[(pool <= asof) & (pool >= asof - pd.DateOffset(years=3))]
    if not len(local):
        return []
    chg = d5.loc[local]
    rows = []
    for j in rng.integers(0, len(local), size=n):
        target = base + chg.iloc[j]
        path = pd.DataFrame({c: np.linspace(base[c], target[c], 5) for c in COLS}, index=fut)
        rows.append(barometer(pd.concat([xd, path])))
    return rows


def realized_at(x, asof):
    future = x.index[x.index > asof]
    if len(future) < 5:
        return None
    d = future[4]                         # T+5（future[0] 是 T+1）
    return barometer(x.loc[:d].iloc[-KEEP_ROWS:])


def fmt(v, nd=1):
    return "—" if v is None else f"{v:.{nd}f}"


def stats(roll):
    done = [r for r in roll if r["real"]]
    hits = sum(1 for r in done if r["hit"])
    thits = sum(1 for r in done if abs(r["fc"]["target"] - r["real"]["target"]) < 0.05)
    mae = (sum(abs(r["fc"]["composite"] - r["real"]["composite"]) for r in done) / len(done)
           if done else None)
    return dict(done=done, hits=hits, thits=thits, mae=mae,
                sides=sorted({r["real"]["side"] for r in done}))


def table_lines(roll):
    L = ["| 预测日 | 预测合成温度 | 预测主信号(概率) | 预测目标 | 实际下周合成 | 实际下周信号 | 实际下周目标 | 命中 |",
         "|---|---:|---|---:|---:|---|---:|---|"]
    for r in roll:
        fc, real = r["fc"], r["real"]
        rv = "未到期" if real is None else f"{real['composite']:.1f}"
        rs = "—" if real is None else real["side"]
        rt = "—" if real is None else f"{real['target']:.1f}"
        hit = "—" if real is None else ("✓" if r["hit"] else "✗")
        L.append(f"| {r['date']} | {fc['composite']:.1f} | "
                 f"{fc['modal_side']}（{fc['modal_prob']:.0f}%） | {fc['target']:.1f} | "
                 f"{rv} | {rs} | {rt} | {hit} |")
    return L


def summary_text(st, label):
    n = len(st["done"])
    txt = (f"> {label}：已到期 {n} 天，主信号命中 {st['hits']}/{n}"
           + (f"（{st['hits'] / n * 100:.1f}%）" if n else ""))
    if len(st["sides"]) == 1:
        txt += (f"。⚠ 实际信号始终为「{st['sides'][0]}」，主信号命中是退化结果，"
                "不能当预测力证据")
    else:
        txt += f"。实际信号分布：{' / '.join(st['sides'])}"
    txt += (f"；更有区分度的目标仓位命中 {st['thits']}/{n}"
            + (f"（{st['thits'] / n * 100:.1f}%）" if n else ""))
    if st["mae"] is not None:
        txt += f"、合成温度平均绝对误差 {st['mae']:.2f}"
    return txt + "。实际值按 T+5 当日可得数据重算，不含前视；未到期留空。"


def main():
    x = load_x()
    end = x.index[-1]
    d5 = x - x.shift(5)
    pool = d5.index[d5.notna().all(axis=1)]
    rng = np.random.default_rng(SEED)

    nxt = summarize(forecast_at(x, end, d5, pool, N_NOW, rng))
    roll_all = []
    dates = x.index[x.index >= end - pd.DateOffset(months=MONTHS_LONG)]
    for d in dates:
        rows = forecast_at(x, d, d5, pool, N_ROLL, rng)
        if not rows:
            continue
        fc = summarize(rows)
        real = realized_at(x, d)
        roll_all.append(dict(
            date=str(d.date()), fc=fc,
            real=None if real is None else dict(
                composite=round(real["composite"], 1), side=real["side"],
                score=real["score"], target=round(real["target"], 1)),
            hit=None if real is None else fc["modal_side"] == real["side"],
        ))
    roll = [r for r in roll_all if r["date"] >= str((end - pd.DateOffset(months=1)).date())]
    st_1m, st_6m = stats(roll), stats(roll_all)
    L = ["# V1 · 下周债市晴雨表概率预测", "",
         f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 数据截止 **{end.date()}** · "
         f"bootstrap 样本池 {len(pool)} 周（近 3 年）· 当日预测 {N_NOW} 次 / 逐日回看 {N_ROLL} 次",
         "",
         "> 方法：把四个温度输入按历史上任一「5 交易日联合变动」推进一周，再用 V1 引擎重算。"
         "是概率情景，不是点预测；情景不入仓位公式。",
         "",
         "## 一、下周（T+5）预测", "",
         f"- 合成温度中位数 **{nxt['composite']:.1f}**（{nxt['c05']:.1f} ~ {nxt['c95']:.1f}）"
         f" · 分项中位：估值 {nxt['valuation']:.1f} / 资金 {nxt['funding']:.1f} / "
         f"动能 {nxt['momentum']:.1f} / 信用 {nxt['credit']:.1f}",
         f"- 主信号 **{nxt['modal_side']}（{nxt['modal_prob']:.1f}%）**；"
         + " / ".join(f"{k} {v:.1f}%" for k, v in nxt["sides"].items()),
         f"- 长端规则目标中位 **{nxt['target']:.1f}%**（{nxt['target05']:.1f} ~ {nxt['target95']:.1f}%）"
         f" · 相对当前 35% 触发滞后带概率 **{nxt['p_move']:.1f}%**",
         "",
         "## 二、最近一个月逐日滚动预测 vs 实际", ""]
    L += table_lines(roll) + ["", summary_text(st_1m, "最近一个月"), "",
         "## 三、最近半年逐日滚动预测 vs 实际", ""]
    L += table_lines(roll_all) + ["", summary_text(st_6m, "最近半年"), "",
          "> ⚠ bootstrap 假设下周的联合变动像过去 3 年某一周，不含政策事件"
          "（R-19 事件日历当前为空）；SHIBOR 只到最新可得日，之后按 ffill 处理。",
          "",
          "> 机械规则输出，仅供研究参考，不构成投资建议。"]
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(dict(generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
                       as_of=str(end.date()), n_now=N_NOW, n_roll=N_ROLL,
                       months_long=MONTHS_LONG, pool=len(pool), next_week=nxt,
                       rolling=roll, rolling_6m=roll_all,
                       m1=dict(matured=len(st_1m["done"]), hits=st_1m["hits"],
                               target_hits=st_1m["thits"], composite_mae=st_1m["mae"]),
                       m6=dict(matured=len(st_6m["done"]), hits=st_6m["hits"],
                               target_hits=st_6m["thits"], composite_mae=st_6m["mae"])),
                  f, ensure_ascii=False, indent=2)
    print(f"[ok] {OUT_MD}")
    print(f"[ok] {OUT_JSON}")
    print(f"  下周: 合成 {nxt['composite']:.1f} · {nxt['modal_side']} {nxt['modal_prob']:.0f}% "
          f"· 目标 {nxt['target']:.1f}% · 触发 {nxt['p_move']:.0f}%")
    print(f"  回看: 1月 {len(roll)} 天（已到期 {len(st_1m['done'])}，信号命中 {st_1m['hits']}、"
          f"目标命中 {st_1m['thits']}）；半年 {len(roll_all)} 天"
          f"（已到期 {len(st_6m['done'])}，信号命中 {st_6m['hits']}、目标命中 {st_6m['thits']}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
