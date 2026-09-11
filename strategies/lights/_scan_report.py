# -*- coding: utf-8 -*-
"""亮灯策略 · 每日操作报告（渲染 scan.py 的产物）。

输入: strategies/lights/_lights_scan_out.jsonl（scan.py 产出, 每标的一行完整指标明细）
      strategies/lights/_lights_state.json（可选, 调仓日历）
输出: strategies/lights/_signal_report.md
      strategies/lights/_signal_visual.png
      strategies/lights/_signal_dashboard.html

版式沿用仓库既有报告惯例: H1 + `>` 口径块 + 分节表格（末行 **全部**）+
内嵌 base64 PNG 的 HTML 看板 + 生成时间脚注。
"""
import base64
import json
import os
import sys
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_DIR))
for p in (_ROOT, os.path.dirname(_DIR), _DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import rule  # noqa: E402

IN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lights_scan_out.jsonl")
STATE = os.path.join(_ROOT, "data", "_lights_state.json")
MD = os.path.join(_DIR, "_signal_report.md")
PNG = os.path.join(_DIR, "_signal_visual.png")
HTML = os.path.join(_DIR, "_signal_dashboard.html")

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def load():
    if not os.path.exists(IN):
        sys.exit(f"缺少扫描产物 {IN}; 先跑 python strategies/lights/scan.py")
    raw = open(IN, "rb").read()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8")
    good, bad = [], []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        r = json.loads(ln)
        (good if r.get("ok") else bad).append(r)
    return good, bad


def pct(x, dp=2):
    return "—" if x is None else f"{x * 100:+.{dp}f}%"


def plot(good):
    """左: 各标的得分横条（红=建仓/蓝=门槛过但分不足/灰=门槛未过）; 右: 各维平均得分。"""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, max(4.5, 0.26 * len(good))))

    rs = sorted(good, key=lambda r: r["score"])
    names = [f"{r['theme']} {r['inner']}" for r in rs]
    scores = [r["score"] for r in rs]
    colors = ["#c62828" if r["target"] > 0 else ("#1565c0" if r["gate_pass"] else "#b0bec5")
              for r in rs]
    ax1.barh(names, scores, color=colors)
    ax1.set_title("各标的得分（红=目标建仓 / 蓝=门槛过但分不足 / 灰=门槛未过）")
    ax1.set_xlabel(f"score（建仓门槛 {good[0]['enter_min']:g}）")
    ax1.axvline(good[0]["enter_min"], color="#666", ls="--", lw=1)
    for i, r in enumerate(rs):
        ax1.text(r["score"] + 0.06, i, f"{r['score']:g}", va="center", fontsize=8)

    keys = list(dict.fromkeys(l[0] for r in good for l in r["lights"]))
    avg = [float(np.mean([next((l[3] for l in r["lights"] if l[0] == k), 0) for r in good]))
           for k in keys]
    ax2.bar([rule.LIGHT_NAMES.get(k, k) for k in keys], avg, color="#5c6bc0")
    ax2.set_title("各维平均得分")
    ax2.tick_params(axis="x", rotation=30)
    for i, v in enumerate(avg):
        ax2.text(i, v + 0.02, f"{v:.2f}", ha="center", fontsize=8)

    fig.tight_layout()
    fig.savefig(PNG, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main():
    good, bad = load()
    if not good:
        sys.exit("扫描产物无有效标的")
    cfg = rule.ACTIVE
    d0 = good[0]["date"]
    snap = good[0].get("snap") or "无快照"
    intraday = d0 == datetime.now().strftime("%Y-%m-%d") and datetime.now().hour < 15
    buys = [r for r in good if r["target"] > 0]
    gateless = [r for r in good if not r["gate_pass"]]
    plot(good)

    glab = {g[0]: g[1] for g in good[0]["gates"]}
    L = ["# 亮灯策略 · 每日操作报告", "",
         f"> 数据日 {d0}{'（**盘中未收盘 bar**，量能类指标会随尾盘变化）' if intraday else ''}"
         f" · 快照 {snap} · 配置 **{cfg.label}**"
         f" · 评估节律 每 {cfg.rebal} 交易日 · 成本 单边 {cfg.fee:.2%}（ETF 佣金）",
         f"> 规则: 门槛层 {' + '.join(glab.get(g, g) for g in cfg.gates)} 全过，"
         f"且得分 ≥ {cfg.enter_min:g}"
         f"{('，且 ' + '、'.join(f'{rule.LIGHT_NAMES.get(k, k)}≥{v:g}' for k, v in cfg.enter_required.items())) if cfg.enter_required else ''}"
         f" → 目标建仓；否则目标空仓。信号变化在**次日开盘**执行（无前视）。",
         f"> 成功 {len(good)} 只 / 失败 {len(bad)} 只 · 门槛全过 {len(good) - len(gateless)} 只"
         f" · 目标建仓 **{len(buys)}** 只", ""]

    # 1 操作清单
    L += ["## 1. 操作清单", "",
          "| 场内 | 主题 | 分级 | 得分 | 目标仓位 | 建议动作 | 未通过门槛 |",
          "|---|---|---|---|---|---|---|"]
    for r in sorted(good, key=lambda r: (-r["score"], r["inner"])):
        act = ("次日开盘买入/持有" if r["target"] > 0
               else f"次日开盘清仓（{r['why']}）")
        fail = "、".join(rule.LIGHT_NAMES.get(g, g) if g in rule.LIGHT_NAMES
                        else next((x[1] for x in r["gates"] if x[0] == g), g)
                        for g in r["failed"]) or "—"
        L.append(f"| `{r['inner']}` | {r['theme']} | {r['grade']} | {r['score']:g} | "
                 f"{r['target']:.2f} | {act} | {fail} |")
    L.append("")

    # 2 汇总统计
    n = len(good)
    L += ["## 2. 汇总统计", "",
          f"- 门槛全过: **{len(good) - len(gateless)} / {n}**"
          f"（流动性 {sum(1 for r in good if all(x[4] for x in r['gates'] if x[0] == 'liq'))}/{n}）",
          f"- 目标建仓: **{len(buys)} / {n}** → " + ("、".join(f"`{r['inner']}`" for r in buys) or "（空仓）"),
          f"- 平均得分: **{np.mean([r['score'] for r in good]):.2f}**"
          f"（最高 {max(r['score'] for r in good):g} / 最低 {min(r['score'] for r in good):g}）",
          f"- 各门槛未通过只数: " + "、".join(
              f"{next((x[1] for x in good[0]['gates'] if x[0] == g), g)} "
              f"{sum(1 for r in good if g in r['failed'])}"
              for g in cfg.gates),
          f"- 各灯平均得分: " + "、".join(
              f"{rule.LIGHT_NAMES.get(k, k)} {np.mean([next((l[3] for l in r['lights'] if l[0] == k), 0) for r in good]):.2f}"
              for k in dict.fromkeys(l[0] for r in good for l in r["lights"])),
          ""]

    # 3 门槛明细
    gate_keys = [g[0] for g in good[0]["gates"]]
    gate_lab = {g[0]: g[1] for g in good[0]["gates"]}
    L += ["## 3. 逐标的 · 门槛明细（实际值 vs 阈值）", "",
          "| 标的 | 主题 | 分级 | " + " | ".join(gate_lab[k] for k in gate_keys)
          + " | 门槛结论 | 未通过项 |",
          "|---" * (len(gate_keys) + 5) + "|"]
    for r in sorted(good, key=lambda r: (not r["gate_pass"], r["inner"])):
        cells = []
        for g in r["gates"]:
            mark = "✔" if g[4] else "✘"
            cells.append(f"{mark} {g[3]}")
        L.append(f"| `{r['inner']}` | {r['theme']} | {r['grade']} | " + " | ".join(cells)
                 + f" | {'通过' if r['gate_pass'] else '未过'} | "
                 + ("、".join(r["failed"]) or "—") + " |")
    L.append("")
    L.append("> 阈值: " + "；".join(f"{gate_lab[k]} = {good[0]['gates'][i][2]}"
                                   for i, k in enumerate(gate_keys)))
    L.append("")

    # 4 亮灯明细
    light_keys = [l[0] for l in good[0]["lights"]]
    keys_used = [k for k in rule.LIGHT_KEYS if k in light_keys]
    L += ["## 4. 逐标的 · 各维亮灯明细（得分 · 触发依据）", "",
          "| 标的 | 主题 | 分级 | " + " | ".join(rule.LIGHT_NAMES.get(k, k) for k in keys_used)
          + " | 得分 | 目标仓位 | 判定 |",
          "|---" * (len(keys_used) + 6) + "|"]
    for r in sorted(good, key=lambda r: (-r["score"], r["inner"])):
        cells = []
        for k in keys_used:
            l = next((x for x in r["lights"] if x[0] == k), None)
            if not l:
                cells.append("—")
            else:
                mark = "✔" if l[3] > 0 else "✘"
                cells.append(f"{mark} {l[3]:g} · {l[4]}")
        L.append(f"| `{r['inner']}` | {r['theme']} | {r['grade']} | " + " | ".join(cells)
                 + f" | **{r['score']:g}** | {r['target']:.2f} | {r['dec']} · {r['why']} |")
    L.append("")
    L.append(f"> 计分维度与权重: " + "；".join(
        f"{rule.LIGHT_NAMES.get(k, k)} = {cfg.lights[k][0]} × {cfg.lights[k][1]}"
        for k in keys_used if cfg.lights.get(k)) )
    L.append(f"> 判定: 得分 > {cfg.exit_max:g} 且 门槛全过 → 持有; 得分 ≤ {cfg.exit_max:g} "
             f"或 门槛不过 → 清仓（当前为"
             f"{'目标制：门槛不过即清仓' if cfg.exit_on_gate_fail else '事件制：按得分与灯组合离场'}）。")
    L.append("")

    # 5 真实快照交叉验证
    L += ["## 5. 真实快照交叉验证（仅作参考，不参与计分）", "",
          "| 标的 | 主题 | 快照主力净额占比 | 快照换手率 | 快照量比 | 代理3日主力强度 | 符号一致性 |",
          "|---|---|---|---|---|---|---|"]
    n_same = n_diff = n_na = 0
    for r in sorted(good, key=lambda r: r["inner"]):
        cap3 = r["values"].get("cap3")
        mp = r["real_main_pct"]
        if mp is None or cap3 is None:
            agree = "无快照数据"
            n_na += 1
        elif (mp >= 0) == (cap3 >= 0):
            agree = "同号"
            n_same += 1
        else:
            agree = "**异号**"
            n_diff += 1
        L.append(f"| `{r['inner']}` | {r['theme']} | "
                 f"{'—' if mp is None else f'{mp:+.2f}%'} | "
                 f"{'—' if r['real_turn'] is None else str(r['real_turn'])} | "
                 f"{'—' if r['real_vol_ratio'] is None else r['real_vol_ratio']} | "
                 f"{'—' if cap3 is None else f'{cap3:+.2f}'} | {agree} |")
    L.append("")
    L.append(f"> 符号一致: 同号 {n_same} / 异号 {n_diff} / 无快照 {n_na}。")
    L.append("> 代理口径: 历史无主力资金/换手率明细时, 用 CMF 三日净流 × 标定系数 估「主力强度」、"
             "成交额 60 日分位 估「换手分位」。")
    L.append("> ⚠ **异号不等于代理失效**: 快照是**当日**主力净额, 代理是**3 日** CMF 均值 —— "
             "窗口不同本身就会产生符号分歧（3 日均值易被前几日主导）。且真实快照只有当日、"
             "无法回溯, 算不出代理与真实主力资金的长期相关性, 因此**无法判定**代理有效与否。"
             "异号只表示「该标的的代理读数当日存疑, 建议人工判断」, 不可据此推断代理结论失效。")
    L.append("")

    if bad:
        L += ["## 扫描失败", "", "| 标的 | 主题 | 原因 |", "|---|---|---|"]
        L.extend(f"| `{r['inner']}` | {r['theme']} | {r.get('err')} |" for r in bad)
        L.append("")

    L += ["", f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 非投资建议，实盘前须人工复核"
          + ("。**当前使用盘中未收盘 bar，请 14:50 后复核。**" if intraday else "。"), ""]

    txt = "\n".join(L)
    open(MD, "w", encoding="utf-8").write(txt)
    b64 = base64.b64encode(open(PNG, "rb").read()).decode()
    esc = txt.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    open(HTML, "w", encoding="utf-8").write(
        '<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">'
        f'<title>亮灯策略 · 每日操作报告 {d0}</title><style>'
        'body{font-family:"Microsoft YaHei",sans-serif;background:#f5f6fa;margin:24px;color:#222}'
        'h1{font-size:20px}img{max-width:100%;border-radius:10px;box-shadow:0 2px 10px rgba(0,0,0,.08)}'
        'pre{background:#fff;padding:16px;border-radius:10px;overflow:auto;font-size:12px;line-height:1.5}'
        f'</style></head><body><h1>亮灯策略 · 每日操作报告（{d0}）</h1>'
        f'<img src="data:image/png;base64,{b64}"><pre>{esc}</pre></body></html>')

    print(f"[每日报告] {len(good)} 只 · 目标建仓 {len(buys)} 只")
    print(f"  -> {MD}")
    print(f"  -> {PNG}")
    print(f"  -> {HTML}")


if __name__ == "__main__":
    main()
