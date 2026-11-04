# -*- coding: utf-8 -*-
"""技术指标家族 · V17（趋势四象限闸门）· 每日仓位档位。

流程：
1. **先刷新家族收盘价**（`data._refresh_data.ensure_fresh`，54 只场内 ETF），失败沿用本地并标注；
   加 `--no-refresh` 可跳过联网（用本地缓存，报告会标「未刷新」）；
2. 对**池内全部 54 只场内 ETF** 逐只算两个数：`close` 与 `MA250` 的关系、`MA250` 近 20 日斜率，
   得到四象限与目标仓位（满仓 1.0 / 半仓 0.5 / 观察 0.2）；
3. 输出：今日调仓（次日生效）+ 各标的档位表 + 减仓标的「还缺什么」+ 最近 120 个交易日动作流水
   + 全池横截面象限分布。

成交提醒：信号当日收盘确定，赚次日收益（家族当日收盘成交惯例，场内收盘价即可，无 T+1 净值时滞）。

产物（相对本文件）：daily/_v17_trend_gate_daily.md / _v17_trend_gate_daily.json / _v17_trend_gate_daily.html。
机械规则输出，非投资建议。
"""
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V17 = os.path.dirname(_HERE)
_PARENT = os.path.dirname(_V17)
sys.path.insert(0, _V17)

import trend_gate as tg  # noqa: E402

OUT_MD = os.path.join(_HERE, "_v17_trend_gate_daily.md")
OUT_JSON = os.path.join(_HERE, "_v17_trend_gate_daily.json")
OUT_HTML = os.path.join(_HERE, "_v17_trend_gate_daily.html")
RECENT = 120

LEVEL = tg.LEVEL_TXT


def level_name(x):
    return LEVEL.get(round(float(x), 6), f"{float(x):.0%}")


def lack(x):
    """「还缺什么才给加仓许可」—— 只对需要加仓的标的意义。"""
    if x["state"] >= tg.FULL - 1e-9:
        return "已满仓"
    gaps = []
    if not x["above"]:
        gaps.append(f"close 需站上年线({x['ma']:.4f}, 现距 {x['dev']:+.1%})")
    if not x["slope_up"]:
        gaps.append("年线需转为上行(现 20 日斜率向下)")
    if x["quad"] == tg.QUADS[2]:
        gaps.append(f"高位回调第 {x['below_days']} 日")
    if x["quad"] == tg.QUADS[3]:
        gaps.append(f"下跌趋势第 {x['below_days']} 日")
    return " + ".join(gaps) if gaps else "两条件已齐, 等下一次调仓窗口"


def lack_short(x):
    """简短版「还缺什么」（详细数值已在其余列与 JSON 里，避免表格单元格过长）。"""
    if x["state"] >= tg.FULL - 1e-9:
        return "—"
    gaps = []
    if not x["above"]:
        gaps.append("站上年线")
    if not x["slope_up"]:
        gaps.append("年线转上")
    if x["quad"] == tg.QUADS[2]:
        gaps.append("高位回调")
    if x["quad"] == tg.QUADS[3]:
        gaps.append("下跌趋势")
    return " + ".join(gaps) if gaps else "等下一次调仓窗口"


def build_html(rows, buys, sells, quad_cnt, flow, dates_desc, as_of, refreshed):
    """生成自包含 HTML 看板（无外部依赖，离线可用）。"""
    for x in rows:
        x["lack_short"] = lack_short(x)
    data = {
        "as_of": as_of,
        "refreshed": not refreshed,
        "n": len(rows),
        "buys": [x["name"] for x in buys],
        "sells": [x["name"] for x in sells],
        "quads": quad_cnt,
        "rows": rows,
        "flow": [{"date": d, "buy": flow[d]["加仓"], "sell": flow[d]["减仓"]}
                 for d in dates_desc],
        "rule": {"ma": tg.MA_WIN, "slope_n": tg.SLOPE_N,
                 "reduce_days": tg.REDUCE_DAYS, "clear_days": tg.CLEAR_DAYS,
                 "action_gap": tg.ACTION_GAP,
                 "levels": {"full": tg.FULL, "half": tg.HALF, "observe": tg.OBSERVE}},
    }
    payload = json.dumps(data, ensure_ascii=False)
    html = HTML_TMPL.replace("/*__DATA__*/", payload)
    return html


HTML_TMPL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>V17 趋势四象限闸门 · 每日档位</title>
<style>
:root{
  --bg:#f7f8fa; --card:#fff; --text:#1f2329; --muted:#6b7280;
  --border:#e5e7eb; --accent:#2563eb; --shadow:0 1px 3px rgba(0,0,0,.08),0 1px 2px rgba(0,0,0,.04);
  --green:#16a34a; --green-bg:#dcfce7; --amber:#d97706; --amber-bg:#fef3c7;
  --orange:#ea580c; --orange-bg:#ffedd5; --red:#dc2626; --red-bg:#fee2e2;
}
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
  background:var(--bg);color:var(--text);line-height:1.5;padding:24px}
.wrap{max-width:1200px;margin:0 auto}
header h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--muted);font-size:13px;margin-bottom:16px}
.cards{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px 18px;
  box-shadow:var(--shadow);min-width:150px;flex:1}
.card .k{font-size:12px;color:var(--muted)}
.card .v{font-size:26px;font-weight:600;margin-top:2px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:860px){.grid{grid-template-columns:1fr}}
section{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px 18px;
  box-shadow:var(--shadow);margin-bottom:16px}
section h2{font-size:16px;margin:0 0 12px}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{background:var(--card);border:1px solid var(--border);border-radius:999px;padding:6px 12px;font-size:13px}
.chip.buy{background:var(--green-bg);color:var(--green);border-color:transparent}
.chip.sell{background:var(--red-bg);color:var(--red);border-color:transparent}
.bar-row{display:flex;align-items:center;gap:10px;margin:8px 0;font-size:13px}
.bar-row .name{width:84px;color:var(--muted)}
.bar-track{flex:1;background:var(--border);border-radius:6px;height:16px;overflow:hidden}
.bar-fill{height:100%;border-radius:6px}
.tools{display:flex;gap:8px;align-items:center;margin-bottom:10px;flex-wrap:wrap}
.tools input{padding:7px 10px;border:1px solid var(--border);border-radius:8px;flex:1;max-width:300px;font-size:13px}
.hint{color:var(--muted);font-size:12px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--border);white-space:nowrap}
th{position:sticky;top:0;background:#f1f5f9;cursor:pointer;user-select:none;z-index:1}
th:hover{color:var(--accent)}
tbody tr:hover{background:#f8fafc}
.tag{display:inline-block;padding:2px 9px;border-radius:6px;font-size:12px;font-weight:600}
.tag.up{background:var(--green-bg);color:var(--green)}
.tag.bounce{background:var(--amber-bg);color:var(--amber)}
.tag.pull{background:var(--orange-bg);color:var(--orange)}
.tag.down{background:var(--red-bg);color:var(--red)}
.lv-full{color:var(--green);font-weight:600}
.lv-half{color:var(--amber);font-weight:600}
.lv-observe{color:var(--red);font-weight:600}
.note{font-size:12.5px;color:var(--muted);margin:6px 0}
.disclaimer{color:var(--muted);font-size:12px;margin-top:20px;text-align:center}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>技术指标家族 · V17 趋势四象限闸门 · 每日档位</h1>
  <div class="sub" id="sub"></div>
</header>
<div class="cards" id="cards"></div>
<div class="grid">
  <section><h2>今日调仓（次日生效）</h2><div class="chips" id="actions"></div></section>
  <section><h2>全池横截面象限分布</h2><div id="quad"></div></section>
</div>
<section>
  <h2>各标的档位（目标仓位 = 该 ETF 露出多少）</h2>
  <div class="tools">
    <input id="q" placeholder="搜索代码 / 名称 …">
    <span class="hint">点击表头排序</span>
  </div>
  <div style="overflow:auto;max-height:560px">
    <table id="tbl"><thead><tr id="thr"></tr></thead><tbody id="tbody"></tbody></table>
  </div>
</section>
<section>
  <h2 id="flowh">近期动作流水</h2>
  <table id="flow"><thead><tr><th>日期</th><th>加仓</th><th>减仓</th></tr></thead><tbody id="flowb"></tbody></table>
</section>
<section>
  <h2>提醒</h2>
  <p class="note">· 本变种是<strong>单标的分档</strong>（满仓/半仓/观察仓），不是 0/1 择时；它压低各标的仓位、换回撤，不保证增收益。</p>
  <p class="note">· <strong>滞后是设计特性</strong>：年线确认「进入下跌趋势」时，价格通常已从高点跌掉两位数（回测实测中位约 −13%）。目标是避开以年为单位的阴跌段，不是躲三个月回调。</p>
  <p class="note">· <strong>250 与 200 不是一回事</strong>：A 股一年约 240–245 个交易日，年线取 250；美股习惯 200，勿混用。</p>
  <p class="note">· 档位变化很少（频率帽限制），长期「满仓」是常态，不是脚本坏了。</p>
</section>
<div class="disclaimer">机械规则输出，非投资建议。</div>
</div>
<script>
const DATA = /*__DATA__*/;
const QUAD_CLASS = {"上涨趋势":"up","反弹":"bounce","高位回调":"pull","下跌趋势":"down"};
const QORDER = ["上涨趋势","反弹","高位回调","下跌趋势"];
const COLS = [
  {k:"code", t:"标的", get:r=>r.code+" "+r.name, raw:r=>r.code+" "+r.name},
  {k:"cm",   t:"收盘价 / 年线", get:r=>(+r.close).toFixed(4)+" / "+(+r.ma).toFixed(4), raw:r=>+r.close},
  {k:"dev",  t:"距年线", get:r=>fmt(r.dev), raw:r=>r.dev},
  {k:"quad", t:"象限", get:r=>quadTag(r.quad), raw:r=>r.quad, html:true},
  {k:"bd",   t:"连续在年线下", get:r=>r.below_days, raw:r=>r.below_days},
  {k:"sl",   t:"年线斜率", get:r=>r.slope_up?"↑":"↓", raw:r=>r.slope},
  {k:"lv",   t:"档位（今日 → 明日）", get:r=>lvTag(r), raw:r=>r.state, html:true},
  {k:"lack", t:"还缺什么", get:r=>r.lack_short, raw:r=>r.lack_short},
];
function fmt(d){return (d>=0?"+":"")+(d*100).toFixed(1)+"%";}
function quadTag(q){const c=QUAD_CLASS[q]||"";return `<span class="tag ${c}">${q}</span>`;}
function lvTag(r){const s=+r.state;const cls=s>=0.99?"lv-full":(s>=0.49?"lv-half":"lv-observe");return `<span class="${cls}">${r.level_txt}</span>`;}
function el(id){return document.getElementById(id);}
function renderHead(){
  const tr=el("thr");tr.innerHTML="";
  COLS.forEach(c=>{const th=document.createElement("th");th.textContent=c.t;th.onclick=()=>sortBy(c.k);tr.appendChild(th);});
}
let sortState={k:null,dir:1};
function sortBy(k){
  if(sortState.k===k){sortState.dir*=-1;}else{sortState.k=k;sortState.dir=1;}
  const col=COLS.find(c=>c.k===k);
  DATA.rows.sort((a,b)=>{const x=col.raw(a),y=col.raw(b);
    if(typeof x==="string")return x.localeCompare(y,"zh")*sortState.dir;
    return (x-y)*sortState.dir;});
  renderBody();
}
function renderBody(){
  const q=(el("q").value||"").trim().toLowerCase();
  const tb=el("tbody");tb.innerHTML="";
  DATA.rows.forEach(r=>{
    if(q && !(r.code.toLowerCase().includes(q)||r.name.toLowerCase().includes(q)))return;
    const tr=document.createElement("tr");
    COLS.forEach(c=>{const td=document.createElement("td");
      if(c.html)td.innerHTML=c.get(r);else td.textContent=c.get(r);tr.appendChild(td);});
    tb.appendChild(tr);});
}
function renderCards(){
  const c=el("cards");
  const cards=[
    ["标的总数",DATA.n],["今日加仓",DATA.buys.length],["今日减仓",DATA.sells.length],
    ["下跌趋势",DATA.quads["下跌趋势"]||0]
  ];
  c.innerHTML=cards.map(([k,v])=>`<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");
}
function renderActions(){
  const a=el("actions");
  const buy=DATA.buys.length?DATA.buys.map(n=>`<span class="chip buy">${n}</span>`).join(""):'<span class="chip">—</span>';
  const sell=DATA.sells.length?DATA.sells.map(n=>`<span class="chip sell">${n}</span>`).join(""):'<span class="chip">—</span>';
  a.innerHTML=`<div style="width:100%"><span class="hint">加仓</span><br>${buy}</div>`+
              `<div style="width:100%;margin-top:8px"><span class="hint">减仓</span><br>${sell}</div>`;
}
function renderQuad(){
  const q=el("quad");
  const max=Math.max(1,...QORDER.map(k=>DATA.quads[k]||0));
  q.innerHTML=QORDER.map(k=>{
    const v=DATA.quads[k]||0;const pct=(v/max*100).toFixed(1);
    return `<div class="bar-row"><span class="name">${k}</span>`+
      `<span class="bar-track"><span class="bar-fill" style="width:${pct}%;background:var(--${(QUAD_CLASS[k]||'muted')})"></span></span>`+
      `<span style="width:28px;text-align:right">${v}</span></div>`;}).join("");
}
function renderFlow(){
  const b=el("flowb");
  if(!DATA.flow.length){b.innerHTML=`<tr><td colspan="3" class="hint">近期无动作</td></tr>`;return;}
  b.innerHTML=DATA.flow.map(f=>`<tr><td>${f.date}</td><td>${f.buy.join("、")||"—"}</td><td>${f.sell.join("、")||"—"}</td></tr>`).join("");
}
function render(){
  const rf=DATA.refreshed?"":" · 未刷新";
  el("sub").textContent=`净值日 ${DATA.as_of||"—"} · 池 = 家族 54 只场内 ETF（收盘价口径）${rf} · 机械规则输出，非投资建议`;
  renderHead();renderCards();renderActions();renderQuad();renderFlow();renderBody();
  el("q").addEventListener("input",renderBody);
}
render();
</script>
</body>
</html>
"""


def main():
    refreshed = "--no-refresh" not in sys.argv
    try:
        panel = tg.vdata.panel(refresh=refreshed)
    except Exception as e:  # noqa: BLE001
        print(f"[行情] 装载失败：{type(e).__name__}: {e}")
        panel = {}
    if panel:
        as_of = str(max(p.index[-1] for p in panel.values()).date())
    else:
        as_of = ""
    rows, flow = [], {}
    for code in tg.LEGS:
        if code not in panel:
            continue
        px = panel[code]
        sm = tg.state_machine(px, code)
        r = sm.iloc[-1]
        if not bool(r.get("ready", False)):
            continue
        close = float(r["close"])
        state = float(r["state"])
        quad = str(r["quad"])
        item = dict(
            code=code, name=tg.NAMES.get(code, code),
            date=px.index[-1].date().isoformat(), close=round(close, 4),
            ma=float(r["ma"]), slope=float(r["slope"]), slope_up=bool(r["slope"] > 0),
            above=bool(r["close"] >= r["ma"]), dev=close / float(r["ma"]) - 1.0,
            quad=quad, below_days=int(r["below_days"]),
            dd_hi=float(r["dd_hi"]),
            state=state, level=level_name(state),
            decide=str(r["decide"]), done=str(r["done"]),
            target=(None if r["target"] != r["target"] else float(r["target"])),
        )
        item["action"] = {"buy": "加仓", "sell": "减仓"}.get(item["decide"], "维持")
        item["lack"] = lack(item)
        item["lack_short"] = lack_short(item)
        nxt = item["target"] if item["decide"] else state
        item["next_level"] = level_name(nxt)
        item["level_txt"] = level_name(state) + (
            f" → {level_name(nxt)}" if item["decide"] else "")
        rows.append(item)

        dec = sm["decide"].to_numpy()
        for i in range(max(1, len(dec) - RECENT), len(dec)):
            if dec[i] in ("buy", "sell"):
                d = px.index[i].date().isoformat()
                flow.setdefault(d, {"加仓": [], "减仓": []})[
                    "加仓" if dec[i] == "buy" else "减仓"].append(item["name"])

    buys = [x for x in rows if x["decide"] == "buy"]
    sells = [x for x in rows if x["decide"] == "sell"]
    dates_desc = sorted(flow, reverse=True)
    quad_cnt = {q: sum(1 for x in rows if x["quad"] == q) for q in tg.QUADS}

    L = [
        f"# 技术指标家族 · V17（趋势四象限闸门）· 每日档位（{as_of or (rows[0]['date'] if rows else '无数据')}）",
        f"> 池 = 家族 54 只场内 ETF（`_pool.py` / `data/_universe.md` 前四类），"
        "口径 = 家族共享 `data/_long_klines.json` **收盘价**。",
        f"> 判断趋势只看两个数：`close` 在 **MA{tg.MA_WIN}** 上/下、MA{tg.MA_WIN} 近 "
        f"**{tg.SLOPE_N} 日**朝上/朝下；两个都朝上才是上涨趋势，其余三格都不给加仓许可。",
        f"> 档位：上涨趋势 → 满仓；反弹 → 只持有；高位回调 → 降一档（半仓）；下跌趋势 → "
        f"满 {tg.REDUCE_DAYS} 日减半、满 {tg.CLEAR_DAYS} 日清至观察仓。"
        f"同一标的两次调仓间隔 ≥{tg.ACTION_GAP} 个交易日（频率帽）。",
        f"> 这是从 `vol_legs/v5_trend_gate` 照搬的规则，数据源换成 54 只场内 ETF 收盘价；"
        f"带内占比直接作为该 ETF 目标仓位（非 0/1）。减仓/空仓部分按无风险利率 "
        f"{tg.CASH_ANNUAL:.0%} 计息；未计佣金/滑点。",
        f"> 全池 {len(rows)} 只。机械规则输出，**非投资建议**。",
        "",
        "## 今日调仓（次日生效）",
        "",
        "| 加仓 | 减仓 |",
        "|---|---|",
        f"| {('、'.join(x['name'] for x in buys) or '—')} | "
        f"{('、'.join(x['name'] for x in sells) or '—')} |",
        "",
        f"## 全池横截面象限分布（{as_of or ''}）",
        "",
        "| 象限 | 标的数 |",
        "|---|---:|",
    ]
    for q in tg.QUADS:
        L.append(f"| {q} | {quad_cnt[q]} |")
    L += [
        "",
        "> 「下跌趋势」占比高 = 多数 ETF 在年线下且均线朝下，是系统性减仓信号；"
        "「上涨趋势」占比高 = 系统性满仓信号。",
        "",
        f"## 各标的档位（目标仓位 = 该 ETF 露出多少 · 净值日 {as_of or ''}）",
        "",
        "| 标的 | 收盘价 / 年线 | 距年线 | 象限 | 连续在年线下 | 年线斜率 | "
        "档位（今日 → 明日） | 还缺什么 |",
        "|---|---|---:|---|---:|---|---|---|",
    ]
    for x in sorted(rows, key=lambda z: (z["quad"] != tg.QUADS[3], z["name"])):
        L.append(
            f"| {x['code']} {x['name']} | {x['close']:.4f} / {x['ma']:.4f} | "
            f"{x['dev']:+.1%} | {x['quad']} | {x['below_days']} | "
            f"{'↑' if x['slope_up'] else '↓'} | {x['level_txt']} | {lack_short(x)} |")

    L += [
        "",
        f"## 近期动作流水（最近 {RECENT} 个交易日 · 仅列有动作的日期）",
        "",
        "| 日期 | 加仓 | 减仓 |",
        "|---|---|---|",
    ]
    if dates_desc:
        for d in dates_desc:
            b = "、".join(flow[d]["加仓"]) or "—"
            s = "、".join(flow[d]["减仓"]) or "—"
            L.append(f"| {d} | {b} | {s} |")
    else:
        L.append(f"| — | 最近 {RECENT} 个交易日内全池无动作 | — |")

    L += [
        "",
        "## 提醒",
        "",
        "- 本变种是**单标的分档**（满仓/半仓/观察仓），不是 0/1 择时；它压低各标的仓位，"
        "换回撤，不保证增收益。",
        "- **滞后是设计特性**：年线确认「进入下跌趋势」时，价格通常已从高点跌掉两位数"
        "（回测实测中位约 −13%）。目标是避开以年为单位的阴跌段，不是躲三个月的回调。",
        "- **250 与 200 不是一回事**：A 股一年约 240–245 个交易日，年线取 250；美股习惯 200，勿混用。",
        "- 档位变化很少（频率帽限制），长期「满仓」是常态，不是脚本坏了。",
        "",
        "> 机械规则输出，**非投资建议**。",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({
            "strategy": tg.LABEL, "as_of": as_of,
            "pool": list(tg.LEGS), "gate_all": tg.GATE_ALL,
            "rule": {"ma": tg.MA_WIN, "slope_n": tg.SLOPE_N,
                     "reduce_days": tg.REDUCE_DAYS, "clear_days": tg.CLEAR_DAYS,
                     "action_gap": tg.ACTION_GAP,
                     "levels": {"full": tg.FULL, "half": tg.HALF, "observe": tg.OBSERVE},
                     "cash_annual": tg.CASH_ANNUAL},
            "summary": {"n": len(rows), "buy": len(buys), "sell": len(sells),
                        "quads": quad_cnt},
            "etfs": rows,
            "recent_flow": [{"date": d, "buy": flow[d]["加仓"], "sell": flow[d]["减仓"]}
                            for d in dates_desc],
        }, f, ensure_ascii=False, indent=1)

    html = build_html(rows, buys, sells, quad_cnt, flow, dates_desc, as_of, refreshed)
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)

    print("报告:", OUT_MD)
    print("JSON:", OUT_JSON)
    print("HTML:", OUT_HTML)
    print(f"净值日 {as_of or (rows[0]['date'] if rows else '—')} | 加仓 "
          f"{[(x['code'], x['name']) for x in buys]} · 减仓 {[(x['code'], x['name']) for x in sells]}")
    for x in sorted(rows, key=lambda z: z["quad"]):
        print(f"  {x['code']:8s} {x['name']:10s} {x['quad']:5s} "
              f"档位={x['level']:3s}({x['state']:.0%}) {x['action']} · {x['lack']}")


if __name__ == "__main__":
    main()
