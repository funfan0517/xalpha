# -*- coding: utf-8 -*-
"""右侧买入策略 · 每日扫描报告（Markdown + HTML 看板）。

输入: strategies/tech_indicators/v9_right_side/daily/_right_side_scan_out.jsonl
输出: strategies/tech_indicators/v9_right_side/daily/_right_side_report.md       （文本版，供 pipeline / diff）
      strategies/tech_indicators/v9_right_side/daily/_right_side_dashboard.html   （**自包含**看板：无 CDN、无外链，
                                                               双击即开，可离线/发给别人）

分工：Python 只出数据（payload → JSON），排序/筛选/渲染交给内联 JS/CSS（见 AGENTS §5）。
用法: python strategies/tech_indicators/v9_right_side/_scan_report.py
"""
import json
import os
import sys
from datetime import datetime

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_DIR)))
for p in (_ROOT, os.path.dirname(_DIR), _DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import rule  # noqa: E402

IN = os.path.join(_DIR, "daily", "_right_side_scan_out.jsonl")
MD = os.path.join(_DIR, "daily", "_right_side_report.md")
HTML = os.path.join(_DIR, "daily", "_right_side_dashboard.html")
BT = os.path.join(_DIR, "backtest", "_right_side_bt.jsonl")

CAT_ORDER = ("宽基/另类", "全球/QDII", "A股行业", "策略/商品", "主动/量化", "债券")


def pct(x, dp=1):
    return "—" if x is None else f"{x * 100:+.{dp}f}%"


def load(path):
    if not os.path.exists(path):
        sys.exit(f"缺少扫描产物 {path}; 先跑 python strategies/tech_indicators/v9_right_side/scan.py")
    raw = open(path, "rb").read()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8")
    good, bad = [], []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        r = json.loads(ln)
        (good if r.get("ok") else bad).append(r)
    return good, bad


def bt_ref():
    """回测产物 -> 中位数参照（缺失则 None）。"""
    if not os.path.exists(BT):
        return None
    rows = [json.loads(l) for l in open(BT, encoding="utf-8") if l.strip().startswith("{")]
    rows = [r for r in rows if r.get("ok")]
    if not rows:
        return None
    med = lambda k: float(np.median([r[k] for r in rows]))  # noqa: E731
    trades = [t for r in rows for t in r.get("trade_log") or []]
    wr = float(np.mean([t["ret"] > 0 for t in trades])) if trades else 0.0
    return dict(n=len(rows), ann=med("st_ann"), mdd=med("st_mdd"),
                base_ann=med("base_ann"), base_mdd=med("base_mdd"),
                pos=med("pos_ratio"), edge=med("timing_edge"), win=wr,
                hit=med("sig_ratio"))


def vmarks(r):
    return " ".join("●" if v["pass"] else "○" for v in r["votes"])


# ---------------------------------------------------------------- Markdown
def build_md(good, bad, ready, watch, veto, ref, d):
    L = [f"# 右侧买入 · 每日扫描（数据日 {d}）", "",
         f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 标的池 `data/_universe.md` 场内 "
         f"{len(good)} 只 · 参数 `{rule.LABEL}` · **机械规则输出，非投资建议**",
         f"> 五票确认：{' / '.join(rule.VOTE_NAMES[k] for k in rule.VOTE_NAMES)}",
         f"> {rule.VOTE_ENTER} 票 = 右侧买点 · {rule.VOTE_WATCH} 票 = 差一票（等确认）· "
         f"信号按**当日收盘**判定，**次日收盘**执行", ""]

    L += ["## 一、今日结论", ""]
    if ready:
        L += [f"**{len(ready)} 只达到右侧买入点（五票全过）**：", "",
              "| 场外基金 | 主题 | 场内 | 收盘 | 五票 | 结构止损 | 距止损 | 250日回撤 | 20日均额 | 分级 |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for r in ready:
            L.append(f"| {r['off_name']} | {r['theme']} | `{r['inner']}` | {r['close']:.3f} | "
                     f"{vmarks(r)} | {('%.3f' % r['stop']) if r['stop'] else '—'} | "
                     f"{pct(r['stop_dist'])} | {pct(r['dd250'])} | {r['amt20_yi']:.1f}亿 | {r['grade']} |")
        L += ["", "> 买入即挂结构止损（信号日前 "
              f"{rule.STOP_LOOKBACK} 日最低收盘）；跌破无条件离场，不补仓、不摊薄。"]
    else:
        L += ["**今日无标的五票全过 —— 空仓等待。**", "",
              f"> 这是该策略的常态：五票全过的历史占比约 1%（每只每年 ~2.5 个交易日）。"
              f"右侧买点本就稀少，硬凑买点等于抄底。当前最接近的是 "
              f"{len(watch)} 只「差一票」，见第二节。"]
    if veto:
        L += ["", f"另有 {len(veto)} 只票数达标但被硬否决：" + "、".join(
            f"`{r['inner']}`（{'追高' if r['veto_hot'] else '流动性'}）" for r in veto) + "。"]
    L.append("")

    L += ["## 二、差一票（观察名单，不提前埋伏）", ""]
    if watch:
        L += ["| 场外基金 | 主题 | 场内 | 收盘 | 五票 | 缺哪一票 | 该票实际值 | 250日回撤 |",
              "|---|---|---|---|---|---|---|---|"]
        for r in watch:
            miss = next((v for v in r["votes"] if not v["pass"]), None)
            L.append(f"| {r['off_name']} | {r['theme']} | `{r['inner']}` | {r['close']:.3f} | "
                     f"{vmarks(r)} | {('、'.join(r['missing']))} | "
                     f"{miss['detail'] if miss else '—'} | {pct(r['dd250'])} |")
        L += ["", "> 观察名单的含义是「明日若该票转正即成为买点」。**不是提前建仓的理由** —— "
                  "提前买就是左侧，本策略的全部意义在于不做这件事。"]
    else:
        L.append("（无）")
    L.append("")

    L += ["## 三、全池票数矩阵（按票数降序）", "",
          "| 场内 | 主题 | 类别 | 收盘 | " + " | ".join(rule.VOTE_SHORT[k] for k in rule.VOTE_NAMES)
          + " | 票数 | 状态 | 说明 |",
          "|---" * (4 + len(rule.VOTE_NAMES) + 3) + "|"]
    cats = [c for c in CAT_ORDER if any(r["cat"] == c for r in good)]
    cats += [c for c in dict.fromkeys(r["cat"] for r in good) if c not in cats]
    for cat in cats:
        rs = sorted([r for r in good if r["cat"] == cat], key=lambda r: (-r["score"], r["inner"]))
        for r in rs:
            L.append(f"| `{r['inner']}` | {r['theme']} | {r['cat']} | {r['close']:.3f} | "
                     + " | ".join("●" if v["pass"] else "○" for v in r["votes"])
                     + f" | {r['score']} | {r['status_cn']} | {r['action']} |")
    L.append("")
    L.append("> ● 通过 · ○ 未通过。票数分布：" + " · ".join(
        f"{n}票 {sum(1 for r in good if r['score'] == n)}只" for n in range(6)) + "。")
    L.append("")

    if ref:
        L += ["## 四、回测参照（同一套规则，全池全历史中位数）", "",
              "| 中位年化 | 中位最大回撤 | 买入持有年化 | 买入持有回撤 | 持仓占比 | 单笔胜率 | 择时边际 |",
              "|---|---|---|---|---|---|---|",
              f"| {pct(ref['ann'], 2)} | {pct(ref['mdd'], 2)} | {pct(ref['base_ann'], 2)} | "
              f"{pct(ref['base_mdd'], 2)} | {ref['pos'] * 100:.1f}% | {ref['win'] * 100:.1f}% | "
              f"{ref['edge'] * 1e4:+.1f}bp |", "",
              "> 该策略是**择时叠加层**（低暴露）：年化低于满仓买入持有是常态，"
              "换来的是回撤压缩与正的择时边际（持仓日日均收益 − 空仓日日均收益）。"
              "它回答「什么时候买」，不回答「买多少、买哪些」—— 标的横向比较请看分级名单。", ""]

    L += ["## 五、五票口径速查", "",
          "| 票 | 判据 | 在排除什么 |",
          "|---|---|---|",
          f"| V1 {rule.VOTE_NAMES['v1']} | 近 {rule.STRUCT_WIN_SHORT} 日最低收盘 > 再往前 {rule.STRUCT_WIN_LONG} 日最低收盘 × (1+{rule.STRUCT_EPS:.1%}) "
          "| 还在创新低（下跌中继） |",
          f"| V2 {rule.VOTE_NAMES['v2']} | 收盘 > 前 {rule.BREAK_WIN} 日最高收盘 | 仍在箱体/下降结构内 |",
          f"| V3 {rule.VOTE_NAMES['v3']} | 收盘 > MA{rule.MA_SHORT} 且 MA{rule.MA_SHORT} 较 {rule.MA_SLOPE} 日前抬升 "
          "| 下行均线上的反抽（最常见的假右侧） |",
          f"| V4 {rule.VOTE_NAMES['v4']} | DIF > DEA 且 DIF > 0 | 零轴下金叉（只是反弹） |",
          f"| V5 {rule.VOTE_NAMES['v5']} | 量 ≥ {rule.VOL_MULT:g}×20 日均量 且 收涨 | 缩量假突破 |",
          "",
          f"硬否决：5 日涨幅 > {rule.RET5_MAX:.0%}（追高） · 20 日均成交额 < {rule.AMT_MIN / 1e8:.1f} 亿（流动性）",
          f"离场：收盘跌破入场结构位（前 {rule.STOP_LOOKBACK} 日最低收盘） · 或连续 {rule.EXIT_CONFIRM} 日收盘 < MA{rule.EXIT_MA}",
          ""]

    if bad:
        L += ["## 六、数据异常", "", "| 场内 | 主题 | 原因 |", "|---|---|---|"]
        L.extend(f"| `{r['inner']}` | {r['theme']} | {r.get('err')} |" for r in bad)
        L.append("")

    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- HTML
CSS = """
:root{
  --bg:#f2f5f9;--card:#fff;--ink:#101828;--muted:#667085;--line:#e4e8ef;
  --ok:#0b7a45;--okbg:#e6f6ee;--okline:#bfe3d0;
  --warn:#b7791f;--warnbg:#fdf6e3;--warnline:#f0ddb7;
  --bad:#b42318;--badbg:#fdecea;--badline:#f5c9c4;
  --accent:#2f6fed;--accentbg:#eaf1fe;
  --r:14px;--shadow:0 1px 2px rgba(16,24,40,.05),0 10px 28px rgba(16,24,40,.06);
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.6 "Microsoft YaHei","PingFang SC",system-ui,-apple-system,sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:1240px;margin:0 auto;padding:26px 20px 64px}
.top{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap;margin-bottom:6px}
h1{font-size:22px;margin:0 0 4px;letter-spacing:.2px}
.sub{color:var(--muted);font-size:12.5px}
.chip{display:inline-flex;align-items:center;gap:6px;padding:4px 11px;border-radius:999px;background:var(--accentbg);color:var(--accent);font-size:12px;font-weight:600;white-space:nowrap}
.dotlive{width:7px;height:7px;border-radius:50%;background:var(--accent);display:inline-block}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(126px,1fr));gap:12px;margin:18px 0 6px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:14px 16px;box-shadow:var(--shadow)}
.kpi .lab{color:var(--muted);font-size:12px}
.kpi .val{font-size:25px;font-weight:700;margin-top:2px;font-variant-numeric:tabular-nums;letter-spacing:-.3px}
.kpi .val small{font-size:13px;font-weight:600;color:var(--muted);margin-left:2px}
.kpi.ok .val{color:var(--ok)}.kpi.warn .val{color:var(--warn)}.kpi.bad .val{color:var(--bad)}
.panel{background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:18px 18px 10px;margin:14px 0;box-shadow:var(--shadow)}
.panel h2{font-size:15px;margin:0 0 3px}
.panel .hint{color:var(--muted);font-size:12px;margin-bottom:12px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:12px;padding-bottom:10px}
.card{border:1px solid var(--line);border-radius:12px;padding:13px 15px;background:#fcfdff}
.card.ready{border-color:var(--okline);background:linear-gradient(180deg,#f4fcf7,#fff 65%)}
.row1{display:flex;justify-content:space-between;align-items:baseline;gap:10px}
.name{font-weight:700;font-size:13.5px}
.code{color:var(--muted);font-size:12px;font-variant-numeric:tabular-nums;white-space:nowrap}
.pills{display:flex;flex-wrap:wrap;gap:5px;margin:9px 0 4px}
.pill{font-size:11px;padding:2px 8px;border-radius:999px;border:1px solid var(--line);color:#98a2b3;background:#fafbfd}
.pill.on{background:var(--okbg);border-color:var(--okline);color:var(--ok);font-weight:600}
.pill.miss{background:var(--warnbg);border-color:var(--warnline);color:var(--warn);border-style:dashed}
.meta{display:flex;gap:14px;flex-wrap:wrap;color:var(--muted);font-size:12px;margin-top:6px}
.meta b{color:var(--ink);font-weight:600;font-variant-numeric:tabular-nums}
.empty{padding:14px 4px 18px;color:var(--muted);font-size:13px}
.empty b{color:var(--ink)}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:4px 0 10px}
.toolbar input{flex:1 1 200px;min-width:160px;padding:7px 12px;border:1px solid var(--line);border-radius:999px;font-size:12.5px;outline:none;background:#fafbfd;color:var(--ink)}
.toolbar input:focus{border-color:#c7d7fb;background:#fff;box-shadow:0 0 0 3px var(--accentbg)}
.btn{border:1px solid var(--line);background:#fff;border-radius:999px;padding:5px 12px;font-size:12px;cursor:pointer;color:var(--muted);transition:.15s}
.btn:hover{border-color:#c7d7fb;color:var(--accent)}
.btn.active{background:var(--accentbg);border-color:#c7d7fb;color:var(--accent);font-weight:600}
.tblwrap{overflow:auto;max-height:70vh;border-radius:10px;border:1px solid var(--line)}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
th:nth-child(-n+3),td:nth-child(-n+3){text-align:left}
th:nth-last-child(1),td:nth-last-child(1){text-align:left;white-space:nowrap;min-width:300px;max-width:320px;overflow:hidden;text-overflow:ellipsis}
thead th{position:sticky;top:0;z-index:1;background:#f8fafd;color:var(--muted);font-weight:600;font-size:11.5px;cursor:pointer;user-select:none}
thead th:hover{color:var(--accent)}
thead th.sorted::after{content:" ▾";font-size:10px}
thead th.sorted.asc::after{content:" ▴"}
tbody tr:hover{background:#f7f9fd}
tbody tr:last-child td{border-bottom:none}
.dotc{display:inline-block;width:17px;height:17px;line-height:17px;border-radius:50%;font-size:9px;text-align:center}
.dotc.on{background:var(--okbg);color:var(--ok);font-weight:700}
.dotc.off{background:#eef1f5;color:#b0b8c4}
.badge{font-size:11px;padding:2px 9px;border-radius:999px;font-weight:600}
.badge.ready{background:var(--okbg);color:var(--ok)}
.badge.watch{background:var(--warnbg);color:var(--warn)}
.badge.veto{background:var(--badbg);color:var(--bad)}
.badge.no{background:#eef1f5;color:#667085}
.dist{display:flex;gap:10px;align-items:flex-end;margin:6px 0 22px}
.db{flex:1;text-align:center;position:relative}
.db .bar{background:linear-gradient(180deg,#dbe7ff,#eef3ff);border-radius:7px 7px 3px 3px;transition:height .3s}
.db .bar.has{background:linear-gradient(180deg,#a9c6ff,#e2ecff)}
.db .cnt{font-size:13px;font-weight:700;margin-top:5px;font-variant-numeric:tabular-nums}
.db .lab{font-size:11px;color:var(--muted)}
.rules{width:100%;border-collapse:collapse;font-size:12.5px}
.rules td,.rules th{border-bottom:1px solid var(--line);padding:8px 10px;text-align:left;white-space:normal}
.rules th{color:var(--muted);font-size:11.5px;font-weight:600;background:#f8fafd}
footer{color:var(--muted);font-size:12px;text-align:center;margin-top:26px;line-height:1.8}
footer b{color:var(--ink)}
@media(max-width:640px){.wrap{padding:18px 12px 48px}h1{font-size:18px}.kpi .val{font-size:21px}}
"""

JS = r"""
const D = window.__RS_DATA__;
const $ = (s, el) => (el || document).querySelector(s);
const fmtP = v => (v === null || v === undefined) ? '—' : (v > 0 ? '+' : '') + (v * 100).toFixed(1) + '%';
const fmtP2 = v => (v === null || v === undefined) ? '—' : (v > 0 ? '+' : '') + (v * 100).toFixed(2) + '%';
const BADGE = {READY: 'ready', WATCH: 'watch', VETO: 'veto', NO: 'no'};

function card(r, kind) {
  const pills = r.votes.map(v =>
    `<span class="pill ${v.pass ? 'on' : 'miss'}" title="${v.detail || ''}">${v.name}</span>`).join('');
  const extra = kind === 'ready'
    ? `<div class="meta"><span>结构止损 <b>${r.stop ? r.stop.toFixed(3) : '—'}</b></span>
       <span>距止损 <b>${fmtP2(r.stop_dist)}</b></span>
       <span>250日回撤 <b>${fmtP(r.dd250)}</b></span>
       <span>分级 <b>${r.grade}</b></span></div>`
    : `<div class="meta"><span>缺 <b>${r.missing.join('、')}</b></span>
       <span>${r.votes.filter(v => !v.pass).map(v => v.detail).join('') || ''}</span></div>
       <div class="meta"><span>250日回撤 <b>${fmtP(r.dd250)}</b></span>
       <span>20日均额 <b>${r.amt20_yi.toFixed(1)}亿</b></span><span>分级 <b>${r.grade}</b></span></div>`;
  return `<div class="card ${kind}">
    <div class="row1"><span class="name">${r.off_name}</span><span class="code">${r.inner}</span></div>
    <div class="sub">${r.theme} · ${r.cat} · 收盘 ${r.close.toFixed(3)}</div>
    <div class="pills">${pills}</div>${extra}</div>`;
}

function renderTable() {
  const q = ($('#q').value || '').trim().toLowerCase();
  const st = ($('.btn.active') || {}).dataset ? $('.btn.active').dataset.st : 'ALL';
  let rows = D.rows.filter(r => (st === 'ALL' || r.status === st) &&
    (!q || (r.inner + r.theme + r.cat + r.off_name).toLowerCase().includes(q)));
  if (sortKey) {
    rows = rows.slice().sort((a, b) => {
      let x = a[sortKey], y = b[sortKey];
      if (Array.isArray(x)) x = x.filter(v => v.pass).length;
      if (Array.isArray(y)) y = y.filter(v => v.pass).length;
      if (x === null || x === undefined) x = -Infinity;
      if (y === null || y === undefined) y = -Infinity;
      if (typeof x === 'string') return sortAsc ? x.localeCompare(y) : y.localeCompare(x);
      return sortAsc ? x - y : y - x;
    });
  }
  $('#tbody').innerHTML = rows.map(r => `<tr>
    <td>${r.inner}</td><td>${r.theme}</td><td>${r.cat}</td>
    <td>${r.close.toFixed(3)}</td>
    ${r.votes.map(v => `<td><span class="dotc ${v.pass ? 'on' : 'off'}" title="${v.detail || ''}">${v.pass ? '●' : '○'}</span></td>`).join('')}
    <td><b>${r.score}</b></td>
    <td><span class="badge ${BADGE[r.status]}">${r.status_cn}</span></td>
    <td>${fmtP2(r.stop_dist)}</td><td>${fmtP(r.dd250)}</td>
    <td title="${r.action}">${r.action}</td></tr>`).join('') ||
    '<tr><td colspan="99" style="text-align:center;color:#98a2b3;padding:22px">无匹配标的</td></tr>';
  $('#cnt').textContent = rows.length + ' / ' + D.rows.length;
}

let sortKey = null, sortAsc = false;

function init() {
  $('#readyCards').innerHTML = D.ready.length ? D.ready.map(r => card(r, 'ready')).join('')
    : `<div class="empty"><b>今日无标的五票全过 —— 空仓等待。</b><br>
       这是该策略的常态：五票全过的历史占比约 ${(D.stats.hit * 100).toFixed(1)}%（每只每年 ~2.5 个交易日）。
       右侧买点本就稀少，硬凑买点等于抄底。当前最接近的是 ${D.watch.length} 只「差一票」。</div>`;
  $('#watchCards').innerHTML = D.watch.length ? D.watch.map(r => card(r, 'watch')).join('')
    : '<div class="empty">（无）</div>';

  $('#dist').innerHTML = D.dist.map((n, i) => {
    const h = 8 + (n / Math.max(...D.dist, 1)) * 62;
    return `<div class="db"><div class="bar ${n ? 'has' : ''}" style="height:${h}px"></div>
      <div class="cnt">${n}</div><div class="lab">${i}票</div></div>`;
  }).join('');

  document.querySelectorAll('.btn[data-st]').forEach(b => b.addEventListener('click', () => {
    document.querySelectorAll('.btn[data-st]').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    renderTable();
  }));
  $('#q').addEventListener('input', renderTable);
  document.querySelectorAll('th[data-k]').forEach(th => th.addEventListener('click', () => {
    const k = th.dataset.k;
    sortAsc = (sortKey === k) ? !sortAsc : false;
    sortKey = k;
    document.querySelectorAll('th').forEach(x => x.classList.remove('sorted', 'asc'));
    th.classList.add('sorted');
    if (sortAsc) th.classList.add('asc');
    renderTable();
  }));
  renderTable();
}
document.addEventListener('DOMContentLoaded', init);
"""


def build_html(good, bad, ready, watch, veto, ref, d):
    dist = [sum(1 for r in good if r["score"] == n) for n in range(6)]
    stats = dict(hit=(ref or {}).get("hit") or 0.01,
                 avg_score=float(np.mean([r["score"] for r in good])) if good else 0.0)
    rows = sorted(good, key=lambda r: (-r["score"], r["cat"], r["inner"]))
    payload = dict(
        date=d, generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
        label=rule.LABEL, vote_enter=rule.VOTE_ENTER, vote_watch=rule.VOTE_WATCH,
        stats=stats, dist=dist,
        counts={k: sum(1 for r in good if r["status"] == k) for k in ("READY", "WATCH", "VETO", "NO")},
        ready=ready, watch=watch, rows=rows, ref=ref, n=len(good),
    )
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")

    rules = [
        ("V1 结构止跌", f"近 {rule.STRUCT_WIN_SHORT} 日最低收盘 > 再往前 {rule.STRUCT_WIN_LONG} 日最低收盘 ×(1+{rule.STRUCT_EPS:.1%})",
         "还在创新低（下跌中继）"),
        ("V2 结构翻越", f"收盘 > 前 {rule.BREAK_WIN} 日最高收盘（不含当日）", "仍在箱体/下降结构内"),
        ("V3 均线转向", f"收盘 > MA{rule.MA_SHORT} 且 MA{rule.MA_SHORT} 较 {rule.MA_SLOPE} 日前抬升",
         "下行均线上的反抽（最常见的假右侧）"),
        ("V4 动能确认", "DIF > DEA 且 DIF > 0", "零轴下金叉（只是反弹）"),
        ("V5 量能确认", f"量 ≥ {rule.VOL_MULT:g}×20 日均量 且 收涨", "缩量假突破"),
    ]
    rule_rows = "".join(f"<tr><td><b>{a}</b></td><td>{b}</td><td>{c}</td></tr>" for a, b, c in rules)

    kpi = [
        ("全池标的", f"{len(good)}<small>只</small>", ""),
        ("右侧买点", f"{len(ready)}<small>只</small>", "ok" if ready else ""),
        ("差一票", f"{len(watch)}<small>只</small>", "warn" if watch else ""),
        ("被否决", f"{len(veto)}<small>只</small>", "bad" if veto else ""),
        ("平均票数", f"{stats['avg_score']:.2f}<small>/5</small>", ""),
    ]
    if ref:
        kpi += [("回测中位年化", f"{ref['ann'] * 100:+.1f}<small>%</small>", ""),
                ("回测中位回撤", f"{ref['mdd'] * 100:.1f}<small>%</small>", "ok"),
                ("择时边际", f"{ref['edge'] * 1e4:+.1f}<small>bp</small>", "ok")]
    kpi_html = "".join(f'<div class="kpi {c}"><div class="lab">{a}</div><div class="val">{b}</div></div>'
                       for a, b, c in kpi)

    head = "".join(f"<th>{rule.VOTE_SHORT[k]}</th>" for k in rule.VOTE_NAMES)
    cols = ('<th data-k="inner">场内</th><th data-k="theme">主题</th><th data-k="cat">类别</th>'
            '<th data-k="close">收盘</th>' + head +
            '<th data-k="score">票数</th><th data-k="status">状态</th>'
            '<th data-k="stop_dist">距止损</th><th data-k="dd250">250日回撤</th>'
            '<th data-k="action">说明</th>')

    html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>右侧买入 · 每日扫描 {d}</title><style>{CSS}</style></head><body>
<div class="wrap">
  <div class="top">
    <div><h1>右侧买入 · 每日扫描</h1>
      <div class="sub">数据日 <b>{d}</b> · 标的池 <code>data/_universe.md</code> 场内 {len(good)} 只 ·
      参数 <b>{rule.LABEL}</b> · 生成 {payload['generated']}</div></div>
    <span class="chip"><span class="dotlive"></span>{rule.VOTE_ENTER} 票买入 · {rule.VOTE_WATCH} 票观察</span>
  </div>

  <div class="kpis">{kpi_html}</div>

  <section class="panel"><h2>一、今日达到右侧买入点</h2>
    <div class="hint">五票全过。买入即挂结构止损（信号日前 {rule.STOP_LOOKBACK} 日最低收盘），跌破无条件离场。</div>
    <div class="cards" id="readyCards"></div></section>

  <section class="panel"><h2>二、差一票（观察名单，不提前埋伏）</h2>
    <div class="hint">含义是「明日若该票转正即成为买点」。提前买就是左侧 —— 本策略的全部意义在于不做这件事。</div>
    <div class="cards" id="watchCards"></div></section>

  <section class="panel"><h2>三、全池票数矩阵</h2>
    <div class="hint">点表头排序 · 点徽章筛选 · 票数分布见下（<span id="cnt"></span> 只）</div>
    <div class="dist" id="dist"></div>
    <div class="toolbar">
      <input id="q" placeholder="搜索代码 / 主题 / 类别 / 场外基金…">
      <button class="btn active" data-st="ALL">全部</button>
      <button class="btn" data-st="READY">买点</button>
      <button class="btn" data-st="WATCH">差一票</button>
      <button class="btn" data-st="VETO">被否决</button>
      <button class="btn" data-st="NO">未达标</button>
    </div>
    <div class="tblwrap"><table><thead><tr>{cols}</tr></thead><tbody id="tbody"></tbody></table></div>
  </section>

  <section class="panel"><h2>四、五票口径速查</h2>
    <div class="hint">硬否决：5 日涨幅 &gt; {rule.RET5_MAX:.0%}（追高） · 20 日均成交额 &lt; {rule.AMT_MIN / 1e8:.1f} 亿（流动性）
      · 离场：收盘跌破入场结构位，或连续 {rule.EXIT_CONFIRM} 日收盘 &lt; MA{rule.EXIT_MA}</div>
    <table class="rules"><thead><tr><th>票</th><th>判据</th><th>在排除什么</th></tr></thead>
      <tbody>{rule_rows}</tbody></table>
    <div style="height:8px"></div>
  </section>

  <footer>
    {'回测参照（全池全历史中位数）：年化 ' + f"{ref['ann']:+.2%}" + ' · 最大回撤 ' + f"{ref['mdd']:.2%}"
     + ' · 买入持有 ' + f"{ref['base_ann']:+.2%}" + ' / ' + f"{ref['base_mdd']:.2%}"
     + ' · 持仓 ' + f"{ref['pos'] * 100:.1f}%" + ' · 择时边际 ' + f"{ref['edge'] * 1e4:+.1f}bp" if ref else ''}
    <br>信号按当日收盘判定、次日收盘执行；机械规则输出，<b>非投资建议</b>。
  </footer>
</div>
<script>window.__RS_DATA__ = {data};</script>
<script>{JS}</script>
</body></html>"""
    return html


def main():
    good, bad = load(IN)
    if not good:
        sys.exit("扫描产物无有效标的")
    d = good[0]["date"]
    ready = sorted([r for r in good if r["status"] == "READY"], key=lambda r: -r["score"])
    watch = sorted([r for r in good if r["status"] == "WATCH"], key=lambda r: -r["score"])
    veto = [r for r in good if r["status"] == "VETO"]
    ref = bt_ref()

    open(MD, "w", encoding="utf-8").write(build_md(good, bad, ready, watch, veto, ref, d))
    open(HTML, "w", encoding="utf-8").write(build_html(good, bad, ready, watch, veto, ref, d))

    print(f"[右侧买入报告] 数据日 {d} · 买点 {len(ready)} · 差一票 {len(watch)} · 被否决 {len(veto)}")
    print(f"  -> {MD}")
    print(f"  -> {HTML}")


if __name__ == "__main__":
    main()
