# -*- coding: utf-8 -*-
"""近五年窗口下：在「近五年当前最优」之上，还能不能往前推一步？——原生引擎 · 同尺版。

与 `research/beat_best/_beat_best_top3.py`（全历史版）的关系：

| | 基线 | 评估窗口 | 后段 |
|---|---|---|---|
| `beat_best/_beat_best_top3.py` | `_best_by_leg.md` 全历史前三 | 全历史 | 全历史后 40% |
| **本文件** | **`best_by_leg_5y/_best_by_leg_5y.md` 近五年前三** | **近五年窗口** | **窗口内后 40%** |

候选构造（E2/E3/MIN2/MAX2/ROT/E2×TV/SW）、同尺成本（统一单边 10bp + 空仓 1.40%）、
双闸门（全窗夏普 ≥ ★1+0.02 且后段 ≥ ★1后段+0.02）全部复用旧脚本的纯函数，
**不重写规则、不做网格搜索**。

## ⚠ 这个研究的可信度低于全历史版，必须先看清

1. **同一段数据被用了两次**：★1 基线是在这 5 年里排出来的，超越项也在这 5 年里挑，
   等于「用答案找答案」。全历史版至少基线来自 20 年、验证在 20 年。
   **本文件的定位是「窗口敏感性检验」**：看同一批组合技法在换成 5 年窗口后，
   还能不能复现全历史版的结论 —— 能复现的才是真的，只在某一窗口成立的才是拟合。
2. **后段闸门只能防「近期失效」**：5 年窗口的后 40% ≈ 2 年，本身仍在窗口内，
   挡不住「整段都过拟合」。看结论时把「后段」当稳健性参考，不要当样本外。
3. **基线会换人**：5 年窗口含 2022 年熊市（A 股与美股同步大跌、港股续跌），
   所以沪深300 / 黄金的 ★1 与全历史版不同。基线一换，「能不能超越」的答案也跟着换。

产出：`_beat_best_5y.md`（本目录）。
用法：`python strategies/vol_legs/research/beat_best_5y/_beat_best_5y.py`
      （先跑 `best_by_leg_5y/_best_by_leg_5y.py`，本脚本读它的 jsonl 当基线）

> 模拟结果，非投资建议。
"""
import contextlib
import datetime
import importlib
import importlib.util
import io
import json
import math
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../research/beat_best_5y
_RES = os.path.dirname(_HERE)                               # .../research
_VL = os.path.dirname(_RES)                                 # strategies/vol_legs
_ROOT = os.path.dirname(os.path.dirname(_VL))               # 仓库根
if _VL not in sys.path:
    sys.path.insert(0, _VL)

OUT = os.path.join(_HERE, "_beat_best_5y.md")
OUT_JSONL = os.path.join(_HERE, "_beat_best_5y.jsonl")
SRC_BB = os.path.join(_RES, "beat_best", "_beat_best_top3.py")
SRC_5Y = os.path.join(_RES, "best_by_leg_5y", "_best_by_leg_5y.jsonl")

WINDOW_YEARS = 5
CUT = 0.60                 # 窗口内前 60% / 后 40%
GATE = 0.02                # 全窗夏普门槛
POST_GATE = 0.02           # 后段夏普门槛
WARMUP_NOTE = "各引擎自己的预热（300 根量级）全部在窗口之前完成"

LEGS = ["a500", "hs300", "ndx", "spx", "kc50", "hkt", "gold"]
NAMES = {"a500": "中证A500", "hs300": "沪深300", "ndx": "纳指100", "spx": "标普500",
         "kc50": "科创50", "hkt": "恒生科技", "gold": "黄金"}
# 5y 榜的 vx -> 本脚本的引擎 key（v6 拆三口径；BH = 恒 100% 仓位）
KEY_OF = {"v1": "v1", "v2": "v2", "v3": "v3", "v5": "v5",
          "v6A": "v6a", "v6B": "v6b", "v6C": "v6c",
          "v7": "v7", "v8": "v8", "v9": "v9", "v10": "v10",
          "v12": "v12", "v13": "v13", "BH": "bh"}
_LABEL = {"v1": "V1 MA20", "v2": "V2 BOLL", "v3": "V3 MACD+BOLL", "v5": "V5.1 黄金仓位",
          "v6a": "V6-A 长期配置", "v6b": "V6-B 控回撤", "v6c": "V6-C 占坑",
          "v7": "V7 年线闸门", "v8": "V8 金叉×波动层", "v9": "V9 波动目标",
          "v10": "V10 波动层", "v12": "V12 八态趋势", "v13": "V13 九宫格",
          "bh": "买入持有"}

# v14~v21 自带 engine.py / data.py，会污染 `import engine` / `import data` 的解析
_SHADOW = {os.path.join(_VL, d) for d in (
    "v14_a500_e2", "v15_hs300_min2", "v16_ndx_sw", "v17_spx_v12",
    "v18_kc50_e2tv", "v19_hkt_v3", "v20_gold_v5", "v21_livermore_keypoint")}


# ---------------------------------------------------------------- 盘中半成品 bar
# 各市场的收盘时刻（含 5 分钟缓冲）：A 股/场内黄金 15:00，港股 16:00。
# 美股（ndx/spx）的 bar 日期是美东交易日，北京白天时它不会等于「今天」，走默认分支即可。
_CLOSE_AT = {"a500": (15, 5), "hs300": (15, 5), "kc50": (15, 5), "gold": (15, 5),
             "hkt": (16, 15)}


def closed_series(px, leg=None):
    """去掉「今天且未收盘」的最后一根 bar。

    `data.series_for_leg` 在盘中会带出当天的半成品 K 线：用它算「今日决策」会得到
    一个随盘中价乱跳的仓位（2026-11-23 14:4x 实测：黄金 V12 的当日档位在 0 与 0.40
    之间来回切）。**信号与仓位一律只用已收盘的 bar**，所以按 leg 的收盘时刻丢最后一根。
    """
    if len(px) == 0:
        return px
    last = px.index[-1]
    if getattr(last, "date", lambda: None)() != datetime.date.today():
        return px
    now = datetime.datetime.now()
    hh, mm = _CLOSE_AT.get(leg or "", (15, 5))
    if (now.hour, now.minute) < (hh, mm):
        print(f"  [未收盘] {leg or '—'} 丢掉 {last.date()} 的半成品 bar（现在 {now:%H:%M}）")
        return px.iloc[:-1]
    return px


# ---------------------------------------------------------------- 加载旧研究的纯函数
def _load_bb():
    """按路径加载 `_beat_best_top3.py`（只取纯函数与 Engines，不跑它的 main）。"""
    spec = importlib.util.spec_from_file_location("_beat_best_top3", SRC_BB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@contextlib.contextmanager
def isolated(sub):
    """隔离 sys.path / sys.modules（仓库里 5 个 data.py、多个 rule.py/engine.py）。"""
    d = os.path.join(_VL, sub.replace("/", os.sep))
    names = ("data", "rule", "engine", "ma20", "boll", "macd_boll", "factors", "score",
             "gold", "us_gate", "golden_cross", "vol_target", "vol_layer")
    old_path = sys.path[:]
    saved = {n: sys.modules.pop(n) for n in names if n in sys.modules}
    shadow = {os.path.abspath(p) for p in _SHADOW}
    sys.path[:] = [p for p in sys.path
                   if os.path.abspath(p) not in shadow
                   and os.path.abspath(p) != os.path.abspath(d)]
    sys.path.insert(0, d)
    if _VL not in sys.path:
        sys.path.append(_VL)
    try:
        yield
    finally:
        sys.path[:] = old_path
        for n in names:
            sys.modules.pop(n, None)
        sys.modules.update(saved)


# ---------------------------------------------------------------- 基线：近五年前三
def cand_kind(label):
    """把候选标签归到「技法类别」（E2 / E3 / MIN2 / MAX2 / ROT / SW / E2×TV），
    用于跨窗口比较「是不是同一个技法」，不受标签后缀（→V12 之类）影响。"""
    s = str(label).strip().lstrip("*")
    for k in ("E2×TV", "E3", "E2", "MIN2", "MAX2", "ROT", "SW"):
        if s.startswith(k):
            return k
    return s


def read_full_wins():
    """从全历史版 `_beat_best_top3.md` 的「## 汇总」表里读各腿的超越项（读不到就当无）。"""
    path = os.path.join(_RES, "beat_best", "_beat_best_top3.md")
    out = {}
    if not os.path.exists(path):
        return out
    inside = False
    with io.open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s.startswith("## 汇总"):
                inside = True
                continue
            if inside and s.startswith("## "):
                break
            if not inside or not s.startswith("|"):
                continue
            parts = [p.strip() for p in s.strip("|").split("|")]
            if len(parts) != 5 or parts[0] not in NAMES.values():
                continue
            if set(parts[2]) <= set("-: "):
                continue
            win = parts[2].replace("**", "").split("（另")[0].strip()
            if win and win not in ("无", "超越项"):
                out[parts[0]] = win
    return out


def read_top3():
    """从 `_best_by_leg_5y.jsonl` 读每腿的前三名（rank 1~3）。"""
    if not os.path.exists(SRC_5Y):
        raise SystemExit(f"缺近五年榜产物：{SRC_5Y}\n先跑 "
                         "`python strategies/vol_legs/research/best_by_leg_5y/_best_by_leg_5y.py`")
    ranks = {}
    with io.open(SRC_5Y, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            rk = r.get("rank")
            if rk in (1, 2, 3):
                ranks.setdefault(r["leg"], {})[rk] = r
    top3, meta = {}, {}
    snap = None
    for leg in LEGS:
        got = ranks.get(leg) or {}
        rows = [got[k] for k in (1, 2, 3) if k in got]
        if len(rows) < 3:
            print(f"  [WARN] {NAMES[leg]} 近五年前三不足 3 条（{len(rows)}），跳过")
            continue
        top3[leg] = [(KEY_OF[r["vx"]], r["vx"], (r.get("label") or _LABEL[KEY_OF[r["vx"]]]))
                     for r in rows]
        meta[leg] = [dict(vx=r["vx"], sharpe=r.get("sharpe"), ann=r.get("ann"),
                          mdd=r.get("mdd"), src_start=r.get("start"), src_end=r.get("end"))
                     for r in rows]
        # 基线快照日：评估窗口必须与排名所用窗口一致，否则自检会差出一天。
        # `_best_by_leg_3y.py` 每行带 `start`/`end`（该腿窗口的起止），取各腿 end 的最大值。
        e = rows[0].get("end")
        if e:
            ts = pd.Timestamp(e)
            snap = max([snap, ts]) if snap is not None else ts
    return top3, meta, snap


# ---------------------------------------------------------------- hold 桥
class Holds:
    """复用 `Engines` 覆盖 9 个变种；v6a/v6c/v7/v8/bh 在这里补齐。"""

    def __init__(self, bb):
        self.bb = bb
        self.eng = bb.Engines()
        self.extra = {}
        for key, sub in (("v7", "v7_us_gate"), ("v8", "v8_golden_cross")):
            with isolated(sub):
                mod = importlib.import_module("us_gate" if key == "v7" else "golden_cross")
            self.extra[key] = (mod, sub)

    def get(self, key, leg, px, lag=True):
        """`lag=True`（默认）→ 「t 日收益对应的仓位」（= 决策 shift 1 天）；
        `lag=False` → 「t 日**收盘决策**」（T+1 起生效，供 `_daily_positions_5y.py` 取当日目标仓位）。"""
        if key == "bh":
            return pd.Series(1.0, index=px.index)
        h = self._raw(key, leg, px)
        return h.shift(1).fillna(0.0).clip(0.0, 1.0) if lag else h

    def _raw(self, key, leg, px):
        """各引擎 `hold()` 里 `.shift(1)` 之前的那根 = 该日收盘做出的仓位决策。"""
        if key in ("v6a", "v6c"):
            m, sub = self.eng.mods["v6b"], "v6_gold"
            with isolated(sub):
                h = m.state_a(px)["state"] if key == "v6a" else m.state_c(px)
        elif key in ("v7", "v8"):
            m, sub = self.extra[key]
            with isolated(sub):
                h = m.build(leg)[1]          # state[t] = t 收盘成交后仓位
        else:
            m = self.eng.mods[key]
            with self.eng._ctx(key):
                if key == "v1":
                    h = pd.Series(m.signal(px)[0], index=px.index)
                elif key == "v2":
                    h = m.weight(px, m.signal(px)[0])
                elif key == "v3":
                    h = m.weight(px, m.signal(px))
                elif key == "v5":
                    F = importlib.import_module("factors")
                    # 用调用方的 px（已截掉盘中 bar），别再去 data 里取一份原始序列
                    df5 = m.run(pd.DataFrame({"close": px}), F.read_rate())
                    h = df5["state"]
                elif key == "v6b":
                    h = m.state_b(px)["state"]
                elif key == "v9":
                    # machine() 的 s[t] = t 收盘已持有仓 = 决策[t-1]（月末才变）
                    h = m.machine(px, m.leg_params(leg), leg=leg)[0]
                elif key == "v10":
                    # state_daily[t] = target_raw[t-1]（T+2 生效）
                    h = m.state_daily(px.index, leg)
                elif key == "v12":
                    h = m.backtest_leg(leg, df=self.eng.dmods[key].frame(leg))["w"]
                elif key == "v13":
                    h = m.state_machine(self.eng.dmods[key].frame(leg), callback_add=True)["w"]
                else:
                    raise KeyError(key)
        # 与 `Engines.hold` 的 fillna 语义对齐：V9/V10 的缺失默认满仓（1.0），其余归零
        h = pd.Series(h).reindex(px.index)
        h = h.ffill().fillna(1.0 if key in ("v9", "v10") else 0.0)
        return h.astype(float).clip(0.0, 1.0)

    def native_fee(self, key, leg):
        """自检用原生费率：BH=0；v7/v8=单边 10bp；v6a/v6c 借 v6b 的费率档。"""
        if key == "bh":
            return 0.0
        if key in ("v7", "v8"):
            return 0.001
        if key in ("v6a", "v6c"):
            return self.bb.native_fee("v6b", leg, self.eng)
        return self.bb.native_fee(key, leg, self.eng)


# ---------------------------------------------------------------- 主流程
def fmt(x, kind="pct"):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    return f"{x * 100:+.2f}%" if kind == "pct" else f"{x:.2f}"


def main():
    bb = _load_bb()
    top3, meta, snap = read_top3()
    holds = Holds(bb)

    vdata = bb.vdata
    rows_5y = []
    with io.open(SRC_5Y, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows_5y.append(json.loads(line))
    pub = {}
    for r in rows_5y:
        pub[(r["leg"], r["vx"])] = r.get("sharpe")

    # 全局窗口：**必须等于基线快照日**（`best_by_leg_5y.jsonl` 里的 window[1]）——
    # 基线排名是在那个窗口上排的，评估窗口再往后延一天，自检和「超越」判定都会错期。
    # 快照之后的新数据不进评估；要看最新仓位另跑 `_daily_positions_5y.py`（它不切历史窗）。
    PX = {leg: closed_series(vdata.series_for_leg(leg).dropna().astype("float64"), leg)
          for leg in LEGS}
    if snap is not None:
        PX = {leg: px.loc[:snap] for leg, px in PX.items()}
        end = snap
        print(f"  评估窗口对齐基线快照：截至 {pd.Timestamp(snap).date()}")
    else:
        ends = [px.index[-1] for px in PX.values() if len(px)]
        end = max(ends)
    w0 = (pd.Timestamp(end) - pd.DateOffset(years=WINDOW_YEARS)).normalize()

    detail, checks, sections, wins = {}, [], [], []
    for leg in LEGS:
        if leg not in top3:
            continue
        px_full = PX[leg]
        before = px_full.index[px_full.index <= w0]
        if len(before) == 0:
            print(f"  [WARN] {NAMES[leg]} 样本不足 {WINDOW_YEARS} 年，跳过")
            continue
        base = before[-1]
        px = px_full.loc[base:]
        # ⚠ 关键：hold 在**全历史**上算（预热与状态机全在窗口之前完成），再切窗
        hs = {}
        for key, _vx, _lab in top3[leg]:
            hh = holds.get(key, leg, px_full)
            hs[key] = hh.reindex(px.index).ffill().fillna(0.0).clip(0.0, 1.0)
        labels = {key: lab for key, _vx, lab in top3[leg]}
        bb.TOP3_LABEL = labels                  # candidates() 用这个全局做标签

        # ---- 自检：同尺引擎 + 原生费率，应复现 5y 榜的夏普
        for key, vx, _lab in top3[leg]:
            m = bb.metrics(hs[key], px, fee=holds.native_fee(key, leg))
            p = pub.get((leg, vx))
            if not m:
                pub_s = f"{p:.2f}" if p is not None else "—"
                checks.append(f"| {NAMES[leg]} | {labels[key]} | — | — | {pub_s} | — | — |")
                continue
            delta = (m["sharpe"] - p) if p is not None else None
            same = bb.metrics(hs[key], px)          # 同尺 10bp，仅用于展示口径代价
            same_s = f"{same['sharpe']:.2f}" if same else "—"
            pub_s = f"{p:.2f}" if p is not None else "—"
            dt_s = ("%+.2f" % delta) if delta is not None else "—"
            checks.append(f"| {NAMES[leg]} | {labels[key]} | {fmt(m['ann'])} | "
                          f"{m['sharpe']:.2f} | {pub_s} | {dt_s} | {same_s} |")

        cut = px.index[int(len(px) * CUT)]
        cands = bb.candidates(hs, px)
        ev = []
        for label, h in cands:
            f, p = bb.evaluate(h, px, cut)
            ev.append((label, h, f, p))
        base_f, base_p = ev[0][2], ev[0][3]
        passed = [x for x in ev[3:]
                  if x[2] and x[3]
                  and x[2]["sharpe"] >= base_f["sharpe"] + GATE
                  and x[3]["sharpe"] >= base_p["sharpe"] + POST_GATE]
        best = max(passed, key=lambda x: x[2]["sharpe"]) if passed else None
        passed_names = {x[0] for x in passed}
        detail[leg] = dict(ev=ev, base_f=base_f, base_p=base_p, best=best,
                           passed=passed, cut=cut, px=px, labels=labels,
                           base_key=top3[leg][0][0], meta=meta[leg])
        if best:
            g_b = (bb.metrics(ev[0][1], px, fee=0.0) or {}).get("sharpe")
            g_w = (bb.metrics(best[1], px, fee=0.0) or {}).get("sharpe")
            detail[leg]["attr"] = ("信号效应" if (g_b is not None and g_w is not None
                                              and g_w > g_b + 0.02) else "成本效应")
            wins.append((leg, best))

        name1 = labels[top3[leg][0][0]]
        sections += ["", f"## {NAMES[leg]} —— 近五年 ★1：{name1}", "",
                     f"同尺基线（★1）：夏普 {base_f['sharpe']:.2f} / 年化 {fmt(base_f['ann'])} / "
                     f"回撤 {fmt(base_f['mdd'])}，后段 {base_p['sharpe']:.4f}；"
                     f"窗口 {px.index[0].date()} ~ {px.index[-1].date()}（{len(px)} 根，"
                     f"后段自 {cut.date()}）。", "",
                     "| 候选 | 年化 | 回撤 | 净夏普 | 毛夏普 | 后段夏普 | Calmar | 平均仓位 | 调仓/年 | 判定 |",
                     "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
        base_gross = (bb.metrics(ev[0][1], px, fee=0.0) or {}).get("sharpe")
        for label, h, f, p in ev:
            if f is None or p is None:
                sections.append(f"| {label} | — | — | — | — | — | — | — | — | 样本不足 |")
                continue
            gross = bb.metrics(h, px, fee=0.0)
            star = ("**★ 超越（最佳）**" if best and label == best[0] else
                    "★ 超越" if label in passed_names else
                    "全样本↑·后段↓" if f["sharpe"] >= base_f["sharpe"] + GATE else "—")
            if (label in passed_names and base_gross is not None
                    and gross["sharpe"] <= base_gross + 0.02):
                star += "·成本效应"
            sections.append(f"| {label} | {fmt(f['ann'])} | {fmt(f['mdd'])} | {f['sharpe']:.2f} | "
                            f"{gross['sharpe']:.2f} | {p['sharpe']:.2f} | {f['calmar']:.2f} | "
                            f"{f['expo']:.0%} | {f['tpy']:.1f} | {star} |")
        if best:
            g_base = bb.metrics(ev[0][1], px, fee=0.0)["sharpe"]
            g_best = bb.metrics(best[1], px, fee=0.0)["sharpe"]
            attr = ("**信号效应为主**" if g_best > g_base + 0.02 else
                    "**主要是成本效应**（毛夏普没有更高，净夏普靠换手下降换来）")
            others = [x[0] for x in passed if x[0] != best[0]]
            sections += ["", f"> **找到超越项：{best[0]}** —— 夏普 {base_f['sharpe']:.2f} → "
                         f"**{best[2]['sharpe']:.2f}**，后段 {base_p['sharpe']:.4f} → "
                         f"**{best[3]['sharpe']:.4f}**，年化 {fmt(base_f['ann'])} → "
                         f"{fmt(best[2]['ann'])}，回撤 {fmt(base_f['mdd'])} → {fmt(best[2]['mdd'])}。",
                         f"> 归因：毛夏普 {g_base:.2f} → {g_best:.2f}，换手 "
                         f"{base_f['tpy']:.1f} → {best[2]['tpy']:.1f} 次/年 —— {attr}。"
                         + (f"另有 {'、'.join(others)} 也通过双闸门。" if others else "")]
        else:
            top = max(ev[3:], key=lambda x: x[2]["sharpe"])
            sections += ["", f"> 无候选通过双闸门。最佳候选 **{top[0]}**：夏普 "
                         f"{top[2]['sharpe']:.2f}（基线 {base_f['sharpe']:.2f}，差 "
                         f"{top[2]['sharpe'] - base_f['sharpe']:+.2f}），后段 "
                         f"{top[3]['sharpe']:.2f}（基线 {base_p['sharpe']:.2f}）。"]
        sections.append("")

    # ---------------------------------------------------------- 报告
    L = [f"# 在「{WINDOW_YEARS} 年当前最优」之上，还能再往前推一步吗？—— 原生引擎 · 同尺版",
         "",
         f"> 由 `research/beat_best_5y/_beat_best_5y.py` 生成（{pd.Timestamp.now():%Y-%m-%d}）。"
         "基线取自 `research/best_by_leg_5y/_best_by_leg_5y.md` §0 的**近五年前三名**，"
         "逐日权重来自各变种**原生引擎**，不重写规则、不做网格搜索。",
         "",
         "## 0. 口径与判定",
         "",
         f"- 窗口：**全局最新交易日前推 {WINDOW_YEARS} 个日历年**（{w0.date()} ~ "
         f"{pd.Timestamp(end).date()}）。{WARMUP_NOTE}。",
         f"- 成本：所有单腿与候选**统一**单边 {bb.FEE_SIDE:.2%}、空仓货基 "
         f"{bb.CASH_ANNUAL:.2%}、T 日收盘决策 → T+1 收益"
         "（V9/V10 按引擎自身的 T+2 语义换算成「当日收益对应仓位」后再同尺）。",
         "- 引擎隔离：与 `_beat_best_top3.py` 同一范式，import 与调用都在隔离上下文里"
         "（V12 `engine.py` 在调用时二次 `import data` 且有 fail-fast 断言）。",
         f"- **★ = 全窗夏普 ≥ 近五年 ★1 + {GATE:.2f}，且后段（窗口后 {1 - CUT:.0%}）夏普 "
         f"≥ ★1 后段 + {POST_GATE:.2f}**。后段只赢 0.00x 属噪声，不予采纳。",
         "- 候选全部无参数或单固定参数（E2/E3 等权、双确认、月末 252 日轮动、MA200 切换、"
         "TV20 上限），与全历史版**完全同一套**，便于对比两个窗口的结论是否一致。",
         "- **毛夏普 = 完全不计费**：净夏普赢而毛夏普输，说明增益来自换手下降省下的费用，"
         "不是信号更准。",
         "",
         "### ⚠ 可信度：低于全历史版",
         "",
         "1. **同一段数据用了两次**：★1 是在这 5 年里排出来的，超越项也在这 5 年里挑。"
         "本文件的定位是「**窗口敏感性检验**」，不是新的落地建议。",
         "2. **后段不是样本外**：窗口后 40% ≈ 2 年，仍在窗口内，只能防「近期失效」，"
         "挡不住「整段过拟合」。",
         "3. **基线换人**：5 年窗口含 2022 年熊市（A 股与美股同步大跌、港股续跌），"
         "沪深300 / 黄金的 ★1 与全历史版不同 —— 基线一换，答案也跟着换。",
         "4. **后段闸门在基线后段为负时几乎不设防**：例如沪深300 的 ★1（V12）后段夏普 "
         "-0.37，闸门线变成 -0.35，任何候选都容易过。这类腿的 ★ 只能当「换一支更稳的」看，"
         "不能当「找到了更好的信号」。",
         "",
         "### ⚠ 零费 C 类腿的同尺陷阱（必读）",
         "",
         "**中证A500 / 科创50 / 恒生科技的 V1/V2/V3 原生零申赎费**（走 C 类）。"
         "本研究的同尺口径是统一单边 10bp —— 对这三条腿上的高换手策略"
         "（一年 20~25 次往返）等于每年多扣 4~5pp，**基线夏普会被直接压到 0 附近**，"
         "于是任何低换手组合都显得「超越」。这类超越在表里标 `·成本效应`，"
         "**不是信号进步**。真实 C 类还有 0.4~0.6%/年销售服务费，但它对所有候选同等，"
         "不改变相对排序，只是整体下移。",
         "",
         "### 自检：原生费率复现 vs 同尺口径的代价",
         "",
         "> `夏普(原生)` 用**各引擎真实费率**重算，应复现 `_best_by_leg_5y.jsonl` 的"
         "（`差` 在 ±0.02 内说明桥接无误，超出部分来自 rebase 与 v5 reindex 的舍入）。"
         "`同尺夏普(10bp)` 是本研究实际比较用的口径 —— **两列之差就是「换口径的代价」**，"
         "C 类零费腿与 V12/V13（原生 5bp）会掉得最多。自检列不参与候选比较。",
         "",
         "| 腿 | 近五年 ★1~★3 | 年化(原生) | 夏普(原生) | 5y 榜夏普 | 差 | 同尺夏普(10bp) |",
         "|---|---|---:|---:|---:|---:|---:|"]
    L += checks
    L += sections

    L += ["## 汇总", "",
          f"七条腿中 **{len(wins)} 条**能在近五年 ★1 之上再往前推一步：", "",
          "| 标的 | 近五年 ★1（同尺夏普） | 超越项 | 新夏普 | 后段 新 vs 旧 | 归因 |",
          "|---|---:|---|---:|---|---|"]
    for leg, best in wins:
        d = detail[leg]
        others = [x[0] for x in d["passed"] if x[0] != best[0]]
        L.append(f"| {NAMES[leg]} | {d['labels'][d['base_key']]}"
                 f"（{d['base_f']['sharpe']:.2f}） | **{best[0]}**"
                 + (f"（另：{'、'.join(others)}）" if others else "") + " | "
                 f"**{best[2]['sharpe']:.2f}** | "
                 f"{best[3]['sharpe']:.2f} vs {d['base_p']['sharpe']:.2f} | "
                 f"{d.get('attr', '—')} |")
    if not wins:
        L.append("| — | — | 无 | — | — | — |")

    # ---- 跨窗口对照：同一技法在两个窗口是否复现（本报告最有价值的一节）
    full_wins = read_full_wins()
    L += ["", "## 跨窗口对照：同一技法在两个窗口是否复现", "",
          "「全历史超越项」取自同族 `beat_best/_beat_best_top3.md` 的汇总表；"
          "本表只比**技法类别**（E2 / E3 / MIN2 / MAX2 / ROT / SW / E2×TV），"
          "不比标签后缀（→V12 之类）。", "",
          "| 标的 | 近五年超越项 | 全历史超越项 | 是否复现 | 怎么解读 |",
          "|---|---|---|---|---|"]
    repl = 0
    for leg in LEGS:
        d = detail.get(leg)
        if not d:
            continue
        now = d["best"][0] if d["best"] else None
        old = full_wins.get(NAMES[leg])
        nk = cand_kind(now) if now else None
        ok = cand_kind(old) if old else None
        if nk and ok and nk == ok:
            verdict, note = "**是**", "两窗口同技法 → 可信度最高"
            repl += 1
        elif nk and not ok:
            verdict, note = "否", "只在 5 年窗口成立 → 大概率吃 2022 后这段行情，不采纳"
        elif ok and not nk:
            verdict, note = "否", "全历史有、近 5 年失效 → 降级为观察"
        elif nk and ok:
            verdict, note = "否", "两窗口技法不同 → 该腿的「最优组合」本身不稳定"
        else:
            verdict, note = "一致（都无）", "两窗口都没有空间 → ★1 已贴规则边界"
        L.append(f"| {NAMES[leg]} | {now or '—'} | {old or '—'} | {verdict} | {note} |")
    L.append("")
    L.append(f"> {len(detail)} 条腿里 **{repl}** 条在两个窗口找到了同一类技法的超越项 —— "
             "这些才是「组合方法本身有效」的证据；其余要么是窗口特异，要么两边都没有空间。")

    L += ["", "## 落地汇总：近五年窗口下每条腿用哪支", "",
          "过双闸门的腿用超越项，其余沿用近五年 ★1。**这不等于「应该这么配」** —— "
          "它与全历史版（`beat_best/_beat_best_top3.md`）冲突时，优先信全历史版。", "",
          "| 标的 | 近五年采用 | 相对 ★1 | 年化 | 回撤 | 净夏普 | 后段夏普 | 归因 |",
          "|---|---|---|---:|---:|---:|---:|---|"]
    for leg in LEGS:
        d = detail.get(leg)
        if not d:
            continue
        first = d["labels"][d["base_key"]]
        if d["best"]:
            m, p = d["best"][2], d["best"][3]
            L.append(f"| {NAMES[leg]} | {d['best'][0]} | **超越项**"
                     f"（{d['base_f']['sharpe']:.2f} → {m['sharpe']:.2f}） | {fmt(m['ann'])} | "
                     f"{fmt(m['mdd'])} | {m['sharpe']:.2f} | {p['sharpe']:.2f} | "
                     f"{d.get('attr', '—')} |")
        else:
            L.append(f"| {NAMES[leg]} | {first} | —（无超越项，保持 ★1） | "
                     f"{fmt(d['base_f']['ann'])} | {fmt(d['base_f']['mdd'])} | "
                     f"{d['base_f']['sharpe']:.2f} | {d['base_p']['sharpe']:.2f} | — |")

    L += ["", "## 结论与边界", "",
          f"1. **候选是「组合/叠加」，不是新信号源**：E2/E3、双确认、轮动、MA200 切换、"
          f"TV20 上限用的全是近五年前三名已有的择时信息。能赢说明「怎么把已有的好策略放在"
          f"一起」还有空间；不能赢说明 ★1 在这条腿上已经贴着这组规则的边界。",
          "2. **同尺成本会压低所有数字**：统一 10bp 单边对 V13（原生 5bp）和 C 类腿"
          "（原生 0 费率）是加压，所以「同尺夏普」低于 `_best_by_leg_5y.md` 的含费数字。"
          "同尺内基线 vs 候选的比较仍成立，绝对数不要跨报告对账。",
          f"3. **{WINDOW_YEARS} 年窗口的样本量**：约 {int(WINDOW_YEARS * 247)} 根日线，"
          "后段 40% 约 2 年。任何 ★ 的置信度都低于全历史版；夏普标准误 ≈ ±0.22。",
          "4. **净夏普赢不等于信号更准**：看毛夏普列。毛夏普没升而净夏普升，增益全部来自"
          "换手下降省下的费用 —— 这类改进在原生低费率通道里会消失。",
          "5. **ROT 与 SW 有过拟合风险**：lookback 252 / MA200 是事先固定的常用值，"
          "不是扫出来的；但换一个值就可能翻号，采纳前应做邻域检验。",
          "",
          "> 机械规则输出，非投资建议。"]
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    # ---------------- 机读落地清单（`_daily_positions_5y.py` 读它取当日目标仓位）
    with io.open(OUT_JSONL, "w", encoding="utf-8") as f:
        for leg in LEGS:
            d = detail.get(leg)
            if not d:
                continue
            best = d["best"]
            base_lab = d["labels"][d["base_key"]]
            rec = dict(
                leg=leg, leg_name=NAMES[leg],
                window=[str(pd.Timestamp(w0).date()), str(pd.Timestamp(end).date())],
                top3=[dict(key=k, vx=vx, label=lab) for k, vx, lab in top3[leg]],
                base_key=d["base_key"], base_label=base_lab,
                base_sharpe=d["base_f"]["sharpe"], base_ann=d["base_f"]["ann"],
                base_mdd=d["base_f"]["mdd"], base_post=d["base_p"]["sharpe"],
                adopted=(best[0] if best else base_lab),
                adopted_kind=(cand_kind(best[0]) if best else "BASE"),
                adopted_idx=([i for i, x in enumerate(d["ev"]) if x[0] == best[0]][0]
                             if best else 0),
                attr=d.get("attr"),
                sharpe=(best[2]["sharpe"] if best else d["base_f"]["sharpe"]),
                ann=(best[2]["ann"] if best else d["base_f"]["ann"]),
                mdd=(best[2]["mdd"] if best else d["base_f"]["mdd"]),
                post=(best[3]["sharpe"] if best else d["base_p"]["sharpe"]),
                cost_effect=bool(best and d.get("attr") == "成本效应"),
            )
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print("WROTE", OUT)
    print("WROTE", OUT_JSONL)
    for leg in LEGS:
        d = detail.get(leg)
        if not d:
            continue
        if d["best"]:
            print(f"  {NAMES[leg]:8s} {d['base_f']['sharpe']:.2f} -> {d['best'][0]} "
                  f"{d['best'][2]['sharpe']:.2f} (post {d['best'][3]['sharpe']:.2f} "
                  f"vs {d['base_p']['sharpe']:.2f})")
        else:
            print(f"  {NAMES[leg]:8s} {d['base_f']['sharpe']:.2f} | no beat")


if __name__ == "__main__":
    main()
