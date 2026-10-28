# -*- coding: utf-8 -*-
"""波动资产操作规则 · 每日操作(决策点复算)。

口径与 backtest.py **逐行同构**: 同一个 engine.run() 跑到最新交易日, 把最后一天产生的
「挂单」当作今日动作 —— 文档 §0 规定「收盘后计算、次日执行」, 所以报告里的动作是
**次日收盘**执行的那一笔, 不是让你今天盘中追。

复算起点(`--since`)决定持仓状态的来历:
  默认 = **目标带起步**(a500=5, ndx=10, spx=10, kc50=2.5, hkt=2.5, gold=10 = 40pp),
  即「已按基准比例持有, 之后完全交给规则调整」—— 实盘口径, 也是 backtest 的 seed 档。
  起点退到「六腿就绪日」, 让起点到今天的漂移有界。`--hold` 覆盖成你的真实持仓(同格式);
  `--from-zero` 切回「空仓起步」对照口径(文档 §2 自检「满梯 9% ≈ 填满 10% 带」的读法)。
  注意: 带内起步时起点现金 = 0, **加仓只能靠卖出其他腿腾挪或追加资金**。

报告里的「今日理论最优持仓比例」回答「按规则今天我该持多少」, 与「今天会不会动手」是两件事:
差额里只有「趋势闸门降档 / 再平衡超配」会真成交, 「闸门 off / 阶梯未填满」只是停在那里等条件,
**不构成卖出指令**(再平衡的锚仍是目标带)。

美股腿(v1.1 §6)的三个特殊点都在报告里显式标注: 信号源是美元指数还是降级到人民币代理、
ERP 闸门取自人工月度复核值、QDII 额度关闭时该档顺延。

用法:
  python strategies/vol_legs/scan.py                      # 当日(读本地行情库, 带内起步)
  python strategies/vol_legs/scan.py --force              # 先刷新场外净值 + 行情库
  python strategies/vol_legs/scan.py --from-zero          # 空仓起步对照口径
  python strategies/vol_legs/scan.py --capital 2000000    # 按总资产折算金额
  python strategies/vol_legs/scan.py --hold a500=5,ndx=10,spx=10,kc50=2.5,hkt=2.5,gold=10
输出:
  strategies/vol_legs/daily/_vol_legs_report.md
  strategies/vol_legs/daily/_vol_legs_signal.json
  strategies/vol_legs/daily/_universe_vol_legs_active.{md,json}
"""
import argparse
import io
import json
import os
import sys
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (_ROOT, _DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import rule  # noqa: E402
import data  # noqa: E402
import engine  # noqa: E402

_DAILY = os.path.join(_DIR, "daily")
_OUT_MD = os.path.join(_DAILY, "_vol_legs_report.md")
_OUT_JSON = os.path.join(_DAILY, "_vol_legs_signal.json")
_ACT_MD = os.path.join(_DAILY, "_universe_vol_legs_active.md")
_ACT_JSON = os.path.join(_DAILY, "_universe_vol_legs_active.json")

# 目标带 = 实盘的起点持仓(用户按基准比例持有), 也是「今日理论最优」的对照锚
BAND_SEED = {k: rule.LEGS[k]["band"] for k in rule.LEG_ORDER}


# ---------------------------------------------------------------- 工具
def _pct(x, nd=1):
    return "—" if x is None else f"{x * 100:+.{nd}f}%"


def _f(x, nd=2):
    return "—" if x is None else f"{x:.{nd}f}"


def _ready_start(ind):
    """六腿指标全部就绪的第一天(与回测同口径)。"""
    starts = [ind[k][ind[k]["ready"]].index[0] for k in rule.LEG_ORDER
              if len(ind[k][ind[k]["ready"]])]
    return max(starts) if starts else None


def parse_hold(s):
    out = {}
    for part in (s or "").split(","):
        part = part.strip()
        if not part:
            continue
        k, _, v = part.partition("=")
        k = k.strip()
        if k not in rule.LEGS:
            raise SystemExit(f"--hold 未知腿 {k}, 可用: {list(rule.LEGS)}")
        out[k] = float(v)
    return out


def _px(x):
    """价格展示: 指数动辄上万点, 别用 3 位小数糊一屏。"""
    if x is None:
        return "—"
    if abs(x) >= 1000:
        return f"{x:,.0f}"
    if abs(x) >= 100:
        return f"{x:.1f}"
    return f"{x:.3f}"


def _zone(row):
    if not row["ready"]:
        return "数据未就绪"
    return "年线上方" if row["above"] else "年线下方"


def _next_tier(pol, key):
    """最低的未填档位 -> (序号, 阈值, 所需价)。"""
    cfg = rule.CFG[key]
    for i, th in enumerate(cfg["ladder_dd"]):
        if not pol.tiers_done[i]:
            return i, th
    return None, None


def _gap_to(dd_th, row):
    """当前价还需跌多少才触及 dd 阈值(负=还要跌)。"""
    if dd_th is None or row["high52w"] is None or not row["close"]:
        return None
    target_price = row["high52w"] * (1.0 - dd_th / 100.0)
    return target_price / row["close"] - 1.0


def leg_view(key, ind, res, pending, gates):
    """单腿展示信息。"""
    leg_ind = ind[key]
    row = rule.row_of(leg_ind, leg_ind.index[-1])
    prev = rule.row_of(leg_ind, leg_ind.index[-2]) if len(leg_ind) > 1 else row
    pol = (res["policies"] or {}).get(key)
    pos = res["pos"].get(key, 0.0)
    ops = [o for o in pending if o["leg"] == key]
    v = dict(name=rule.LEGS[key]["name"], signal=rule.LEGS[key]["signal"],
             signal_name=rule.LEGS[key]["signal_name"], exec=rule.LEGS[key]["exec"],
             exec_name=rule.LEGS[key]["exec_name"], band=rule.LEGS[key]["band"],
             close=row["close"], ma=row["ma"], high52w=row["high52w"],
             dev=row["ma_dev"], dd=row["dd"], rsi=row["rsi"],
             rsi_prev=prev["rsi"], zone=_zone(row), below_days=row["below_days"],
             ready=row["ready"], pos_pp=round(pos, 2), as_of=str(leg_ind.index[-1].date()),
             action="none", action_label="不动", rule="", detail="")

    if ops:
        o = ops[0]
        cur = pos
        delta = (o["target_pp"] - cur) if o.get("target_pp") is not None else o.get("delta_pp") or 0
        v.update(action="buy" if delta > 0 else "sell",
                 action_label=("买入" if delta > 0 else "卖出") + f" {abs(delta):.2f}pp",
                 rule=o["rule"], detail=o["detail"], delta_pp=round(delta, 3))
    elif pos > 0.05:
        v.update(action="hold", action_label=f"持有 {pos:.1f}pp",
                 rule="无触发", detail="维持当前仓位")
    else:
        v.update(action="none", action_label="空仓", rule="无触发", detail="等待阈值")

    # 触发条件说明
    if key in ("a500", "ndx", "spx", "gold") and pol is not None:
        i, th = _next_tier(pol, key)
        gate_off = key != "gold" and gates.get(f"{key}_erp") == "off"
        if i is None:
            v["trigger"] = "阶梯已填满(受仓位带上限约束)"
        else:
            gap = _gap_to(th, row)
            extra = ""
            if key in ("a500", "ndx", "spx"):
                extra = " + RSI<40 扳机" if (pol.armed_tier is not None) else ""
            txt = (f"第{i + 1}档 dd≥{th}%"
                   + (f"(还需再跌 {abs(gap):.1%})" if gap is not None and gap < 0 else
                      "(已触及)" if gap is not None else "")
                   + extra)
            if gate_off:
                txt += " · **但启动闸门 off(暂停新增)**"
            v["trigger"] = txt
    elif key in ("kc50", "hkt"):
        tgt = pol.target_pp() if pol else 0.0
        band = rule.LEGS[key]["band"]
        half = band / 2.0
        clear_n = rule.CFG["kc50"]["clear_after_days"]
        half_n = rule.CFG["kc50"]["half_in_days"]
        below = int(row["below_days"] or 0)
        if not row["ready"]:
            v["trigger"] = "250 日窗口未满"
        elif row["above"]:
            v["trigger"] = (f"跌破 MA250 → {half_n} 日内减半(现距年线 {_pct(row['ma_dev'])} 缓冲)"
                            if tgt >= band - 1e-9 else
                            f"已在年线上, 加仓许可成立即加至 {band:g}pp")
        elif tgt >= band - 1e-9:
            v["trigger"] = (f"年线下方第 {below} 日 → {max(half_n - below, 0)} 日后减至半仓"
                            f"(现距年线 {_pct(row['ma_dev'])})")
        elif tgt > rule.OBSERVE_PP + 1e-9:
            v["trigger"] = (f"已减半; 年线下第 {below} 日, {max(clear_n - below, 0)} 日后清至观察仓")
        elif pos > rule.OBSERVE_PP + 0.05:
            v["trigger"] = f"已减至观察仓({pos:.1f}pp); 收复年线才加回带顶"
        elif pos > 0.05:
            v["trigger"] = (f"剩余 {pos:.1f}pp(<观察仓 {rule.OBSERVE_PP:g}pp); "
                            f"年线下第 {below} 日, 收复年线才加仓")
        else:
            v["trigger"] = f"空仓(年线下第 {below} 日); 收复年线才加仓"
    return v


def _theory_note(key, pol, gates, legs):
    """单腿「今日理论最优」的依据文字。"""
    if key in ("a500", "ndx", "spx"):
        txt = f"阶梯已建 {float(pol.units):.2f} 份 × {rule.unit_pp(key):g}pp"
        gate = gates.get(f"{key}_erp")
        if gate == "off":
            txt += "; 启动闸门 off(只停新增, 存量不动)"
        elif gate is None:
            txt += "; 启动闸门 null(不设限)"
        return txt
    if key == "gold":
        txt = f"阶梯已建 {float(pol.units):.2f} 份 × {rule.unit_pp('gold'):g}pp"
        if getattr(pol, "degraded", False):
            txt += "; 长期保险丝: 年线下满 60 日 → 带 10→5"
        elif legs[key]["zone"] != "年线上方":
            txt += "; 年线下只停加仓"
        return txt
    x = legs[key]
    if x["zone"] == "年线上方":
        txt = "年线上 → 可持带顶"
    else:
        txt = f"年线下 {int(x['below_days'] or 0)} 日 → 半仓/观察仓"
    ok, why = pol._add_allowed(gates)
    if not ok:
        txt += f"; 加仓许可不成立({why})"
    return txt


def theory_table(res, legs, gates):
    """今日理论最优持仓: 规则授权目标(target_pp) vs 目标带 vs 当前市值。

    口径: 1pp = 总资产 1%(与本策略 40pp = 总资产 40% 同刻度)。「理论最优」是规则
    今日授权的仓位上限/目标(阶梯已建份数、趋势闸门档位、黄金保险丝降档后的带),
    不是「必须立刻买到的量」; 与市值的差要看依据列里是哪种规则在管。
    """
    rows, total, band_sum, pos_sum = {}, 0.0, 0.0, 0.0
    for key in rule.LEG_ORDER:
        pol = res["policies"][key]
        band = float(rule.LEGS[key]["band"])
        tgt = float(pol.target_pp())
        pos = float(res["pos"].get(key, 0.0))
        rows[key] = dict(key=key, name=rule.LEGS[key]["name"], band=band,
                         target_pp=round(tgt, 2), diff_pp=round(pos - tgt, 2),
                         pos_pp=round(pos, 2), note=_theory_note(key, pol, gates, legs))
        total += tgt
        band_sum += band
        pos_sum += pos
    return dict(legs=rows, total_pp=round(total, 2), band_pp=round(band_sum, 2),
                pos_pp=round(pos_sum, 2),
                sleeve_pct=round(total / band_sum, 4) if band_sum else 0.0)


def render(ctx):
    v, res, gates = ctx["legs"], ctx["res"], ctx["gates"]
    cap = ctx["capital"]
    as_of = max(x["as_of"] for x in v.values())
    inv = sum(x["pos_pp"] for x in v.values())
    L = []
    A = L.append
    A("# 波动资产操作规则 · 每日操作")
    A("")
    nav = res["cash"] + inv
    base = ctx.get("base", "band")
    base_txt = {"band": "目标带起步", "hold": "`--hold` 自定义起步",
                "zero": "空仓起步(`--from-zero`)"}[base]
    A(f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 净值/行情截至 **{as_of}** · 复算起点 "
      f"**{ctx['since']:%Y-%m-%d}**({base_txt}) · 管辖额度 "
      f"{rule.SLEEVE_PP:.0f}pp · 当前投入 {inv:.1f}pp / 管辖净值 {nav:.1f}pp"
      f"(仓位率 {inv / nav:.0%})。")
    A(f"> **口径: 信号 & 记账 & 执行全用场外基金复权净值**(`rule.SIGNAL_MODE=\"{rule.SIGNAL_MODE}\"`)"
      " —— 净值涨跌就是你的账户涨跌, 不含场内溢价; 换回场内/美元指数口径置 "
      f"`SIGNAL_MODE=\"exchange\"`。")
    A("> §0 口径: **收盘后计算、次日执行**。场外基金 15:00 前提交才拿当日净值, 所以"
      "「今日动作」= **明天下单**、按明天净值成交。机械规则输出, 非投资建议。")
    if base == "band":
        A("> 起点口径: **按目标带 5/10/10/2.5/2.5/10 起步**(= 你的实际持仓), 之后完全交给规则; "
          "起点现金为 0, 所以**加仓只能靠卖出其他腿腾挪或追加资金**。换一个起点用 "
          "`--hold a500=...,ndx=...`; 想看「空仓起步」对照用 `--from-zero`。")
    elif base == "hold":
        A("> 起点口径: `--hold` 自定义持仓(**起点日**的值, 不是今日); 起点已退到六腿就绪日。"
          "想让「今天 = 你的实际持仓」, 把 `--since` 设成你建仓的日期。")
    else:
        A("> 起点口径: **空仓起步**(`--from-zero`) —— 由阶梯/趋势闸门从 0 填到带, 是「规则自己建仓」"
          "的对照读法, 与实盘(已持满带)不同。")
    if rule.SIGNAL_MODE == "exchange" and rule.A500_USE_PROXY_HISTORY:
        A(f"> A500 信号序列 = `{rule.A500_PROXY_CODE}`(沪深300ETF) 链接至 "
          f"`{rule.LEGS['a500']['signal']}` 上市(2024-10)之后 —— 只为补齐 250 日窗口。")
    A("")

    # ---- 一、今日动作
    A("## 一、今日动作(下一交易日下单)")
    A("")
    acts = [x for x in v.values() if x["action"] in ("buy", "sell")]
    if acts:
        A("| 资产 | 动作 | 金额 | 触发规则 | 当时关键数值 |")
        A("|---|---|---:|---|---|")
        for x in acts:
            amt = f"{abs(x.get('delta_pp') or 0):.2f}pp"
            if cap:
                amt += f" ≈ {cap * abs(x.get('delta_pp') or 0) / 100:,.0f} 元"
            A(f"| {x['name']} | **{x['action_label']}** | {amt} | {x['rule']} | {x['detail']} |")
    else:
        A("**无动作** —— 六条腿均未触发阈值, 维持现有仓位。")
    if any(x["action"] in ("buy", "sell") and x["name"] in ("纳指100", "标普500") for x in v.values()):
        A("")
        A("> QDII 时滞(§6): 美股腿信号日提交、T+2 确认; 时滞内价格波动**不改单不撤单**。")
    A("")

    # ---- 二、今日理论最优持仓
    th = ctx["theory"]
    A("## 二、今日理论最优持仓比例")
    A("")
    A(f"> 「理论最优」= 规则在今天授权下**该持多少**(pp of 总资产; 1pp = 总资产 1%, "
      f"40pp 即占总资产 40%)。合计 **{th['total_pp']:.1f}pp / {th['band_pp']:.0f}pp"
      f"({th['sleeve_pct']:.0%} 的额度)**。")
    A("> **怎么读**: 「差值」= 当前市值 − 理论最优。>0 是超配(偏离超过 "
      f"min({rule.BAND_PP:.0f}pp, 带×{rule.BAND_FRAC:.0%}) 时由季度再平衡减持), "
      "<0 是低于授权(受闸门/现金约束, **不等于该买**)。理论最优低于目标带, 说明规则今天不愿把"
      "这部分额度用完 —— 差额留货币基金, 不是让你卖掉存量。")
    A("")
    A("| 腿 | 目标带 | **今日理论最优** | 占总资产 | 当前市值 | 差值 | 依据 |")
    A("|---|---:|---:|---:|---:|---:|---|")
    for key in rule.LEG_ORDER:
        r = th["legs"][key]
        A(f"| {r['name']} | {r['band']:g}pp | **{r['target_pp']:.1f}pp** | "
          f"{r['target_pp']:.1f}% | {r['pos_pp']:.1f}pp | {r['diff_pp']:+.1f}pp | {r['note']} |")
    A(f"| **合计** | {th['band_pp']:g}pp | **{th['total_pp']:.1f}pp** | "
      f"{th['total_pp']:.1f}% | {th['pos_pp']:.1f}pp | "
      f"{th['pos_pp'] - th['total_pp']:+.1f}pp | 未授权额度 "
      f"{th['band_pp'] - th['total_pp']:.1f}pp 留货币基金 |")
    A("")
    if cap:
        A(f"> 按总资产 {cap:,.0f} 元折算: 1pp = {cap / 100:,.0f} 元。")
        A("")

    # ---- 三、六腿状态
    A("## 三、六腿状态")
    A("")
    otc_mode = rule.SIGNAL_MODE == "otc"
    A("| 资产 | 净值/信号代码 | " + ("复权净值" if otc_mode else "收盘") +
      " | MA250 | 偏离 | 52周高 | 回撤 dd | RSI14 | 年线 | 当前仓位 | 目标带 | 下一触发条件 |")
    A("|---|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---|")
    for key in rule.LEG_ORDER:
        x = v[key]
        code = rule.LEGS[key]["otc"] if otc_mode else x["signal"]
        A(f"| {x['name']} | `{code}` | {_px(x['close'])} | {_px(x['ma'])} | "
          f"{_pct(x['dev'])} | {_px(x['high52w'])} | {_pct(-x['dd']) if x['dd'] is not None else '—'} | "
          f"{_f(x['rsi'], 1)} | {x['zone']} | {x['pos_pp']:.1f}pp | {x['band']:g}pp | "
          f"{x.get('trigger', '—')} |")
    A("")
    if otc_mode:
        srcs = [f"{rule.LEGS[k]['name']} {data.SOURCE_NOTE.get(k, '')}"
                for k in rule.LEG_ORDER if data.SOURCE_NOTE.get(k)]
        if srcs:
            A("> 净值序列(全部场外, 累计净值含分红再投): " + "; ".join(srcs) + "。")
            A("")
    A("> 回撤 dd = 1 − close / 近 250 交易日最高收盘; RSI14 为 Wilder 口径; "
      "「目标带」是§0 的战略上限, 不是建议立刻满仓。")
    A("")

    # ---- 三、阶梯与闸门
    A("## 四、阶梯状态与闸门")
    A("")
    for key, title in (("a500", "A500 回撤阶梯(§2)"), ("ndx", "纳指100 回撤阶梯(§6)"),
                       ("spx", "标普500 回撤阶梯(§6)")):
        s = v[key]
        pol = res["policies"][key]
        cfg = rule.CFG[key]
        full = sum(cfg["ladder_units"]) * rule.unit_pp(key)
        A(f"### {title}")
        A("")
        A(f"- 阶梯进度: {rule.ladder_text(key, pol)}")
        A(f"- 累计买入 **{pol.units:.2f} 份**(满梯 {sum(cfg['ladder_units'])} 份 = {full:.1f}pp, "
          f"1 份 = {rule.unit_pp(key):.2f}pp), 当前仓位 **{s['pos_pp']:.1f}pp** / 带宽 "
          f"{s['band']:g}pp(份数会因「创 52 周新高→阶梯归零」超过满梯, 仓位由带宽封顶 + "
          "季度再平衡减持)")
        armed = pol.armed_tier
        if armed is not None:
            A(f"- **已触及第 {armed + 1} 档, 正等 RSI 扳机**: RSI14 = {_f(s['rsi'], 1)}, "
              f"需 <{rule.RSI_TRIGGER:.0f} 或从 <{rule.RSI_TRIGGER:.0f} 回升的首日; "
              f"本月已用 {pol.month_tranche}/{cfg['max_tranche_per_month']} 档(频率帽)")
        else:
            A(f"- 未挂档(RSI {_f(s['rsi'], 1)}, dd "
              f"{_pct(-s['dd']) if s['dd'] is not None else '—'})")
        A(f"- 启动闸门: **{pol.gate_text(gates)}**")
        if key != "a500":
            src = data.SOURCE_NOTE.get(key, f"`{s['signal']}`")
            A(f"- 信号源: {src}" + ("" if otc_mode else "(§6 要求美元指数口径)"))
            closed = key in (gates.get("qdii_limit_closed") or [])
            A(f"- QDII 通道: 只走场外 A 类 `{s['exec']}`"
              f"{'; ⚠️ **额度关闭/限大额 → 该档顺延**(禁止换场内追溢价)' if closed else ''}")
            if gates.get("fomc_blackout"):
                A("- ⚠️ 今日在 FOMC 禁新仓窗口内(§6): 阶梯档位顺延")
        A("")
    A("### 黄金(§5)")
    A("")
    gd = v["gold"]
    gpol = res["policies"]["gold"]
    A(f"- 阶梯进度: {rule.ladder_text('gold', gpol)}")
    A(f"- 累计买入 **{gpol.units:.2f} 份**, 当前仓位 **{gd['pos_pp']:.1f}pp** / 带宽 "
      f"{gd['band']:g}pp; 波动闸门: {gd['zone']}(年线下只停加仓不清仓)")
    if gpol.degraded:
        A("- ⚠️ 长期保险丝生效: 年线下满 60 日 → 目标带 10% → 5pp")
    A("")
    A("### 进攻腿(§3/§4)")
    A("")
    A("| 腿 | 年线 | 连续天数 | 当前目标 | 加仓许可 | 说明 |")
    A("|---|---|---:|---:|---|---|")
    for key in ("kc50", "hkt"):
        x = v[key]
        tp = res["policies"][key]
        ok, why = tp._add_allowed(gates)
        A(f"| {x['name']} | {x['zone']} | {int(x['below_days'] or 0)} | "
          f"{tp.target_pp():.1f}pp | {'允许' if ok else '**不允许**'} | {why} |")
    A("")

    # ---- 四、再平衡
    A("## 五、季度再平衡接口(§7)")
    A("")
    A(f"- 下次核对: **{ctx['next_qend']:%Y-%m-%d}**(季度末最后一个交易日); 偏离超过 "
      f"**min({rule.BAND_PP:.0f}pp, 带×{rule.BAND_FRAC:.0%})** 才动, 否则不动。")
    A(f"- 方向口径: **{'双向回带' if rule.REBAL_BUY_BACK else '只减持超配'}**"
      f"(rule.REBAL_BUY_BACK={rule.REBAL_BUY_BACK})—— 补仓由阶梯/趋势闸门授权, 不在季度末强行拉回。")
    A("")
    A("| 腿 | 当前仓位 | 再平衡目标带 | 偏离 | 阈值 | 是否触发 |")
    A("|---|---:|---:|---:|---:|---|")
    for key in rule.LEG_ORDER:
        x = v[key]
        tgt = res["policies"][key].band_target()
        dev = x["pos_pp"] - tgt
        tol = rule.rebalance_pp(key)
        hit = abs(dev) > tol and (dev > 0 or rule.REBAL_BUY_BACK)
        A(f"| {x['name']} | {x['pos_pp']:.1f}pp | {tgt:.1f}pp | {dev:+.1f}pp | "
          f"{tol:g}pp | {'**是**' if hit else '否'} |")
    A("")

    # ---- 五、估值输入
    A("## 六、估值输入状态(§1「数据不足 → null → 规则不执行」)")
    A("")
    erp = gates["_erp"]
    vi = gates["_inputs"]
    A("| 输入 | 当前值 | 来源 | 状态 |")
    A("|---|---|---|---|")
    erp_txt = "null" if erp["erp_pct"] is None else f"{erp['erp_pct'] * 100:.0f}%"
    erp_state = f"**不执行**({erp['why']})" if erp["erp_pct"] is None else "生效"
    A(f"| A500 ERP 分位 | {erp_txt} | data/_a500_pe_hist.csv + bond10y_m.csv | {erp_state} |")
    for label, val in (("科创50 PB 分位", vi["kc50_pb_pct"]),
                       ("恒生科技 ERP 分位", vi["hkt_erp_pct"]),
                       ("纳指100 ERP 分位", vi["ndx_erp_pct"]),
                       ("标普500 ERP 分位", vi["spx_erp_pct"])):
        cur_txt = "null" if val is None else f"{val * 100:.0f}%"
        st = "生效" if val is not None else "**不执行**(未维护)"
        A(f"| {label} | {cur_txt} | data/_val_inputs.json | {st} |")
    qdii = gates.get("qdii_limit_closed") or []
    A(f"| QDII 额度状态 | {'关闭: ' + '/'.join(qdii) if qdii else '正常'} | data/_val_inputs.json | "
      f"{'生效(该档顺延)' if qdii else '—'} |")
    tips_txt = "null" if gates["tips_fuse"] is None else ("上行→暂停加仓" if gates["tips_fuse"] else "未上行")
    wgrc_txt = "null" if gates["wgrc_fuse"] is None else ("连续 2 季为负→降档" if gates["wgrc_fuse"] else "正常")
    for label, cur_txt, on in (("10Y TIPS 保险丝", tips_txt, gates["tips_fuse"] is not None),
                               ("WGRC 央行购金保险丝", wgrc_txt, gates["wgrc_fuse"] is not None)):
        A(f"| {label} | {cur_txt} | data/_val_inputs.json | "
          f"{'生效' if on else '**不执行**(未维护)'} |")
    n_fomc = len(vi["fomc_days"])
    A(f"| FOMC 禁新仓窗口 | {n_fomc} 个决议日 | data/_val_inputs.json | "
      f"{'生效' if n_fomc else '**不执行**(日历未维护)'} |")
    A("")
    if erp["erp"] is not None:
        A(f"> A500: PE(TTM) {erp['pe']:.2f} · 10Y 中债 {erp['bond10y']:.2f}% · "
          f"ERP {erp['erp']:.2f}% · 月度样本 {erp['n_months']} 个"
          f"(分位需 ≥{data.PCT_MIN_MONTHS} 个)。")
    us = gates.get("_us_erp") or {}
    if us.get("ust10y") is not None:
        extra = ""
        if us.get("ndx_fwd_pe"):
            extra = (f" · NDX 前瞻 PE {us['ndx_fwd_pe']:.2f}({us['ndx_fwd_pe_asof']}) → 前瞻盈利收益率 "
                     f"{us['ndx_ey']:.2f}%")
        A(f"> 美股辅助读数(**仅展示, 不参与闸门判定**): 10Y 美债 {us['ust10y']:.2f}%"
          f"({us['ust10y_asof']}){extra}。§6 要的是 PE(TTM) 与 10Y 美债之差, 本地无 NDX/SPX 的 TTM "
          "PE 月度库, 故分位走人工复核; forward PE 会系统性抬高 ERP, 不能顶替。")
    A("")

    # ---- 六、最近触发
    A("## 七、最近触发记录(§10 日志格式)")
    A("")
    log = res["trades"][-14:]
    if log:
        A("| 日期 | 资产 | 触发规则 | 动作 | 份数/金额 | 当时关键数值 |")
        A("|---|---|---|---|---:|---|")
        for t in log:
            units = t.get("units")
            size = (f"{units:.2f}份 / " if units else "") + f"{t['amount_pp']:.2f}pp"
            A(f"| {t['date']:%Y-%m-%d} | {rule.LEGS[t['leg']]['name']} | {t['rule']} | "
              f"{'买入' if t['kind'] == 'buy' else '卖出'} | {size} | {t['detail']} |")
    else:
        A("复算区间内无成交。")
    A("")

    # ---- 七、硬过滤
    A("## 八、§8 硬过滤核对(一票否决)")
    A("")
    A("| 标的 | 判据 | 实测 | 结论 |")
    A("|---|---|---|---|")
    for name, crit, val, resu in rule.hard_filter_rows():
        A(f"| {name} | {crit} | {val} | {resu} |")
    A("")
    A("> 溢价 / 日均成交 / 跟踪误差三项需盘前在交易软件核对(本表只带规模); "
      "场内 |溢价| > 2% 一律不买, 长持走 A 类。场外代码见 `daily/_universe_vol_legs_active.md`。")
    A("")
    A("> 免责声明: 机械规则输出仅供研究参考, 不构成投资建议; 市场有风险, 投资需谨慎。")
    return "\n".join(L)


def active_md():
    L = ["# 波动资产操作规则 · 固定 6 腿名单", "",
         f"> 生成 {datetime.now():%Y-%m-%d} · 由《波动资产操作规则 v1.3》§0 直接定档"
         "(非回测 select, 不做 A/B 分级)。", "",
         "| # | 腿 | 目标带 | 信号标的(场内) | 执行代码(场外) | 执行标的 |",
         "|---|---|---:|---|---|---|"]
    for i, key in enumerate(rule.LEG_ORDER, 1):
        x = rule.LEGS[key]
        L.append(f"| {i} | {x['name']}({x['role']}) | {x['band']:g}pp | "
                 f"`{x['signal']}` {x['signal_name']} | `{x['exec']}` | {x['exec_name']} |")
    core = sum(rule.LEGS[k]["band"] for k in ("a500", "ndx", "spx"))
    atk = sum(rule.LEGS[k]["band"] for k in ("kc50", "hkt"))
    L += ["", f"> 管辖额度合计 **{rule.SLEEVE_PP:.0f}pp**(核心腿 {core:g} = A股 "
          f"{rule.LEGS['a500']['band']:g} + 美股 {rule.LEGS['ndx']['band'] + rule.LEGS['spx']['band']:g}; "
          f"进攻腿 {atk:g} = 科创50 {rule.LEGS['kc50']['band']:g} + 恒生科技 "
          f"{rule.LEGS['hkt']['band']:g}; 黄金 {rule.LEGS['gold']['band']:g}); "
          "其余 60pp(红利 20 / 国债 30 / 货币基金 10)"
          "由《红利池操作规则》与静态配置管理, 不在本策略内。",
          "> 场外代码来自 `data/_universe.md`; A 类/C 类与费率请在申购前于销售平台核对。",
          "> 对应 JSON: `strategies/vol_legs/daily/_universe_vol_legs_active.json`"]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="先刷新场外净值(data/_otc_nav.json)与行情库")
    ap.add_argument("--capital", type=float, default=None, help="总资产(元), 用于折算金额")
    ap.add_argument("--since", default=None,
                    help="复算起点(YYYY-MM-DD), 默认带内起步时为六腿就绪日")
    ap.add_argument("--hold", default="",
                    help="存量持仓覆盖, 如 a500=5,ndx=10,spx=10,kc50=2.5,hkt=2.5,gold=10")
    ap.add_argument("--from-zero", action="store_true",
                    help="空仓起步对照口径(默认从目标带起步, 因为实际持仓就是基准比例)")
    args = ap.parse_args()

    if args.force:
        try:
            sys.path.insert(0, os.path.join(_ROOT, "data"))
            import _refresh_data  # noqa: E402
            _refresh_data.ensure_fresh()
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] A 股行情刷新失败, 沿用本地库: {type(exc).__name__}: {exc}")
        try:
            latest, fails = data.refresh_us()
            if fails:
                print(f"[warn] 美股指数刷新部分失败(用缓存): {fails}")
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] 美股指数刷新失败, 沿用缓存: {type(exc).__name__}: {exc}")
        try:
            latest, fails = data.refresh_otc()
            if fails:
                print(f"[warn] 场外净值刷新部分失败(用缓存): {fails}")
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] 场外净值刷新失败, 沿用缓存: {type(exc).__name__}: {exc}")

    ind = data.indicators_all()
    gates, fomc = data.gates(ind["gold"].index)
    hold = parse_hold(args.hold)
    # 起点口径: 默认 = 目标带(实盘就是按基准比例持有), --hold 覆盖, --from-zero 切回空仓
    init = {} if args.from_zero else (hold or dict(BAND_SEED))
    since = args.since
    if init and not since:
        # 注入的是**起点**仓位; 若从 2009 年回放, 十几年涨幅会把仓位放大到离谱。
        # 未显式给 --since 时退到「六腿就绪日」, 让起点到今天的漂移有界、读数可解释。
        since = _ready_start(ind)
    res = engine.run(ind, gates, fomc, since=since, init=init)
    engine_nav = float(res["nav"].iloc[-1])

    legs = {k: leg_view(k, ind, res, res["pending"], gates) for k in rule.LEG_ORDER}
    last = res["cal"][-1]
    next_q = _next_quarter_end(last)
    base = "zero" if args.from_zero else ("hold" if hold else "band")
    theory = theory_table(res, legs, gates)
    ctx = dict(legs=legs, res=res, gates=gates, capital=args.capital,
               since=res["cal"][0], next_qend=next_q, hold=bool(hold),
               base=base, theory=theory, init=init)

    os.makedirs(_DAILY, exist_ok=True)
    with open(_OUT_MD, "w", encoding="utf-8") as f:
        f.write(render(ctx) + "\n")

    log = [dict(date=f"{t['date']:%Y-%m-%d}", leg=t["leg"], kind=t["kind"],
                amount_pp=round(t["amount_pp"], 3), rule=t["rule"], detail=t["detail"])
           for t in res["trades"][-40:]]
    snap = dict(
        strategy="波动资产操作规则 v1.3",
        as_of=max(x["as_of"] for x in legs.values()),
        generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
        since=f"{res['cal'][0]:%Y-%m-%d}",
        capital=args.capital,
        base=base,
        sleeve_pp=rule.SLEEVE_PP,
        unit_pp=rule.UNIT,
        theory=theory,
        invested_pp=round(sum(x["pos_pp"] for x in legs.values()), 2),
        cash_pp=round(res["cash"], 2),
        nav_pp=round(engine_nav, 2),
        legs=legs,
        actions=[dict(leg=k, name=v["name"], kind=v["action"], label=v["action_label"],
                      rule=v["rule"], detail=v["detail"], delta_pp=v.get("delta_pp"))
                 for k, v in legs.items() if v["action"] in ("buy", "sell")],
        ladder={k: dict(
            unit_pp=rule.unit_pp(k), band=rule.LEGS[k]["band"],
            units=round(res["policies"][k].units, 3),
            built_pp=round(res["policies"][k].built_pp(), 2),
            tiers_done=res["policies"][k].tiers_done,
            armed_tier=getattr(res["policies"][k], "armed_tier", None),
            month_tranche=getattr(res["policies"][k], "month_tranche", None),
            degraded=getattr(res["policies"][k], "degraded", None))
            for k in ("a500", "ndx", "spx", "gold")},
        rebalance=dict(next_check=f"{next_q:%Y-%m-%d}", band_pp=rule.BAND_PP,
                       band_frac=rule.BAND_FRAC,
                       tol_pp={k: round(rule.rebalance_pp(k), 2) for k in rule.LEG_ORDER},
                       buy_back=rule.REBAL_BUY_BACK,
                       target={k: round(res["policies"][k].band_target(), 2)
                               for k in rule.LEG_ORDER},
                       dev={k: round(legs[k]["pos_pp"] - res["policies"][k].band_target(), 2)
                            for k in rule.LEG_ORDER}),
        valuation=dict(a500_erp=round(gates["_erp"]["erp"], 2) if gates["_erp"]["erp"] is not None else None,
                       a500_erp_pct=gates["a500_erp_pct"],
                       a500_erp_months=gates["_erp"]["n_months"],
                       a500_erp_gate=gates["a500_erp"],
                       ndx_erp_pct=gates["ndx_erp_pct"], ndx_erp_gate=gates["ndx_erp"],
                       spx_erp_pct=gates["spx_erp_pct"], spx_erp_gate=gates["spx_erp"],
                       qdii_limit_closed=gates.get("qdii_limit_closed") or [],
                       kc50_pb_pct=gates["kc50_pb_pct"],
                       hkt_erp_pct=gates["hkt_erp_pct"],
                       tips_fuse=gates["tips_fuse"], wgrc_fuse=gates["wgrc_fuse"],
                       fomc_days=len(gates["_inputs"]["fomc_days"]),
                       us_erp=gates.get("_us_erp")),
        sources={k: data.SOURCE_NOTE.get(k) for k in rule.LEG_ORDER
                 if data.SOURCE_NOTE.get(k)},
        log=log,
    )
    with open(_OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2, default=str)

    with open(_ACT_MD, "w", encoding="utf-8") as f:
        f.write(active_md() + "\n")
    with open(_ACT_JSON, "w", encoding="utf-8") as f:
        json.dump(dict(strategy="波动资产操作规则 v1.3", as_of=snap["as_of"],
                       sleeve_pp=rule.SLEEVE_PP,
                       legs=[dict(key=k, name=rule.LEGS[k]["name"], role=rule.LEGS[k]["role"],
                                  band=rule.LEGS[k]["band"], signal=rule.LEGS[k]["signal"],
                                  exec=rule.LEGS[k]["exec"],
                                  exec_name=rule.LEGS[k]["exec_name"])
                             for k in rule.LEG_ORDER]),
                  f, ensure_ascii=False, indent=2)

    print(f"[ok] {_OUT_MD}")
    for k in rule.LEG_ORDER:
        x = legs[k]
        print(f"  {x['name']:6s} {_px(x['close']):>10s}  MA250 {_pct(x['dev']):>7s}  "
              f"dd {_pct(-x['dd']) if x['dd'] is not None else '—':>7s}  "
              f"RSI {_f(x['rsi'], 1):>5s}  {x['zone']:4s}  {x['pos_pp']:5.1f}pp  "
              f"→ {x['action_label']}")


def _next_quarter_end(d):
    """下一个季度末(3/6/9/12 月)的日历日。"""
    d = d.date() if hasattr(d, "date") else d
    y, m = d.year, d.month
    for _ in range(6):
        if m in (3, 6, 9, 12) and _month_end(y, m) >= d:
            return _month_end(y, m)
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return _month_end(y, m)


def _month_end(y, m):
    import calendar
    import datetime as dt
    return dt.date(y, m, calendar.monthrange(y, m)[1])


if __name__ == "__main__":
    main()
