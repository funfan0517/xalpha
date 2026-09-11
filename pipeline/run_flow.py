# -*- coding: utf-8 -*-
"""Pipeline orchestrator —— 按阶段驱动一个策略的完整生命周期。

用法(在 g:/xalpha 下):
  python pipeline/run_flow.py list
  python pipeline/run_flow.py universe                      # 公共标的池摘要
  python pipeline/run_flow.py rules                         # 规则化定义(打印)
  python pipeline/run_flow.py backtest --codes 588000,...   # 回测(追加进 data/_bt_out.jsonl)
  python pipeline/run_flow.py backtest --codes 588000,... --fresh   # 清空重建后再跑
  python pipeline/run_flow.py backtest --report             # 仅用现有 jsonl 重新生成回测报告
  python pipeline/run_flow.py select                        # 自动生成 A/B 分级名单
  python pipeline/run_flow.py daily                         # 每日信号(14:05 由 automation 自动调用本入口亦可)
"""
import io
import json
import os
import subprocess
import sys
import argparse

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = "g:/xalpha"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
CFG = os.path.join(ROOT, "pipeline", "strategies.json")
config = json.load(open(CFG, encoding="utf-8"))


def strategy(name):
    if name not in config["strategies"]:
        sys.exit(f"未知策略 {name}，可用: {list(config['strategies'])}")
    return config["strategies"][name]


def gate(cond, msg):
    print(("[OK] " if cond else "[X] ") + msg)
    return cond


def cmd_phases(args):
    sub = ["universe", "rules", "backtest", "select", "daily"]
    print("可用策略:", list(config["strategies"]))
    print("阶段流程:")
    for i, s in enumerate(sub, 1):
        print(f"  {i}. {s}")
    print("用法: python pipeline/run_flow.py <phase> [--strategy lights] [选项]")


def cmd_universe(args):
    st = strategy(args.strategy)
    md = os.path.join(ROOT, st["universe"]["offshore_pool"])
    n_off = 0
    if os.path.exists(md):
        import re
        n_off = sum(1 for l in open(md, encoding="utf-8")
                    if re.match(r"^\|\s*\d+\s*\|", l))
    act_json = os.path.join(ROOT, st["select"]["out_active_json"])
    if os.path.exists(act_json):
        aj = json.load(open(act_json, encoding="utf-8"))
        a_codes = [x["code"] for x in aj["A"]]
        b_codes = [x["code"] for x in aj["B"]]
        print(f"公共池(场外): {os.path.basename(md)} ~{n_off} 行")
        from pipeline import universe
        print(f"场内内池(唯一池派生): {len(universe.inner_rows())} 只")
        print(f"分级名单(生成 {aj['generated_at']}): A={len(a_codes)} B={len(b_codes)}")
        print(f"A 每日执行: {a_codes}")
    else:
        gate(False, f"缺少分级名单 {act_json}，请先运行 select")


def cmd_rules(args):
    st = strategy(args.strategy)
    print(f"策略: {st['name']}")
    print(f"方法论: {st['source_methodology']}")
    print("规则化(灯评分):")
    for i, r in enumerate(st["rules"], 1):
        print(f"  {i}. {r}")
    print(f"决策阈值: {json.dumps(st.get('thresholds', {}), ensure_ascii=False)}")
    bt = st.get("backtest", {})
    print(f"回测引擎: {bt.get('engine', '—')} · 数据/窗口: {bt.get('data') or bt.get('start', '—')} "
          f"/ {bt.get('window') or bt.get('window_from', '—')}")


def cmd_backtest(args):
    st = strategy(args.strategy)
    # 仅重生成报告：不跑引擎、不追加 jsonl
    if args.report:
        rep = os.path.join(ROOT, st["backtest"]["report"])
        # 报告生成器与「引擎」同目录（引擎留在策略包根），而不是与报告产物同目录 ——
        # 产物已按职能移入 backtest/ 子目录，若按产物目录推断会找不到生成器。
        eng = os.path.join(ROOT, st["backtest"]["engine"])
        gen = os.path.join(os.path.dirname(eng), "_reportbt.py")
        if not os.path.exists(gen):                     # 退化：与报告产物同目录
            gen = os.path.join(os.path.dirname(rep), "_reportbt.py")
        if not os.path.exists(gen):
            sys.exit(f"找不到报告生成器 _reportbt.py（engine={st['backtest']['engine']}）")
        subprocess.run([sys.executable, "-W", "ignore", gen], cwd=ROOT, check=False)
        print(f"回测报告: {st['backtest']['report']}")
        return
    codes = (args.codes or "").strip()
    if not codes:
        sys.exit("backtest 需 --codes \"...\"（或 --report 仅用现有 jsonl 重生成报告）")
    out = os.path.join(ROOT, st["backtest"]["raw_out"])
    if args.fresh and os.path.exists(out):
        os.remove(out)
    eng = os.path.join(ROOT, st["backtest"]["engine"])
    p = subprocess.run([sys.executable, "-W", "ignore", eng, codes],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    ok_lines = [l for l in p.stdout.splitlines() if l.strip().startswith("{")]
    if p.returncode != 0:
        print(p.stderr[-2000:] if p.stderr else p.stdout[-2000:])
        sys.exit("backtest 引擎失败")
    if ok_lines:
        mode = "a" if os.path.exists(out) else "w"
        with open(out, mode, encoding="utf-8") as f:
            for l in ok_lines:
                f.write(l + "\n")
        print(f"已追加 {len(ok_lines)} 行 -> {out}")
    elif os.path.exists(out):
        # 引擎自行落盘（lights/backtest.py 的 OUT_DEFAULT 就是 raw_out, 只打印人类可读摘要）
        print(f"引擎已自行写入 -> {out}")
    else:
        print(p.stderr[-2000:] if p.stderr else p.stdout[-2000:])
        sys.exit("backtest 引擎既未产出 stdout JSONL, 也未写入 raw_out")


def cmd_select(args):
    p = subprocess.run([sys.executable, os.path.join(ROOT, "pipeline", "flow_select.py"), args.strategy],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    print(p.stdout.strip())
    if p.returncode != 0:
        print(p.stderr[-1000:])
        sys.exit("select 失败")


def cmd_daily(args):
    st = strategy(args.strategy)
    act = os.path.join(ROOT, st["select"]["out_active_json"])
    if not os.path.exists(act):
        sys.exit("缺少分级名单，请先: python pipeline/run_flow.py select")
    print(f"调用 daily runner: {st['daily']['runner']} (A={st['daily']['codes']})")
    p = subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-File",
                        os.path.join(ROOT, st["daily"]["runner"])], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    print(p.stdout[-1500:])
    if p.returncode != 0:
        print(p.stderr[-1000:])


def cmd_scaffold(args):
    """新增策略脚手架: 注册配置 + 生成 strategies/<name>/ 骨架，等待策略文档填充规则。"""
    import re as _re
    name = (args.name or "").strip().lower()
    if not _re.fullmatch(r"[a-z0-9_]+", name or ""):
        sys.exit("--name 需为小写字母/数字/下划线")
    if name in config["strategies"]:
        sys.exit(f"策略 {name} 已存在")
    dirp = os.path.join(ROOT, "strategies", name)
    os.makedirs(dirp, exist_ok=True)
    cfg = {
        "name": f"{name} (待规则化)",
        "source_methodology": "待填写: 方法论文档路径",
        "universe": {"offshore_pool": "data/_universe.md",
                     "inner_mapping": f"strategies/{name}/rule.py (标的池)",
                     "note": "待按文档确定标的池范围"},
        "rules": [],
        "thresholds": {"buy_total_min": None, "sell_total_max": None,
                       "core_light_min": None, "fee": None, "trade_at": "待定"},
        "backtest": {"engine": f"strategies/{name}/backtest.py", "start": "待定",
                     "window_from": "待定", "raw_out": f"data/_{name}_bt.jsonl",
                     "report": f"strategies/{name}/report_bt.md"},
        "select": {"out_active_md": f"data/_universe_{name}_active.md",
                   "out_active_json": f"data/_universe_{name}_active.json",
                   "min_sample_years": 3.0,
                   "criteria": "A=策略年化>0 且 超额>0; B=超额>0 且 策略年化<=0"},
        "daily": {"codes": [], "runner": f"run_daily_{name}.ps1",
                  "signal_report": f"strategies/{name}/report_daily.md",
                  "automation": "(未创建)"},
        "note": "TODO: 收到策略文档后填充 rules/thresholds/daily.codes 并实现 rule.py/backtest.py",
    }
    files = {
        "README.md": f"# 策略 {name}\n\n> 待用户提供策略文档后，按 pipeline/README.md 流程接入。\n",
        "rule.py": (
            "import pandas as pd\nimport xalpha as xa\n\n\n"
            "def load_bars(code, start):\n"
            "    \"\"\"标的代码 -> 日线 DataFrame(date/open/high/low/close/volume)。\"\"\"\n"
            "    pre = 'SH' if code.startswith(('5','6','9')) else 'SZ'\n"
            "    df = xa.get_daily(pre + code, start=start).dropna(subset=['close'])\n"
            "    df['date'] = pd.to_datetime(df['date'])\n"
            "    return df.sort_values('date').reset_index(drop=True)\n\n\n"
            "def score(df):\n"
            "    raise NotImplementedError('按策略文档实现逐行评分(见 strategies/lights/rule.py 的评分口径)')\n"
        ),
        "backtest.py": (
            "# 回测骨架：完成后引擎逐行输出 JSON，schema 需含\n"
            "# code/theme/off/years/base_ann/base_mdd/st_ann/st_mdd/pos_ratio\n"
            "# 单笔统计统一复用 pipeline/bt_stats: 引擎收集 trades=[{code,entry_date,exit_date,bars,ret}],\n"
            "# 输出 t_stats=bt_stats.trade_stats(trades)+trade_log; 报告默认含 bt_stats.section_lines\n"
            "def run_backtest(df):\n"
            "    raise NotImplementedError('实现状态机回测，参考 strategies/lights/backtest.py')\n"
        ),
    }
    for fn, content in files.items():
        with open(os.path.join(dirp, fn), "w", encoding="utf-8") as f:
            f.write(content)
    config["strategies"][name] = cfg
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    print(f"已注册策略 {name}: strategies/{name}/ 骨架生成")
    print("下一步: 收到策略文档后由我来填充 rule.py/backtest.py 并运行 backtest->select->daily")


def main():
    ap = argparse.ArgumentParser(description="strategy pipeline orchestrator")
    ap.add_argument("phase", nargs="?", default="list")
    ap.add_argument("--strategy", default="lights")
    ap.add_argument("--name", default="")
    ap.add_argument("--codes", default="")
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    {"list": cmd_phases, "universe": cmd_universe, "rules": cmd_rules,
     "backtest": cmd_backtest, "select": cmd_select, "daily": cmd_daily,
     "scaffold": cmd_scaffold}.get(args.phase, cmd_phases)(args)


if __name__ == "__main__":
    main()
