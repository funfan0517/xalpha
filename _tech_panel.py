# -*- coding: utf-8 -*-
"""为任意场内标的（指数 / ETF / LOF / 股票）生成「炒股五大指标」决策看板。

获取: 均线 MA、成交量 VOL、MACD、KDJ、BOLL 布林带（历史 + 实时近似）
输出: 同目录 tech_panel_<CODE>.html 看板 + 控制台文字简报

用法:
    python _tech_panel.py SH000300
    python _tech_panel.py SH510300 2024-01-01
    python _tech_panel.py SH000300 --no-rt

说明:
    * 历史由 xa.vinfo(代码) 包装 get_daily 取得，自带 open/close/high/low/volume。
    * 实时由 xa.get_rt 取最新价，并追加到最后一日复算当日指标（盘中近似，
      收盘后最准；xalpha 不提供分钟级数据）。
    * 指数(如沪深300)的 volume 是成分股成交量汇总；要看真实 ETF 成交量，
      用跟踪它的场内 ETF，例如 SH510300（华泰柏瑞沪深300ETF）。
    * 内置 KDJ/BOLL 只用收盘价（无日内 high/low），与看盘软件会有偏差。

全部输出均为机械规则的客观结果，不构成任何投资建议。
"""
import os
import sys

import pandas as pd
import xalpha as xa
from pyecharts import options as opts
from pyecharts.charts import Kline, Line, Bar, Grid, Page
from pyecharts.commons.utils import JsCode

ROOT = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------------- #
# 指标计算
# --------------------------------------------------------------------------- #
def calc_indicators(f):
    """在 vinfo 对象的 price 表上批量计算五大指标（close 尺度）。"""
    p = f.price.reset_index(drop=True)  # KDJ/RSI 内部用 .loc[i]，需 RangeIndex
    for w in (5, 10, 20, 60):
        f.ma(w, col="close")
    f.macd(col="close")
    f.kdj(col="close")
    f.boll(20, col="close")
    # 成交量均线（量价配合用）
    f.price["VOL_MA5"] = f.price["volume"].rolling(5).mean()
    return f.price


# --------------------------------------------------------------------------- #
# 信号判断
# --------------------------------------------------------------------------- #
def detect_cross(a, b):
    """返回 (state, days_ago): state ∈ 'gold'/'dead'/'bull'/'bear'/None。
    gold=近期金叉, dead=近期死叉, bull=持续多头(无死叉), bear=持续空头。
    """
    diff = a - b
    sign = (diff > 0).astype(int)
    cross = sign.diff().fillna(0)
    # 最近一次交叉位置
    idx = cross[cross != 0].index
    if len(idx) == 0:
        return ("bull" if diff.iloc[-1] > 0 else "bear"), None
    last = idx[-1]
    days_ago = len(a) - 1 - last
    state = "gold" if diff.iloc[last] > 0 else "dead"
    # 若交叉发生在很久以前且无新交叉，视为持续排列
    if days_ago > 30:
        state = "bull" if diff.iloc[-1] > 0 else "bear"
    return state, days_ago


def volume_price_state(vol_last, vol_ma, chg):
    """量价配合分类。"""
    ratio = vol_last / vol_ma if vol_ma else 1.0
    if ratio > 1.5:
        vol_t = "放量"
    elif ratio < 0.7:
        vol_t = "缩量"
    else:
        vol_t = "平量"
    up = chg > 0
    table = {
        ("放量", True): ("放量上涨：主动买入，趋势强", "good"),
        ("放量", False): ("放量下跌：恐慌抛售，趋势弱", "bad"),
        ("缩量", True): ("缩量上涨：惜售/上攻乏力", "neutral"),
        ("缩量", False): ("缩量下跌：抛压减轻/阴跌", "neutral"),
        ("平量", True): ("平量小涨：方向不明", "neutral"),
        ("平量", False): ("平量小跌：方向不明", "neutral"),
    }
    return vol_t, ratio, table[(vol_t, up)]


def kdj_state(k, j):
    if k > 80 or j > 100:
        return "超买区（高位，注意回落/钝化）", "bad"
    if k < 20:
        return "超卖区（低位，关注反弹机会）", "good"
    return "中性区", "neutral"


def boll_state(close, upper, lower):
    pos = (close - lower) / (upper - lower) if (upper - lower) else 0.5
    if pos > 0.8:
        return pos, "接近上轨：遇阻回落压力", "bad"
    if pos < 0.2:
        return pos, "接近下轨：反弹支撑", "good"
    return pos, "通道中部：方向待选", "neutral"


def trend_state(close, mas):
    """价格相对各均线的位置，判断强弱。"""
    above = sum(close > m for m in mas if pd.notna(m))
    n = sum(1 for m in mas if pd.notna(m))
    if above == n:
        return "多头强：价格在全部均线之上", "good"
    if above == 0:
        return "空头强：价格在全部均线之下", "bad"
    return f"震荡：价格位于 {above}/{n} 条均线之上", "neutral"


# --------------------------------------------------------------------------- #
# 图表
# --------------------------------------------------------------------------- #
def _vol_bar(x, vol, open_, close_):
    bar = (
        Bar()
        .add_xaxis(x)
        .add_yaxis(
            "成交量",
            list(vol),
            itemstyle_opts=opts.ItemStyleOpts(
                color=JsCode(
                    "function(p){return p.data[1]>=p.data[2]?'#ef232a':'#14b143';}"
                )
            ),
        )
    )
    # 把 open/close 作为附加维度用于着色判定
    bar.add_yaxis("__oc", list(zip(open_, close_)), is_selected=False)
    return bar


def build_price_chart(p):
    x = [d.strftime("%Y-%m-%d") for d in p["date"]]
    kline = (
        Kline()
        .add_xaxis(x)
        .add_yaxis(
            "K线",
            list(zip(p["open"], p["close"], p["low"], p["high"])),
            itemstyle_opts=opts.ItemStyleOpts(
                color="#ef232a",
                color0="#14b143",
                border_color="#ef232a",
                border_color0="#14b143",
            ),
        )
        .set_global_opts(
            title_opts=opts.TitleOpts(title="价格 / 均线 / 布林带", pos_left="center"),
            xaxis_opts=opts.AxisOpts(type_="category", grid_index=0),
            yaxis_opts=opts.AxisOpts(grid_index=0),
            datazoom_opts=[
                opts.DataZoomOpts(type_="inside", xaxis_index=[0, 1]),
                opts.DataZoomOpts(type_="slider", xaxis_index=[0, 1]),
            ],
            tooltip_opts=opts.TooltipOpts(trigger="axis", axis_pointer_type="cross"),
            legend_opts=opts.LegendOpts(pos_top="5%"),
        )
    )
    for col, name in [
        ("MA5", "MA5"),
        ("MA10", "MA10"),
        ("MA20", "MA20"),
        ("MA60", "MA60"),
        ("BOLL_UPPER", "BOLL上轨"),
        ("BOLL_LOWER", "BOLL下轨"),
    ]:
        if col in p:
            kline = kline.overlap(
                Line()
                .add_xaxis(x)
                .add_yaxis(name, list(p[col]), is_symbol_show=False, is_smooth=False)
            )
    # 成交量（按涨跌着色）
    vol = (
        Bar()
        .add_xaxis(x)
        .add_yaxis(
            "成交量",
            [list(v) for v in zip(p["volume"], p["open"], p["close"])],
            itemstyle_opts=opts.ItemStyleOpts(
                color=JsCode(
                    "function(p){return p.data[1]>=p.data[2]?'#ef232a':'#14b143';}"
                )
            ),
            tooltip_opts=opts.TooltipOpts(
                formatter=JsCode("function(p){return p.data[1].toFixed(0);}")
            ),
        )
        .set_global_opts(
            xaxis_opts=opts.AxisOpts(type_="category", grid_index=1),
            yaxis_opts=opts.AxisOpts(grid_index=1),
            datazoom_opts=[
                opts.DataZoomOpts(type_="inside", xaxis_index=[0, 1]),
                opts.DataZoomOpts(type_="slider", xaxis_index=[0, 1]),
            ],
            legend_opts=opts.LegendOpts(pos_top="5%"),
        )
    )
    grid = (
        Grid()
        .add(kline, grid_opts=opts.GridOpts(pos_left="8%", pos_right="5%", height="60%"))
        .add(vol, grid_opts=opts.GridOpts(pos_left="8%", pos_right="5%", pos_top="72%", height="18%"))
    )
    return grid


def build_macd_chart(p):
    x = [d.strftime("%Y-%m-%d") for d in p["date"]]
    line = (
        Line()
        .add_xaxis(x)
        .add_yaxis("DIF", list(p["MACD_DIFF_12_26"]), is_symbol_show=False, is_smooth=False)
        .add_yaxis("DEA", list(p["MACD_DEM_12_26"]), is_symbol_show=False, is_smooth=False)
        .set_global_opts(
            title_opts=opts.TitleOpts(title="MACD (12,26,9)", pos_left="center"),
            tooltip_opts=opts.TooltipOpts(trigger="axis", axis_pointer_type="cross"),
            legend_opts=opts.LegendOpts(pos_top="5%"),
            datazoom_opts=[opts.DataZoomOpts(type_="inside"), opts.DataZoomOpts(type_="slider")],
        )
    )
    bar = (
        Bar()
        .add_xaxis(x)
        .add_yaxis(
            "MACD柱",
            [list(v) for v in zip(p["MACD_OSC_12_26"], p["MACD_DIFF_12_26"], p["MACD_DEM_12_26"])],
            itemstyle_opts=opts.ItemStyleOpts(
                color=JsCode(
                    "function(p){return p.data[1]-p.data[2]>=0?'#ef232a':'#14b143';}"
                )
            ),
        )
        .set_global_opts(
            tooltip_opts=opts.TooltipOpts(
                formatter=JsCode("function(p){return p.data[1].toFixed(3);}")
            ),
            datazoom_opts=[opts.DataZoomOpts(type_="inside"), opts.DataZoomOpts(type_="slider")],
        )
    )
    grid = (
        Grid()
        .add(line, grid_opts=opts.GridOpts(pos_left="8%", pos_right="5%", height="55%"))
        .add(bar, grid_opts=opts.GridOpts(pos_left="8%", pos_right="5%", pos_top="68%", height="22%"))
    )
    return grid


def build_kdj_chart(p):
    x = [d.strftime("%Y-%m-%d") for d in p["date"]]
    n = len(x)
    line = (
        Line()
        .add_xaxis(x)
        .add_yaxis("K", [round(v * 100, 2) for v in p["KDJ_K"]], is_symbol_show=False)
        .add_yaxis("D", [round(v * 100, 2) for v in p["KDJ_D"]], is_symbol_show=False)
        .add_yaxis("J", [round(v * 100, 2) for v in p["KDJ_J"]], is_symbol_show=False)
        .add_yaxis("超买80", [80] * n, is_symbol_show=False, linestyle_opts=opts.LineStyleOpts(type_="dashed"))
        .add_yaxis("超卖20", [20] * n, is_symbol_show=False, linestyle_opts=opts.LineStyleOpts(type_="dashed"))
        .set_global_opts(
            title_opts=opts.TitleOpts(title="KDJ (9,3,3)", pos_left="center"),
            tooltip_opts=opts.TooltipOpts(trigger="axis", axis_pointer_type="cross"),
            legend_opts=opts.LegendOpts(pos_top="5%"),
            datazoom_opts=[opts.DataZoomOpts(type_="inside"), opts.DataZoomOpts(type_="slider")],
        )
    )
    return line


# --------------------------------------------------------------------------- #
# 文字简报
# --------------------------------------------------------------------------- #
def build_report(p, rt=None):
    cur = p.iloc[-1]
    prev = p.iloc[-2]
    close = float(cur["close"])
    chg = (close - float(prev["close"])) / float(prev["close"])
    mas = [cur.get("MA5"), cur.get("MA10"), cur.get("MA20"), cur.get("MA60")]

    rows = []

    # 趋势
    t_state, t_tone = trend_state(close, mas)
    rows.append(("趋势强弱", t_state, t_tone))

    # 均线
    ma_txt = " / ".join(
        f"{int(w)}日={cur['MA'+str(w)]:.2f}" for w in (5, 10, 20, 60) if f"MA{str(w)}" in cur
    )
    rows.append(("均线 MA", ma_txt, "neutral"))

    # 量价配合
    vol_t, vol_ratio, (vp_txt, vp_tone) = volume_price_state(
        float(cur["volume"]), float(cur["VOL_MA5"]), chg
    )
    rows.append(("量价配合", f"{vol_t}(量比{vol_ratio:.2f}) · {vp_txt}", vp_tone))

    # MACD 金叉死叉
    macd_state, macd_days = detect_cross(p["MACD_DIFF_12_26"], p["MACD_DEM_12_26"])
    macd_map = {
        "gold": ("金叉（看多）", "good"),
        "dead": ("死叉（看空）", "bad"),
        "bull": ("多头排列（DIF>DEA）", "good"),
        "bear": ("空头排列（DIF<DEA）", "bad"),
    }
    m_txt, m_tone = macd_map[macd_state]
    if macd_days is not None and macd_state in ("gold", "dead"):
        m_txt += f"（约{macd_days}日前）"
    m_txt += f"  DIF={cur['MACD_DIFF_12_26']:.2f} DEA={cur['MACD_DEM_12_26']:.2f} 柱={cur['MACD_OSC_12_26']:.2f}"
    rows.append(("MACD", m_txt, m_tone))

    # KDJ 金叉死叉 + 超买超卖
    kdj_cross, kdj_days = detect_cross(p["KDJ_K"], p["KDJ_D"])
    kdj_c_txt = {"gold": "金叉", "dead": "死叉", "bull": "K>D", "bear": "K<D"}[kdj_cross]
    if kdj_days is not None and kdj_cross in ("gold", "dead"):
        kdj_c_txt += f"(约{kdj_days}日前)"
    kdj_ob_txt, kdj_ob_tone = kdj_state(float(cur["KDJ_K"]) * 100, float(cur["KDJ_J"]) * 100)
    kdj_txt = f"{kdj_c_txt} · K={cur['KDJ_K']*100:.1f} D={cur['KDJ_D']*100:.1f} J={cur['KDJ_J']*100:.1f} · {kdj_ob_txt}"
    rows.append(("KDJ", kdj_txt, kdj_ob_tone))

    # BOLL
    boll_pos, boll_txt, boll_tone = boll_state(
        close, float(cur["BOLL_UPPER"]), float(cur["BOLL_LOWER"])
    )
    boll_full = (
        f"位置{boll_pos:.0%} · 上轨{cur['BOLL_UPPER']:.2f} 中轨{cur['MA20']:.2f} "
        f"下轨{cur['BOLL_LOWER']:.2f} · {boll_txt}"
    )
    rows.append(("BOLL 布林", boll_full, boll_tone))

    return rows, close, chg


def render_html(page, rows, name, code, close, chg, rt_time):
    out = os.path.join(ROOT, f"tech_panel_{code}.html")
    page.render(out)
    # 注入文字简报到 <body> 顶部
    tone_color = {"good": "#14b143", "bad": "#ef232a", "neutral": "#888"}
    items = "".join(
        f'<div class="row"><span class="k">{k}</span>'
        f'<span class="v" style="color:{tone_color[t]}">{v}</span></div>'
        for k, v, t in rows
    )
    summary = (
        f'<div class="panel">'
        f'<div class="title">{name}（{code}）　收盘 {close:.2f}　'
        f'<span style="color:{"#ef232a" if chg>=0 else "#14b143"}">'
        f'{"+" if chg>=0 else ""}{chg:.2%}</span>'
        f'　{rt_time}</div>{items}'
        f'<div class="foot">机械规则输出，非投资建议</div></div>'
    )
    css = (
        "<style>"
        ".panel{font-family:-apple-system,'Microsoft YaHei',sans-serif;"
        "max-width:1080px;margin:12px auto;padding:14px 18px;"
        "background:#fafafa;border:1px solid #eee;border-radius:10px;box-shadow:0 1px 4px #0001}"
        ".title{font-size:18px;font-weight:700;margin-bottom:8px}"
        ".row{display:flex;padding:5px 0;border-bottom:1px dashed #eee;font-size:14px}"
        ".k{width:90px;color:#555;flex:none}"
        ".v{flex:1;font-weight:600}"
        ".foot{margin-top:8px;color:#aaa;font-size:12px}"
        "</style>"
    )
    with open(out, "r", encoding="utf-8") as fh:
        html = fh.read()
    html = html.replace("<head>", f"<head>{css}", 1)
    html = html.replace("<body>", f"<body>{summary}", 1)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    return out


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    code = args[0] if len(args) > 0 else "SH000300"
    start = args[1] if len(args) > 1 else "2024-01-01"
    use_rt = "--no-rt" not in flags

    print(f"== 获取 {code} 历史行情（自 {start}）==")
    f = xa.vinfo(code, start=start)
    p = calc_indicators(f)

    rt_time = "历史数据"
    if use_rt:
        try:
            rt = xa.get_rt(code)
            live = float(rt["current"])
            last = p.iloc[-1].copy()
            last["close"] = live
            p2 = pd.concat([p.iloc[:-1], last.to_frame().T], ignore_index=True)
            f.price = p2
            calc_indicators(f)  # 复算当日指标
            p = f.price
            rt_time = f"实时 {rt.get('time','')}  最新价 {live:.2f} ({rt.get('percent','?')}%)"
            print(f"== 已叠加实时价 {live:.2f} 复算当日指标 ==")
        except Exception as e:  # 实时为可选项，失败不影响历史看板
            print(f"  实时获取失败（仅用历史）: {type(e).__name__}: {e}")

    rows, close, chg = build_report(p, None)
    price_chart = build_price_chart(p)
    macd_chart = build_macd_chart(p)
    kdj_chart = build_kdj_chart(p)
    page = Page(layout=Page.SimplePageLayout)
    page.add(price_chart, macd_chart, kdj_chart)
    out = render_html(page, rows, f.name, code, close, chg, rt_time)

    # 控制台简报
    print(f"\n标的: {f.name} ({code})  收盘 {close:.2f}  ({chg:+.2%})  {rt_time}")
    print("-" * 60)
    for k, v, _ in rows:
        print(f"  {k:<10}: {v}")
    print("-" * 60)
    print(f"看板已生成: {out}")


if __name__ == "__main__":
    main()
