# -*- coding: utf-8 -*-
"""稳健资产（债腿 30pp + 现金腿 10pp）· 每日扫描。

输入  ``data/_universe.md`` 第六类（债券 601–611）+ 第七类（现金 701–704）的场外累计净值
处理  ``engine.evaluate`` —— 档位 / 各腿 pp / 卫星条件 / 连跌纪律
输出
  ``strategies/bond_cash/daily/_bond_cash_daily.md``   人读报告
  ``strategies/bond_cash/daily/_bond_cash_daily.json`` 机器可读（看板板块6 直接读它）
  ``strategies/bond_cash/daily/_universe_bond_cash_active.{md,json}``  固定 15 只名单

用法
  python strategies/bond_cash/daily/_daily_scan.py            # 用当日缓存
  python strategies/bond_cash/daily/_daily_scan.py --force     # 强制重抓净值
  python strategies/bond_cash/daily/_daily_scan.py --gate      # 按动量开关口径出档位（默认关）
"""
import argparse
import io
import json
import os
import sys
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_HERE = os.path.dirname(os.path.abspath(__file__))          # strategies/bond_cash/daily
_PKG = os.path.dirname(_HERE)                               # strategies/bond_cash
_ROOT = os.path.dirname(os.path.dirname(_PKG))              # 仓库根
for _p in (_ROOT, _PKG):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import rule      # noqa: E402
import data      # noqa: E402
import engine    # noqa: E402

OUT_MD = os.path.join(_HERE, "_bond_cash_daily.md")
OUT_JSON = os.path.join(_HERE, "_bond_cash_daily.json")
ACT_MD = os.path.join(_HERE, "_universe_bond_cash_active.md")
ACT_JSON = os.path.join(_HERE, "_universe_bond_cash_active.json")


def _f(v, nd=2, suffix=""):
    return "—" if v is None else f"{v:.{nd}f}{suffix}"


def render(ctx):
    cfg = rule.CFG
    g = ctx["gate"]
    L = []
    A = L.append
    A("# 稳健资产 · 债腿 30pp + 现金腿 10pp")
    A("")
    A(f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 数据截止 **{ctx['as_of']}**（场外累计净值口径）· "
      f"口径 = 债腿 {cfg['sleeve_bond_pp']:.0f}pp + 现金腿 {cfg['sleeve_cash_pp']:.0f}pp。")
    A("")
    A("## 一、当前配置")
    A("")
    A("| 腿 | 标的 | 代码 | 占腿 | 占总资产 | 角色 |")
    A("|---|---|---|---:|---:|---|")
    for code, pp in sorted(ctx["bond"]["items"].items(), key=lambda x: -x[1]):
        A(f"| 债腿 | {rule.POOL[code]['name']} | `{code}` | {pp / cfg['sleeve_bond_pp'] * 100:.0f}% | "
          f"**{pp:.1f}pp** | {rule.ROLE_LABEL[rule.POOL[code]['role']]} |")
    for code, pp in sorted(ctx["cash"]["items"].items(), key=lambda x: -x[1]):
        A(f"| 现金腿 | {rule.POOL[code]['name']} | `{code}` | {pp / cfg['sleeve_cash_pp'] * 100:.0f}% | "
          f"**{pp:.1f}pp** | 现金腿 |")
    A("")
    A(f"> 债腿合计 **{sum(ctx['bond']['items'].values()):.1f}pp** + 现金腿 "
      f"**{sum(ctx['cash']['items'].values()):.1f}pp** = "
      f"{sum(ctx['bond']['items'].values()) + sum(ctx['cash']['items'].values()):.1f}pp。")
    A("")

    A("## 二、动量开关（可选，默认关闭）")
    A("")
    A(f"- 读数：中债 7-10 年国开（用 `003376` 累计净值代理）近 **{g['win']}** 个交易日累计收益 "
      f"**{g['mom']:+.3f}%**")
    A(f"- 影子档位：**{'进攻档' if g['shadow'] else '防守档'}**"
      f"（死区 ±{g['deadband']:.1f}%；{'今日翻转' if g['flip'] else '今日未翻转'}）")
    A(f"- 当前是否生效：**{'是' if g['on'] else '否（固定哑铃，不做择时）'}** → {g['text']}")
    A("")
    A("> 默认关闭的理由见 `README.md` §4：本仓库用真实壳复现不出文档 §3-1 的增量 —— "
      "零死区时 10 年翻 50+ 次（文档同窗 20 次），成本把毛 alpha 吃穿；"
      f"文档闸门的价值集中在 2016-11 债灾，而 `003376` 2016-09-26 成立、加 {g['win']} 日预热后"
      "第一个信号已在债灾中段，拿真实壳吃不到。")
    A("")

    A("## 三、纪律读数（文档 §3-5）")
    A("")
    A(f"- {ctx['discipline']['text']}")
    A("")

    A("## 四、卫星（默认 0）")
    A("")
    A("| 代码 | 标的 | 上限 | 条件 | 当前 |")
    A("|---|---|---:|---|---|")
    for s in ctx["satellites"]:
        A(f"| `{s['code']}` | {s['name']} | {s['max_pp']:.1f}pp | {s['cond']} | {s['state']} |")
    A("")

    A("## 五、全谱系监控（11 债 + 4 现金）")
    A("")
    A("| # | 代码 | 标的 | 主题 | 角色 | 净值 | 20日 | 250日 | 备注 |")
    A("|---|---|---|---|---|---:|---:|---:|---|")
    for r in ctx["rows"]:
        A(f"| {r['num']} | `{r['code']}` | {r['name']} | {r['theme']} | {r['role_label']} | "
          f"{_f(r['nav'], 4)} | {_f(r['r20'], 2, '%')} | {_f(r['r250'], 2, '%')} | {r['note']} |")
    A("")
    A("> 全谱系只作监控：`spare` 是替补候选（不动手）、`satellite` 需条件、`excluded` 不计入债腿。"
      "债腿**只用 6xx**、现金腿**只用 7xx**，两档不混用。")
    A("")
    A("> 免责声明：机械规则输出，仅供研究参考，不构成投资建议。")
    return "\n".join(L)


def active_md(ctx):
    L = ["# 稳健资产 · 固定 15 只名单（6xx 债 + 7xx 现金）", "",
         f"> 生成 {datetime.now():%Y-%m-%d} · 由《债券全谱系与操作规则》+ `data/_universe.md` "
         "第六/七类直接定档（非回测 select）。", "",
         "| # | 代码 | 名称 | 主题 | 角色 | 跟踪指数 / 基准 |", "|---|---|---|---|---|---|"]
    for code in rule.ORDER:
        p = rule.POOL[code]
        L.append(f"| {p['num']} | `{code}` | {p['name']} | {p['theme']} | "
                 f"{rule.ROLE_LABEL[p['role']]} | {p['index']} |")
    L += ["", "> 对应 JSON: `strategies/bond_cash/daily/_universe_bond_cash_active.json`"]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="强制重抓净值")
    ap.add_argument("--gate", action="store_true", help="按动量开关口径出档位（默认关）")
    args = ap.parse_args(argv)

    navs, asof = data.load_pool(force=args.force)
    ctx = engine.evaluate(navs, gate_on=True if args.gate else None)
    ctx["data_asof"] = asof

    os.makedirs(_HERE, exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(render(ctx) + "\n")

    snap = dict(
        strategy="稳健资产 · 债腿30pp+现金腿10pp",
        as_of=ctx["as_of"],
        generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
        config=ctx["config"],
        gate=ctx["gate"],
        discipline=ctx["discipline"],
        bond=ctx["bond"],
        cash=ctx["cash"],
        satellites=ctx["satellites"],
        rows=ctx["rows"],
        data_asof=asof,
    )
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)

    with open(ACT_MD, "w", encoding="utf-8") as f:
        f.write(active_md(ctx) + "\n")
    with open(ACT_JSON, "w", encoding="utf-8") as f:
        json.dump(dict(
            strategy="稳健资产 · 债 + 现金",
            as_of=ctx["as_of"],
            source="data/_universe.md 第六类(6xx) + 第七类(7xx)",
            items=[dict(num=rule.POOL[c]["num"], code=c, name=rule.POOL[c]["name"],
                        theme=rule.POOL[c]["theme"], role=rule.POOL[c]["role"],
                        index=rule.POOL[c]["index"]) for c in rule.ORDER],
        ), f, ensure_ascii=False, indent=2)

    print(f"[ok] {OUT_MD}")
    print(f"[ok] {OUT_JSON}")
    g = ctx["gate"]
    print(f"  as_of {ctx['as_of']} · 动量 {g['mom']:+.3f}% · 影子 {'进攻档' if g['shadow'] else '防守档'}"
          f" · 生效 {'是' if g['on'] else '否'}")
    for code, pp in sorted(ctx["bond"]["items"].items(), key=lambda x: -x[1]):
        print(f"  债腿 {code} {rule.POOL[code]['name']:22s} {pp:5.1f}pp")
    for code, pp in sorted(ctx["cash"]["items"].items(), key=lambda x: -x[1]):
        print(f"  现金 {code} {rule.POOL[code]['name']:22s} {pp:5.1f}pp")
    return 0


if __name__ == "__main__":
    sys.exit(main())
