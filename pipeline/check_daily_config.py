# -*- coding: utf-8 -*-
"""每日操作配置自检: `pipeline/strategies.json` 的 `daily.*` 与 runner 文件是否自洽。

**为什么需要**：18 个策略里只有 5 个 `daily.runner` 非空，其余靠 `runner_note`
的自由文本解释「为什么留空、由谁代跑」（典型：变种的每日脚本写在家族 runner 的第 2 步里）。
这层约束原先只靠人记，挡不住两类「配置漂移」——代码没改、结果悄悄错：

  E1 重复调度：同一个 runner 被 2 个 enabled 策略引用 → `run_daily_all` 会把该 ps1 跑两遍
     （最典型的翻车方式：新增变种时忘了把 runner 置空）
  E2 悬空引用：runner 指向不存在的 .ps1，或 daily.block 不在 blocks[].key
     → 运行时只打印一行 [skip]，表现是「看板有卡但数据是昨天的」
  W1 留空无解释：runner 为空却没有 runner_note
  W2 产物缺失：声明的 signal_report / signal_json 尚未生成（可能只是还没跑过）
  W3 空板块：某个 block 下没有任何 enabled 策略

用法:
  python pipeline/check_daily_config.py            # ERROR 非 0 退出, WARN 只提示
  python pipeline/check_daily_config.py --strict   # WARN 也算失败
"""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(_ROOT, "pipeline", "strategies.json")

_PATH_EXT = (".md", ".json", ".html")
# 只有这些语义的字段才可能是产物路径。缺了这层，`enabled_note` / `card_note` 这类
# 长段中文说明里恰好嵌了「xxx.md 还未生成」就会被误判成路径（实测踩到过）。
_PATH_KEY_HINT = ("md", "json", "html", "report", "dashboard")


def _is_path(k, v):
    """daily 里「看起来是产物路径」的字段值（用于存在性检查）。"""
    if not (isinstance(v, str) and v and " " not in v):
        return False
    if not v.lower().endswith(_PATH_EXT):
        return False
    if "/" not in v and "\\" not in v:
        return False
    key = k.lower()
    return any(h in key for h in _PATH_KEY_HINT)


def check(cfg):
    """-> (errors, warns, stats)，纯函数，不打印。"""
    errors, warns = [], []
    blocks = [b.get("key") for b in (cfg.get("blocks") or []) if b]
    strategies = cfg.get("strategies") or {}

    runner_users = {}          # runner -> [enabled 策略 id]
    enabled_by_block = {k: 0 for k in blocks}
    n_enabled = 0

    for sid, st in strategies.items():
        daily = (st or {}).get("daily")
        if not daily:
            errors.append(f"{sid}: 缺 daily 段（run_daily_all 会把它排到最后但仍无 runner）")
            continue
        enabled = daily.get("enabled", True) is not False

        block = daily.get("block")
        if block not in blocks:
            errors.append(f"{sid}: daily.block={block!r} 不在 blocks {blocks}")
        if enabled:
            n_enabled += 1
            if block in enabled_by_block:
                enabled_by_block[block] += 1

        runner = (daily.get("runner") or "").strip()
        if runner:
            if not os.path.isfile(os.path.join(_ROOT, runner)):
                errors.append(f"{sid}: daily.runner={runner} 文件不存在")
            if enabled:
                runner_users.setdefault(runner, []).append(sid)
        elif enabled and not (daily.get("runner_note") or "").strip():
            warns.append(f"{sid}: daily.runner 为空且没有 runner_note（为什么留空？由谁代跑？）")

        for k, v in daily.items():
            if _is_path(k, v) and not os.path.isfile(os.path.join(_ROOT, v)):
                warns.append(f"{sid}: daily.{k}={v} 尚未生成")

    for runner, users in runner_users.items():
        if len(users) > 1:
            errors.append(f"{runner} 被 {len(users)} 个 enabled 策略引用 {users}"
                          f" —— run_daily_all 会把同一个 runner 跑 {len(users)} 遍")

    for k, n in enabled_by_block.items():
        if n == 0:
            warns.append(f"block={k} 下没有 enabled 策略（该板块的看板卡片将长期沿用旧数据）")

    stats = {"strategies": len(strategies), "enabled": n_enabled,
             "runners": len(runner_users), "blocks": len(blocks)}
    return errors, warns, stats


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    strict = "--strict" in argv

    with open(CFG, encoding="utf-8") as f:
        cfg = json.load(f)
    errors, warns, stats = check(cfg)

    print("每日操作配置自检 · pipeline/strategies.json")
    print(f"  策略 {stats['strategies']} · enabled {stats['enabled']} · "
          f"唯一 runner {stats['runners']} 个 · 板块 {stats['blocks']} 个")

    for msg in errors:
        print(f"  [ERROR] {msg}")
    for msg in warns:
        print(f"  [WARN ] {msg}")

    if errors:
        print(f"\n[X] {len(errors)} 个 ERROR、{len(warns)} 个 WARN —— 先修 ERROR 再跑每日流程")
        return 1
    if strict and warns:
        print(f"\n[X] --strict: {len(warns)} 个 WARN 也算失败")
        return 1
    print(f"\n[OK] 无 ERROR（WARN {len(warns)} 个，不影响执行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
