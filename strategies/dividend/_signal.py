# -*- coding: utf-8 -*-
r"""中证红利ETF 策略 · 每日信号报告（§4.2 三指标 + 红线）。

自动抓取:
  - 中证红利估值锚(蛋卷 djapi/index_eva/dj, SH000922): 股息率(yeild) + PE分位(pe_percentile)
  - 10Y 国债收益率: xalpha.universal.get_bond_rates('N')
  - 股债收益比 = 股息率 ÷ 10Y  —— 全部实时, 无需人工
人工录入(可选, data/_dividend_redline.json): §4.4 红线(成分股暴雷/下调分红/系统性)

输出（相对脚本自身, 见 AGENTS.md §8.2）:
  daily/_signal_report.md · daily/_dividend_signal.json
  daily/_universe_dividend_active.md · daily/_universe_dividend_active.json

网络失败时回退到本地缓存重建序列(data.latest), 并标注来源, 不静默用旧值冒充实时。
用法: python strategies/dividend/_signal.py
"""
import io
import json
import os
import sys
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)
_ROOT = os.path.dirname(os.path.dirname(_DIR))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import data as dt   # noqa: E402
import rule         # noqa: E402

_DJ_URL = "https://danjuanfunds.com/djapi/index_eva/dj"
_DJ_UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"),
          "Referer": "https://danjuanfunds.com/"}


def fetch_danjuan_red():
    """蛋卷 SH000922(中证红利) -> {as_of, yeild(%), pe, pe_pct}。"""
    import requests
    r = requests.get(_DJ_URL, headers=_DJ_UA, timeout=12)
    r.raise_for_status()
    rows = ((r.json().get("data") or {}).get("items")) or []
    red = next((x for x in rows if x.get("index_code") == "SH000922"), None)
    if red is None or red.get("yeild") is None or red.get("pe_percentile") is None:
        raise RuntimeError("蛋卷接口未返回 SH000922 股息率/PE分位")
    return dict(as_of=datetime.fromtimestamp(int(red.get("ts", 0)) / 1000).strftime("%Y-%m-%d"),
                yeild=round(float(red["yeild"]) * 100, 2),
                pe=float(red["pe"]), pe_pct=float(red["pe_percentile"]))


def fetch_y10():
    """10Y 国债收益率(%)。"""
    from xalpha.universal import get_bond_rates
    df = get_bond_rates("N", datetime.now().strftime("%Y-%m-%d"))
    df = df[df["rate"].notna()]
    row = df.iloc[(df["year"] - 10).abs().argsort()[:1]]
    return round(float(row["rate"].iloc[0]), 3), float(row["year"].iloc[0])


def load_redline():
    if not os.path.exists(rule.REDLINE_FILE):
        json.dump(rule.make_redline_template(),
                  open(rule.REDLINE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        return rule.make_redline_template()
    return json.load(open(rule.REDLINE_FILE, encoding="utf-8"))


def collect():
    """组装当期三指标 -> (values, source)。优先实时, 失败回退本地重建。"""
    src = {}
    try:
        dj = fetch_danjuan_red()
        y10, term = fetch_y10()
        values = dict(as_of=dj["as_of"], dy=dj["yeild"], pe_pct=dj["pe_pct"], y10=y10, term=term)
        values["ratio"] = round(values["dy"] / y10, 3) if y10 else None
        src = {"估值": "蛋卷基金 djapi/index_eva/dj(SH000922, 实时)",
               "10Y": f"xalpha.universal.get_bond_rates('N', {term:g}年, 实时)"}
        return values, src
    except Exception as e:  # noqa: BLE001
        cur = dt.latest()
        values = dict(as_of=cur["as_of"], dy=round(cur["dy"], 2), pe_pct=cur["pe_pct"],
                      y10=round(cur["y10"], 3), term=10)
        values["ratio"] = round(cur["ratio"], 3)
        src = {"估值": f"本地缓存重建(实时失败: {type(e).__name__}: {e})",
               "10Y": "本地缓存(中债月末 ffill)"}
        return values, src


def zones_of(values):
    z = {k: rule.ZONE_FN[k](values[k]) for k in rule.IND_KEYS}
    z["combo"] = rule.compose_zone({k: z[k] for k in rule.IND_KEYS})
    return z


def render(values, z, redline, src):
    rl_hit = rule.redline_triggered(redline)
    act = rule.ZONE_CN[z["combo"]]
    final = "空仓 / 不加仓（红线）" if rl_hit else {
        "buy": "满仓", "hold": "维持现有仓位", "sell": "清仓 / 停加"}[z["combo"]]
    L = ["# 中证红利ETF · 每日信号（§4.2）", "",
         f"> 生成 {rule.now_str()} · 数据截至 {values['as_of']} · "
         f"标的 场外 `{rule.ASSET['off_code']}`（{rule.ASSET['off_name']}）/ 场内代理 `{rule.ASSET['inner_code']}`", ""]

    L += ["## 一、当前三指标与分区", "",
          "| 指标 | 当前值 | 阈值(买/持/卖) | 分区 |",
          "|---|---:|---|---|"]
    for k in rule.IND_KEYS:
        bt, ht, st = rule.ZONE_TEXT[k]
        shown = f"{values[k]:.2f}" if k == "ratio" else (f"{values[k]:.0%}" if k == "pe_pct"
                                                        else f"{values[k]:.2f}%")
        L.append(f"| {rule.IND_NAMES[k]} | {shown} | {bt} / {ht} / {st} | {rule.ZONE_CN[z[k]]} |")
    L.append(f"| **合成(任一卖出即停)** | — | — | **{act}** |")
    L += ["", f"- 10Y 国债：**{values['y10']:.2f}%**（{values['term']:g}年）"
              + (f"，股息率底线 {rule.DY_FLOOR:g}%：{'达标' if values['dy'] >= rule.DY_FLOOR else '**跌破**'}"
                 if values.get("dy") is not None else ""), ""]

    L += ["## 二、今日操作结论", "",
          f"- **{final}**",
          f"- 依据：三指标分区 = " + "、".join(
              f"{rule.IND_NAMES[k]} {rule.ZONE_CN[z[k]]}" for k in rule.IND_KEYS),
          ""]

    L += ["## 三、§4.4 风险红线（人工）", "",
          f"- 触发状态：**{'⚠ 已触发 → 停加/减仓' if rl_hit else '未触发'}**"
          f"（暴雷 {redline.get('n_blowup', 0)} 只 / 下调分红 {redline.get('n_cut_div', 0)} 只 / "
          f"系统性 {'是' if redline.get('systemic') else '否'}；判据：暴雷≥2 或 下调≥3 或 系统性）",
          f"- 录入文件：`data/_dividend_redline.json`（截至 {redline.get('as_of') or '未填'}）"
          + (f" · 备注：{redline['note']}" if redline.get("note") else ""),
          ""]

    L += ["## 四、数据来源与口径", ""]
    for k, v in src.items():
        L.append(f"- {k}：{v}")
    L += ["- 口径：股息率(TTM)=蛋卷实时；PE分位=蛋卷(10年)；股债收益比=股息率÷10Y；"
          "回测口径下的指标由中证官网 H00922/000922 + 中债10Y 重建（见 backtest.py）。",
          "",
          "---",
          "> 机械输出，非投资建议；市场有风险，投资需谨慎。"]
    return "\n".join(L) + "\n", final


def write_active(values, z, redline, final):
    a = rule.ASSET
    md = ["# 中证红利ETF · 每日执行名单（固定 1 标的）", "",
          f"> 来源：《核心轮动投资策略手册》§4 直接定档（单标的, 非回测 A/B 分级）。生成 {rule.now_str()}。",
          "> 场外 `012644` 为唯一执行通道（按净值申赎）；场内 `515080` 只作日频信号/溢价观察，不交易。", "",
          "| 角色 | 场外主仓（执行） | 场内代理（信号） | 场内代理名 | 当前动作 |",
          "| --- | --- | --- | --- | --- |",
          f"| {a['name']} | {a['off_code']} {a['off_name']} | {a['inner_code']} | {a['inner_name']} | **{final}** |",
          "",
          "> 只此 1 只。对应 JSON：`strategies/dividend/daily/_universe_dividend_active.json`"]
    with open(rule.OUT_ACTIVE_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(md) + "\n")
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d"),
        "criteria": "核心轮动手册 §4 中证红利单标的(固定, 非回测 select)",
        "A": [{"code": a["off_code"], "name": a["off_name"], "proxy": a["inner_code"],
               "proxy_name": a["inner_name"], "theme": "中证红利", "cat": "策略/商品", "role": "中证红利"}],
        "B": [],
    }
    with open(rule.OUT_ACTIVE_JSON, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def main():
    os.makedirs(rule.DAILY_DIR, exist_ok=True)
    values, src = collect()
    redline = load_redline()
    z = zones_of(values)
    txt, final = render(values, z, redline, src)
    with open(rule.OUT_SIGNAL_MD, "w", encoding="utf-8") as fh:
        fh.write(txt)
    snap = {"as_of": values["as_of"], "generated_at": rule.now_str(), "asset": rule.ASSET,
            "values": values, "zones": z, "redline_hit": rule.redline_triggered(redline),
            "action": final, "source": src}
    with open(rule.OUT_SIGNAL_JSON, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, ensure_ascii=False, indent=2)
    write_active(values, z, redline, final)
    print(txt)
    print(f"\n产物: {rule.OUT_SIGNAL_MD}\n      {rule.OUT_SIGNAL_JSON}\n      {rule.OUT_ACTIVE_MD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
