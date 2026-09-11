# -*- coding: utf-8 -*-
"""亮灯策略 · 每日信号扫描（逐标的，实盘口径，与回测同一套 rule.py）。

产出**每个标的的完整指标明细**（门槛层逐条实际值 vs 阈值 + 各维灯得分与触发依据），
外加 score 与 enter_min / exit_max 的关系、目标仓位、建议动作，供 `_scan_report.py` 渲染
每日操作报告。

与回测的差别只有数据来源（扫描用当日**盘中未收盘 bar**，规则完全一致）:
  - 门槛/灯/评分/决策全部调用 rule.py 的同一套函数;
  - 额外并列本地 mx 快照的**真实**主力净额占比 / 换手率 / 量比供交叉验证（不参与计分）。

输出: data/_lights_scan_out.jsonl（每标的一行）
      data/_lights_state.json（调仓日历状态, --commit 推进）
用法:
  python strategies/lights/scan.py                  # 全池扫描（联网刷新当日数据）
  python strategies/lights/scan.py --cache          # 用本地缓存（不联网）
  python strategies/lights/scan.py --commit         # 扫描并把今天记为最近一次调仓
  python strategies/lights/scan.py 512800,515080    # 只扫指定标的
"""
import json
import os
import re
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_DIR))
for p in (_ROOT, os.path.dirname(_DIR), _DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import rule  # noqa: E402

MX = "G:/tradingagents/fund_data/data/mx_snapshot_latest.json"
OUT = os.path.join(_ROOT, "data", "_lights_scan_out.jsonl")
STATE = os.path.join(_ROOT, "data", "_lights_state.json")
ACT_JSON = os.path.join(_ROOT, "data", "_lights_active.json")

# 门槛展示文案: name -> (短标签, 阈值模板)
GATE_LABELS = {
    "liq": ("流动性", "近20日日均成交额"),
    "age": ("成立年限", "有效交易日"),
    "ma_trend": ("中期趋势", "收盘 vs MA20"),
    "ret3_max": ("短期暴涨", "3日涨幅 ≤ {v:.0%}"),
    "ret5_max": ("追高否决", "5日涨幅 ≤ {v:.0%}"),
    "cap_abs_max": ("资金衰竭", "|3日主力强度| ≤ {v:g}"),
    "entry_ma10": ("短线确认", "收盘 vs MA10"),
    "indicators_ready": ("指标就绪", "全部指标非空"),
}


def _money(s):
    """'2677万元' / '-4.743亿元' -> float(元)；失败 None。"""
    if s is None:
        return None
    t = str(s).strip().replace(",", "")
    if t in ("", "-", "--", "None", "nan"):
        return None
    m = re.match(r"[-+]?[\d.]+", t)
    if not m:
        return None
    v = float(m.group())
    if "亿" in t:
        v *= 1e8
    elif "万" in t:
        v *= 1e4
    return v


def _pct(s):
    try:
        return float(str(s).strip().replace("%", ""))
    except (TypeError, ValueError):
        return None


def load_snapshot():
    """mx 快照 -> ({code: {turn, vol_ratio, amount, main_net, main_pct}}, 快照时间)。"""
    if not os.path.exists(MX):
        return {}, "无快照"
    try:
        d = json.load(open(MX, encoding="utf-8"))
    except json.JSONDecodeError:
        return {}, "快照解析失败"
    out = {}
    for code, v in (d.get("funds") or {}).items():
        amt, net = _money(v.get("amount")), _money(v.get("mainflow"))
        out[code] = dict(name=v.get("name"), turn=_pct(v.get("turnover")),
                         vol_ratio=v.get("vol_ratio"), amount=amt, main_net=net,
                         main_pct=(net / amt * 100.0) if (amt and net is not None) else None)
    return out, str(d.get("snapshot_time", "?"))[:16]


def load_grades():
    """select 阶段产物 -> ({code: 'A'|'B'|'不适合'}, 生成时间)。"""
    if not os.path.exists(ACT_JSON):
        return {}, None
    try:
        d = json.load(open(ACT_JSON, encoding="utf-8"))
    except json.JSONDecodeError:
        return {}, None
    g = {}
    for tag, key in (("A", "A"), ("B", "B"), ("不适合", "not_suitable")):
        for r in d.get(key) or []:
            if r.get("code"):
                g[r["code"]] = tag
    return g, d.get("generated_at")


def state_load():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def _num(f, key, i, fmt="{:.3f}", scale=1.0):
    v = f[key].iloc[i]
    if pd.isna(v):
        return "—"
    return fmt.format(float(v) * scale)


def gate_detail(name, f, i, cfg):
    """单条门槛 -> (短标签, 阈值说明, 实际值, 是否通过, 判定文案)。"""
    lab, desc = GATE_LABELS.get(name, (name, name))
    if name == "liq":
        v = f["amt20"].iloc[i]
        thr, act = f"≥ {cfg.liq_amt_min / 1e8:.2f}亿", (f"{v / 1e8:.2f}亿" if pd.notna(v) else "—")
        ok = bool(pd.notna(v) and v >= cfg.liq_amt_min)
    elif name == "age":
        v = int(f["bars"].iloc[i])
        thr, act, ok = f"≥ {cfg.list_min_bars}日", f"{v}日", v >= cfg.list_min_bars
    elif name == "ma_trend":
        c, m = f["c"].iloc[i], f["ma20"].iloc[i]
        thr = "收盘 > MA20"
        act = f"{c:.3f} vs {m:.3f}" if pd.notna(m) else "—"
        ok = bool(pd.notna(m) and c > m)
    elif name == "ret3_max":
        v = f["ret3"].iloc[i]
        thr, act = desc.format(v=cfg.ret3_max), (f"{v:+.2%}" if pd.notna(v) else "—")
        ok = bool(pd.notna(v) and v <= cfg.ret3_max)
    elif name == "ret5_max":
        v = f["ret5"].iloc[i]
        thr, act = desc.format(v=cfg.ret5_max), (f"{v:+.2%}" if pd.notna(v) else "—")
        ok = bool(pd.notna(v) and v <= cfg.ret5_max)
    elif name == "cap_abs_max":
        v = f["cap3"].iloc[i]
        thr, act = desc.format(v=cfg.cap_abs_max), (f"{v:+.2f}" if pd.notna(v) else "—")
        ok = bool(pd.notna(v) and abs(v) <= cfg.cap_abs_max)
    elif name == "entry_ma10":
        c, m = f["c"].iloc[i], f["ma10"].iloc[i]
        thr = "收盘 > MA10"
        act = f"{c:.3f} vs {m:.3f}" if pd.notna(m) else "—"
        ok = bool(pd.notna(m) and c > m)
    elif name == "indicators_ready":
        miss = [k for k in cfg.ready_fields if pd.isna(f[k].iloc[i])]
        thr, act, ok = "全部指标非空", ("就绪" if not miss else "缺 " + ",".join(miss)), not miss
    else:
        thr, act, ok = desc, "—", True
    return lab, thr, act, ok


def light_detail(key, defname, f, i):
    """单盏灯 -> (得分, 触发依据文案)。"""
    r = lambda k: f[k].iloc[i]                                   # noqa: E731
    n = lambda k: (None if pd.isna(f[k].iloc[i]) else float(f[k].iloc[i]))  # noqa: E731
    desc = ""
    if defname == "ma_short_adx":
        ma5, ma10, ma20, adx = n("ma5"), n("ma10"), n("ma20"), n("adx")
        desc = (f"MA5{'>' if (ma5 or 0) > (ma10 or 0) else '≤'}MA10"
                f"{'>' if (ma10 or 0) > (ma20 or 0) else '≤'}MA20"
                f" · ADX {('%.1f' % adx) if adx is not None else '—'}"
                f" · 收{'>' if (n('c') or 0) > (ma20 or 0) else '≤'}MA20")
    elif defname == "ma_long_mom":
        c60 = f["c"].shift(60).iloc[i]
        mom60 = (("%+.1f%%" % ((n("c") / c60 - 1) * 100))
                 if (pd.notna(c60) and c60) else "—")
        desc = (f"MA20{'>' if (n('ma20') or 0) > (n('ma60') or 0) else '≤'}MA60"
                f" · 收{'>' if (n('c') or 0) > (n('ma60') or 0) else '≤'}MA60"
                f" · 60日动量 {mom60}")
    elif defname == "ret5":
        desc = f"ret5 {_num(f, 'ret5', i, '{:+.2%}')} · ret3 {_num(f, 'ret3', i, '{:+.2%}')}"
    elif defname == "ret3_band":
        desc = f"ret3 {_num(f, 'ret3', i, '{:+.2%}')}"
    elif defname in ("vr5_ge07", "vr5_any"):
        desc = (f"量比 {_num(f, 'vr5', i, '{:.2f}')} · "
                f"{'收阳' if bool(r('yang')) else '收阴'}")
    elif defname == "cap3":
        desc = f"3日主力强度 {_num(f, 'cap3', i, '{:+.2f}')}"
    elif defname in ("macd_weekly", "macd", "macd_binary"):
        dif, dea = n("dif"), n("dea")
        wkb = "" if defname == "macd" else f" · 周线{'多' if bool(r('wk_bull')) else '空'}"
        desc = (f"DIF{'>' if (dif or 0) > (dea or 0) else '≤'}DEA"
                f" · DIF {('%+.3f' % dif) if dif is not None else '—'}"
                f" · ret3 {_num(f, 'ret3', i, '{:+.1%}')}{wkb}")
    elif defname == "ret5_vr5":
        desc = f"ret5 {_num(f, 'ret5', i, '{:+.1%}')} · 量比 {_num(f, 'vr5', i, '{:.2f}')}"
    elif defname == "vr60_band":
        desc = (f"vr60 {_num(f, 'vr60', i, '{:.2f}')} · "
                f"{'收阳' if bool(r('yang')) else '收阴'}")
    elif defname == "ret5_band":
        desc = f"ret5 {_num(f, 'ret5', i, '{:+.1%}')}"
    elif defname == "turn_pct":
        lo, hi = f["band"]
        desc = f"换手分位 {_num(f, 'turn_pct', i, '{:.0%}')}（带 {lo:.0%}~{hi:.0%}）"
    elif defname == "div":
        desc = f"分化度 {_num(f, 'div', i, '{:.2f}')}"
    return desc


def explain(sig, cfg, i, light_vals):
    """第 i 日 -> (目标仓位, 决策, 原因)。"""
    if not bool(sig["gate_pass"][i]):
        return 0.0, "清仓", "门槛未过"
    if not bool(sig["enter"][i]):
        sc, need = float(sig["score"][i]), cfg.enter_min
        miss = [k for k, v in (cfg.enter_required or {}).items() if light_vals[k][i] < v]
        if miss:
            names = "、".join(rule.LIGHT_NAMES.get(k, k) for k in miss)
            return 0.0, "清仓", f"灯未达标（{names}）"
        if sc < need:
            return 0.0, "清仓", f"得分 {sc:.1f} < 门槛 {need:.1f}"
        return 0.0, "清仓", "未满足建仓条件"
    return float(sig["weight"][i]), "建仓/持有", "门槛全过 且 得分/灯达标"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    commit = "--commit" in sys.argv
    refresh = "--cache" not in sys.argv
    want = set(args[0].split(",")) if args and args[0].strip() else None
    cfg = rule.ACTIVE

    panels = rule._data.load_panels(refresh=refresh, ffill=False)
    rule._panels_raw = panels                      # 复用同一份面板
    snap, snap_time = load_snapshot()
    grades, grade_at = load_grades()

    rows = []
    with open(OUT, "w", encoding="utf-8") as fh:
        for meta in rule.POOL:
            code = meta["code"]
            if want and code not in want:
                continue
            base = dict(idx=meta["idx"], cat=meta["cat"], theme=meta["theme"],
                        off_name=meta["off_name"], inner=code,
                        inner_name=meta["inner_name"], snap=snap_time)
            try:
                df = rule.trading_frame(panels, code)
                if len(df) < 60:
                    raise ValueError(f"样本不足({len(df)}日)")
                f = rule.compute_factors(df, cfg, code)
                sig = rule.build_signals(f, cfg)
                i = len(df) - 1
                t = df.index[i]
                gates = []
                for gname in cfg.gates:
                    lab, thr, act, ok = gate_detail(gname, f, i, cfg)
                    gates.append((gname, lab, thr, act, bool(ok)))
                lights = []
                for key in rule.LIGHT_KEYS:
                    spec = cfg.lights.get(key)
                    if not spec:
                        continue
                    defname = spec[0]
                    sc = float(sig["light_vals"][key][i])
                    lights.append((key, rule.LIGHT_NAMES[key], defname, sc,
                                   light_detail(key, defname, f, i)))
                target, dec, why = explain(sig, cfg, i, sig["light_vals"])
                real = snap.get(code, {})
                r = dict(ok=True, **base, date=str(t.date()),
                         close=round(float(f["c"].iloc[i]), 3),
                         gate_pass=bool(sig["gate_pass"][i]),
                         failed=[g[0] for g in gates if not g[4]],
                         gates=gates, lights=lights,
                         score=round(float(sig["score"][i]), 2),
                         enter_min=cfg.enter_min, exit_max=cfg.exit_max,
                         n_light=len(lights), target=round(target, 3),
                         dec=dec, why=why, budget=float(sig["weight"][i]),
                         grade=grades.get(code, "—"), grade_at=grade_at,
                         values={k: (None if pd.isna(f[k].iloc[i]) else round(float(f[k].iloc[i]), 4))
                                 for k in ("ret3", "ret5", "ret20", "cap3", "turn_pct",
                                           "div", "vr5", "vr60", "adx", "ma20", "ma60")},
                         real_main_pct=(round(real["main_pct"], 2)
                                        if real.get("main_pct") is not None else None),
                         real_turn=real.get("turn"), real_vol_ratio=real.get("vol_ratio"),
                         has_real=real.get("main_pct") is not None)
            except Exception as e:  # noqa: BLE001 - 单标的失败不影响其他
                r = dict(ok=False, **base, err=f"{type(e).__name__}: {e}")
            rows.append(r)
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    good = [r for r in rows if r.get("ok")]
    if commit and good:
        st = state_load()
        st["last_rebal"] = good[0]["date"]
        st["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    buys = [r for r in good if r["target"] > 0]
    print(f"[亮灯策略] 扫描 {len(good)} 只 | 配置 {cfg.label}"
          f" | 数据日 {good[0]['date'] if good else '—'}"
          f"{'（盘中未收盘 bar）' if refresh else '（本地缓存）'} | 快照 {snap_time}")
    print(f"  门槛全过 {sum(1 for r in good if r['gate_pass'])} 只 | 目标建仓 {len(buys)} 只："
          + (" ".join(f"{r['inner']}({r['grade']},分{r['score']:g})" for r in buys) or "（空仓）"))
    print(f"  分级来源: {os.path.basename(ACT_JSON)} @ {grade_at or '未生成(先跑 select)'}")
    print(f"  明细 -> {OUT}")


if __name__ == "__main__":
    main()
