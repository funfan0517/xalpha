# -*- coding: utf-8 -*-
"""按「近五年各标的最优组合」给出**当日各标的目标仓位**。

落地清单不在这里维护：读同目录 `_beat_best_5y.jsonl`（由 `_beat_best_5y.py` 生成），
每腿取 `adopted_idx` 指向的候选（无超越项时 = 0 = ★1 本身），然后用**同一套组合公式**
在**决策层**重算最后一根 bar 的仓位。

## 三个仓位概念，别混

| 列 | 含义 | 什么时候用 |
|---|---|---|
| `当前生效` | 最新一根 bar 收益所对应的仓位（= 决策 shift 1 天） | 看「账户现在应该是多少」 |
| `今日目标` | 最新一根 bar **收盘做出**的决策（T+1 起生效） | 明天开盘要调到的仓位 |
| `调整` | 今日目标 − 当前生效 | 正数加仓、负数减仓、0 不动 |

- 对 V9/V10 参与的腿（本清单里是纳指100），引擎是 **T+2** 语义：今日目标自**后天**起生效。
- SW（MA200 切换）的上下方判断、ROT 的排序都用**前一日及以前**的数据（与
  `_beat_best_5y.py` 的口径一致），不含当日收盘价，所以今日目标可以在收盘后直接算。
- 若当日尚未收盘，脚本读到的最新 bar 是上一交易日 —— 那 `当前生效` 就是「今天全天持有」，
  `今日目标` 是「上一交易日收盘做出的决策」，两者都成立，看数据截止日即可。

## ⚠ 这不是实盘指令

1. 组合来自**近五年窗口内的双闸门筛选**（同一段数据既选基线又挑超越项），
   未经样本外验证；其中 A500 / 沪深300 的超越技法**跨窗口不复现**（见 `_beat_best_5y.md`）。
2. 同尺口径是统一单边 10bp，与各变种原生费率不同；零费 C 类腿上「超越项」可能只是省费。
3. 真正落地前请对照 `_beat_best_top3.md`（全历史版）：**两版冲突时以全历史版为准**。

用法：`python strategies/vol_legs/research/beat_best_5y/_daily_positions_5y.py`

> 模拟结果，非投资建议。
"""
import importlib.util
import io
import json
import os
import sys

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
OUT_MD = os.path.join(_HERE, "_daily_positions_5y.md")
# 机读版：`pipeline/daily_digest.py::parse_vol_5y` 读它渲染板块2 的 5y 卡片。
# 看板**不解析 md**（列宽/文案一改就断），产物结构变更要同步那边的字段名。
OUT_JSON = os.path.join(_HERE, "_daily_positions_5y.json")
REC_JSONL = os.path.join(_HERE, "_beat_best_5y.jsonl")


def _load(mod_name):
    spec = importlib.util.spec_from_file_location(mod_name, os.path.join(_HERE, mod_name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pct(x):
    return "—" if x is None or pd.isna(x) else f"{x * 100:.0f}%"


def _last(s):
    s = s.dropna()
    return float(s.iloc[-1]) if len(s) else None


def explain(rec, legs_dec, px, LAST):
    """把组合拆成分项，给出「为什么是这个仓位」。"""
    kind = rec["adopted_kind"]
    ks = [t["key"] for t in rec["top3"]]
    lab = {t["key"]: t["label"] for t in rec["top3"]}
    part = " / ".join(f"{lab[k]} {_pct(LAST[k])}" for k in ks)

    if kind == "BASE":
        return f"{lab[ks[0]]} 单支：{_pct(LAST[ks[0]])}"
    if kind == "E2":
        return f"{lab[ks[0]]} + {lab[ks[1]]} 等权 → {part}"
    if kind == "E3":
        return f"前三等权 → {part}"
    if kind == "MIN2":
        return f"取两者较低 → {part}"
    if kind == "MAX2":
        hi = max(ks[:2], key=lambda k: LAST[k])
        return f"任一在场（取较高：{lab[hi]}） → {part}"
    if kind == "SW":
        ma = px.rolling(200).mean()
        above = bool((px > ma).shift(1).iloc[-1])
        used = ks[0] if above else ks[1]
        return (f"MA200 **{'上方' if above else '下方'}**（前一日判断）→ 用 {lab[used]}；"
                f"{part}")
    # `_beat_best_5y.py::cand_kind` 归类别时只保留 "E2×TV"（不带 20），故两种写法都要认，
    # 否则这一支永不命中，科创50 的解释会退化成裸的「分项 / 分项」。
    if kind in ("E2×TV", "E2×TV20"):
        avg = (LAST[ks[0]] + LAST[ks[1]]) / 2.0
        cap = _last(legs_dec["__tv20__"])
        return (f"{lab[ks[0]]} + {lab[ks[1]]} 平均 {_pct(avg)}，过 TV20 上限 "
                f"{_pct(cap)} → 取低者；{part}")
    if kind == "ROT":
        return f"月末 252 日动量轮动 → {part}"
    return part


def main():
    if not os.path.exists(REC_JSONL):
        raise SystemExit(f"缺落地清单：{REC_JSONL}\n先跑 "
                         "`python strategies/vol_legs/research/beat_best_5y/_beat_best_5y.py`")
    m = _load("_beat_best_5y")
    bb = m._load_bb()
    holds = m.Holds(bb)

    # 黄金的当日 bar 来自 `_long_klines.json`，盘中跑流程时写进去的是**盘中快照**。
    # `_refresh_data.ensure_fresh` 自带「最后写入 < 当日 15:00 且现在已过 15:00 → 强制修正」
    # 的判据，这里先兜底一次（幂等：已是最新时只读文件日期，不联网）。
    try:
        _dd = os.path.join(m._ROOT, "data")
        if _dd not in sys.path:
            sys.path.insert(0, _dd)
        import _refresh_data                        # noqa: PLC0415
        _refresh_data.ensure_fresh(verbose=False)
    except Exception as exc:                        # noqa: BLE001
        print(f"  [warn] 行情兜底刷新跳过：{type(exc).__name__}: {exc}")

    with io.open(REC_JSONL, encoding="utf-8") as f:
        recs = [json.loads(line) for line in f if line.strip()]

    rows = []
    for rec in recs:
        leg = rec["leg"]
        # 盘中会带出当天半成品 bar —— 必须截掉，否则「今日目标」随盘中价乱跳
        px = m.closed_series(bb.vdata.series_for_leg(leg).dropna().astype("float64"), leg)
        dec, hold = {}, {}
        for t in rec["top3"]:
            hold[t["key"]] = holds.get(t["key"], leg, px, lag=True)
            dec[t["key"]] = holds.get(t["key"], leg, px, lag=False)
        bb.TOP3_LABEL = {t["key"]: t["label"] for t in rec["top3"]}

        cands_h = bb.candidates(hold, px)
        cands_d = bb.candidates(dec, px)
        idx = int(rec.get("adopted_idx") or 0)
        # TV20 上限单独算一次，供 explain 显示（E2×TV20 那条候选的封顶值）
        dec["__tv20__"] = bb.vol_cap(px, 0.20)

        LAST = {k: _last(dec[k]) for k in dec}
        now = _last(cands_h[idx][1])
        tgt = _last(cands_d[idx][1])
        rows.append(dict(
            leg=leg, name=rec["leg_name"], adopted=rec["adopted"],
            kind=rec["adopted_kind"], attr=rec.get("attr"),
            now=now, tgt=tgt,
            ret5=(float(px.iloc[-1] / px.iloc[-6] - 1) if len(px) > 6 else None),
            diff=(tgt - now) if (tgt is not None and now is not None) else None,
            last_date=px.index[-1].date(),
            lag=("T+2" if any(t["key"] in ("v9", "v10") for t in rec["top3"]) else "T+1"),
            why=explain(rec, dec, px, LAST),
        ))
        print(f"  {rec['leg_name']:8s} 当前 {_pct(now):>5s} → 今日目标 {_pct(tgt):>5s}"
              f"  ({rec['adopted']})")

    dates = sorted({r["last_date"] for r in rows})

    # ---------------- 机读产物（看板卡片读它，不解析 md）
    try:
        if m._VL not in sys.path:
            sys.path.insert(0, m._VL)
        import rule as _rule                       # noqa: PLC0415
        codes = {lg: (_rule.LEGS.get(lg) or {}).get("exec", "") for lg in _rule.LEGS}
    except Exception:                              # noqa: BLE001
        codes = {}
    payload = dict(
        as_of=str(max(dates)) if dates else None,
        generated=pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
        window=list(recs[0].get("window") or []) if recs else [],
        total_target=round(float(sum(r["tgt"] for r in rows if r["tgt"] is not None)), 4),
        legs=[dict(leg=r["leg"], name=r["name"], code=codes.get(r["leg"], ""),
                   adopted=r["adopted"], kind=r["kind"], attr=r.get("attr"),
                   now=r["now"], target=r["tgt"], diff=r["diff"], ret5=r["ret5"],
                   lag=r["lag"], last_date=str(r["last_date"]), why=r["why"])
              for r in rows],
    )
    with io.open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    held = [r for r in rows if (r["tgt"] or 0) > 0]
    worst = sorted([r for r in rows if r["ret5"] is not None], key=lambda r: r["ret5"])[:3]
    brief = ""
    if held:
        brief = ("七条腿里仍有仓位的是 " + "、".join(
            f"**{r['name']} {r['tgt'] * 100:.0f}%**" for r in held) + "，其余全部 0%——")
    if worst:
        brief += ("近 5 日 " + "、".join(f"{r['name']} {r['ret5'] * 100:+.1f}%" for r in worst)
                  + "，八态趋势 / 波动层类策略集体转空仓。这**不是脚本没跑出数**，是规则在防守。")
    L = ["# 按「近五年各标的最优组合」给出的当日目标仓位", "",
         f"> 由 `research/beat_best_5y/_daily_positions_5y.py` 生成（{pd.Timestamp.now():%Y-%m-%d %H:%M}）。"
         f"行情截止：**{'、'.join(str(d) for d in dates)}**。", ""]
    if brief:
        L += [f"> **一句话**：{brief}", ""]
    L += ["## 0. 怎么读这三列", "",
         "- **当前生效** = 最新一根 bar 收益所对应的仓位（决策已滞后 1 天）→ 看账户现在应该是多少；",
         "- **今日目标** = 最新一根 bar **收盘做出**的决策 → 下一交易日开盘要调到的仓位；",
         "- **调整** = 今日目标 − 当前生效（正数加仓 / 负数减仓 / 0 不动）。", "",
         "> 若行情只更新到上一交易日（如周五），那么「当前生效」就是**今天全天**应持有的仓位，"
         "「今日目标」是该收盘做出的决策 —— 两个都成立，看「行情截止」列即可。", "",
         "## 1. 今日目标仓位", "",
         "| 标的 | 采用策略 | 当前生效 | **今日目标** | 调整 | 近 5 日 | 生效 | 行情截止 |",
         "|---|---|---:|---:|---:|---:|---|---|"]
    for r in rows:
        diff = ("—" if r["diff"] is None else
                "0" if abs(r["diff"]) < 0.005 else f"{r['diff'] * 100:+.0f}%")
        r5 = "—" if r["ret5"] is None else f"{r['ret5'] * 100:+.1f}%"
        L.append(f"| **{r['name']}** | {r['adopted']} | {_pct(r['now'])} | "
                 f"**{_pct(r['tgt'])}** | {diff} | {r5} | "
                 f"{r['lag']} | {r['last_date']} |")
    tot = sum(r["tgt"] for r in rows if r["tgt"] is not None)
    L += ["", f"> 七条腿的目标仓位合计 **{tot * 100:.0f}%**（等权口径下每腿上限 100%，"
              "未做跨品种相关性约束，不是组合建议）。", "",
          "## 2. 每个仓位是怎么来的", "",
          "| 标的 | 组合构成（分项为各支的今日决策） |", "|---|---|"]
    for r in rows:
        L.append(f"| {r['name']} | {r['why']} |")

    L += ["", "## 3. 边界（必读）", "",
          "1. **这是近五年窗口内的筛选结果，未经样本外验证**：★1 与超越项用的是同一段数据。"
          "跨窗口复现的只有纳指100（SW MA200→V12）与科创50（E2×TV20）；"
          "**中证A500（MAX2）与沪深300（E3）的超越技法在两个窗口不一致**，这两条腿的"
          "「超越项」不宜单独当实盘依据。",
          "2. **同尺口径 vs 原生费率**：本组合的评估用统一单边 10bp。零费 C 类腿"
          "（A500/科创50/恒科）上的 V1~V3 在原生口径下更强，`_beat_best_5y.md` 的"
          "`·成本效应` 标记说明哪些「超越」只是省费。",
          "3. **与全历史版冲突时以全历史版为准**：`beat_best/_beat_best_top3.md` 的基线来自 20 年样本，"
          "其超越项（A500=E2、沪深300=MIN2、纳指=SW、科创50=E2×TV20）才是主要参考。",
          "4. **仓位是「单腿目标」，不是组合权重**：七条腿合计不等于账户整体风险敞口，"
          "空仓部分只拿货基（1.40%），未计跨腿相关性。", "",
          "> 机械规则输出，非投资建议。"]
    with io.open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("WROTE", OUT_MD)


if __name__ == "__main__":
    main()
