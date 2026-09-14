# -*- coding: utf-8 -*-
"""成长/红利风格轮动 · 回测报告重生成器（供 pipeline run_flow --report 调用）。

与引擎(backtest.py)同目录：python pipeline/run_flow.py backtest --strategy growth_div_rotation --report
会运行本文件；数据已落本地缓存，直接重算并重写报告/图/摘要。
"""
import sys

import backtest


if __name__ == "__main__":
    alt = "--alt" in sys.argv
    out, res, plot = backtest.compute(alt=alt)
    backtest._write_products(out, res, plot)
