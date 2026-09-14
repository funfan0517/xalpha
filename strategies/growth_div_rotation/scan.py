# -*- coding: utf-8 -*-
"""成长/红利风格轮动 · 每日信号报告（盘后运行，决策次日开盘执行）

口径与回测引擎 engine.run_strategy **逐行同构**（差一处就给错建议）：
  - 指标用 R_{-1}（最新可得收盘，盘后即今日收盘）对应的 MA20/缓冲带/Q
  - 当前持仓 = 历史状态机推到今日的仓位（pos[-1]）
  - 明日动作 = 在「当前持仓」上套用规则（成长/红利二元），并受月频上限约束
  - 信号用指数、执行走场外联接基金（见 rule.EXEC）

只读本地缓存（先跑 data.py --refresh 或收盘后 data.py 增量刷新），本脚本不重抓全量。
用法: python strategies/growth_div_rotation/scan.py [--alt]
输出: daily/_signal_report.md · daily/_gd_signal.json
"""
import io
import json
import os
import sys
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_DIR))
for _p in (_DIR, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pandas as pd  # noqa: E402

import rule  # noqa: E402
import data as datamod  # noqa: E402
import factors  # noqa: E402
import engine  # noqa: E402

OUT_MD = os.path.join(_DIR, "daily", "_signal_report.md")
OUT_JSON = os.path.join(_DIR, "daily", "_gd_signal.json")
_WD = "一二三四五六日"


def dstr(ts):
    return f"{ts.date()}（周{_WD[ts.weekday()]}）"


def side_name(s):
    return "成长" if s == 1 else "红利/现金"


def _fmt(v, nd=4):
    return "—" if v is None else f"{v:.{nd}f}"


def main():
    alt = "--alt" in sys.argv
    datamod.append_latest()  # 增量刷最新 bar
    gcode = rule.growth_code(alt)
    dcode = rule.DIV_INDEX
    wide = datamod.load_wide(codes=[gcode, dcode])
    if wide.shape[1] < 2 or len(wide) < rule.QUANTILE_WINDOW + 30:
        sys.exit("数据不足：先 python strategies/growth_div_rotation/data.py --refresh")
    gclose, dclose = wide[gcode], wide[dcode]
    sig = factors.compute_signals(gclose, dclose)
    res = engine.run_strategy(sig, gclose, dclose)

    today = sig.index[-1]
    snap = factors.latest_snapshot(sig)
    cur_pos = int(res["pos"].iloc[-1])            # 今日持仓（由 R_{-1}=昨日 决定）
    cur_pos_prev = int(res["pos"].iloc[-2]) if len(res["pos"]) >= 2 else cur_pos

    R = snap["R"]
    up, lo, q = snap["upper"], snap["lower"], snap["q"]
    # 明日动作（在「当前持仓」上套规则）
    action = "不动"
    reason = "处于中间地带 / 未触发切换阈值"
    indicated = False
    paused = False
    if snap["valid"]:
        if cur_pos == 0:
            if R > up and q < rule.ENTER_Q_MAX:
                indicated = True
                action = "切成长"
                reason = f"R={R:.4f} > 上轨 {up:.4f} 且 Q={q:.2%} < {rule.ENTER_Q_MAX:.0%}（成长低估切换窗口）"
        else:
            if R < lo or q > rule.EXIT_Q_MIN:
                indicated = True
                action = "切红利"
                reason = (f"R={R:.4f} < 下轨 {lo:.4f}" if R < lo
                          else f"Q={q:.2%} > {rule.EXIT_Q_MIN:.0%}（成长高估，风控离场）")
    # 月频上限：本月已执行切换次数
    mk = (today.year, today.month)
    done_this_month = res["month_count"].get(mk, 0)
    if indicated:
        if done_this_month >= rule.MAX_TRADES_PER_MONTH:
            paused = True
            action = "不动（本月已超上限，暂停一次）"
            reason += f" · 但本月已切换 {done_this_month} 次 ≥ {rule.MAX_TRADES_PER_MONTH}，防摩擦暂停"

    g_code, g_name, d_code, d_name = rule.exec_pair(alt)

    # ---- 报告 ----
    L = ["# 成长/红利风格轮动 · 每日信号报告", "",
         f"> 生成 {datetime.now().strftime('%Y-%m-%d %H:%M')} · 数据 {sig.index.min().date()}"
         f"~{today.date()}（{len(sig)} 交易日）· 成长={rule.growth_label(alt)} ÷ 红利={rule.DIV_NAME}",
         f"> 口径：R=成长/红利 比值；缓冲带 ±{rule.BUFFER:.1%}；分位窗口 {rule.QUANTILE_WINDOW} 日；"
         f"次日开盘执行；信号用指数、申赎走场外联接 · 机械规则，非投资建议。",
         f"> 注：R 的绝对值量级取决于数据源指数点位（如中证红利在不同源约 11~5500），"
         f"但策略只看 R 相对自身均线/分位的相对位置，缓冲带与分位均无量纲，信号与数据源缩放无关。", ""]

    # 一、明日结论
    L += ["## 一、明日（开盘）操作结论", "",
         f"- **今日持仓（策略口径）**：{side_name(cur_pos)}"
         + ("" if cur_pos == cur_pos_prev else f"（昨日为 {side_name(cur_pos_prev)}）"),
         f"- **明日建议**：**{action}**",
         f"- **依据**：{reason}",
         f"- **成长侧执行**：`{g_code}` {g_name}　**红利侧执行**：`{d_code}` {d_name}",
         f"- **切换成本**：单边 {rule.FEE:.2%}（佣金，未含滑点）；本月已切换 {done_this_month}"
         f"/{rule.MAX_TRADES_PER_MONTH} 次", ""]

    # 二、指标快照
    L += ["## 二、当日指标快照（盘后）", "",
          "| 指标 | 数值 | 判读 |",
          "|---|---|---|",
          f"| 风格比值 R | {_fmt(R)} | — |",
          f"| MA20 | {_fmt(snap['ma20'])} | 缓冲带基准 |",
          f"| MA30 | {_fmt(snap['ma30'])} | 辅助（震荡大可改用） |",
          f"| 上轨 (MA20×1+{rule.BUFFER:.0%}) | {_fmt(up)} | R>上轨 且 Q<{rule.ENTER_Q_MAX:.0%} → 切成长 |",
          f"| 下轨 (MA20×1-{rule.BUFFER:.0%}) | {_fmt(lo)} | R<下轨 或 Q>{rule.EXIT_Q_MIN:.0%} → 切红利 |",
          f"| 250日分位 Q | {f'{q * 100:.1f}%' if q is not None else '—'} | "
          f"{('<0.3 成长低估' if (q is not None and q < rule.Q_LOW) else '>0.7 成长高估' if (q is not None and q > rule.Q_HIGH) else '0.3~0.7 中性')} |",
          ""]

    # 三、每日看盘清单（复制即可用）
    L += ["## 三、每日看盘清单（复制即可用）", "",
          "1. 取成长、红利最新收盘价，算 R = 成长 ÷ 红利",
          f"2. 算 MA20/MA30 与上下缓冲带（±{rule.BUFFER:.1%}）",
          f"3. 算 250 日分位数 Q",
          "4. 按规则判断：切成长 / 切红利 / 不动",
          "5. 记录成交价、换手率、手续费；若当月已超 "
          f"{rule.MAX_TRADES_PER_MONTH} 次，暂停一次防摩擦", ""]

    # 四、风控与调参提示
    L += ["## 四、风控与微调提示", "",
          f"- 分位风控：Q<{rule.Q_LOW:.0%} 成长低估、Q>{rule.Q_HIGH:.0%} 成长高估；"
          f"缓冲带默认 ±{rule.BUFFER:.1%}，可在 {rule.BUFFER_RANGE[0]:.1%}~{rule.BUFFER_RANGE[1]:.1%} 微调",
          "- 震荡大（R 频繁穿均线）：加宽缓冲带到 1.5% 或改用 30 日均线",
          "- 科创50（波动更小）：缓冲带建议 0.5%~1%，避免信号失效",
          "- 交易成本高（佣金+滑点>0.3%）：提高缓冲带、限制月频 ≤2 次",
          f"- 本月已切换 {done_this_month}/{rule.MAX_TRADES_PER_MONTH} 次"
          + (" → 已达上限，下次信号将暂停" if done_this_month >= rule.MAX_TRADES_PER_MONTH else ""), ""]

    # 五、近 8 次切换
    L += ["## 五、最近切换记录", "",
          "| 切换日 | 由 | 切到 | R_{-1} | Q_{-1} |",
          "|---|---|---|---|---|"]
    if res["switches"]:
        for s in res["switches"][-8:]:
            L.append(f"| {s['date']} | {side_name(s['from_side'])} | **{side_name(s['to_side'])}** "
                     f"| {s['R']:.4f} | {s['q']:.2%} |")
    else:
        L.append("| — | — | — | — | — |")
    L.append("")
    L.append("> 免责声明：机械规则仅供参考，不构成投资建议；最大回撤仍可达 50%+，"
             "单策略不宜满仓，建议与红利/债券做 50/50 仓位中枢、仅用部分资金轮动。")

    txt = "\n".join(L) + "\n"
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    open(OUT_MD, "w", encoding="utf-8").write(txt)
    print(txt)

    json.dump({
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "as_of": str(today.date()),
        "alt": alt,
        "growth": rule.growth_label(alt),
        "dividend": rule.DIV_NAME,
        "snapshot": snap,
        "current_position": side_name(cur_pos),
        "current_position_code": (g_code if cur_pos == 1 else d_code),
        "action_tomorrow": action,
        "indicated": indicated,
        "paused_by_monthly_cap": paused,
        "reason": reason,
        "month_switches": done_this_month,
        "month_cap": rule.MAX_TRADES_PER_MONTH,
        "exec": {"growth_off": g_code, "growth_name": g_name,
                 "dividend_off": d_code, "dividend_name": d_name},
        "params": dict(buffer=rule.BUFFER, enter_q=rule.ENTER_Q_MAX,
                       exit_q=rule.EXIT_Q_MIN, q_low=rule.Q_LOW, q_high=rule.Q_HIGH,
                       max_trades_month=rule.MAX_TRADES_PER_MONTH, fee=rule.FEE),
    }, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
