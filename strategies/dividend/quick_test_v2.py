# -*- coding: utf-8 -*-
"""快速测试优化方案V2。"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)
_ROOT = os.path.dirname(os.path.dirname(_DIR))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import data as dt
import rule_optimized_v2 as rule
import pandas as pd
import numpy as np


def quick_test():
    """快速测试优化方案。"""
    print("优化方案V2快速测试")
    print("=" * 50)
    
    # 加载数据
    df = dt.load_series()
    sample_from = pd.Timestamp(rule.SAMPLE_FROM)
    df = df[df.index >= sample_from].copy()
    
    print(f"样本区间: {df.index[0].date()} ~ {df.index[-1].date()} ({len(df)} 交易日)")
    print()
    
    # 显示阈值
    print("=== 优化方案V2阈值 ===")
    print(f"股债收益比: 买入>{rule.RATIO_BUY}, 卖出<{rule.RATIO_SELL}")
    print(f"PE分位: 买入<{rule.PE_BUY:.0%}, 卖出>{rule.PE_SELL:.0%}")
    print(f"股息率: 买入>{rule.DY_BUY}%, 卖出<{rule.DY_SELL}%")
    print()
    
    # 分析信号分布
    print("=== 信号分布分析 ===")
    
    signals = {}
    for key in rule.IND_KEYS:
        zones = []
        for val in df[key]:
            zone = rule.ZONE_FN[key](val)
            zones.append(zone)
        
        buy_count = sum(1 for z in zones if z == "buy")
        hold_count = sum(1 for z in zones if z == "hold")
        sell_count = sum(1 for z in zones if z == "sell")
        total = len(zones)
        
        signals[key] = {
            "buy": buy_count / total,
            "hold": hold_count / total,
            "sell": sell_count / total,
            "buy_days": buy_count,
            "total_days": total
        }
        
        print(f"{rule.IND_NAMES[key]}:")
        print(f"  买入: {buy_count/total:.1%} ({buy_count}天)")
        print(f"  持有: {hold_count/total:.1%} ({hold_count}天)")
        print(f"  卖出: {sell_count/total:.1%} ({sell_count}天)")
    
    print()
    
    # 合成信号分析
    print("=== 合成信号分析 ===")
    combo_zones = []
    for i in range(len(df)):
        zones = {}
        for key in rule.IND_KEYS:
            zones[key] = rule.ZONE_FN[key](df.iloc[i][key])
        combo_zone = rule.compose_zone(zones)
        combo_zones.append(combo_zone)
    
    buy_count = sum(1 for z in combo_zones if z == "buy")
    hold_count = sum(1 for z in combo_zones if z == "hold")
    sell_count = sum(1 for z in combo_zones if z == "sell")
    total = len(combo_zones)
    
    print(f"合成信号:")
    print(f"  买入: {buy_count/total:.1%} ({buy_count}天)")
    print(f"  持有: {hold_count/total:.1%} ({hold_count}天)")
    print(f"  卖出: {sell_count/total:.1%} ({sell_count}天)")
    
    # 计算持仓比例
    pos_ratio = (buy_count + hold_count) / total
    print(f"  预计持仓比例: {pos_ratio:.1%}")
    print()
    
    # 与原始策略对比
    print("=== 与原始策略信号对比 ===")
    print("注：原始策略阈值 - 股债收益比: >2.5/<1.5, PE分位: <30%/>70%, 股息率: >4.5%/<3.5%")
    
    # 简单计算原始策略信号
    original_buy_days = {}
    for key in rule.IND_KEYS:
        if key == "ratio":
            buy_days = sum(1 for val in df[key] if val > 2.5)
        elif key == "pe_pct":
            buy_days = sum(1 for val in df[key] if val < 0.30)
        elif key == "dy":
            buy_days = sum(1 for val in df[key] if val > 4.5)
        original_buy_days[key] = buy_days / len(df)
    
    for key in rule.IND_KEYS:
        orig_pct = original_buy_days[key]
        opt_pct = signals[key]["buy"]
        change = (opt_pct - orig_pct) / orig_pct * 100 if orig_pct > 0 else 0
        print(f"{rule.IND_NAMES[key]}: 原始买入{orig_pct:.1%} → 优化买入{opt_pct:.1%} ({change:+.1f}%)")
    
    return signals


if __name__ == "__main__":
    quick_test()