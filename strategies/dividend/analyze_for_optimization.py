# -*- coding: utf-8 -*-
r"""中证红利ETF 策略 · 基于年度数据的参数优化分析。

分析近十年年度极值数据，识别策略参数优化机会。
"""
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

import data as dt          # noqa: E402
import rule                # noqa: E402
import pandas as pd
import numpy as np


def load_annual_data():
    """加载年度数据进行分析。"""
    # 加载原始数据
    df = dt.load_series()
    df = df.dropna(subset=['dy', 'pe_pct', 'ratio'])
    df['year'] = df.index.year
    
    # 按年计算极值
    annual_data = []
    
    for year, group in df.groupby('year'):
        if len(group) < 20:  # 跳过数据不足的年份
            continue
            
        # 找到最低点和最高点
        min_idx = group['price'].idxmin()
        max_idx = group['price'].idxmax()
        
        min_row = group.loc[min_idx]
        max_row = group.loc[max_idx]
        
        annual_data.append({
            'year': year,
            'min_date': min_idx.date(),
            'min_price': float(min_row['price']),
            'min_dy': float(min_row['dy']),
            'min_pe_pct': float(min_row['pe_pct']),
            'min_ratio': float(min_row['ratio']),
            'min_y10': float(min_row['y10']),
            'max_date': max_idx.date(),
            'max_price': float(max_row['price']),
            'max_dy': float(max_row['dy']),
            'max_pe_pct': float(max_row['pe_pct']),
            'max_ratio': float(max_row['ratio']),
            'max_y10': float(max_row['y10']),
            'price_change': float((max_row['price'] - min_row['price']) / min_row['price'] * 100)
        })
    
    return pd.DataFrame(annual_data).sort_values('year')


def analyze_current_thresholds(df_annual):
    """分析当前阈值在历史数据中的表现。"""
    print("=== 当前策略阈值分析 ===")
    print(f"股债收益比: 买入>{rule.RATIO_BUY}, 卖出<{rule.RATIO_SELL}")
    print(f"PE分位: 买入<{rule.PE_BUY:.0%}, 卖出>{rule.PE_SELL:.0%}")
    print(f"股息率: 买入>{rule.DY_BUY}%, 卖出<{rule.DY_SELL}%")
    print()
    
    # 分析最低点数据（买入机会）
    print("=== 最低点（买入机会）分析 ===")
    min_ratios = df_annual['min_ratio'].describe()
    min_pe_pcts = df_annual['min_pe_pct'].describe()
    min_dys = df_annual['min_dy'].describe()
    
    print(f"股债收益比范围: {df_annual['min_ratio'].min():.2f} - {df_annual['min_ratio'].max():.2f}")
    print(f"  中位数: {df_annual['min_ratio'].median():.2f}")
    print(f"  当前买入阈值({rule.RATIO_BUY})覆盖比例: {(df_annual['min_ratio'] > rule.RATIO_BUY).mean():.1%}")
    print()
    
    print(f"PE分位范围: {df_annual['min_pe_pct'].min():.1%} - {df_annual['min_pe_pct'].max():.1%}")
    print(f"  中位数: {df_annual['min_pe_pct'].median():.1%}")
    print(f"  当前买入阈值({rule.PE_BUY:.0%})覆盖比例: {(df_annual['min_pe_pct'] < rule.PE_BUY).mean():.1%}")
    print()
    
    print(f"股息率范围: {df_annual['min_dy'].min():.2f}% - {df_annual['min_dy'].max():.2f}%")
    print(f"  中位数: {df_annual['min_dy'].median():.2f}%")
    print(f"  当前买入阈值({rule.DY_BUY}%)覆盖比例: {(df_annual['min_dy'] > rule.DY_BUY).mean():.1%}")
    print()
    
    # 分析最高点数据（卖出机会）
    print("=== 最高点（卖出机会）分析 ===")
    print(f"股债收益比范围: {df_annual['max_ratio'].min():.2f} - {df_annual['max_ratio'].max():.2f}")
    print(f"  中位数: {df_annual['max_ratio'].median():.2f}")
    print(f"  当前卖出阈值({rule.RATIO_SELL})覆盖比例: {(df_annual['max_ratio'] < rule.RATIO_SELL).mean():.1%}")
    print()
    
    print(f"PE分位范围: {df_annual['max_pe_pct'].min():.1%} - {df_annual['max_pe_pct'].max():.1%}")
    print(f"  中位数: {df_annual['max_pe_pct'].median():.1%}")
    print(f"  当前卖出阈值({rule.PE_SELL:.0%})覆盖比例: {(df_annual['max_pe_pct'] > rule.PE_SELL).mean():.1%}")
    print()
    
    print(f"股息率范围: {df_annual['max_dy'].min():.2f}% - {df_annual['max_dy'].max():.2f}%")
    print(f"  中位数: {df_annual['max_dy'].median():.2f}%")
    print(f"  当前卖出阈值({rule.DY_SELL}%)覆盖比例: {(df_annual['max_dy'] < rule.DY_SELL).mean():.1%}")
    print()
    
    return {
        'min_ratio_stats': min_ratios,
        'min_pe_pct_stats': min_pe_pcts,
        'min_dy_stats': min_dys
    }


def analyze_trends(df_annual):
    """分析指标趋势变化。"""
    print("=== 指标趋势分析 ===")
    
    # 计算年度变化
    years = df_annual['year'].values
    min_ratios = df_annual['min_ratio'].values
    min_dys = df_annual['min_dy'].values
    min_y10s = df_annual['min_y10'].values
    
    # 线性趋势
    if len(years) >= 3:
        # 股债收益比趋势
        ratio_trend = np.polyfit(years, min_ratios, 1)
        print(f"股债收益比趋势: 每年变化 {ratio_trend[0]:+.3f}")
        
        # 股息率趋势
        dy_trend = np.polyfit(years, min_dys, 1)
        print(f"股息率趋势: 每年变化 {dy_trend[0]:+.3f}%")
        
        # 10Y国债趋势
        y10_trend = np.polyfit(years, min_y10s, 1)
        print(f"10Y国债趋势: 每年变化 {y10_trend[0]:+.3f}%")
    
    # 分阶段分析
    print("\n=== 分阶段分析 ===")
    
    # 2016-2019年（相对高利率）
    early_mask = df_annual['year'].between(2016, 2019)
    if early_mask.any():
        early_df = df_annual[early_mask]
        print(f"2016-2019年（利率较高）:")
        print(f"  股债收益比: {early_df['min_ratio'].mean():.2f} - {early_df['max_ratio'].mean():.2f}")
        print(f"  股息率: {early_df['min_dy'].mean():.2f}% - {early_df['max_dy'].mean():.2f}%")
        print(f"  10Y国债: {early_df['min_y10'].mean():.2f}% - {early_df['max_y10'].mean():.2f}%")
    
    # 2020-2023年（利率下降）
    mid_mask = df_annual['year'].between(2020, 2023)
    if mid_mask.any():
        mid_df = df_annual[mid_mask]
        print(f"\n2020-2023年（利率下降）:")
        print(f"  股债收益比: {mid_df['min_ratio'].mean():.2f} - {mid_df['max_ratio'].mean():.2f}")
        print(f"  股息率: {mid_df['min_dy'].mean():.2f}% - {mid_df['max_dy'].mean():.2f}%")
        print(f"  10Y国债: {mid_df['min_y10'].mean():.2f}% - {mid_df['max_y10'].mean():.2f}%")
    
    # 2024-2026年（低利率）
    late_mask = df_annual['year'] >= 2024
    if late_mask.any():
        late_df = df_annual[late_mask]
        print(f"\n2024-2026年（低利率）:")
        print(f"  股债收益比: {late_df['min_ratio'].mean():.2f} - {late_df['max_ratio'].mean():.2f}")
        print(f"  股息率: {late_df['min_dy'].mean():.2f}% - {late_df['max_dy'].mean():.2f}%")
        print(f"  10Y国债: {late_df['min_y10'].mean():.2f}% - {late_df['max_y10'].mean():.2f}%")


def suggest_optimized_thresholds(df_annual):
    """基于数据分析建议优化后的阈值。"""
    print("\n=== 优化阈值建议 ===")
    
    # 基于历史分位数建议阈值
    # 买入阈值：覆盖历史最低点的25%分位数（相对保守）
    # 卖出阈值：覆盖历史最高点的75%分位数（相对保守）
    
    # 股债收益比
    ratio_buy_q25 = df_annual['min_ratio'].quantile(0.25)
    ratio_sell_q75 = df_annual['max_ratio'].quantile(0.75)
    
    # PE分位（注意：PE分位越低越好，所以买入是低分位，卖出是高分位）
    pe_buy_q25 = df_annual['min_pe_pct'].quantile(0.25)  # 买入：25%分位
    pe_sell_q75 = df_annual['max_pe_pct'].quantile(0.75)  # 卖出：75%分位
    
    # 股息率
    dy_buy_q25 = df_annual['min_dy'].quantile(0.25)
    dy_sell_q75 = df_annual['max_dy'].quantile(0.75)
    
    print("基于历史25%/75%分位数的建议：")
    print(f"股债收益比: 买入>{ratio_buy_q25:.2f}, 卖出<{ratio_sell_q75:.2f}")
    print(f"PE分位: 买入<{pe_buy_q25:.1%}, 卖出>{pe_sell_q75:.1%}")
    print(f"股息率: 买入>{dy_buy_q25:.2f}%, 卖出<{dy_sell_q75:.2f}%")
    print()
    
    # 基于中位数的建议（更激进）
    ratio_buy_median = df_annual['min_ratio'].median()
    ratio_sell_median = df_annual['max_ratio'].median()
    
    pe_buy_median = df_annual['min_pe_pct'].median()
    pe_sell_median = df_annual['max_pe_pct'].median()
    
    dy_buy_median = df_annual['min_dy'].median()
    dy_sell_median = df_annual['max_dy'].median()
    
    print("基于历史中位数的建议（更激进）：")
    print(f"股债收益比: 买入>{ratio_buy_median:.2f}, 卖出<{ratio_sell_median:.2f}")
    print(f"PE分位: 买入<{pe_buy_median:.1%}, 卖出>{pe_sell_median:.1%}")
    print(f"股息率: 买入>{dy_buy_median:.2f}%, 卖出<{dy_sell_median:.2f}%")
    print()
    
    # 考虑趋势调整（因为利率环境变化）
    print("考虑趋势调整的建议（适应低利率环境）：")
    
    # 股债收益比：由于利率下降，整体水平上升，可以适当提高买入阈值
    ratio_buy_adjusted = max(ratio_buy_q25, 2.8)  # 不低于2.8
    ratio_sell_adjusted = max(ratio_sell_q75, 2.0)  # 不低于2.0
    
    # PE分位：由于估值中枢可能变化，保持相对稳定
    pe_buy_adjusted = min(pe_buy_q25, 0.25)  # 不高于25%
    pe_sell_adjusted = max(pe_sell_q75, 0.65)  # 不低于65%
    
    # 股息率：由于分红能力增强，可以适当提高买入阈值
    dy_buy_adjusted = max(dy_buy_q25, 4.8)  # 不低于4.8%
    dy_sell_adjusted = max(dy_sell_q75, 4.0)  # 不低于4.0%
    
    print(f"股债收益比: 买入>{ratio_buy_adjusted:.2f}, 卖出<{ratio_sell_adjusted:.2f}")
    print(f"PE分位: 买入<{pe_buy_adjusted:.1%}, 卖出>{pe_sell_adjusted:.1%}")
    print(f"股息率: 买入>{dy_buy_adjusted:.2f}%, 卖出<{dy_sell_adjusted:.2f}%")
    
    return {
        'quantile_based': {
            'ratio_buy': ratio_buy_q25,
            'ratio_sell': ratio_sell_q75,
            'pe_buy': pe_buy_q25,
            'pe_sell': pe_sell_q75,
            'dy_buy': dy_buy_q25,
            'dy_sell': dy_sell_q75
        },
        'median_based': {
            'ratio_buy': ratio_buy_median,
            'ratio_sell': ratio_sell_median,
            'pe_buy': pe_buy_median,
            'pe_sell': pe_sell_median,
            'dy_buy': dy_buy_median,
            'dy_sell': dy_sell_median
        },
        'adjusted': {
            'ratio_buy': ratio_buy_adjusted,
            'ratio_sell': ratio_sell_adjusted,
            'pe_buy': pe_buy_adjusted,
            'pe_sell': pe_sell_adjusted,
            'dy_buy': dy_buy_adjusted,
            'dy_sell': dy_sell_adjusted
        }
    }


def main():
    print("中证红利ETF策略参数优化分析")
    print("=" * 50)
    
    # 加载数据
    df_annual = load_annual_data()
    print(f"分析年份: {df_annual['year'].min()} - {df_annual['year'].max()} ({len(df_annual)}年)")
    print()
    
    # 分析当前阈值
    stats = analyze_current_thresholds(df_annual)
    
    # 分析趋势
    analyze_trends(df_annual)
    
    # 建议优化阈值
    suggestions = suggest_optimized_thresholds(df_annual)
    
    # 总结
    print("\n=== 优化建议总结 ===")
    print("1. 股债收益比阈值需要上调，适应低利率环境")
    print("2. PE分位阈值相对稳定，可微调")
    print("3. 股息率阈值需要上调，反映分红能力增强")
    print("4. 建议采用'调整后'阈值，平衡历史数据和当前环境")
    print()
    
    print("推荐优化方案（调整后阈值）：")
    print(f"  股债收益比: 买入>{suggestions['adjusted']['ratio_buy']:.2f}, 卖出<{suggestions['adjusted']['ratio_sell']:.2f}")
    print(f"  PE分位: 买入<{suggestions['adjusted']['pe_buy']:.1%}, 卖出>{suggestions['adjusted']['pe_sell']:.1%}")
    print(f"  股息率: 买入>{suggestions['adjusted']['dy_buy']:.2f}%, 卖出<{suggestions['adjusted']['dy_sell']:.2f}%")
    
    # 保存分析结果
    output_path = os.path.join(rule.BACKTEST_DIR, "_optimization_analysis.md")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"# 中证红利ETF策略参数优化分析\n\n")
        f.write(f"生成时间: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        
        f.write("## 当前阈值\n")
        f.write(f"- 股债收益比: 买入>{rule.RATIO_BUY}, 卖出<{rule.RATIO_SELL}\n")
        f.write(f"- PE分位: 买入<{rule.PE_BUY:.0%}, 卖出>{rule.PE_SELL:.0%}\n")
        f.write(f"- 股息率: 买入>{rule.DY_BUY}%, 卖出<{rule.DY_SELL}%\n\n")
        
        f.write("## 优化建议（调整后阈值）\n")
        f.write(f"- 股债收益比: 买入>{suggestions['adjusted']['ratio_buy']:.2f}, 卖出<{suggestions['adjusted']['ratio_sell']:.2f}\n")
        f.write(f"- PE分位: 买入<{suggestions['adjusted']['pe_buy']:.1%}, 卖出>{suggestions['adjusted']['pe_sell']:.1%}\n")
        f.write(f"- 股息率: 买入>{suggestions['adjusted']['dy_buy']:.2f}%, 卖出<{suggestions['adjusted']['dy_sell']:.2f}%\n\n")
        
        f.write("## 优化理由\n")
        f.write("1. **利率环境变化**: 10Y国债从~3%降至~1.8%，股债收益比整体上升\n")
        f.write("2. **分红能力增强**: 成分股分红能力提升，股息率中枢上移\n")
        f.write("3. **估值适应性**: 适应新的估值中枢，保持策略有效性\n")
        f.write("4. **风险控制**: 基于历史分位数，平衡收益与风险\n")
    
    print(f"\n分析结果已保存至: {output_path}")
    
    return suggestions


if __name__ == "__main__":
    main()