# -*- coding: utf-8 -*-
r"""中证红利ETF 策略 · 年度表格生成器。

从原始数据直接生成年度汇总表格。
用法: python strategies/dividend/generate_annual_tables.py
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


def generate_annual_tables():
    """生成年度数据表格。"""
    # 加载数据
    print("加载数据中...")
    df = dt.load_series()
    df = df.dropna(subset=['dy', 'pe_pct', 'ratio'])
    df['year'] = df.index.year
    
    # 按年分组计算
    annual_data = []
    
    for year, group in df.groupby('year'):
        if len(group) < 20:  # 跳过数据不足的年份
            continue
            
        # 找到最低点和最高点
        min_idx = group['price'].idxmin()
        max_idx = group['price'].idxmax()
        
        min_row = group.loc[min_idx]
        max_row = group.loc[max_idx]
        
        # 计算涨幅
        price_change = ((max_row['price'] - min_row['price']) / min_row['price']) * 100
        
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
            'price_change': float(price_change),
            'dy_change': float(max_row['dy'] - min_row['dy']),
            'pe_pct_change': float((max_row['pe_pct'] - min_row['pe_pct']) * 100),
            'ratio_change': float(max_row['ratio'] - min_row['ratio'])
        })
    
    # 转换为DataFrame并按年份排序
    df_annual = pd.DataFrame(annual_data).sort_values('year')
    
    # 生成详细表格
    detailed_table = generate_detailed_table(df_annual)
    
    # 生成简洁表格
    compact_table = generate_compact_table(df_annual)
    
    return detailed_table, compact_table, df_annual


def generate_detailed_table(df_annual):
    """生成详细年度表格（每行一年）。"""
    lines = []
    lines.append("# 中证红利ETF 近十年年度数据汇总表（每行一年）")
    lines.append("")
    lines.append("> 数据来源：中证官网 000922（价格指数） + H00922（全收益指数） + 10Y国债")
    lines.append(f"> 生成时间：{pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("> 注：股息率为TTM代理口径，PE分位为10年滚动窗口")
    lines.append("")
    lines.append("## 年度数据汇总表（每行一年）")
    lines.append("")
    
    # 表头
    lines.append("| 年份 | 最低点日期 | 最低点指数 | 最低股息率(%) | 最低PE分位 | 最低股债比 | 最低10Y(%) | "
                 "最高点日期 | 最高点指数 | 最高股息率(%) | 最高PE分位 | 最高股债比 | 最高10Y(%) | "
                 "价格涨幅(%) | 股息率变化(pp) | PE分位变化(pp) | 股债比变化 |")
    lines.append("|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    
    # 数据行
    for _, row in df_annual.iterrows():
        lines.append(f"| {row['year']} | {row['min_date']} | {row['min_price']:.2f} | {row['min_dy']:.2f} | "
                     f"{row['min_pe_pct']:.1%} | {row['min_ratio']:.2f} | {row['min_y10']:.2f} | "
                     f"{row['max_date']} | {row['max_price']:.2f} | {row['max_dy']:.2f} | "
                     f"{row['max_pe_pct']:.1%} | {row['max_ratio']:.2f} | {row['max_y10']:.2f} | "
                     f"{row['price_change']:+.1f} | {row['dy_change']:+.2f} | {row['pe_pct_change']:+.1f} | "
                     f"{row['ratio_change']:+.2f} |")
    
    lines.append("")
    lines.append("> 免责声明：公开数据回填的口径估算，仅供研究参考，不构成投资建议；市场有风险，投资需谨慎。")
    
    return "\n".join(lines)


def generate_compact_table(df_annual):
    """生成简洁年度表格。"""
    lines = []
    lines.append("# 中证红利ETF 近十年年度核心数据表")
    lines.append("")
    lines.append("> 数据来源：中证官网 000922（价格指数） + H00922（全收益指数） + 10Y国债")
    lines.append(f"> 生成时间：{pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append("## 核心数据表（每行一年）")
    lines.append("")
    
    # 表头
    lines.append("| 年份 | 最低点 | 最高点 | 价格涨幅 | 股息率范围 | PE分位范围 | 股债收益比范围 | 10Y国债范围 |")
    lines.append("|---|---|:---:|:---:|:---:|:---:|:---:|:---:|")
    
    # 数据行
    for _, row in df_annual.iterrows():
        min_month_day = row['min_date'].strftime('%m/%d')
        max_month_day = row['max_date'].strftime('%m/%d')
        
        lines.append(f"| {row['year']} | {row['min_price']:.0f} ({min_month_day}) | "
                     f"{row['max_price']:.0f} ({max_month_day}) | {row['price_change']:+.1f}% | "
                     f"{row['min_dy']:.2f}%-{row['max_dy']:.2f}% | "
                     f"{row['min_pe_pct']:.1%}-{row['max_pe_pct']:.1%} | "
                     f"{row['min_ratio']:.2f}-{row['max_ratio']:.2f} | "
                     f"{row['min_y10']:.2f}%-{row['max_y10']:.2f}% |")
    
    # 添加分析部分
    lines.append("")
    lines.append("## 指标说明")
    lines.append("")
    lines.append("- **最低点/最高点**：指数价格，括号内为月份/日期")
    lines.append("- **价格涨幅**：年度内从最低点到最高点的涨幅")
    lines.append("- **股息率范围**：TTM股息率，最低点值-最高点值")
    lines.append("- **PE分位范围**：10年滚动PE分位，最低点值-最高点值")
    lines.append("- **股债收益比范围**：股息率÷10Y国债，最低点值-最高点值")
    lines.append("- **10Y国债范围**：10年期国债收益率，最低点值-最高点值")
    lines.append("")
    
    # 策略信号
    lines.append("## 投资信号（按策略规则）")
    lines.append("")
    lines.append("- **买入信号**：股债收益比>2.5，PE分位<30%，股息率>4.5%")
    lines.append("- **持有信号**：股债收益比1.5-2.5，PE分位30%-70%，股息率3.5%-4.5%")
    lines.append("- **卖出信号**：股债收益比<1.5，PE分位>70%，股息率<3.5%")
    lines.append("")
    lines.append("> 注：数据为历史回测，仅供参考。投资需谨慎。")
    
    return "\n".join(lines)


def main():
    # 生成表格
    detailed_table, compact_table, df_annual = generate_annual_tables()
    
    # 输出到文件
    detailed_path = os.path.join(rule.BACKTEST_DIR, "_annual_summary_table.md")
    compact_path = os.path.join(rule.BACKTEST_DIR, "_annual_compact_table.md")
    
    os.makedirs(rule.BACKTEST_DIR, exist_ok=True)
    
    with open(detailed_path, "w", encoding="utf-8") as f:
        f.write(detailed_table)
    
    with open(compact_path, "w", encoding="utf-8") as f:
        f.write(compact_table)
    
    print("表格生成完成！")
    print(f"1. 详细表格已保存至: {detailed_path}")
    print(f"2. 简洁表格已保存至: {compact_path}")
    print("")
    
    # 显示简要统计
    print("=== 简要统计 ===")
    print(f"数据年份: {df_annual['year'].min()} - {df_annual['year'].max()}")
    print(f"平均年度涨幅: {df_annual['price_change'].mean():.1f}%")
    print(f"最大年度涨幅: {df_annual['price_change'].max():.1f}% ({df_annual.loc[df_annual['price_change'].idxmax(), 'year']}年)")
    print(f"最小年度涨幅: {df_annual['price_change'].min():.1f}% ({df_annual.loc[df_annual['price_change'].idxmin(), 'year']}年)")
    print("")
    
    # 显示最新数据
    latest = df_annual.iloc[-1]
    print(f"=== {latest['year']}年最新数据 ===")
    print(f"最低点: {latest['min_date']}, 指数{latest['min_price']:.0f}, "
          f"股息率{latest['min_dy']:.2f}%, PE分位{latest['min_pe_pct']:.1%}")
    print(f"最高点: {latest['max_date']}, 指数{latest['max_price']:.0f}, "
          f"股息率{latest['max_dy']:.2f}%, PE分位{latest['max_pe_pct']:.1%}")
    print(f"年度涨幅: {latest['price_change']:.1f}%")


if __name__ == "__main__":
    main()