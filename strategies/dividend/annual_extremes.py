# -*- coding: utf-8 -*-
r"""中证红利ETF 策略 · 年度极值查询。

列出近十年每年中证红利的最低点和最高点的数据（指数、股息率、PE分位、股债收益比）。

用法: python strategies/dividend/annual_extremes.py
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


def find_annual_extremes(df):
    """计算每年最低点和最高点数据。
    
    返回: DataFrame with columns:
        year, type(min/max), date, price, dy, pe_pct, ratio, y10
    """
    # 确保有年份列，并剔除NaN值
    df = df.copy()
    df['year'] = df.index.year
    
    # 只使用有效数据（剔除dy, pe_pct, ratio中的NaN）
    valid_df = df.dropna(subset=['dy', 'pe_pct', 'ratio'])
    
    # 初始化结果列表
    results = []
    
    # 对每一年
    for year, group in valid_df.groupby('year'):
        if len(group) < 20:  # 跳过数据不足的年份
            continue
            
        # 找到最低点（价格最低）
        min_idx = group['price'].idxmin()
        min_row = group.loc[min_idx]
        
        # 找到最高点（价格最高）
        max_idx = group['price'].idxmax()
        max_row = group.loc[max_idx]
        
        # 添加到结果
        results.append({
            'year': year,
            'type': '最低点',
            'date': min_idx.date(),
            'price': float(min_row['price']),
            'dy': float(min_row['dy']),
            'pe_pct': float(min_row['pe_pct']),
            'ratio': float(min_row['ratio']),
            'y10': float(min_row['y10'])
        })
        
        results.append({
            'year': year,
            'type': '最高点',
            'date': max_idx.date(),
            'price': float(max_row['price']),
            'dy': float(max_row['dy']),
            'pe_pct': float(max_row['pe_pct']),
            'ratio': float(max_row['ratio']),
            'y10': float(max_row['y10'])
        })
    
    return pd.DataFrame(results)


def format_report(df_extremes):
    """格式化输出报告。"""
    lines = []
    lines.append("# 中证红利ETF 近十年年度极值数据")
    lines.append("")
    lines.append("> 数据来源：中证官网 000922（价格指数） + H00922（全收益指数） + 10Y国债")
    lines.append("> 生成时间：{}".format(pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")))
    lines.append("> 注：股息率为TTM代理口径，PE分位为10年滚动窗口")
    lines.append("")
    
    # 按年份分组显示
    years = sorted(df_extremes['year'].unique())
    
    for year in years:
        if year < 2016:  # 只显示近十年（2016-2026）
            continue
            
        year_data = df_extremes[df_extremes['year'] == year]
        min_data = year_data[year_data['type'] == '最低点'].iloc[0]
        max_data = year_data[year_data['type'] == '最高点'].iloc[0]
        
        lines.append(f"## {year}年")
        lines.append("")
        
        # 最低点
        lines.append("### 最低点")
        lines.append(f"- **日期**: {min_data['date']}")
        lines.append(f"- **指数价格**: {min_data['price']:.2f}")
        lines.append(f"- **股息率(TTM)**: {min_data['dy']:.2f}%")
        lines.append(f"- **PE分位(10年)**: {min_data['pe_pct']:.1%}")
        lines.append(f"- **股债收益比**: {min_data['ratio']:.2f}")
        lines.append(f"- **10Y国债**: {min_data['y10']:.2f}%")
        lines.append("")
        
        # 最高点
        lines.append("### 最高点")
        lines.append(f"- **日期**: {max_data['date']}")
        lines.append(f"- **指数价格**: {max_data['price']:.2f}")
        lines.append(f"- **股息率(TTM)**: {max_data['dy']:.2f}%")
        lines.append(f"- **PE分位(10年)**: {max_data['pe_pct']:.1%}")
        lines.append(f"- **股债收益比**: {max_data['ratio']:.2f}")
        lines.append(f"- **10Y国债**: {max_data['y10']:.2f}%")
        lines.append("")
        
        # 计算差值
        price_change = ((max_data['price'] - min_data['price']) / min_data['price']) * 100
        dy_change = max_data['dy'] - min_data['dy']
        pe_pct_change = (max_data['pe_pct'] - min_data['pe_pct']) * 100
        ratio_change = max_data['ratio'] - min_data['ratio']
        
        lines.append("### 年度波动")
        lines.append(f"- **价格涨幅**: {price_change:.1f}%")
        lines.append(f"- **股息率变化**: {dy_change:+.2f}%")
        lines.append(f"- **PE分位变化**: {pe_pct_change:+.1f}pp")
        lines.append(f"- **股债收益比变化**: {ratio_change:+.2f}")
        lines.append("")
        lines.append("---")
        lines.append("")
    
    # 汇总表格
    lines.append("## 汇总表格")
    lines.append("")
    lines.append("| 年份 | 类型 | 日期 | 指数价格 | 股息率(%) | PE分位 | 股债收益比 | 10Y国债(%) |")
    lines.append("|---|---|---|---:|---:|---:|---:|---:|")
    
    for _, row in df_extremes.iterrows():
        if row['year'] < 2016:
            continue
            
        lines.append(f"| {row['year']} | {row['type']} | {row['date']} | {row['price']:.2f} | "
                     f"{row['dy']:.2f} | {row['pe_pct']:.1%} | {row['ratio']:.2f} | {row['y10']:.2f} |")
    
    lines.append("")
    lines.append("> 免责声明：公开数据回填的口径估算，仅供研究参考，不构成投资建议；市场有风险，投资需谨慎。")
    
    return "\n".join(lines)


def main():
    # 加载数据
    print("加载数据中...")
    df = dt.load_series()
    print(f"数据范围: {df.index[0].date()} ~ {df.index[-1].date()} ({len(df)} 交易日)")
    
    # 计算年度极值
    print("计算年度极值中...")
    df_extremes = find_annual_extremes(df)
    
    # 生成报告
    report = format_report(df_extremes)
    
    # 输出到控制台
    print("\n" + report)
    
    # 保存到文件
    output_path = os.path.join(rule.BACKTEST_DIR, "_annual_extremes.md")
    os.makedirs(rule.BACKTEST_DIR, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)
    
    print(f"\n报告已保存至: {output_path}")
    
    # 同时输出简表
    print("\n=== 简表（近十年） ===")
    print(f"{'年份':<6} {'类型':<8} {'日期':<12} {'指数':>8} {'股息率':>7} {'PE分位':>7} {'股债比':>7}")
    print("-" * 65)
    
    for year in sorted(df_extremes['year'].unique()):
        if year < 2016:
            continue
            
        year_data = df_extremes[df_extremes['year'] == year]
        for _, row in year_data.iterrows():
            print(f"{row['year']:<6} {row['type']:<8} {row['date']:<12} "
                  f"{row['price']:>8.0f} {row['dy']:>6.1f}% {row['pe_pct']:>6.0%} {row['ratio']:>6.2f}")


if __name__ == "__main__":
    main()