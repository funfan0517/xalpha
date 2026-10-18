# -*- coding: utf-8 -*-
"""右侧买入策略 · 每日扫描报告（渲染 scan.py 的产物）。

输入: strategies/right_side/daily/_right_side_scan_out.jsonl
输出: strategies/right_side/daily/_right_side_report.md

版式: 今日结论 -> 买点名单 -> 差一票观察名单 -> 全池票数矩阵 -> 口径速查 -> 回测参照。
用法: python strategies/right_side/_scan_report.py
"""
import json
import os
import sys
from datetime import datetime

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_DIR))
for p in (_ROOT, os.path.dirname(_DIR), _DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import rule  # noqa: E402

IN = os.path.join(_DIR, "daily", "_right_side_scan_out.jsonl")
MD = os.path.join(_DIR, "daily", "_right_side_report.md")
BT = os.path.join(_DIR, "backtest", "_right_side_bt.jsonl")

CAT_ORDER = ("宽基/另类", "全球/QDII", "A股行业", "策略/商品", "主动/量化", "债券")


def pct(x, dp=1):
    return "—" if x is None else f"{x * 100:+.{dp}f}%"


def load(path):
    if not os.path.exists(path):
        sys.exit(f"缺少扫描产物 {path}; 先跑 python strategies/right_side/scan.py")
    raw = open(path, "rb").read()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8")
    good, bad = [], []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        r = json.loads(ln)
        (good if r.get("ok") else bad).append(r)
    return good, bad


def bt_ref():
    """回测产物 -> 一行参照（缺失则 None）。"""
    if not os.path.exists(BT):
        return None
    rows = [json.loads(l) for l in open(BT, encoding="utf-8") if l.strip().startswith("{")]
    rows = [r for r in rows if r.get("ok")]
    if not rows:
        return None
    med = lambda k: float(np.median([r[k] for r in rows]))  # noqa: E731
    trades = [t for r in rows for t in r.get("trade_log") or []]
    wr = float(np.mean([t["ret"] > 0 for t in trades])) if trades else 0.0
    return dict(n=len(rows), ann=med("st_ann"), mdd=med("st_mdd"),
                base_ann=med("base_ann"), base_mdd=med("base_mdd"),
                pos=med("pos_ratio"), edge=med("timing_edge"), win=wr)


def vmarks(r):
    return " ".join("●" if v["pass"] else "○" for v in r["votes"])


def main():
    good, bad = load(IN)
    if not good:
        sys.exit("扫描产物无有效标的")
    d = good[0]["date"]
    ready = sorted([r for r in good if r["status"] == "READY"], key=lambda r: -r["score"])
    watch = sorted([r for r in good if r["status"] == "WATCH"], key=lambda r: -r["score"])
    veto = [r for r in good if r["status"] == "VETO"]
    ref = bt_ref()

    L = [f"# 右侧买入 · 每日扫描（数据日 {d}）", "",
         f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 标的池 `data/_universe.md` 场内 "
         f"{len(good)} 只 · 参数 `{rule.LABEL}` · **机械规则输出，非投资建议**",
         f"> 五票确认：{' / '.join(rule.VOTE_NAMES[k] for k in rule.VOTE_NAMES)}",
         f"> {rule.VOTE_ENTER} 票 = 右侧买点 · {rule.VOTE_WATCH} 票 = 差一票（等确认）· "
         f"信号按**当日收盘**判定，**次日收盘**执行", ""]

    L += ["## 一、今日结论", ""]
    if ready:
        L += [f"**{len(ready)} 只达到右侧买入点（五票全过）**：", "",
              "| 场外基金 | 主题 | 场内 | 收盘 | 五票 | 结构止损 | 距止损 | 250日回撤 | 20日均额 | 分级 |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for r in ready:
            L.append(f"| {r['off_name']} | {r['theme']} | `{r['inner']}` | {r['close']:.3f} | "
                     f"{vmarks(r)} | {('%.3f' % r['stop']) if r['stop'] else '—'} | "
                     f"{pct(r['stop_dist'])} | {pct(r['dd250'])} | {r['amt20_yi']:.1f}亿 | {r['grade']} |")
        L += ["", "> 买入即挂结构止损（信号日前 "
              f"{rule.STOP_LOOKBACK} 日最低收盘）；跌破无条件离场，不补仓、不摊薄。"]
    else:
        L += [f"**今日无标的五票全过 —— 空仓等待。**", "",
              f"> 这是该策略的常态：五票全过的历史占比约 1%（每只每年 ~2.5 个交易日）。"
              f"右侧买点本就稀少，硬凑买点等于抄底。当前最接近的是 "
              f"{len(watch)} 只「差一票」，见第二节。"]
    if veto:
        L += ["", f"另有 {len(veto)} 只票数达标但被硬否决：" + "、".join(
            f"`{r['inner']}`（{'追高' if r['veto_hot'] else '流动性'}）" for r in veto) + "。"]
    L.append("")

    L += ["## 二、差一票（观察名单，不提前埋伏）", ""]
    if watch:
        L += ["| 场外基金 | 主题 | 场内 | 收盘 | 五票 | 缺哪一票 | 该票实际值 | 250日回撤 |",
              "|---|---|---|---|---|---|---|---|"]
        for r in watch:
            miss = next((v for v in r["votes"] if not v["pass"]), None)
            L.append(f"| {r['off_name']} | {r['theme']} | `{r['inner']}` | {r['close']:.3f} | "
                     f"{vmarks(r)} | {('、'.join(r['missing']))} | "
                     f"{miss['detail'] if miss else '—'} | {pct(r['dd250'])} |")
        L += ["", "> 观察名单的含义是「明日若该票转正即成为买点」。**不是提前建仓的理由** —— "
                  "提前买就是左侧，本策略的全部意义在于不做这件事。"]
    else:
        L.append("（无）")
    L.append("")

    L += ["## 三、全池票数矩阵（按票数降序）", "",
          "| 场内 | 主题 | 类别 | 收盘 | " + " | ".join(rule.VOTE_SHORT[k] for k in rule.VOTE_NAMES)
          + " | 票数 | 状态 | 说明 |",
          "|---" * (4 + len(rule.VOTE_NAMES) + 3) + "|"]
    cats = [c for c in CAT_ORDER if any(r["cat"] == c for r in good)]
    cats += [c for c in dict.fromkeys(r["cat"] for r in good) if c not in cats]
    for cat in cats:
        rs = sorted([r for r in good if r["cat"] == cat], key=lambda r: (-r["score"], r["inner"]))
        for r in rs:
            L.append(f"| `{r['inner']}` | {r['theme']} | {r['cat']} | {r['close']:.3f} | "
                     + " | ".join("●" if v["pass"] else "○" for v in r["votes"])
                     + f" | {r['score']} | {r['status_cn']} | {r['action']} |")
    L.append("")
    L.append("> ● 通过 · ○ 未通过。票数分布：" + " · ".join(
        f"{n}票 {sum(1 for r in good if r['score'] == n)}只" for n in range(6)) + "。")
    L.append("")

    if ref:
        L += ["## 四、回测参照（同一套规则，55 只全历史中位数）", "",
              "| 中位年化 | 中位最大回撤 | 买入持有年化 | 买入持有回撤 | 持仓占比 | 单笔胜率 | 择时边际 |",
              "|---|---|---|---|---|---|---|",
              f"| {pct(ref['ann'], 2)} | {pct(ref['mdd'], 2)} | {pct(ref['base_ann'], 2)} | "
              f"{pct(ref['base_mdd'], 2)} | {ref['pos'] * 100:.1f}% | {ref['win'] * 100:.1f}% | "
              f"{ref['edge'] * 1e4:+.1f}bp |", "",
              "> 该策略是**择时叠加层**（低暴露）：年化低于满仓买入持有是常态，"
              "换来的是回撤压缩与正的择时边际（持仓日日均收益 − 空仓日日均收益）。"
              "它回答「什么时候买」，不回答「买多少、买哪些」—— 标的横向比较请看分级名单。", ""]

    L += ["## 五、五票口径速查", "",
          "| 票 | 判据 | 在排除什么 |",
          "|---|---|---|",
          f"| V1 {rule.VOTE_NAMES['v1']} | 近 {rule.STRUCT_WIN_SHORT} 日最低收盘 > 再往前 {rule.STRUCT_WIN_LONG} 日最低收盘 × (1+{rule.STRUCT_EPS:.1%}) "
          "| 还在创新低（下跌中继） |",
          f"| V2 {rule.VOTE_NAMES['v2']} | 收盘 > 前 {rule.BREAK_WIN} 日最高收盘 | 仍在箱体/下降结构内 |",
          f"| V3 {rule.VOTE_NAMES['v3']} | 收盘 > MA{rule.MA_SHORT} 且 MA{rule.MA_SHORT} 较 {rule.MA_SLOPE} 日前抬升 "
          "| 下行均线上的反抽（最常见的假右侧） |",
          f"| V4 {rule.VOTE_NAMES['v4']} | DIF > DEA 且 DIF > 0 | 零轴下金叉（只是反弹） |",
          f"| V5 {rule.VOTE_NAMES['v5']} | 量 ≥ {rule.VOL_MULT:g}×20 日均量 且 收涨 | 缩量假突破 |",
          "",
          f"硬否决：5 日涨幅 > {rule.RET5_MAX:.0%}（追高） · 20 日均成交额 < {rule.AMT_MIN / 1e8:.1f} 亿（流动性）",
          f"离场：收盘跌破入场结构位（前 {rule.STOP_LOOKBACK} 日最低收盘） · 或连续 {rule.EXIT_CONFIRM} 日收盘 < MA{rule.EXIT_MA}",
          ""]

    if bad:
        L += ["## 六、数据异常", "", "| 场内 | 主题 | 原因 |", "|---|---|---|"]
        L.extend(f"| `{r['inner']}` | {r['theme']} | {r.get('err')} |" for r in bad)
        L.append("")

    open(MD, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(f"[右侧买入报告] 数据日 {d} · 买点 {len(ready)} · 差一票 {len(watch)} · 被否决 {len(veto)}")
    print(f"  -> {MD}")


if __name__ == "__main__":
    main()
