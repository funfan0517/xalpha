# -*- coding: utf-8 -*-
"""波动资产 · V2 变种（资金渗透率 / 份额拥挤度）· HTML 展示（单文件看板）。

产物：`daily/_v2_flow_dashboard.html`（单文件，数据内联，除 Chart.js CDN 外可离线打开）。

版式：
1. 顶部操作看板 —— 六腿卡片：当前档位（满带 / 半带）、拥挤度、季度净申购率、目标仓位 pp；
2. 六腿图 —— Chart.js 双轴：左轴**拥挤度分位**(%) 带 90/30 阈值线，右轴该腿**场外复权净值**；
   A 股腿额外一条**渗透率**（头部 ETF 市值 ÷ 指数流通市值，默认隐藏，点图例可开）；
3. 明细表 + 口径说明（口径、已知误差、数据源、asof 都写在页面上，不在别处）。

颜色沿用 `head_fund_ratio_rules.md` R-19：占比/主指标 `#7f5af0`、价格 `#ef8f3c`；
拥挤区用 `#e05252` 系、冷清区用 `#2f9e6f` 系。浅色主题（R-20）。
"""
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_VL = os.path.dirname(_HERE)
for _p in (_HERE, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import flowdata          # noqa: E402
import flowrule          # noqa: E402

OUT = os.path.join(_HERE, "daily", "_v2_flow_dashboard.html")
STEP = 5                 # 抽稀：每 5 个交易日一个点（季报阶跃型指标足够）


def _picks(axis, step=STEP):
    """统一抽稀基准：净值轴上的位置样本（保证所有序列共用同一 labels）。"""
    n = len(axis)
    idx = list(range(0, n, step))
    if idx[-1] != n - 1:
        idx.append(n - 1)
    return idx


def _aligned(series, axis, idx, scale=1.0):
    """把序列对齐到抽稀后的净值日期；缺失 -> None（Chart.js 断线，不连点）。"""
    if series is None or not len(series):
        return [None] * len(idx)
    re = series.reindex(axis.index[idx])
    return [None if v != v else round(float(v) * scale, 4) for v in re.to_numpy()]


def leg_payload(p):
    """单腿图表数据 + 卡片数据（所有序列共用 labels = 抽稀后的净值日）。"""
    full = p["axis"]
    start = p["g"].first_valid_index() if len(p["g"]) else None
    axis = full.loc[start:] if start is not None else full     # 图表从信号成形日开始
    idx = _picks(axis)
    labels = [axis.index[i].date().isoformat() for i in idx]
    nav_v = [round(float(axis.iloc[i]), 4) for i in idx]
    c_v = _aligned(p["crowd"] if len(p["crowd"]) else None, axis, idx, 100.0)
    g_v = _aligned(p["g"] if len(p["g"]) else None, axis, idx, 100.0)
    ratio = p["ratio"]
    r_v = _aligned(ratio["ratio_pct"] if ratio is not None else None, axis, idx)
    return dict(
        leg=p["leg"], name=p["name"], role=p["role"], band=float(p["band"]),
        exec_code=p["exec"], exec_name=p["exec_name"],
        head=[f"{c} {p['head_names'].get(c, c)}" for c in p["head"]],
        last_anchor=p["last_anchor"],
        nav_date=axis.index[-1].date().isoformat(), nav=float(axis.iloc[-1]),
        pos=float(p["pos"].iloc[-1]), target_pp=float(p["band"]) * float(p["pos"].iloc[-1]),
        crowd=(None if not len(p["crowd"]) or p["crowd"].iloc[-1] != p["crowd"].iloc[-1]
               else float(p["crowd"].iloc[-1])),
        g_now=(None if not len(p["g"]) or p["g"].iloc[-1] != p["g"].iloc[-1]
               else float(p["g"].iloc[-1])),
        ratio_now=(None if ratio is None or not len(ratio) else float(ratio["ratio_pct"].iloc[-1])),
        shares_now=(float(p["shares"].iloc[-1].sum()) if len(p["shares"]) else None),
        action=str(p["action"].iloc[-1]) if len(p["action"]) else "不执行",
        labels=labels, nav_series=nav_v, crowd_series=c_v,
        g_series=g_v, ratio_series=r_v,
    )


TPL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>波动资产 · V2 资金渗透率闸门 · __ASOF__</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{
  --bg:#f6f7fb; --card:#ffffff; --line:#e6e8f0; --ink:#1f2233; --ink2:#5b6076;
  --main:#7f5af0; --price:#ef8f3c; --hot:#e05252; --cold:#2f9e6f;
}
*{box-sizing:border-box}
body{margin:0;padding:28px 20px 48px;background:var(--bg);color:var(--ink);
  font:14px/1.6 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1240px;margin:0 auto}
h1{font-size:22px;margin:0 0 6px}
h2{font-size:16px;margin:34px 0 12px;padding-left:10px;border-left:4px solid var(--main)}
.sub{color:var(--ink2);font-size:13px;margin:0 0 4px}
.tag{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;font-weight:600}
.tag.full{background:#eaf7f0;color:var(--cold)}
.tag.half{background:#fdecec;color:var(--hot)}
.tag.none{background:#eef0f6;color:var(--ink2)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;margin-top:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.card .nm{font-weight:700;font-size:15px}
.card .rl{color:var(--ink2);font-size:12px}
.card .big{font-size:22px;font-weight:700;margin:6px 0 2px}
.card .kv{color:var(--ink2);font-size:12px}
.bar{height:6px;border-radius:4px;background:#eef0f6;margin:8px 0 6px;overflow:hidden}
.bar > i{display:block;height:100%;border-radius:4px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(370px,1fr));gap:16px}
.chartbox{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px 6px}
.chartbox .hd{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px}
.chartbox .hd b{font-size:14px}
.chartbox .hd span{color:var(--ink2);font-size:12px}
canvas{width:100%!important;height:220px!important}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);
  border-radius:12px;overflow:hidden;font-size:13px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:right}
th{background:#fafbff;font-weight:600;color:var(--ink2)}
th:first-child,td:first-child{text-align:left}
tr:last-child td{border-bottom:none}
.note{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 18px;
  color:var(--ink2);font-size:13px}
.note b{color:var(--ink)}
.note ul{margin:8px 0 0 18px;padding:0}
.note li{margin:3px 0}
footer{margin-top:28px;color:#9aa0b4;font-size:12px;text-align:center}
</style>
</head>
<body>
<div class="wrap">
  <h1>波动资产 · V2 变种：资金渗透率（份额拥挤度）闸门</h1>
  <p class="sub">数据截至 <b>__ASOF__</b>（份额季报 __ANCHOR__）· 六腿合计目标 <b>__TOTAL__ / __BAND__pp</b>
    · T 日盘后算、T+1 净值成交</p>
  <p class="sub">信号 = 头部 ETF 合计份额的<b>季度净申购率</b> → 近 3 年<b>分位</b>（拥挤度）。
    拥挤度 ≥ 90% → 半带；≤ 30% → 满带；中间维持。带宽沿用主策略 v1.3，本变种只做减法。</p>

  <h2>一、今日操作看板</h2>
  <div class="cards" id="cards"></div>

  <h2>二、拥挤度 × 净值（左轴拥挤度分位，右轴该腿复权净值）</h2>
  <div class="grid" id="charts"></div>

  <h2>三、明细</h2>
  <table id="tbl">
    <thead><tr>
      <th>腿</th><th>角色</th><th>目标带</th><th>档位</th><th>目标仓位</th><th>拥挤度</th>
      <th>净申购率(季)</th><th>头部ETF合计份额</th><th>渗透率</th><th>执行代码</th>
      <th>最新季报</th><th>净值日</th>
    </tr></thead>
    <tbody></tbody>
  </table>

  <h2>四、口径与已知误差</h2>
  <div class="note">
    <b>指标口径</b>
    <ul>
      <li><b>拥挤度</b> = 该腿头部 ETF「合计份额季度净申购率」在过去 750 个交易日中的分位。
        季度净申购率 = 63 个交易日的份额加权增长率（权重 = 各头部基金当前份额占比）。</li>
      <li><b>渗透率</b> = 头部 N 只 ETF 市值 ÷ 指数流通市值 × 100%，只在 A 股腿（中证A500 / 科创50）
        可算；美股 / 港股 / 黄金没有标的指数流通市值口径，只算分子（合计份额）。</li>
      <li><b>头部</b> = 候选池内按最新季报净资产降序前 N（A500/纳指/科创50/恒生科技/黄金 N=4，
        标普500 因境内只有 3 只跟踪产品故 N=3）。</li>
    </ul>
    <b>已知误差（不隐藏）</b>
    <ul>
      <li><b>份额是季报口径</b>（东财 gmbd 期末总份额），期内线性内插；
        锚点之前不产出，不向前填充 —— 否则会伪造「0 增长」。</li>
      <li><b>份额只到最近季报</b>，季内真实申购赎回要等下一期季报才进信号（约 1 个季度时滞）。
        本图默认不用「腾讯实时市值/价格」的实时份额锚（会破坏可复现性，见 M-03）。</li>
      <li><b>流通市值是价格等比代理</b>（今日流通市值 × 指数收盘/今日收盘），忽略成分调整、
        增发、解禁，绝对值 ±5~15%，趋势可靠。</li>
      <li><b>A500 的份额史仅 2024-12 起</b>（4 只头部 ETF 2024-10 才上市），此前该腿按
        「不执行」保持满带。</li>
      <li>未计 C 类销售服务费（0.2%/年）、大额限购、QDII 额度关闭顺延、赎回确认时滞。</li>
    </ul>
    <b>数据源</b>：东财 gmbd（季报份额/净资产）· 新浪日 K（指数与 ETF 收盘）· 腾讯行情（ETF 名称/市值）·
    东财 push2（指数流通市值，限流时用本地快照）。缓存：<code>data/_shares_cache.json</code>。
  </div>

  <footer>机械规则输出，非投资建议 · 回测与结论见 backtest/_v2_flow_bt.md</footer>
</div>
<script>
const DATA = __DATA__;

const COL = {main:'#7f5af0', price:'#ef8f3c', hot:'#e05252', cold:'#2f9e6f', ink2:'#5b6076'};
const pct = v => (v === null || v === undefined) ? '—' : (v >= 0 ? '+' : '') + (v*100).toFixed(2) + '%';

function cardHtml(d){
  const c = d.crowd;
  const cls = d.pos < 0.75 ? 'half' : 'full';
  const tag = d.action === '减半' ? '今日降档' : (d.action === '恢复' ? '今日回档' : '维持');
  const col = c === null ? COL.ink2 : (c >= 0.9 ? COL.hot : (c <= 0.3 ? COL.cold : COL.main));
  const w = c === null ? 0 : Math.max(2, Math.min(100, c*100));
  return `<div class="card">
    <div class="nm">${d.name} <span class="tag ${cls}">${(d.pos*100).toFixed(0)}% 带</span></div>
    <div class="rl">${d.role} · 目标带 ${d.band}pp · ${tag}</div>
    <div class="big" style="color:${col}">${c === null ? '—' : (c*100).toFixed(0) + '%'}</div>
    <div class="bar"><i style="width:${w}%;background:${col}"></i></div>
    <div class="kv">拥挤度分位 · 净申购率(季) ${pct(d.g_now)}</div>
    <div class="kv">目标仓位 <b style="color:#1f2233">${d.target_pp}pp</b>（带 ${d.band}pp）</div>
    <div class="kv">份额 ${d.shares_now === null ? '—' : d.shares_now.toFixed(0) + ' 亿份'}
      ${d.ratio_now === null ? '' : ' · 渗透率 ' + d.ratio_now.toFixed(3) + '%'}</div>
  </div>`;
}

function chartHtml(d, i){
  return `<div class="chartbox">
    <div class="hd"><b>${d.name}</b>
      <span>${d.role} · 带 ${d.band}pp · 当前 ${(d.pos*100).toFixed(0)}%</span></div>
    <div class="hd"><span>头部：${d.head.join('、')}</span><span>净值日 ${d.nav_date}</span></div>
    <canvas id="c${i}"></canvas>
  </div>`;
}

document.getElementById('cards').innerHTML = DATA.legs.map(cardHtml).join('');
document.getElementById('charts').innerHTML = DATA.legs.map(chartHtml).join('');
document.querySelector('#tbl tbody').innerHTML = DATA.legs.map(d => `<tr>
  <td>${d.name}</td><td>${d.role}</td><td>${d.band}pp</td>
  <td>${(d.pos*100).toFixed(0)}%</td><td>${d.target_pp}pp</td>
  <td>${d.crowd === null ? '—' : (d.crowd*100).toFixed(0) + '%'}</td>
  <td>${pct(d.g_now)}</td>
  <td>${d.shares_now === null ? '—' : d.shares_now.toFixed(0) + ' 亿份'}</td>
  <td>${d.ratio_now === null ? '—' : d.ratio_now.toFixed(3) + '%'}</td>
  <td>${d.exec_code}</td><td>${d.last_anchor}</td><td>${d.nav_date}</td></tr>`).join('');

DATA.legs.forEach((d, i) => {
  const sets = [];
  if (d.crowd_series.length){
    sets.push({label:'拥挤度分位 %', data:d.crowd_series, yAxisID:'y', borderColor:COL.main,
      backgroundColor:'rgba(127,90,240,.08)', borderWidth:2, pointRadius:0, tension:.15, fill:true});
    sets.push({label:'拥挤线 90%', data:d.labels.map(()=>90), yAxisID:'y', borderColor:COL.hot,
      borderWidth:1, borderDash:[5,4], pointRadius:0, fill:false});
    sets.push({label:'冷清线 30%', data:d.labels.map(()=>30), yAxisID:'y', borderColor:COL.cold,
      borderWidth:1, borderDash:[5,4], pointRadius:0, fill:false});
  }
  sets.push({label:'复权净值', data:d.nav_series, yAxisID:'y1', borderColor:COL.price,
    borderWidth:1.8, pointRadius:0, tension:.1});
  if (d.ratio_series.some(v => v !== null)){
    sets.push({label:'渗透率 %（独立轴，默认隐藏）', data:d.ratio_series, yAxisID:'y2',
      borderColor:'#12b5b0', borderWidth:1.6, pointRadius:0, borderDash:[3,3], hidden:true});
  }
  new Chart(document.getElementById('c'+i), {
    type:'line',
    data:{labels:d.labels, datasets:sets},
    options:{
      responsive:true, maintainAspectRatio:false, animation:false,
      interaction:{mode:'index', intersect:false},
      plugins:{legend:{position:'bottom', labels:{boxWidth:12, font:{size:11}}},
        tooltip:{callbacks:{label:ctx => ctx.dataset.label + ': ' +
          (ctx.parsed.y === null ? '—' : ctx.parsed.y.toFixed(2))}}},
      scales:{
        y:{position:'left', title:{display:true, text:'拥挤度分位 %', font:{size:10}},
           min:0, max:100, grid:{color:'#eef0f6'}, ticks:{font:{size:10}}},
        y1:{position:'right', title:{display:true, text:'复权净值', font:{size:10}},
           grid:{display:false}, ticks:{font:{size:10}}},
        y2:{display:false, position:'left'},
        x:{grid:{display:false}, ticks:{font:{size:10}, maxTicksLimit:7, maxRotation:0}}
      }
    }
  });
});
</script>
</body>
</html>
"""


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    ps = flowrule.panels()
    legs = [leg_payload(ps[k]) for k in flowrule.LEGS]
    asof = max(x["nav_date"] for x in legs)
    anchor = min(x["last_anchor"] for x in legs)
    total = sum(x["target_pp"] for x in legs)
    band = sum(x["band"] for x in legs)
    html = (TPL.replace("__ASOF__", asof)
               .replace("__ANCHOR__", anchor)
               .replace("__TOTAL__", f"{total:g}")
               .replace("__BAND__", f"{band:g}")
               .replace("__DATA__", json.dumps(dict(as_of=asof, legs=legs),
                                               ensure_ascii=False)))
    assert "{{" not in html, "模板占位符未替换干净"
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print("看板:", OUT)
    print(f"as_of {asof} · 六腿合计目标 {total:g}/{band:g}pp · "
          f"半带 {sum(1 for x in legs if x['pos'] < 0.75)} 条")


if __name__ == "__main__":
    main()
