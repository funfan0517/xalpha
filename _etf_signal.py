"""演示：用 xalpha 为场内 ETF 构建「规则化决策面板」。

全部输出均为机械规则的客观结果，不构成任何投资建议。
"""
import pandas as pd
import xalpha as xa

CODE = "SH512800"
START = "2024-01-01"
TOT = 100000.0

f = xa.vinfo(CODE, start=START)
# rsi/kdj 等指标内部用 .loc[i] 定位，要求 RangeIndex，vinfo 返回的不满足
f.price = f.price.reset_index(drop=True)
# 统一用 close 尺度计算，避免与 netvalue 归一化尺度混淆
f.ma(20, col="close")
f.ma(60, col="close")
f.bias(20, col="close")
f.boll(20, col="close")
f.rsi(14, col="close")

p = f.price
cur = p.iloc[-1]

print(f"标的: {f.name}  ({CODE})")
print(f"最新收盘: {cur['close']:.3f}   日期: {cur['date'].date()}   样本 {len(p)} 个交易日\n")

print("【1】技术面快照")
print(f"  MA20={cur['MA20']:.4f}   MA60={cur['MA60']:.4f}")
print(f"  价格相对 MA20: {cur['close'] / cur['MA20'] - 1:+.2%}"
      f"    相对 MA60: {cur['close'] / cur['MA60'] - 1:+.2%}")
print(f"  BIAS20 = {cur['BIAS20']:+.2%}")
bpos = (cur["close"] - cur["BOLL_LOWER"]) / (cur["BOLL_UPPER"] - cur["BOLL_LOWER"])
print(f"  BOLL 位置 = {bpos:.0%}   (0%=下轨, 100%=上轨)")
print(f"  RSI14 = {cur['RSI14'] * 100:.1f}    (0~100)")

print("\n【2】价格分位（当前价在历史区间中的位置）")
for n, label in [(60, "3个月"), (250, "1年"), (len(p), "全区间")]:
    if len(p) >= n:
        w = p["close"].tail(n)
        print(f"  近{label:<7}: 高于 {(w < cur['close']).mean():.1%} 的交易日")

print("\n【3】场内折溢价（溢价高时买入不划算）")
try:
    op = xa.OverPriced(CODE, start="2025-01-01")
    d = op.df.dropna(subset=["diff_rate"])
    d = d[d["diff_rate"].abs() < 20]  # 源数据偶有异常值
    print(f"  最新溢价率: {d.iloc[-1]['diff_rate']:+.2f}%")
    print(f"  近期区间 {d['diff_rate'].min():+.2f}% ~ {d['diff_rate'].max():+.2f}%"
          f"   中位数 {d['diff_rate'].median():+.2f}%")
except Exception as e:  # noqa
    print(f"  获取失败: {type(e).__name__}: {e}")

# ---- 规则回测：xalpha 的 policy+mulfix 链条对场内代码不兼容，此处等价实现 ----
print("\n【4】规则回测：[上穿MA20买入 / 下穿卖出] vs 买入持有")
px = p[["date", "close"]].copy()
px["ma20"] = px["close"].rolling(20).mean()
px = px.dropna().reset_index(drop=True)

pos, shares, cash, trades = 0, 0.0, 0.0, 0
for i in range(1, len(px)):
    prev_d = px.loc[i - 1, "close"] - px.loc[i - 1, "ma20"]
    cur_d = px.loc[i, "close"] - px.loc[i, "ma20"]
    if prev_d <= 0 < cur_d and pos == 0:
        shares = TOT / px.loc[i, "close"]
        pos, trades = 1, trades + 1
    elif prev_d >= 0 > cur_d and pos == 1:
        cash = shares * px.loc[i, "close"]
        pos, trades = 0, trades + 1

final_rule = cash if pos == 0 else shares * px.loc[len(px) - 1, "close"]
final_hold = TOT / px.loc[0, "close"] * px.loc[len(px) - 1, "close"]
print(f"  MA20金叉死叉 : 期末 {final_rule:,.0f}  ({final_rule / TOT - 1:+.2%})"
      f"   交易 {trades} 次")
print(f"  买入持有(基准): 期末 {final_hold:,.0f}  ({final_hold / TOT - 1:+.2%})")
print(f"  区间: {px.loc[0, 'date'].date()} ~ {px.loc[len(px) - 1, 'date'].date()}")

state = "持仓" if cur["close"] > cur["MA20"] else "空仓"
gap = cur["close"] / cur["MA20"] - 1
print(f"\n【5】今日规则信号: MA20 规则当前状态 = {state}"
      f"   (现价偏离 MA20 {gap:+.2%})")
print("     —— 机械规则输出，非投资建议")
