# -*- coding: utf-8 -*-
"""永久组合 · 基准占比研究（回答「8 项该怎么分」）。

方法：不看「历史最优权重」（那是过拟合），而是看三件事
  ① 每项的真实风险量级（年化波动 / 最大回撤）
  ② 项间相关性（纳指 vs 标普 是不是同一个风险？A500 vs 科创 呢？）
  ③ 给定权重下的**风险贡献分解** —— 谁在真正决定组合的波动

数据：长窗口代理口径（2020-01 起 6.7 年，含 2022 股债双杀），
      短债/货基用常数收益（成立晚于窗口且本就是类现金工具）。

用法: python strategies/permanent/research/_alloc_study.py
输出: strategies/permanent/research/_alloc_report.md
"""
import io
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_DIR)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import xalpha as xa  # noqa: E402

_OUT = os.path.join(_DIR, "_alloc_report.md")
_START = "2020-02-01"
_BOND_S_YIELD = 0.020      # 短债近似（真实 012265 2021-05 才成立）
_CASH_YIELD = 0.014

# item -> 长窗口价格源（("px", 代码) / ("nav", 场外代码) / ("const", 年化)）
SRC = {
    "bond": None,  # 三条腿单独指定
    "divlv": ("nav", "008163"),
    "ndx": ("nav", "270042"),
    "spx": ("px", "SH513500"),
    "gold": ("nav", "000216"),
    "cash": ("const", _CASH_YIELD),
    "a500": ("px", "SH000510"),
    "kc": ("px", "SH000688"),
}
BOND_LEGS = [("bond_s", ("const", _BOND_S_YIELD)), ("bond_m", ("nav", "001512")),
             ("bond_l", ("nav", "003377"))]

ORDER = ["bond", "divlv", "ndx", "spx", "gold", "cash", "a500", "kc"]
NAME = {"bond": "短中长期国债", "divlv": "红利低波", "ndx": "纳指100", "spx": "标普500",
        "gold": "黄金", "cash": "货币基金", "a500": "中证A500", "kc": "科创50"}


def _sh(code):
    return ("SH" if code.startswith(("5", "6", "9")) else "SZ") + code


def _c(x):
    """相关系数格式化; 常数序列(标准差 0)返回 —。"""
    return "—" if x != x else f"{x:+.2f}"


def load(key):
    kind, val = key
    if kind == "const":
        return None, val
    if kind == "px":
        df = xa.get_daily(val, start="2019-06-01").dropna(subset=["close"])
        s = pd.Series([float(x) for x in df["close"]],
                      index=pd.to_datetime([str(d)[:10] for d in df["date"]]))
    else:
        # 必须用累计净值: 008163 分红使单位净值只 +7% 而累计 +78%
        f = xa.fundinfo(val)
        p = f.price.sort_values("date")
        tot = p["totvalue"].fillna(p["netvalue"]) if "totvalue" in p.columns else p["netvalue"]
        p = p.assign(_tot=tot).dropna(subset=["_tot"])
        s = pd.Series([float(x) for x in p["_tot"]],
                      index=pd.to_datetime([str(d)[:10] for d in p["date"]]))
    return s.sort_index(), None


def build_monthly():
    """-> DataFrame(index=月末, columns=8 项) 的月度简单收益。"""
    cols, consts = {}, {}
    for item in ORDER:
        if item == "bond":
            # 项内三等分：中债/长债用真实净值, 短债(2021-05 才成立)用年化常数复利曲线
            parts = [load(key)[0] for _, key in BOND_LEGS if key[0] != "const"]
            base = parts[0].to_frame("a").join(parts[1].to_frame("b"),
                                               how="outer").sort_index()
            base = base.ffill().bfill().dropna()
            norm = base["a"] / base["a"].iloc[0] + base["b"] / base["b"].iloc[0]
            days = (norm.index - norm.index[0]).days
            short = (1 + _BOND_S_YIELD) ** (days / 365.0)
            cols["bond"] = (norm + short) / 3.0
            continue
        kind, _ = SRC[item]
        if kind == "const":
            consts[(item, item)] = SRC[item][1]
            continue
        cols[item] = load(SRC[item])[0]

    # 月末对齐
    idx = None
    for s in cols.values():
        idx = s.index if idx is None else idx.union(s.index)
    idx = idx[idx >= pd.Timestamp(_START)]
    me = pd.Series(idx, index=idx).groupby([idx.year, idx.month]).last().values

    out = pd.DataFrame(index=pd.to_datetime(me))
    for item, s in cols.items():
        out[item] = s.reindex(out.index, method="ffill")

    # 转月度收益
    ret = out.pct_change(fill_method=None).dropna(how="all")
    # 常数腿补月度收益
    for (item, _), y in consts.items():
        ret[item] = (1 + y) ** (1 / 12) - 1
    return ret[ORDER]


def stats(r):
    r = pd.Series(np.asarray(r, dtype=float))
    n = len(r)
    nav = (1 + r).cumprod()
    cum = nav.iloc[-1] - 1
    ann = (1 + cum) ** (12 / n) - 1
    vol = r.std() * np.sqrt(12)
    peak = nav.cummax()
    mdd = (nav / peak - 1).min()
    sharpe = (ann - _CASH_YIELD) / vol if vol > 1e-9 else 0.0
    return dict(n=n, cum=cum, ann=ann, vol=vol, mdd=mdd, sharpe=sharpe, nav=nav)


def port(ret, w):
    ww = np.array([w[k] for k in ORDER], dtype=float)
    ww = ww / ww.sum()
    return (ret.values * ww).sum(axis=1)


def risk_contrib(ret, w):
    ww = np.array([w[k] for k in ORDER], dtype=float)
    ww = ww / ww.sum()
    cov = ret.cov().values * 12
    var = float(ww @ cov @ ww)
    if var <= 1e-12:
        return {k: 0.0 for k in ORDER}
    mrc = cov @ ww
    return {k: float(ww[i] * mrc[i] / var) for i, k in enumerate(ORDER)}


def candidates(ret):
    ann_vol = ret.std() * np.sqrt(12)
    # 风险平价：波动率倒数（常数腿波动 0 → 给下限保护）
    inv = {}
    for k in ORDER:
        v = float(ann_vol[k])
        inv[k] = 1.0 / max(v, 0.02)
    tot = sum(inv.values())
    rp = {k: inv[k] / tot * 100 for k in ORDER}

    return {
        "等权 12.5×8": {k: 12.5 for k in ORDER},
        "用户原案 30/20/10/10/10/10/5/5": {"bond": 30, "divlv": 20, "ndx": 10, "spx": 10,
                                   "gold": 10, "cash": 10, "a500": 5, "kc": 5},
        "平衡推荐 30/20/9/9/12/10/5/5": {"bond": 30, "divlv": 20, "ndx": 9, "spx": 9,
                                  "gold": 12, "cash": 10, "a500": 5, "kc": 5},
        "稳健 35/20/7/7/12/12/4/3": {"bond": 35, "divlv": 20, "ndx": 7, "spx": 7,
                                "gold": 12, "cash": 12, "a500": 4, "kc": 3},
        "进取 25/20/12/12/10/5/8/8": {"bond": 25, "divlv": 20, "ndx": 12, "spx": 12,
                                 "gold": 10, "cash": 5, "a500": 8, "kc": 8},
        "风险平价(波动倒数)": rp,
        "经典永久(Browne 映射)": {"bond": 37.5, "divlv": 0, "ndx": 6.25, "spx": 6.25,
                            "gold": 25, "cash": 25, "a500": 0, "kc": 0},
    }


def main():
    ret = build_monthly()
    print(f"[data] {ret.index[0]:%Y-%m} ~ {ret.index[-1]:%Y-%m}, {len(ret)} 个月")

    L = []
    A = L.append
    A("# 永久组合 · 基准占比研究")
    A("")
    A(f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 样本 {ret.index[0]:%Y-%m} ~ "
      f"{ret.index[-1]:%Y-%m}（{len(ret)} 个月，月度）")
    A(f"> 口径: A500→中证A500指数, 科创→科创50指数, 标普→513500, 短债/货基→常数"
      f"({_BOND_S_YIELD:.1%}/{_CASH_YIELD:.1%}); 其余腿=场外真实净值。未计费。")
    A("")

    # ---- 1 单项统计
    A("## 一、单项风险量级（长窗口）")
    A("")
    A("| 资产 | 年化收益 | 年化波动 | 最大回撤 | 夏普 | 与「权益篮子」相关 |")
    A("|---|---:|---:|---:|---:|---:|")
    eqbasket = ret[["ndx", "spx", "a500", "kc"]].mean(axis=1)
    for k in ORDER:
        s = stats(ret[k])
        cv = ret[k].corr(eqbasket)
        A(f"| {NAME[k]} | {s['ann']:.1%} | {s['vol']:.1%} | {s['mdd']:.1%} | "
          f"{s['sharpe']:.2f} | {_c(cv)} |")
    A("")
    A("> 「与权益篮子相关」= 与 (纳指+标普+A500+科创) 等权收益的相关系数。"
      "≥0.7 说明它本质是同一个风险因子，分散效果有限。")
    A("")

    # ---- 2 相关性
    A("## 二、相关性矩阵（月度收益）")
    A("")
    A("| | " + " | ".join(NAME[k] for k in ORDER) + " |")
    A("|---" * (len(ORDER) + 1) + "|")
    corr = ret.corr()
    for i in ORDER:
        A(f"| **{NAME[i]}** | " + " | ".join(_c(corr.loc[i, j]) for j in ORDER) + " |")
    A("")

    # ---- 3 候选方案
    cands = candidates(ret)
    A("## 三、候选方案对比")
    A("")
    A("| 方案 | 年化 | 波动 | 最大回撤 | 夏普 | 最差 12 个月 | 权益合计 |")
    A("|---|---:|---:|---:|---:|---:|---:|")
    eq_items = ["ndx", "spx", "a500", "kc"]
    rows = {}
    for name, w in cands.items():
        pr = port(ret, w)
        s = stats(pr)
        roll12 = (1 + pd.Series(pr, index=ret.index)).rolling(12).apply(np.prod, raw=True) - 1
        worst = roll12.min()
        eqw = sum(w[k] for k in eq_items)
        rows[name] = (s, w, roll12)
        A(f"| {name} | {s['ann']:.2%} | {s['vol']:.2%} | {s['mdd']:.1%} | "
          f"{s['sharpe']:.2f} | {worst:.1%} | {eqw:.0f}% |")
    A("")

    # ---- 4 风险贡献
    A("## 四、风险贡献分解（谁在决定组合波动）")
    A("")
    A("> 资金占比 ≠ 风险占比。下表每行 = 该方案下各资产对组合方差的贡献占比。")
    A("")
    hdr = "| 方案 | " + " | ".join(NAME[k] for k in ORDER) + " |"
    A(hdr)
    A("|---" * (len(ORDER) + 1) + "|")
    for name, w in cands.items():
        rc = risk_contrib(ret, w)
        cells = []
        for k in ORDER:
            mark = "**" if rc[k] >= 0.25 else ""
            cells.append(f"{mark}{rc[k]:.0%}{mark}")
        A(f"| {name} | " + " | ".join(cells) + " |")
    A("")
    A("> 加粗 = 单项风险贡献 ≥25%，意味着这一项的涨跌基本决定了组合体验。")
    A("")

    # ---- 5 逐年
    A("## 五、逐年收益")
    A("")
    A("| 方案 | " + " | ".join(str(y) for y in sorted(set(ret.index.year))) + " |")
    A("|---" * (len(set(ret.index.year)) + 1) + "|")
    for name, w in cands.items():
        pr = pd.Series(port(ret, w), index=ret.index)
        cells = []
        for y in sorted(set(ret.index.year)):
            yy = (1 + pr[pr.index.year == y]).prod() - 1
            cells.append(f"{yy:+.1%}")
        A(f"| {name} | " + " | ".join(cells) + " |")
    A("")

    A("> 免责声明: 历史统计不代表未来; 代理口径存在跟踪误差; 不构成投资建议。")
    md = "\n".join(L)
    with open(_OUT, "w", encoding="utf-8") as f:
        f.write(md + "\n")
    print(f"[ok] {_OUT}")
    for name, w in cands.items():
        s = rows[name][0]
        print(f"  {name:32s} 年化 {s['ann']:6.2%} 波动 {s['vol']:5.2%} "
              f"回撤 {s['mdd']:7.1%} 夏普 {s['sharpe']:.2f}")


if __name__ == "__main__":
    main()
