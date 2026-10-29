#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V4 · RSI14 择时卡片看板（单文件 HTML，内联 CSS，无 CDN / 无外部依赖）

只读 `daily/_v4_signal.json` 与同目录的逐标的小图（`chart.py` 产物），不重算任何指标 ——
避免「报告和看板数字不一样」。

产物：`v4_rsi/daily/_v4_dashboard.html`

用法::

    python strategies/dividend/v4_rsi/chart.py        # 先出逐标的图
    python strategies/dividend/v4_rsi/dashboard.py    # 再渲染看板（内嵌小图）
"""
from __future__ import annotations

import json
import os
import sys

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import rule as V4  # noqa: E402  pylint: disable=wrong-import-position

OUT_JSON = os.path.join(_DIR, "daily", "_v4_signal.json")
OUT_HTML = os.path.join(_DIR, "daily", "_v4_dashboard.html")
CHART_DIR = os.path.join(_DIR, "daily")


def tier_of(rsi: float) -> tuple[str, str]:
    if rsi >= V4.Q_OUT * 100:
        return "清仓", "清仓（0%）"
    if rsi >= V4.Q_HALF * 100:
        return "减半", "减半（50%）"
    return "满仓", "满仓（100%）"


def color(tag: str) -> str:
    return {"清仓": "#c0504d", "减半": "#e8a33d", "满仓": "#5c8a4a"}[tag]


def soft(tag: str) -> str:
    return {"清仓": "#f7dcd9", "减半": "#fbeccf", "满仓": "#e4efe0"}[tag]


def bar(v: float, c: str, height: int = 8) -> str:
    v = max(0.0, min(100.0, float(v)))
    return (f'<div class="track" style="height:{height}px">'
            f'<div class="fill" style="width:{v:.1f}%;background:{c}"></div></div>')


def _chart(code: str) -> str:
    fn = f"_v4_{code}_chart.png"
    if os.path.exists(os.path.join(CHART_DIR, fn)):
        return f'<div class="chartwrap"><img class="chart" src="{fn}" alt="{code} 净值与 RSI"></div>'
    return '<div class="chartwrap muted">图表未生成（先运行 chart.py）</div>'


def _card(f: dict, rsi: float, weight: float, held: bool) -> str:
    tag, tier_txt = tier_of(rsi)
    c, s = color(tag), soft(tag)
    held_tag = '<span class="held">持有</span>' if held else ''
    hint = {"清仓": "RSI ≥ 70 → 清仓 0%", "减半": "RSI 50–70 → 减半 50%",
            "满仓": "RSI < 50 → 满仓 100%"}[tag]
    return f"""<div class="card">
        <div class="card-head">
          <div>
            <div class="cname">{f['name']} <span class="cfund">{f['code']}</span>{held_tag}</div>
            <div class="ccode">当前权重 {weight * 100:.0f}% · 单只档位仅读图参考</div>
          </div>
          <div class="tempbox" style="color:{c}">
            <span class="tnum">{rsi:.1f}</span><span class="tunit">RSI</span>
          </div>
        </div>
        <div class="card-bar">{bar(rsi, c, 9)}</div>
        <div class="kv">
          <div><b>{rsi:.1f}</b><i>RSI14</i></div>
          <div><b>{weight * 100:.0f}%</b><i>当前权重</i></div>
          <div><b>{tag}</b><i>单只档位</i></div>
          <div><b>{int(V4.Q_HALF * 100)}/{int(V4.Q_OUT * 100)}</b><i>减半/清仓线</i></div>
        </div>
        <div class="advice" style="background:{s}"><b>{tag}</b> · {hint}</div>
        {_chart(f['code'])}
      </div>"""


def render(d: dict) -> str:
    funds = d["pool"]
    rsi_map = {p["code"]: float(p["rsi"]) for p in d["preview"]["picks"]}
    weight_map = {p["code"]: float(p["weight"]) for p in d["pool"]}
    held = {p["code"] for p in d["positions"]}
    rows = sorted(funds, key=lambda f: rsi_map.get(f["code"], 100.0))

    rank_rows = ""
    for i, f in enumerate(rows, 1):
        rsi = rsi_map.get(f["code"], 0.0)
        tag, _ = tier_of(rsi)
        c = color(tag)
        hold_flag = '<span class="rhold">持</span>' if f["code"] in held else ''
        w = weight_map.get(f["code"], 0.0)
        rank_rows += (
            f'<div class="rank"><span class="rno">{i}</span>'
            f'<span class="rname">{f["name"]}<i>{f["code"]}</i>{hold_flag}'
            f'<em class="rw">{w * 100:.0f}%</em></span>'
            f'<div class="rbar">{bar(rsi, c, 10)}</div>'
            f'<span class="rval" style="color:{c}">{rsi:.1f}</span>'
            f'<span class="rtag" style="color:{c};border-color:{c}">{tag}</span></div>')

    pv = d.get("preview") or {}
    mult = d.get("mult")
    pos_txt = "、".join(f"{p['name']} {p['weight'] * 100:.0f}%" for p in d["positions"]) or "空仓"
    oneline = (f"板块 RSI 均值 <b>{d['board_rsi']:.1f}</b> → <b>{d['tier']}</b>；"
               f"本月（{d['hold_month']}）持有 {pos_txt}；"
               f"下次在 <b>{d['next_decision']}</b> 收盘确认。")
    held_n = len(d["positions"])
    held_w = sum(weight_map.get(c, 0.0) for c in held)

    chips = "".join([
        f'<div class="chip"><b>{d["board_rsi"]:.1f}</b><i>板块 RSI 均值</i></div>',
        f'<div class="chip"><b>{d["tier"]}</b><i>仓位档位</i></div>',
        f'<div class="chip"><b>{held_n} 只 / {held_w * 100:.0f}%</b><i>当前持仓</i></div>',
        f'<div class="chip"><b>{d["next_decision"] or "—"}</b><i>下次决策</i></div>',
    ])

    preview_txt = ""
    if pv:
        chg = "会变" if pv.get("changed") else "不变"
        preview_txt = (f"进行中读数：板块 RSI {pv.get('board_rsi', 0):.1f} → {pv.get('tier', '—')}，"
                       f"若月末就是今天，目标<b>{chg}</b>"
                       f"（换手 {pv.get('turnover', 0) * 100:.0f}%）；"
                       f"仅预览，以月末收盘为准。")

    cards = "".join(
        _card(f, rsi_map.get(f["code"], 0.0), weight_map.get(f["code"], 0.0),
              f["code"] in held)
        for f in rows)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>V4 RSI14 择时看板 · {d['as_of']}</title>
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
  .oneline {{ margin-top: 14px; padding: 12px 14px; border-left: 3px solid #e8a33d;
              background: #fbf4e6; border-radius: 0 8px 8px 0; font-size: 13.5px; line-height: 1.65; }}
  .chips {{ display: flex; gap: 10px; margin-top: 14px; flex-wrap: wrap; }}
  .chip {{ flex: 1; min-width: 120px; background: #f7f1e3; border: 1px solid #ece1cb;
           border-radius: 10px; padding: 9px 12px; display: flex; align-items: baseline; gap: 8px; }}
  .chip b {{ font-size: 18px; font-variant-numeric: tabular-nums; }}
  .chip i {{ font-style: normal; font-size: 11.5px; color: #8a7860; }}
  .preview {{ margin-top: 10px; font-size: 12px; color: #8a6a3a; }}
  .rankbox {{ background: #fffdf7; border: 1px solid #e6dcc8; border-radius: 14px;
              padding: 18px 24px 10px; margin-bottom: 18px; }}
  .sect {{ font-size: 14px; font-weight: 700; margin: 0 0 12px; color: #4a3d2c; }}
  .sect small {{ font-weight: 400; color: #a08a63; margin-left: 8px; font-size: 11.5px; }}
  .rank {{ display: flex; align-items: center; gap: 10px; padding: 7px 0; }}
  .rno {{ width: 20px; font-size: 12px; color: #b0a08a; }}
  .rname {{ width: 210px; font-size: 13.5px; font-weight: 600; }}
  .rname i {{ font-style: normal; font-size: 10.5px; color: #b0a08a; margin-left: 4px; font-weight: 400; }}
  .rhold {{ font-size: 10px; background: #e8a33d; color: #fff; border-radius: 4px;
            padding: 0 4px; margin-left: 5px; vertical-align: 1px; }}
  .rw {{ font-style: normal; font-size: 11px; color: #9a8a70; margin-left: 6px; font-weight: 500; }}
  .rbar {{ flex: 1; }}
  .rval {{ width: 44px; text-align: right; font-weight: 700; font-size: 14px;
          font-variant-numeric: tabular-nums; }}
  .rtag {{ width: 46px; text-align: center; font-size: 11px; padding: 2px 0;
          border: 1px solid; border-radius: 20px; opacity: .9; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(460px, 1fr)); gap: 16px; }}
  .card {{ background: #fffdf7; border: 1px solid #e6dcc8; border-radius: 14px; padding: 18px 20px 16px; }}
  .card-head {{ display: flex; justify-content: space-between; align-items: flex-start; }}
  .cname {{ font-size: 16px; font-weight: 800; }}
  .cfund {{ font-size: 12px; font-weight: 600; color: #a08a63; margin-left: 4px; }}
  .held {{ font-size: 10px; background: #e8a33d; color: #fff; border-radius: 4px;
          padding: 1px 5px; margin-left: 6px; vertical-align: 2px; }}
  .ccode {{ font-size: 11px; color: #a08a63; margin-top: 3px; }}
  .tempbox {{ display: flex; align-items: baseline; gap: 2px; }}
  .tnum {{ font-size: 32px; font-weight: 800; font-variant-numeric: tabular-nums; line-height: 1; }}
  .tunit {{ font-size: 12px; color: #a08a63; }}
  .card-bar {{ margin: 12px 0 14px; }}
  .track {{ background: #ece3d2; border-radius: 6px; overflow: hidden; height: 5px; }}
  .fill {{ height: 100%; border-radius: 6px; }}
  .kv {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px 6px; margin-bottom: 12px; }}
  .kv div {{ display: flex; flex-direction: column; gap: 2px; }}
  .kv b {{ font-size: 13px; font-weight: 700; font-variant-numeric: tabular-nums; }}
  .kv i {{ font-size: 10.5px; color: #a08a63; font-style: normal; }}
  .advice {{ padding: 8px 10px; border-radius: 8px; font-size: 12px; color: #5c4a33; }}
  .chartwrap {{ margin-top: 12px; }}
  .chart {{ width: 100%; height: auto; display: block; border: 1px solid #eee3cf;
            border-radius: 8px; background: #fff; }}
  .chartwrap.muted {{ font-size: 11px; color: #b0a08a; padding: 10px 2px; }}
  .foot {{ margin-top: 18px; background: #fffdf7; border: 1px solid #e6dcc8; border-radius: 14px;
          padding: 16px 22px; font-size: 11.5px; color: #8a7860; line-height: 1.95; }}
  .foot b {{ color: #5c4a33; }}
  @media (max-width: 560px) {{
    .grid {{ grid-template-columns: 1fr; }}
    .kv {{ grid-template-columns: repeat(2, 1fr); }}
    h1 {{ font-size: 22px; }}
    .rname {{ width: 132px; }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <div class="head">
    <div class="eyebrow">V4 · RSI14 单指标红利策略</div>
    <h1>RSI14 择时看板</h1>
    <div class="meta">
      数据截至 <b>{d['as_of']}</b> · 生成于 {d['today']} · 池 {len(funds)} 只（V4 专用池）<br>
      当前生效信号 <b>{d['signal_date']}</b> 收盘 → 持有期 <b>{d['hold_month']}</b> ·
      RSI14（基金累计净值，Wilder）· 月末算、下月生效 · 单边 0.03%<br>
      规则：板块 RSI 均值 ≥{int(V4.Q_OUT * 100)} 清仓 / ≥{int(V4.Q_HALF * 100)} 减半 / 否则满仓；
      横截面取 RSI 最低 {V4.TOP_K} 只等权
    </div>
    <div class="oneline"><b>一句话</b>：{oneline}</div>
    <div class="chips">{chips}</div>
    <div class="preview">{preview_txt}</div>
  </div>

  <div class="rankbox">
    <div class="sect">RSI 排位<small>越低越超卖 · 「持」= 当前持有 · 灰字为当前权重 · 档位是单只参考（真实仓位看板块均值）</small></div>
    {rank_rows}
  </div>

  <div class="grid">{cards}</div>

  <div class="foot">
    <b>规则</b>：时间维度按<b>板块 RSI 均值</b>判定档位（≥{int(V4.Q_OUT * 100)} 清仓 / ≥{int(V4.Q_HALF * 100)} 减半），
    横截面每月取 <b>RSI 最低 {V4.TOP_K} 只</b>等权；月末收盘算、下月首个交易日调仓。<br>
    <b>图口径</b>：每张图左轴为基金<b>累计净值</b>（含分红再投），右轴为<b>RSI14</b>；
    背景三档（<span style="color:#5c8a4a">满仓 RSI&lt;50</span> /
    <span style="color:#b8860b">减半 50–70</span> / <span style="color:#c0504d">清仓 ≥70</span>）
    是把<b>单只自己的 RSI</b>套用同一组阈值，仅作读图参考 —— 真实仓位以板块均值为准。<br>
    <b>已知失效模式</b>：强单边趋势里 RSI 会钝化，红利慢牛中 RSI 长期在 50–70，
    按阈值减半会持续错配、长期半仓；这是结构性代价，不是参数没调好。<br>
    仅为个人数据记录与研究分析，不构成投资建议；市场有风险，投资需谨慎。
  </div>
</div>
</body>
</html>
"""


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    with open(OUT_JSON, encoding="utf-8") as fh:
        d = json.load(fh)
    html = render(d)
    if "{{" in html:
        raise RuntimeError("模板占位符未渲染完")
    os.makedirs(os.path.dirname(OUT_HTML), exist_ok=True)
    with open(OUT_HTML, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"[v4:html] 看板 -> {os.path.relpath(OUT_HTML, _DIR)}"
          f"（{len(d['pool'])} 张卡片 · 截止 {d['as_of']}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
