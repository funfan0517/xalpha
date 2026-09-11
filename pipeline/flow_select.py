# -*- coding: utf-8 -*-
"""Phase 4 · select: 依回测结果自动生成分级名单（通用，按 strategies.json 当前策略取路径）。

A = 样本>=3 且 策略年化>0 且 超额(策略-基准)>0  -> 核心，用该策略主推
B = 样本>=3 且 超额>0 且 策略年化<=0           -> 防守候选，仅供已持仓参考
其余 = 不适合该策略（买入持有/定投替代）
用法: python pipeline/flow_select.py [strategy_name]   默认 lights
"""
import io
import json
import os
import sys
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 仓库根（随项目移动，勿写死）
CFG = os.path.join(ROOT, "pipeline", "strategies.json")
MIN_YEARS = 3.0


def pct(x):
    return f"{x * 100:+.2f}%"


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "lights"
    cfg = json.load(open(CFG, encoding="utf-8"))
    if name not in cfg["strategies"]:
        sys.exit(f"未知策略 {name}")
    st = cfg["strategies"][name]
    # 读写用绝对路径；写进产物的 source/输出说明用**相对仓库根**的路径，保证机器无关
    BT_REL = st["backtest"]["raw_out"]
    MD_REL = st["select"]["out_active_md"]
    JSON_REL = st["select"]["out_active_json"]
    BT = os.path.join(ROOT, BT_REL)
    MD = os.path.join(ROOT, MD_REL)
    JSON = os.path.join(ROOT, JSON_REL)

    raw = open(BT, "rb").read()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8")
    rows = []
    for ln in text.splitlines():
        if '"ok"' not in ln:
            continue
        try:
            r = json.loads(ln)
            if r.get("ok"):
                rows.append(r)
        except json.JSONDecodeError:
            pass

    A, B, NA = [], [], []
    for r in rows:
        if r.get("years", 0) < MIN_YEARS:
            NA.append(r)
            continue
        excess = r["st_ann"] - r["base_ann"]
        if excess > 0 and r["st_ann"] > 0:
            A.append(r)
        elif excess > 0:
            B.append(r)
        else:
            NA.append(r)

    def key(r):
        return -r["st_ann"]

    A.sort(key=key)
    B.sort(key=key)
    NA.sort(key=key)

    def table(rs):
        lines = []
        for r in rs:
            off = r.get("off") or r["theme"]
            lines.append(
                f"| {r['code']} | {r['theme']} | {off} | {r['years']} | "
                f"{pct(r['base_ann'])} | **{pct(r['st_ann'])}** | {pct(r['st_ann']-r['base_ann'])} | "
                f"{pct(r['base_mdd'])} | {pct(r['st_mdd'])} |"
            )
        return lines

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    L = []
    L.append(f"# {name} 策略执行标的池（自动生成）{now}")
    L.append("")
    L.append(f"> 来源: 策略回测 {BT_REL} · 判定: A=策略年化>0 且 超额>0；B=超额>0 但策略年化≤0(防守)")
    L.append(f"> 输出: 本文件 + {JSON_REL} · 仅供流程参考，非投资建议")
    L.append("")
    L.append("## A 类 · 核心（用该策略主推）")
    L.append("")
    L.append("| 场内 | 主题 | 场外基金 | 样本年 | 基准年化 | 策略年化 | 超额 | 基准回撤 | 策略回撤 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    L.extend(table(A))
    L.append("")
    L.append("## B 类 · 防守候选（仅参考，不主动加仓）")
    L.append("")
    L.append("| 场内 | 主题 | 场外基金 | 样本年 | 基准年化 | 策略年化 | 超额 | 基准回撤 | 策略回撤 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    L.extend(table(B))
    L.append("")
    L.append("## 不适合该策略（买入持有/定投替代，不推送该策略信号）")
    L.append("")
    for r in NA:
        L.append(f"- `{r['code']}` {r['theme']}：样本 {r['years']} 年，超额 {pct(r['st_ann']-r['base_ann'])}")
    txt = "\n".join(L) + "\n"
    with open(MD, "w", encoding="utf-8") as f:
        f.write(txt)

    data = {
        "strategy": name,
        "generated_at": now,
        "source": BT_REL,
        "A": [{"code": r["code"], "theme": r["theme"], "off": r.get("off") or r["theme"]} for r in A],
        "B": [{"code": r["code"], "theme": r["theme"], "off": r.get("off") or r["theme"]} for r in B],
        "not_suitable": [{"code": r["code"], "theme": r["theme"]} for r in NA],
    }
    with open(JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"== select ==")
    print(f"A(核心): {len(A)} -> {[r['code'] for r in A]}")
    print(f"B(防守): {len(B)} -> {[r['code'] for r in B]}")
    print(f"NA(不适合): {len(NA)}")
    print(f"写入 {MD} / {JSON}")


if __name__ == "__main__":
    main()
