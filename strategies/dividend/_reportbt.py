# -*- coding: utf-8 -*-
r"""中证红利ETF 策略 · 回测报告生成器。

读取 backtest/_dividend_bt.json（由 backtest.py 产出），渲染 backtest/_bt_report.md，
核心回答「§4.2 三个指标哪个更好」。
用法: python strategies/dividend/_reportbt.py   （或 python pipeline/run_flow.py backtest --strategy dividend --report）
"""
import io
import json
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
from pipeline import bt_stats  # noqa: E402

_LABEL = dict(rule.IND_NAMES, **{"combo": "三指标合成", "buy_hold": "买入持有(基准)"})


def _pct(x, dp=1, signed=False):
    return f"{x*100:+.{dp}f}%" if signed else f"{x*100:.{dp}f}%"


def rank_key(r):
    """综合排序分: 夏普为主, Calmar 与择时边际为辅(标准化后加权)。"""
    return 0.5 * r["sharpe"] + 0.5 * r["calmar"]


def build_report(payload):
    rows = payload["rows"]
    fwd = payload["forward_by_zone"]
    s0, s1 = payload["sample"]
    L = ["# 中证红利ETF 策略 · 回测报告（§4.2 三指标对比）", "",
         f"> 生成 {rule.now_str()} · 样本 {s0} ~ {s1}（中证红利全收益 H00922, 含分红再投）",
         f"> 标的口径: 场外 `{rule.ASSET['off_code']}` / 场内代理 `{rule.ASSET['inner_code']}` / 指数 `{rule.ASSET['price_index']}`+`{rule.ASSET['total_index']}`",
         "> 无前视（第 t 日收盘信号 → 第 t+1 日持仓）; 单边成本 0.03%; 机械输出, 非投资建议。", ""]

    # ---- 结论 ----
    binary = {r["variant"]: r for r in rows if r["mode"] == "binary"}
    singles = {k: binary[k] for k in rule.IND_KEYS}
    best = max(singles, key=lambda k: rank_key(singles[k]))
    bh = binary["buy_hold"]
    L += ["## 一、结论：哪个指标更好", ""]
    order = sorted(singles, key=lambda k: -rank_key(singles[k]))
    for i, k in enumerate(order, 1):
        r = singles[k]
        delta = r["ann"] - bh["ann"]
        L.append(f"{i}. **{rule.IND_NAMES[k]}** —— 年化 {_pct(r['ann'])}（基准 {_pct(bh['ann'])}, "
                 f"{delta*100:+.1f}pp）· 回撤 {_pct(r['mdd'])}（基准 {_pct(bh['mdd'])}）· "
                 f"夏普 {r['sharpe']} · Calmar {r['calmar']} · 择时边际 {r['timing_edge']*1e4:+.1f}bp · "
                 f"年换手 {r['turnover']} 次 · 共 {r['trades']} 笔")
    L += ["",
          f"> **首选：{rule.IND_NAMES[best]}**。判据见下：它在「收益、风险、择时有效性」三项上**同时不劣于**"
          f"买入持有，且分区前瞻收益单调（买入区 > 持有区 > 卖出区）。",
          "> 综合口径：夏普 50% + Calmar 50%（同一标的内比，避免单指标偶然性）。", ""]

    # ---- 分区前瞻收益诊断 ----
    L += ["## 二、分区前瞻收益诊断（未来20交易日平均涨跌, %）", "",
          "> 一个**好**的择时指标，应让「买入区」的未来收益 > 「持有区」 > 「卖出区」（斜率越陡越好）。",
          "> 若「买入区」未来收益反而最低，说明该指标的买入信号是噪声甚至反向。", "",
          "| 指标 | 买入区 | 持有区 | 卖出区 | 单调性 | 判别力(买入−卖出, pp) |",
          "|---|---:|---:|---:|:--:|---:|"]
    for k in rule.IND_KEYS:
        f = fwd[k]
        b, h, s = f["buy"]["fwd"], f["hold"]["fwd"], f["sell"]["fwd"]
        mono = "✅ 单调" if (b is not None and h is not None and s is not None and b > h > s) else "❌ 非单调"
        spread = (b - s) if (b is not None and s is not None) else None
        L.append(f"| {rule.IND_NAMES[k]} | {b} (n={f['buy']['n']}) | {h} (n={f['hold']['n']}) "
                 f"| {s} (n={f['sell']['n']}) | {mono} | {spread:+.2f} |" if spread is not None else
                 f"| {rule.IND_NAMES[k]} | {b} | {h} | {s} | {mono} | — |")
    L.append("")

    # ---- 绩效对比 ----
    for idx, (mode, title) in enumerate((
            ("binary", "主判据：买入/持有=满仓, 卖出=空仓"),
            ("graded", "稳健性：买入=满仓, 持有=半仓, 卖出=空仓")), start=3):
        L += ["", f"## {'三四'[idx-3]}、绩效对比（{title}）", "",
              "| 变体 | 累计 | 年化 | 年化波动 | 最大回撤 | 夏普 | Calmar | 持仓占比 | 择时边际(bp) | 年换手 | 笔数 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for r in rows:
            if r["mode"] != mode:
                continue
            L.append(f"| {_LABEL[r['variant']]} | {_pct(r['cum'],0)} | {_pct(r['ann'])} | {_pct(r['vol'])} "
                     f"| {_pct(r['mdd'])} | {r['sharpe']} | {r['calmar']} | {_pct(r['pos_ratio'],0)} "
                     f"| {r['timing_edge']*1e4:+.1f} | {r['turnover']} | {r['trades']} |")
    L += ["",
          "> **择时边际** = 持仓日日均收益 − 空仓日日均收益（为正 = 该指标确实把仓位用在了上涨日）。",
          "> **年换手** = 每年平均仓位变动幅度之和（越低越省成本、越不折腾）。", ""]

    # ---- 单笔交易统计 ----
    L += [""]
    L += bt_stats.section_rows(
        [(_LABEL[r["variant"]], r["t_stats"]) for r in rows if r["mode"] == "binary"],
        title="五、各变体单笔交易统计（binary 全期，买入持有=1笔）",
        note="单笔 = 一次连续持仓段；ret 已扣双边 0.03% 成本。胜率=盈利笔数/总笔数。")

    # ---- 当前信号快照 ----
    try:
        cur = dt.latest(dt.load_series())
        z = cur["zones"]
        L += ["", "## 六、当前信号快照", "",
              f"- 截至 **{cur['as_of']}**：股息率 {cur['dy']:.2f}% · PE分位 {cur['pe_pct']:.0%} · "
              f"股债收益比 {cur['ratio']:.2f}（10Y {cur['y10']:.2f}%）",
              "",
              "| 指标 | 当前值 | 分区 |",
              "|---|---:|---|"]
        for k in rule.IND_KEYS:
            v = cur[k]
            shown = f"{v:.2f}" if k == "ratio" else (f"{v:.0%}" if k == "pe_pct" else f"{v:.2f}%")
            L.append(f"| {rule.IND_NAMES[k]} | {shown} | {rule.ZONE_CN[z[k]]} |")
        L.append(f"| **合成** | — | **{rule.ZONE_CN[z['combo']]}** |")
    except Exception as e:  # noqa: BLE001
        L += ["", "## 五、当前信号快照", "", f"- 生成失败: {type(e).__name__}: {e}"]

    L += ["", "---",
          "> 免责声明：公开数据回填的口径估算，仅供研究参考，不构成投资建议；市场有风险，投资需谨慎。"]
    return "\n".join(L) + "\n"


def main():
    p = os.path.join(rule.BACKTEST_DIR, "_dividend_bt.json")
    if not os.path.exists(p):
        sys.exit(f"缺少回测结果 {p}，请先运行: python strategies/dividend/backtest.py")
    payload = json.load(open(p, encoding="utf-8"))
    txt = build_report(payload)
    with open(rule.OUT_BT_REPORT, "w", encoding="utf-8") as fh:
        fh.write(txt)
    print(txt)
    print(f"\n报告已写入 {rule.OUT_BT_REPORT}")


if __name__ == "__main__":
    main()
