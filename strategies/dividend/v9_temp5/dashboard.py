#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V9 · 温度看板（单文件 HTML，内联 CSS，无 CDN / 无外部依赖）

只读 `daily/_v9_signal.json`，不重算任何指标 —— 与 `daily.py` 同一份数据，
避免「报告和看板数字不一样」。

产物：`daily/_v9_temp.html`

用法::

    python strategies/dividend/v9_temp5/dashboard.py
"""
from __future__ import annotations

import json
import os
import sys

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import rule  # noqa: E402  pylint: disable=wrong-import-position

FACTORS = [("spread", "股债收益差", "35%"), ("pe", "PE", "20%"), ("pb", "PB", "16%"),
           ("dev", "年线乖离", "15%"), ("pos", "价格位置", "14%")]


def zone_color(t: float) -> str:
    if t < 20:
        return "#3c6fa8"
    if t < 40:
        return "#4a86bd"
    if t < 60:
        return "#d9a13b"
    if t < 75:
        return "#e07b27"
    if t < 85:
        return "#cf4b25"
    return "#a8271a"


def zone_soft(t: float) -> str:
    if t < 40:
        return "#e8f0f8"
    if t < 60:
        return "#fbf2dd"
    if t < 75:
        return "#fbe8d6"
    return "#f9e0da"


def bar(v, color, height=7) -> str:
    if v is None:
        return f'<div class="track" style="height:{height}px"></div>'
    v = max(0.0, min(100.0, float(v)))
    return (f'<div class="track" style="height:{height}px">'
            f'<div class="fill" style="width:{v:.1f}%;background:{color}"></div></div>')


def fmt(v, suffix="", digits=2, dash="N/A") -> str:
    return dash if v is None else f"{v:.{digits}f}{suffix}"


def _card(m: dict) -> str:
    c = zone_color(m["temp"])
    is_px = m["temp_kind"] != "五因子温度"
    rows = []
    for key, label, w in FACTORS:
        v = m["sub"].get(key)
        head = f'<span class="fname">{label}<i>{w}</i></span>'
        if v is None:
            rows.append(f'<div class="frow muted">{head}<div class="track"></div>'
                        f'<span class="fval">—</span></div>')
        else:
            rows.append(f'<div class="frow">{head}{bar(v, c, 5)}'
                        f'<span class="fval">{v:.0f}</span></div>')
    factors = "".join(rows)
    return f"""
      <div class="card{' partial' if is_px else ''}">
        <div class="card-head">
          <div>
            <div class="cname">{m['name']} <span class="cfund">{m['code']}{'†' if is_px else ''}</span></div>
            <div class="ccode">{m['index']} · 跟踪 {m['index_name']}</div>
          </div>
          <div class="tempbox" style="color:{c}">
            <span class="tnum">{m['temp']:.1f}</span><span class="tunit">/100</span>
          </div>
        </div>
        <div class="card-bar">{bar(m['temp'], c, 9)}</div>
        <div class="kv">
          <div><b>{fmt(m['close'], '', 2)}</b><i>点位</i></div>
          <div><b>{fmt(m['pe'], '', 2)}</b><i>PE</i></div>
          <div><b>{fmt(m['dy'], '%')}</b><i>股息率</i></div>
          <div><b>{fmt(m['spread'], 'pp')}</b><i>股债差</i></div>
          <div><b>{fmt(m['dev'], '%', 2)}</b><i>年线乖离</i></div>
          <div><b>{fmt(m['pos'], '', 1)}</b><i>位置分位</i></div>
          <div><b>{fmt(m['dd52'], '%', 2)}</b><i>52周回撤</i></div>
          <div><b>{fmt(m['sub'].get('pe'), '', 0)}</b><i>PE 分位</i></div>
        </div>
        <div class="factors">{factors}</div>
        <div class="advice" style="background:{zone_soft(m['temp'])}">
          <b>{m['band']}</b> · {'仅乖离 50% + 位置 50%，估值面缺失，不与五因子温度并列'
                               if is_px else m['action']}
        </div>
      </div>"""


def render(d: dict) -> str:
    funds = d["funds"]
    valued = [m for m in funds if m["temp_kind"] == "五因子温度"]
    px = [m for m in funds if m["temp_kind"] != "五因子温度"]
    ranked = sorted(valued, key=lambda x: x["temp"])
    cold, hot = (ranked[0], ranked[-1]) if ranked else (None, None)
    q = "—" if d["board_pct"] is None else f"{d['board_pct'] * 100:.0f}%"

    rank_rows = "".join(
        f'<div class="rank"><span class="rno">{i}</span>'
        f'<span class="rname">{m["name"]}<i>{m["code"]}</i></span>'
        f'<div class="rbar">{bar(m["temp"], zone_color(m["temp"]), 10)}</div>'
        f'<span class="rval" style="color:{zone_color(m["temp"])}">{m["temp"]:.1f}</span>'
        f'<span class="rtag" style="color:{zone_color(m["temp"])};'
        f'border-color:{zone_color(m["temp"])}">{m["band"]}</span></div>'
        for i, m in enumerate(ranked, 1))
    for m in sorted(px, key=lambda x: x["temp"]):
        rank_rows += (
            f'<div class="rank partial-row"><span class="rno">†</span>'
            f'<span class="rname">{m["name"]}<i>{m["code"]}</i></span>'
            f'<div class="rbar">{bar(m["temp"], "#b9ac96", 10)}</div>'
            f'<span class="rval" style="color:#b9ac96">{m["temp"]:.1f}</span>'
            f'<span class="rtag" style="color:#b9ac96;border-color:#b9ac96">价格温度</span></div>')

    buckets = {f"过热 ≥{rule.ABS_OUT:.0f}": 0, f"偏热 {rule.ABS_CUT:.0f}–{rule.ABS_OUT:.0f}": 0,
               "中性 40–65": 0, "偏冷 <40": 0}
    for m in valued:
        t = m["temp"]
        if t >= rule.ABS_OUT:
            buckets[f"过热 ≥{rule.ABS_OUT:.0f}"] += 1
        elif t >= rule.ABS_CUT:
            buckets[f"偏热 {rule.ABS_CUT:.0f}–{rule.ABS_OUT:.0f}"] += 1
        elif t < 40:
            buckets["偏冷 <40"] += 1
        else:
            buckets["中性 40–65"] += 1
    chips = "".join(f'<div class="chip"><b>{v}</b><i>{k}</i></div>'
                    for k, v in buckets.items())

    oneline = (
        f"有估值口径的 {len(ranked)} 只平均温度 <b>{d['board_temp']:.1f}</b>"
        f"（自身历史分位 <b>{q}</b>）；最冷 <b>{cold['name']} {cold['temp']:.1f}</b>、"
        f"最热 <b>{hot['name']} {hot['temp']:.1f}</b> —— {hot['band']}，{hot['action']}。"
        if ranked else "无可用估值读数")

    act_hot = "、".join(f"{m['name']}({m['code']})" for m in valued
                       if m["temp"] >= rule.ABS_OUT) or "无"
    act_cold = "、".join(f"{m['name']}({m['code']})" for m in valued
                        if m["temp"] < 40) or "无"
    stale = d.get("stale") or {}
    stale_txt = ("、".join(f"{k} 至 {v}" for k, v in stale.items())) if stale else "无"

    bt = d.get("backtest") or {}

    def _cal(v) -> str:
        return "—" if v is None else f"{v:.2f}"

    abs_pos = bt.get("abs_band_pos")
    abs_pos_txt = "—" if abs_pos is None else f"{abs_pos * 100:.0f}%"
    bt_txt = (f"回测（{bt.get('window') or '—'}，{bt.get('months') or '—'} 月）："
              f"按分位减仓 Calmar {_cal(bt.get('overlay_calmar'))}"
              f"（基准 {_cal(bt.get('bench_calmar'))}）、"
              f"横截面 top2 {_cal(bt.get('xs_top2_calmar'))}、"
              f"绝对档平均仓位 {abs_pos_txt}")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>V9 红利五因子估值温度 · {d['as_of']}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 28px 20px 40px;
    background: #f4efe4; color: #2b2622;
    font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  }}
  .wrap {{ max-width: 1120px; margin: 0 auto; }}
  .head {{ background: #fffdf7; border: 1px solid #e6dcc8; border-radius: 14px;
           padding: 22px 26px; margin-bottom: 18px; }}
  .eyebrow {{ font-size: 12px; letter-spacing: 2px; color: #a08a63; font-weight: 600; }}
  h1 {{ margin: 6px 0 4px; font-size: 30px; letter-spacing: 1px; font-weight: 800; }}
  .meta {{ font-size: 12.5px; color: #7c6a52; line-height: 1.75; }}
  .oneline {{ margin-top: 14px; padding: 12px 14px; border-left: 3px solid #e07b27;
              background: #fbf4e6; border-radius: 0 8px 8px 0; font-size: 13.5px; line-height: 1.65; }}
  .chips {{ display: flex; gap: 10px; margin-top: 14px; flex-wrap: wrap; }}
  .chip {{ flex: 1; min-width: 96px; background: #f7f1e3; border: 1px solid #ece1cb;
           border-radius: 10px; padding: 9px 12px; display: flex; align-items: baseline; gap: 8px; }}
  .chip b {{ font-size: 20px; font-variant-numeric: tabular-nums; }}
  .chip i {{ font-style: normal; font-size: 11.5px; color: #8a7860; }}
  .rankbox {{ background: #fffdf7; border: 1px solid #e6dcc8; border-radius: 14px;
              padding: 18px 24px 10px; margin-bottom: 18px; }}
  .sect {{ font-size: 14px; font-weight: 700; margin: 0 0 12px; color: #4a3d2c; }}
  .sect small {{ font-weight: 400; color: #a08a63; margin-left: 8px; font-size: 11.5px; }}
  .rank {{ display: flex; align-items: center; gap: 10px; padding: 7px 0; }}
  .rank.partial-row {{ opacity: .72; border-top: 1px dashed #e6dcc8; margin-top: 4px; }}
  .rno {{ width: 20px; font-size: 12px; color: #b0a08a; }}
  .rname {{ width: 148px; font-size: 13.5px; font-weight: 600; }}
  .rname i {{ font-style: normal; font-size: 10.5px; color: #b0a08a; margin-left: 4px; font-weight: 400; }}
  .rbar {{ flex: 1; }}
  .rval {{ width: 44px; text-align: right; font-weight: 700; font-size: 14px;
           font-variant-numeric: tabular-nums; }}
  .rtag {{ width: 62px; text-align: center; font-size: 11px; padding: 2px 0;
           border: 1px solid; border-radius: 20px; opacity: .85; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(460px, 1fr)); gap: 16px; }}
  .card {{ background: #fffdf7; border: 1px solid #e6dcc8; border-radius: 14px; padding: 18px 20px 16px; }}
  .card.partial {{ background: #fbf8f1; border-style: dashed; }}
  .card-head {{ display: flex; justify-content: space-between; align-items: flex-start; }}
  .cname {{ font-size: 17px; font-weight: 800; }}
  .cfund {{ font-size: 12px; font-weight: 600; color: #a08a63; margin-left: 4px; }}
  .ccode {{ font-size: 11px; color: #a08a63; margin-top: 3px; }}
  .tempbox {{ display: flex; align-items: baseline; gap: 2px; }}
  .tnum {{ font-size: 34px; font-weight: 800; font-variant-numeric: tabular-nums; line-height: 1; }}
  .tunit {{ font-size: 12px; color: #a08a63; }}
  .card-bar {{ margin: 12px 0 14px; }}
  .track {{ background: #ece3d2; border-radius: 6px; overflow: hidden; height: 5px; }}
  .fill {{ height: 100%; border-radius: 6px; }}
  .kv {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px 6px; margin-bottom: 14px; }}
  .kv div {{ display: flex; flex-direction: column; gap: 2px; }}
  .kv b {{ font-size: 13px; font-weight: 700; font-variant-numeric: tabular-nums; }}
  .kv i {{ font-size: 10.5px; color: #a08a63; font-style: normal; }}
  .factors {{ border-top: 1px dashed #e6dcc8; padding-top: 10px; }}
  .frow {{ display: flex; align-items: center; gap: 8px; margin: 5px 0; }}
  .frow.muted {{ opacity: .45; }}
  .fname {{ width: 108px; font-size: 11.5px; color: #6d5c47; }}
  .fname i {{ font-style: normal; color: #b3a48d; margin-left: 4px; font-size: 10px; }}
  .frow .track {{ flex: 1; }}
  .fval {{ width: 24px; text-align: right; font-size: 11px; color: #8a7860;
           font-variant-numeric: tabular-nums; }}
  .advice {{ margin-top: 12px; padding: 8px 10px; border-radius: 8px; font-size: 12px; color: #5c4a33; }}
  .foot {{ margin-top: 18px; background: #fffdf7; border: 1px solid #e6dcc8; border-radius: 14px;
           padding: 16px 22px; font-size: 11.5px; color: #8a7860; line-height: 1.95; }}
  .foot b {{ color: #5c4a33; }}
  @media (max-width: 560px) {{
    .grid {{ grid-template-columns: 1fr; }}
    .kv {{ grid-template-columns: repeat(2, 1fr); }}
    h1 {{ font-size: 22px; }}
    .rname {{ width: 104px; }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <div class="head">
    <div class="eyebrow">V9 · 红利五因子估值温度</div>
    <h1>红利族估值温度看板</h1>
    <div class="meta">
      数据截止 <b>{d['as_of']}</b> · 生成于 {d['generated']} · 池 {len(funds)} 只
      （{len(valued)} 只有估值口径 / {len(px)} 只仅价格温度）<br>
      温度 = 股债收益差 35% + PE 20% + PB 16% + 年线乖离 15% + 价格位置 14%
      （各取近 10 年日频分位，0 = 冰点，100 = 过热）<br>
      数据滞后标的：{stale_txt}
    </div>
    <div class="oneline"><b>一句话</b>：{oneline}</div>
    <div class="chips">{chips}</div>
  </div>

  <div class="rankbox">
    <div class="sect">温度排位<small>越冷越靠前 · † 为估值数据缺失标的，不参与排名 ·
      子分一律「越高越热」（股债收益差 = 100 − 利差分位，利差越宽越冷）</small></div>
    {rank_rows}
  </div>

  <div class="grid">{''.join(_card(m) for m in ranked + sorted(px, key=lambda x: x['temp']))}
  </div>

  <div class="foot">
    <b>动作清单</b>：过热（≥{rule.ABS_OUT:.0f}）{act_hot} —— 需要在腾额度时优先减；<br>
    偏冷（&lt;40）{act_cold} —— 新增额度优先给它们，分批建仓。
    <b>本看板不输出仓位倍率</b>：{bt_txt} —— 温度是「贵不贵」的体检表，
    不是择时/择基信号（时间维度分位口径越严越差，绝对口径经不起剔除检验）。
    控回撤走 V4（RSI14），选品走 V0 打分。<br>
    <b>口径</b>：温度算在<b>跟踪指数</b>上（基金有费率/跟踪误差）；PE 与点位取中证指数官网日频；
    10Y 中债取东财中债收益率曲线（018388 港股用 10Y 美债锚）；
    <b>PB = PE × ROE 中枢（合成）</b> —— 常数 ROE 使 <b>PB 分位 ≡ PE 分位</b>，
    故「五因子」实际权重是 PE 36% + 利差 35% + 乖离 15% + 位置 14%。<br>
    <b>局限</b>：008163（标普中国A股大盘红利低波动50）与 005125（标普中国A股红利机会）三源皆无估值，
    只算价格温度（乖离 50% + 位置 50%，价格用 501029 代理、562060 仅作回退），<b>不可与五因子温度比大小</b>；
    931468 的 PE 自 2020-05、930955 自 2017-05、931157 自 2019-04，
    这些标的的「10 年分位」实为全历史分位。<br>
    仅为个人数据记录与研究分析，不构成投资建议；市场有风险，投资需谨慎。
  </div>
</div>
</body>
</html>
"""


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    with open(rule.OUT_JSON, encoding="utf-8") as fh:
        d = json.load(fh)
    html = render(d)
    if "{{" in html:
        raise RuntimeError("模板占位符未渲染完")
    os.makedirs(rule.DAILY_DIR, exist_ok=True)
    with open(rule.OUT_HTML, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"[v9:html] 看板 -> {os.path.relpath(rule.OUT_HTML, rule.ROOT)}"
          f"（{len(d['funds'])} 张卡片 · 截止 {d['as_of']}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
