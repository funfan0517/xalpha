# -*- coding: utf-8 -*-
import sys

sys.path.insert(0, "strategies/vol_legs/v6_pe_band")
import backtest as bt  # noqa: E402
import band  # noqa: E402

p = band.params()
print("== 阈值扫描（沪深300 · weekly / quarterly）==")
for th in (0.05, 0.10, 0.15, 0.20, 0.25, 0.30):
    line = f"gap>={th:.2f}"
    for freq in ("weekly", "quarterly"):
        r = bt.run_mode("hs300", bt.over(p, VETO_GAP=th), freq=freq)
        s, me = r["sim"], r["sim"]["me"]
        cond = int((me["veto_gap"] >= th).sum())
        line += (f" | {freq[:1].upper()}: 年化 {s['m']['ann']:+.2%} MDD {s['m']['mdd']:+.2%} "
                 f"Calmar {s['m']['calmar']:.3f} 曝光 {s['exposure']:.0%} 笔 {s['n_trades']:2d} "
                 f"veto {int(me['veto'].sum()):2d}（单月{cond:2d}）")
    print(line)

print("\n== A500 erp 覆盖 ==")
r = bt.run_mode("a500", p)
me = r["sim"]["me"]
print("pe非NaN", int(me["pe"].notna().sum()), "y10非NaN", int(me["y10"].notna().sum()),
      "erp非NaN", int(me["erp"].notna().sum()))
print(me.loc[me["pe"].notna(), ["pe", "y10", "erp"]].head(3).to_string())
print(me.loc[me["pe"].notna(), ["pe", "y10", "erp"]].tail(3).to_string())
