#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""红利池操作规则 v1.0 · 利差读数卡

核心指标
--------
    利差 = 税后股息率 − 锚国债收益率
    税后股息率 = 指数股息率 × 税制系数 × 可持续系数
    税制系数: A 股 1.0 / 港股通 0.8 / QDII 0.9
    可持续系数: 派息率 30–70% 且 FCF 覆盖 >1 → 1.0；>80% 或覆盖 <1 → 0.7

决策（读数，不自动下单）
------------------------
    利差自身近 10 年分位 ≥70% → 买入区（买全池分位最高的一只）
    30–70% → 不买新、持有存量
    全池 <30% → 红利板块整体不碰
    <20% 或 税后股息率 <3% → 卖出（先卖分位最低）
    保险丝：红利 − 全 A 的 40 日收益差 > +10% → 仓位减半
    仓位带（相对**总资产**）：≥70% → 30% / 40–70% → 20% / 20–40% → 10% / <20% → 0%

本期数据边界（不伪造，缺就标 ⚠）
--------------------------------
  * 股息率**月度历史**由 `yield_hist.py` 构建：
    - `000922` / `H30269` 用**全收益指数 − 价格指数**的 250 日滚动差反推（真实实现股息率）；
    - 其余 4 只无全收益版本 → 用中证红利(000922)的变化率**代理**（水平按官网快照校准，标 †）；
    - 港股分红节奏不同 → **不做代理**，分位留空。
  * 10Y 美债已补齐（FRED `DGS10` 月末，财政部 CSV 备用）→ 港股按美元锚给**当前利差**，
    因缺港股股息率历史，分位仍不可用。
  * 派息率 / FCF 无源 → 可持续系数统一 1.0（未核验，防周期顶的折扣未生效）。
  * 规模 / 日均成交 / 溢价 / 跟踪误差无源 → 硬过滤**未执行**，仅列示。
  * 008163 的股息率来自持仓穿透 → **单列、不参与分位与仓位判定**。

用法::

    python strategies/dividend/pool_rule_v1.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR)
import pool  # noqa: E402  pylint: disable=wrong-import-position
import yield_hist  # noqa: E402  pylint: disable=wrong-import-position

DATA_DIR = os.path.join(_DIR, "data")
HIST_DIR = os.path.join(DATA_DIR, "_csi_hist")
OUT_MD = os.path.join(_DIR, "daily", "_pool_rule_v1.md")
OUT_JSON = os.path.join(_DIR, "daily", "_pool_rule_v1.json")

TAX = {"A": 1.0, "HK": 0.8, "QDII": 0.9}
ANCHOR_NAME = {"A": "10Y 中债", "HK": "10Y 美债", "QDII": "10Y 美债"}
BANDS = [(0.70, "满配带", 30), (0.40, "标准带", 20), (0.20, "低配带", 10), (0.0, "观察带", 0)]
FUSE = 10.0          # 40 日超额 > +10% 触发保险丝
SELL_YIELD = 3.0     # 税后股息率 < 3% 强制卖出
MAX_YEARS = 10


# ------------------------------------------------------------------ 数据层
def _y10_series():
    """10Y 中债月末序列（%），来自仓库共用数据缓存。"""
    raw = pd.read_csv(pool.BOND_10Y_CACHE)
    date_col = raw.columns[0]
    val_col = "close" if "close" in raw.columns else raw.columns[-1]
    s = pd.Series(pd.to_numeric(raw[val_col], errors="coerce").to_numpy(),
                  index=pd.to_datetime(raw[date_col]))
    return s.dropna().sort_index()


def _csi_close(code, max_stale_days=3):
    """中证指数日线（date, close, peg）—— 缓存到 data/_csi_hist/，过期则重抓。"""
    os.makedirs(HIST_DIR, exist_ok=True)
    path = os.path.join(HIST_DIR, f"csi_{code}.csv")
    df = None
    if os.path.exists(path):
        df = pd.read_csv(path, parse_dates=["date"])
        if df["date"].iloc[-1] >= datetime.now() - pd.Timedelta(days=max_stale_days):
            return df, df["date"].iloc[-1].date(), False

    import requests

    url = (f"https://www.csindex.com.cn/csindex-home/perf/index-perf?indexCode={code}"
           f"&startDate={pool.CSI_START.replace('-', '')}&endDate={datetime.now():%Y%m%d}")
    try:
        raw = requests.get(url, headers=pool._UA, timeout=25).json().get("data") or []
    except Exception as exc:  # noqa: BLE001 - 网络失败退回缓存
        if df is not None:
            return df, df["date"].iloc[-1].date(), True
        raise RuntimeError(f"csi {code} 抓取失败且无缓存: {exc}") from exc

    if not raw:
        if df is not None:
            return df, df["date"].iloc[-1].date(), True
        raise RuntimeError(f"csi {code} 返回空数据")

    new = pd.DataFrame(raw)
    new["date"] = pd.to_datetime(new["tradeDate"], format="%Y%m%d")
    new["close"] = pd.to_numeric(new["close"], errors="coerce")
    new["peg"] = pd.to_numeric(new.get("peg"), errors="coerce")
    new = new[["date", "close", "peg"]].dropna(subset=["close"]).sort_values("date")
    new.to_csv(path, index=False, encoding="utf-8")
    return new.reset_index(drop=True), new["date"].iloc[-1].date(), False


def _spread_from_monthly(dy_monthly, tax, sus, y10_monthly):
    """月度股息率 + 月度国债 -> 利差月度序列的自身 10 年分位（排除当前点自身）。"""
    if dy_monthly is None or dy_monthly.empty:
        return None
    idx = dy_monthly.index.intersection(y10_monthly.index)
    if len(idx) < 12:
        return None
    sp = (dy_monthly.reindex(idx) * tax * sus - y10_monthly.reindex(idx)).dropna()
    sp = sp[sp.index >= sp.index[-1] - pd.DateOffset(years=MAX_YEARS)]
    if len(sp) < 12:
        return None
    now = float(sp.iloc[-1])
    pct = float((sp.iloc[:-1] < now).mean())
    years = (sp.index[-1] - sp.index[0]).days / 365.25
    return {
        "now": round(now, 3),
        "pct": round(pct, 4),
        "years": round(years, 1),
        "start": str(sp.index[0].date()),
        "n": int(len(sp)),
    }


def _pe_rel_pct(csi_code, peg, mkt_peg):
    """「指数 PE ÷ 全A PE」的自身近 10 年分位（规则要求的交叉验证）。"""
    if mkt_peg is None or csi_code not in peg.columns:
        return None
    pe = peg[csi_code].dropna()
    both = pd.concat([pe.rename("pe"), mkt_peg.rename("mkt")], axis=1, join="inner").dropna()
    both = both[both["mkt"] > 0]
    if len(both) < 300:
        return None
    rel = both["pe"] / both["mkt"]
    end = rel.index[-1]
    win = rel[rel.index >= end - pd.DateOffset(years=MAX_YEARS)]
    monthly = win.resample("ME").last().dropna()
    if len(monthly) < 12:
        return None
    return float((monthly < rel.iloc[-1]).mean())


# ------------------------------------------------------------------ 主流程
def collect():
    val = pool.valuation()
    peg = pool.load_peg()
    y10 = _y10_series()
    y10_now = float(y10.iloc[-1])

    # 真实股息率月度序列 + 10Y 美债（缺失时自动构建）
    yh = yield_hist.load()
    yh_series = {c: yield_hist.series(yh, c) for c in yh["index_code"].unique()}
    yh_method = {c: g["method"].iloc[0] for c, g in yh.groupby("index_code")}
    ust = yield_hist.ust10y()

    # 全 A 市场 PE：沪深300 作代理（万得全A 无公开接口），用于「PE ÷ 全A」交叉验证
    try:
        mkt_df, _, _ = _csi_close("000300")
        mkt_peg = mkt_df.set_index("date")["peg"].dropna()
    except Exception:  # noqa: BLE001 - 交叉验证失败不阻断主流程
        mkt_peg = None

    rows = []
    for rec in pool.POOL:
        code = rec["code"]
        v = val.get(code, {})
        ch = "HK" if rec["cat"] == "港股红利" else "A"
        tax = TAX[ch]
        sus = 1.0                      # 派息率/FCF 无源 → 未核验
        dy = v.get("dy")
        pe = v.get("pe")
        pen = bool(rec.get("mx")) and str(rec.get("mx")).endswith(".HOLDINGS")

        row = {
            "code": code, "name": rec["name"], "cat": rec["cat"],
            "idx": rec["idx_name"], "channel": ch, "tax": tax, "sus": sus,
            "pe": pe, "dy": dy, "src_dy": v.get("src_dy", "缺失"),
            "penetration": pen, "anchor_name": ANCHOR_NAME[ch],
            "anchor": None, "after_tax": None, "spread": None,
            "pct": None, "years": None, "pe_rel": None,
            "status": "", "advice": "", "note": "",
        }

        if dy is not None:
            row["after_tax"] = round(float(dy) * tax * sus, 2)

        # 港股/QDII：锚 10Y 美债（已补齐），但港股股息率无历史序列 → 给当前利差、不给分位
        if ch in ("HK", "QDII"):
            anchor = float(ust.iloc[-1])
            row["anchor"] = round(anchor, 2)
            if row["after_tax"] is not None:
                row["spread"] = round(row["after_tax"] - anchor, 2)
                if row["spread"] <= 0:
                    row["status"] = "出局"
                    row["advice"] = f"对美元锚倒挂 {row['spread']:+.2f}pp → 不买"
                else:
                    row["status"] = "利差为正"
                    row["advice"] = "分位待补（港股股息率无历史序列）"
            else:
                row["status"] = "挂起"
                row["advice"] = "股息率缺失"
            row["note"] = (f"锚 10Y 美债 {anchor:.2f}%（{ust.index[-1].date()} 月末）；"
                           "港股分红节奏与 A 股不同，未做代理，分位不可用")
            rows.append(row)
            continue

        row["anchor"] = round(y10_now, 2)
        if row["after_tax"] is not None:
            row["spread"] = round(row["after_tax"] - y10_now, 2)

        # 穿透估算值：单列，不参与分位
        if pen:
            row["status"] = "单列"
            row["advice"] = "穿透估算股息率，不参与分位与仓位判定"
            row["note"] = "股息率来自持仓穿透（非指数公司口径）"
            rows.append(row)
            continue

        row["pe_rel"] = _pe_rel_pct(rec["csi"], peg, mkt_peg)
        dy_m = yh_series.get(rec["csi"]) if rec["csi"] else None
        hist = _spread_from_monthly(dy_m, tax, sus, y10) if dy_m is not None else None
        if hist:
            row["pct"] = hist["pct"]
            row["years"] = hist["years"]
            row["pct_src"] = (yh_method.get(rec["csi"]) or "real")
            row["note"] = f"分位窗口 {hist['start']} 起（{hist['years']}y，月度 {hist['n']} 点）"
            if row["pct_src"] == "proxy":
                row["note"] += "；股息率历史为代理序列（该指数无全收益版本）"
            if hist["years"] < 7:
                row["note"] += "；不足 7 年，分位仅供参考、不驱动仓位带"
        elif dy is None:
            row["note"] = "股息率缺失"
        else:
            row["note"] = "无股息率历史序列，分位不可用"

        rows.append(row)

    # 板块保险丝：红利(000922) − 全 A(000300 近似万得全A) 的 40 日收益差
    fuse = None
    try:
        a, a_date, a_stale = _csi_close("000922")
        b, b_date, b_stale = _csi_close("000300")
        sa = a.set_index("date")["close"]
        sb = b.set_index("date")["close"]
        diff = (sa.pct_change(40) - sb.pct_change(40)).dropna() * 100
        fuse = {
            "now": round(float(diff.iloc[-1]), 2),
            "as_of": str(diff.index[-1].date()),
            "trigger": bool(diff.iloc[-1] > FUSE),
            "freq": round(float((diff > FUSE).mean()) * 100, 2),
            "days": int((diff > FUSE).sum()),
            "sample": int(len(diff)),
            "stale": bool(a_stale or b_stale),
            "years": {},
        }
        hit = diff[diff > FUSE]
        fuse["years"] = {str(k): int(v) for k, v in hit.groupby(hit.index.year).size().items()}
        fuse["years_txt"] = " / ".join(f"{k}年 {v} 天" for k, v in sorted(fuse["years"].items()))
    except Exception as exc:  # noqa: BLE001 - 保险丝失败不阻断读数
        fuse = {"error": f"{type(exc).__name__}: {exc}"}

    # 仓位带：按可用分位里的最高者。窗口 <7 年（⚠）只作参考，不驱动仓位带
    all_scored = [r for r in rows if r["pct"] is not None and not r["penetration"]]
    scored = [r for r in all_scored if (r["years"] or 0) >= 7]
    best = max(scored, key=lambda r: r["pct"]) if scored else None
    band = None
    if best:
        for lo, name, ratio in BANDS:
            if best["pct"] >= lo:
                band = {"name": name, "ratio": ratio, "by": best["code"], "pct": best["pct"]}
                break
    # 被排除在仓位判定外、但分位更高的短窗口标的（仅提示）
    excluded = [r for r in all_scored if (r["years"] or 0) < 7 and
                (best is None or r["pct"] > best["pct"])]

    # 逐个给状态/建议
    for r in rows:
        if r["status"]:
            continue
        pct = r["pct"]
        if pct is None:
            r["status"] = "分位不可用"
            r["advice"] = "缺股息率历史，本期不判"
        elif pct >= 0.70:
            r["status"] = "买入区"
            r["advice"] = "能买（优先选全池分位最高者）"
        elif pct >= 0.30:
            r["status"] = "持有"
            r["advice"] = "不买新、持有存量"
        elif pct >= 0.20:
            r["status"] = "低配"
            r["advice"] = "只减不加"
        else:
            r["status"] = "卖出"
            r["advice"] = "分位 <20%，先卖分位最低者"
        if r["after_tax"] is not None and r["after_tax"] < SELL_YIELD:
            r["status"] = "卖出"
            r["advice"] = f"税后股息率 {r['after_tax']:.2f}% < {SELL_YIELD:.0f}%"

        if r["years"] is not None and r["years"] < 7:
            r["status"] += "（参考）"
        # 规则要求的交叉验证：利差宽 ⇔ 便宜 ⇒ PE/全A 分位应偏低
        if pct is not None and r["pe_rel"] is not None:
            rel_pct = r["pe_rel"] * 100
            if r["pe_rel"] >= 0.85:
                r["note"] += (f"；⚠ 相对全A 估值处于历史高位（PE/全A 分位 {rel_pct:.0f}%）"
                              "→ 利差宽不是因为它便宜，主要来自国债低位")
            elif r["pe_rel"] <= 0.30:
                r["note"] += f"；相对全A 估值处于历史低位（PE/全A 分位 {rel_pct:.0f}%）"

    return {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "as_of_dy": (val.get("090010") or {}).get("as_of") or "",
        "y10": round(y10_now, 2),
        "y10_as_of": str(y10.index[-1].date()),
        "rows": rows,
        "fuse": fuse,
        "band": band,
        "excluded": [{"code": r["code"], "pct": r["pct"], "years": r["years"]} for r in excluded],
        "gaps": [
            "股息率历史：2 只为全收益反推（真实），4 只为中证红利代理（标 †），港股无序列",
            "派息率/FCF 无源 → 可持续系数统一 1.0（未核验）",
            "规模/成交/溢价/跟踪误差无源 → 硬过滤未执行",
        ],
        "ust": round(float(ust.iloc[-1]), 2),
        "ust_as_of": str(ust.index[-1].date()),
    }


# ------------------------------------------------------------------ 渲染
def _pct_txt(r):
    if r["penetration"]:
        return "—（单列）"
    if r["pct"] is None:
        return "—（不可用）"
    mark = "†" if (r.get("pct_src") or "real") == "proxy" else ""
    warn = " ⚠" if (r["years"] or 0) < 7 else ""
    return f"{r['pct'] * 100:.0f}%{mark}{warn}"


def render(d):
    L = ["# 红利池利差读数（操作规则 v1.0）", "",
         f"> 生成 {d['generated']} · 股息率快照 {d['as_of_dy']} · "
         f"10Y 中债 **{d['y10']:.2f}%**（{d['y10_as_of']}）· "
         f"10Y 美债 **{d['ust']:.2f}%**（{d['ust_as_of']} 月末）", "",
         "> 核心指标：**利差 = 税后股息率 − 锚国债收益率**。本期只做读数，不产生交易指令。",
         "> 股息率历史：`000922`/`H30269` 用**全收益指数 − 价格指数**的 250 日滚动差反推"
         "（真实实现股息率）；其余 4 只无全收益版本，用中证红利(000922)的变化率**代理**（标 †）；"
         "港股不做代理。窗口 <7 年者标 ⚠。",
         "> 10Y 美债：FRED `DGS10` 月末值（`data/_bt_caches/ust10y_m.csv`）。", ""]

    fuse = d["fuse"]
    L += ["## 一、板块保险丝", ""]
    if fuse and "error" not in fuse:
        state = "**触发** → 仓位减半" if fuse["trigger"] else "未触发"
        L += [f"- 红利(000922) − 全A(000300 近似) **40 日收益差 = {fuse['now']:+.2f}%** → {state}",
              f"- 截止 {fuse['as_of']}{'（缓存，未刷新成功）' if fuse['stale'] else ''}；"
              f"历史上 >+{FUSE:.0f}% 的天数 {fuse['days']}/{fuse['sample']}"
              f"（{fuse['freq']:.2f}%）：{fuse.get('years_txt') or '无'}", ""]
    else:
        L += [f"- ⚠ 无法计算：{(fuse or {}).get('error', '未知')}", ""]

    L += ["## 二、逐标的读数", "",
          "| 代码 | 简称 | 通道 | 税前股息率 | 税后 | 锚 | 利差 | 利差分位 | PE/全A分位 | 状态 | 建议 |",
          "|---|---|---|---:|---:|---|---:|---:|---:|---|---|"]
    for r in d["rows"]:
        if r["penetration"]:
            continue          # 穿透估算值单列在第四节，不混进主表
        dy_txt = f"{r['dy']:.2f}%" if r["dy"] is not None else "—"
        at_txt = f"{r['after_tax']:.2f}%" if r["after_tax"] is not None else "—"
        anchor = f"{r['anchor_name']} {r['anchor']:.2f}%" if r["anchor"] is not None else r["anchor_name"]
        sp_txt = f"{r['spread']:+.2f}pp" if r["spread"] is not None else "—"
        rel_txt = f"{r['pe_rel'] * 100:.0f}%" if r.get("pe_rel") is not None else "—"
        L.append(f"| {r['code']} | {r['name']} | {r['channel']} | {dy_txt} | {at_txt} | "
                 f"{anchor} | {sp_txt} | {_pct_txt(r)} | {rel_txt} | {r['status']} | {r['advice']} |")
    L.append("")
    L += ["> 利差分位与 PE/全A 分位应当**反向**（利差宽 = 便宜 = PE 相对低）；两者同时偏高说明"
          "利差走阔主要由国债下行驱动，需人工复核。",
          "> † = 该指数无全收益版本，股息率历史用中证红利(000922)的变化率代理、水平按官网快照校准；"
          "分位可比性弱于真实序列。", ""]

    rels = [r["pe_rel"] for r in d["rows"] if r.get("pe_rel") is not None]
    if rels:
        med = sorted(rels)[len(rels) // 2]
        if med >= 0.70:
            verdict = ("红利**相对全A 处于历史偏贵区间** —— 利差宽是国债 1.68% 的历史低位"
                       "贡献的，不是红利自身便宜；此时「利差分位高 → 加仓」存在把利率下行"
                       "误读成估值便宜的风险")
        elif med <= 0.30:
            verdict = "红利相对全A 处于历史偏低区间，绝对利差与相对估值同向，信号一致性较好"
        else:
            verdict = "红利相对全A 处于中性区间，绝对利差与相对估值不冲突"
        L += [f"> **全池相对估值**：PE/全A 分位中位数 **{med * 100:.0f}%**（{len(rels)} 只可比）→ {verdict}。", ""]

    band = d["band"]
    L += ["## 三、仓位带（红利合计 / 总资产）", ""]
    if band:
        L += [f"- 当前最高利差分位（仅计窗口 ≥7 年者）：`{band['by']}` **{band['pct'] * 100:.0f}%** → "
              f"**{band['name']}：{band['ratio']}%**（相对总资产）"]
    else:
        L += ["- 无可用分位 → **不给仓位档位**"]
    for e in d.get("excluded") or []:
        L += [f"- ⚠ `{e['code']}` 分位 {e['pct'] * 100:.0f}% 更高，但窗口仅 {e['years']}y"
              "（<7 年）→ 仅供参考，不驱动仓位带"]
    if fuse and "error" not in fuse and fuse["trigger"]:
        base = band["ratio"] if band else None
        half = f"，保险丝触发 → 实际上限 {base / 2:.0f}%" if base is not None else ""
        L += [f"- 保险丝已触发（40 日超额 {fuse['now']:+.2f}% > +{FUSE:.0f}%）→ **仓位减半**{half}"]
    L += [""]

    pen = [r for r in d["rows"] if r["penetration"]]
    hang = [r for r in d["rows"] if r["status"] == "挂起"]
    if pen or hang:
        L += ["## 四、不参与分位判定的标的", ""]
    if pen:
        L += ["### 单列（持仓穿透估算）", "",
              "| 代码 | 简称 | 税前股息率 | 税后 | 说明 |", "|---|---|---:|---:|---|"]
        for r in pen:
            dy_txt = f"{r['dy']:.2f}%" if r["dy"] is not None else "—"
            at_txt = f"{r['after_tax']:.2f}%" if r["after_tax"] is not None else "—"
            L.append(f"| {r['code']} | {r['name']} | {dy_txt} | {at_txt} | "
                     f"{r['note'] or '穿透估算'} |")
        L.append("")
    if hang:
        L += ["### 挂起（缺锚）", "",
              "| 代码 | 简称 | 通道 | 税前股息率 | 原因 |", "|---|---|---|---:|---|"]
        for r in hang:
            dy_txt = f"{r['dy']:.2f}%" if r["dy"] is not None else "—"
            L.append(f"| {r['code']} | {r['name']} | {r['channel']} | {dy_txt} | {r['advice']} |")
        L.append("")

    L += ["## 五、未执行项（数据缺失，非结论）", ""]
    L += [f"- {g}" for g in d["gaps"]]
    L += ["", "> 机械读数，非投资建议。规则改动请走 `红利池操作规则.md` 的修订记录。", ""]
    return "\n".join(L)


def main():
    d = collect()
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write(render(d))
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)

    fuse = d["fuse"] or {}
    print(f"[v1] 利差读数 {d['as_of_dy']} · 锚(中债) {d['y10']:.2f}%")
    for r in d["rows"]:
        pct = "  n/a" if r["pct"] is None else f"{r['pct'] * 100:5.0f}%"
        print(f"[v1] {r['code']} {r['name'][:16]:18s} {pct}  {r['status']}")
    if "now" in fuse:
        print(f"[v1] 保险丝 40日超额 {fuse['now']:+.2f}% -> "
              f"{'触发' if fuse['trigger'] else '未触发'}")
    print(f"[v1] 产物: {os.path.relpath(OUT_MD, _DIR)} / {os.path.relpath(OUT_JSON, _DIR)}")


if __name__ == "__main__":
    main()
