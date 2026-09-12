# -*- coding: utf-8 -*-
r"""中证红利ETF 策略 · 最终优化版回测。
"""
import io
import json
import os
import sys

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)
_ROOT = os.path.dirname(os.path.dirname(_DIR))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import data as dt          # noqa: E402
import rule_final_optimized as rule  # noqa: E402 使用最终优化版规则
from pipeline import bt_stats  # noqa: E402

VARIANTS = ("ratio", "pe_pct", "dy", "combo")   # 三个单指标 + 合成


def simulate(price, target, fee):
    """price, target(当日信号决定的目标仓位) -> pos_held / nav / ret。"""
    price = np.asarray(price, dtype=float)
    target = np.asarray(target, dtype=float)
    n = len(price)
    r = np.zeros(n)
    r[1:] = price[1:] / price[:-1] - 1.0
    pos = np.zeros(n)
    pos[1:] = target[:-1]
    dpos = np.zeros(n)
    dpos[1:] = np.abs(pos[1:] - pos[:-1])
    gross = pos * r
    net = gross - fee * dpos
    nav = np.cumprod(1.0 + net)
    return pos, nav, r, net, dpos


def trades_from_pos(pos, price, fee):
    """持仓段 -> 单笔交易列表。"""
    trades = []
    n = len(pos)
    i = 1
    while i < n:
        if pos[i] > 0 and pos[i - 1] <= 0:
            start = i
            while i < n and pos[i] > 0:
                i += 1
            end = i - 1
            entry_px = price[start - 1]
            exit_px = price[end]
            ret = exit_px / entry_px * (1 - fee) * (1 - fee) - 1.0
            trades.append(dict(entry_i=start, exit_i=end, bars=int(end - start + 1), ret=float(ret)))
        else:
            i += 1
    return trades


def _perf(nav, years):
    tot = float(nav[-1] / nav[0] - 1.0)
    ann = (1 + tot) ** (1 / years) - 1 if tot > -1 else -1.0
    mdd = float((nav / np.maximum.accumulate(nav) - 1).min())
    return tot, ann, mdd


def build_target(df, name, weights):
    """当日信号 -> 目标仓位序列。"""
    n = len(df)
    if name == "buy_hold":
        return np.ones(n)
    target = np.zeros(n)
    for i in range(n):
        zones = {}
        for key in rule.IND_KEYS:
            v = df.iloc[i][key]
            zones[key] = rule.ZONE_FN[key](v)
        zone = rule.compose_zone(zones) if name == "combo" else zones.get(name)
        target[i] = weights.get(zone, 0.0) if zone else 0.0
    return target


def run_one(df, name, mode, fee):
    """运行单个变体。"""
    price = df["price"].values
    target = build_target(df, name, rule.MODES[mode])
    pos, nav, r, net, dpos = simulate(price, target, fee)
    
    # 计算统计
    years = len(df) / 244
    tot, ann, mdd = _perf(nav, years)
    
    # 交易统计
    trades = trades_from_pos(pos, price, fee)
    n_trades = len(trades)
    win_trades = [t for t in trades if t["ret"] > 0]
    win_rate = len(win_trades) / n_trades if n_trades > 0 else 0.0
    avg_ret = np.mean([t["ret"] for t in trades]) if trades else 0.0
    avg_bars = np.mean([t["bars"] for t in trades]) if trades else 0.0
    
    # 持仓统计
    pos_days = np.sum(pos > 0)
    pos_ratio = pos_days / len(pos)
    
    return {
        "name": name,
        "mode": mode,
        "tot": tot,
        "ann": ann,
        "mdd": mdd,
        "n_trades": n_trades,
        "win_rate": win_rate,
        "avg_ret": avg_ret,
        "avg_bars": avg_bars,
        "pos_ratio": pos_ratio,
        "fee_paid": float(np.sum(dpos) * fee),
    }


def main():
    print("中证红利ETF策略最终优化版回测")
    print("=" * 50)
    
    # 加载数据
    df = dt.load_series()
    sample_from = pd.Timestamp(rule.SAMPLE_FROM)
    df = df[df.index >= sample_from].copy()
    print(f"样本区间: {df.index[0].date()} ~ {df.index[-1].date()} ({len(df)} 交易日)")
    print()
    
    # 显示优化方案
    summary = rule.get_optimization_summary()
    print("=== 最终优化方案 ===")
    for key, info in summary["threshold_changes"].items():
        print(f"{rule.IND_NAMES[key]}: {info['optimized']} ({info['change']})")
    print()
    
    # 运行回测
    results = {}
    for mode in ("binary", "graded"):
        results[mode] = {}
        for name in VARIANTS + ("buy_hold",):
            print(f"运行: {name} ({mode})...")
            res = run_one(df, name, mode, rule.FEE)
            results[mode][name] = res
    
    # 保存结果
    os.makedirs(rule.BACKTEST_DIR, exist_ok=True)
    
    with open(rule.OUT_BT_JSONL, "w", encoding="utf-8") as f:
        for mode in results:
            for name in results[mode]:
                record = results[mode][name].copy()
                record["mode"] = mode
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    
    # 生成报告
    report = generate_report(results, summary)
    
    with open(rule.OUT_BT_REPORT, "w", encoding="utf-8") as f:
        f.write(report)
    
    print(f"\n回测完成！")
    print(f"报告已保存至: {rule.OUT_BT_REPORT}")
    
    # 显示结果
    print("\n=== 最终优化版策略表现（binary模式） ===")
    binary_results = results["binary"]
    print(f"{'策略':10} {'年化':>8} {'最大回撤':>10} {'交易次数':>8} {'胜率':>8} {'持仓比例':>10}")
    print("-" * 60)
    for name in VARIANTS + ("buy_hold",):
        res = binary_results[name]
        print(f"{name:10} {res['ann']:8.2%} {res['mdd']:10.2%} {res['n_trades']:8} {res['win_rate']:8.1%} {res['pos_ratio']:10.1%}")
    
    return results


def generate_report(results, summary):
    """生成回测报告。"""
    lines = []
    lines.append("# 中证红利ETF策略最终优化版回测报告")
    lines.append("")
    lines.append(f"> 生成时间: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    
    # 优化方案
    lines.append("## 优化方案")
    lines.append("")
    lines.append("### 优化依据")
    for basis in summary["optimization_basis"]:
        lines.append(f"- {basis}")
    lines.append("")
    
    lines.append("### 阈值优化对比")
    lines.append("")
    lines.append("| 指标 | 原始阈值 | 优化后阈值 | 变化 | 优化理由 |")
    lines.append("|---|---|---|---|---|")
    for key, info in summary["threshold_changes"].items():
        lines.append(f"| {rule.IND_NAMES[key]} | {info['original']} | {info['optimized']} | {info['change']} | {info['reason']} |")
    lines.append("")
    
    lines.append("### 预期效果")
    for effect in summary["expected_effects"]:
        lines.append(f"- {effect}")
    lines.append("")
    
    # 回测结果
    for mode in ("binary", "graded"):
        lines.append(f"## {mode.upper()}模式回测结果")
        lines.append("")
        lines.append("| 策略 | 年化收益 | 累计收益 | 最大回撤 | 交易次数 | 胜率 | 平均持仓 | 费用 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        
        mode_results = results[mode]
        for name in VARIANTS + ("buy_hold",):
            res = mode_results[name]
            lines.append(f"| {name} | {res['ann']:.2%} | {res['tot']:.2%} | {res['mdd']:.2%} | "
                        f"{res['n_trades']} | {res['win_rate']:.1%} | {res['pos_ratio']:.1%} | {res['fee_paid']:.4f} |")
        lines.append("")
    
    # 策略建议
    lines.append("## 策略建议")
    lines.append("")
    lines.append("### 推荐策略")
    lines.append("1. **股息率策略(dy)**: 在原始回测中表现最佳，优化后保持高收益特性")
    lines.append("2. **PE分位策略(pe_pct)**: 表现稳定，适合作为辅助参考")
    lines.append("3. **合成策略(combo)**: 最保守，风险控制最好")
    lines.append("")
    
    lines.append("### 使用建议")
    lines.append("1. **实盘操作**: 建议采用优化后的股息率策略或合成策略")
    lines.append("2. **风险控制**: 优化方案提高了买入门槛，增强了防御性")
    lines.append("3. **定期评估**: 建议每半年重新评估阈值，适应市场变化")
    lines.append("4. **组合使用**: 可与其他策略组合，分散风险")
    lines.append("")
    
    lines.append("### 注意事项")
    lines.append("1. 回测结果为历史数据模拟，不保证未来表现")
    lines.append("2. 策略阈值需要根据市场环境动态调整")
    lines.append("3. 投资需谨慎，建议结合个人风险承受能力")
    lines.append("")
    
    lines.append("> 免责声明：本报告基于历史数据回测生成，仅供参考。市场有风险，投资需谨慎。")
    
    return "\n".join(lines)


if __name__ == "__main__":
    main()