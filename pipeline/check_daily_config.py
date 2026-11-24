# -*- coding: utf-8 -*-
"""每日操作配置自检: `pipeline/strategies.json` 的 `daily.*` 与 runner 文件是否自洽。

**为什么需要**：只有少数策略的 `daily.runner` 非空，其余靠 `runner_note` 的自由文本
解释「为什么留空、由谁代跑」（典型：变种的每日脚本写在家族 runner 的第 2/3/4 步里）。
这层约束原先只靠人记，挡不住两类「配置漂移」——代码没改、结果悄悄错：

  E1 重复调度：同一个 runner 被 2 个**上板**策略引用 → 允许（`run_daily_all` 按 runner
     去重只跑一次），但必须打印出来；**跨板块**共享才算 ERROR（产物归属不清）
  E2 悬空引用：runner 指向不存在的 .ps1，或 daily.block 不在 blocks[].key
     → 运行时只打印一行 [skip]，表现是「看板有卡但数据是昨天的」
  E3 缺显式开关：`daily` 段没有 `enabled` 键 —— **新策略未经允许不得写入每日操作报告**
     （`daily.enabled=false` 才是默认；漏写会按 True 处理，等于偷偷上板）
  E4 上板无凭据：`enabled=true` 但 `enabled_note` 为空或不含日期
     —— 上板必须留下「YYYY-MM-DD 谁同意的」这一行，否则下次没人知道它为什么在报告里
  E5 不出卡却占调度：`card=false` 但 `runner` 非空 —— 用户 2026-11-24 规则是
     「不上报告看板的策略无需随每日操作跑」，`run_daily_all` 不会调度它；必跑产物
     应把调度权挂到出卡的家族卡上（如 vol_variants / gd_v5），否则产物永远不刷新
  W1 留空无解释：上板策略 runner 为空、又没有 runner_note
  W2 产物缺失：声明的 signal_report / signal_json 尚未生成（可能只是还没跑过）
  W3 空板块：某个 block 下没有任何上板策略

用法:
  python pipeline/check_daily_config.py            # ERROR 非 0 退出, WARN 只提示
  python pipeline/check_daily_config.py --strict   # WARN 也算失败
"""
import json
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(_ROOT, "pipeline", "strategies.json")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

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
    """-> (errors, warns, stats, infos)，纯函数，不打印。"""
    errors, warns, infos = [], [], []
    blocks = [b.get("key") for b in (cfg.get("blocks") or []) if b]
    strategies = cfg.get("strategies") or {}

    runner_users = {}          # runner -> [(策略 id, block)]，只收上板策略
    enabled_by_block = {k: 0 for k in blocks}
    n_on_board = 0

    for sid, st in strategies.items():
        daily = (st or {}).get("daily")
        if not daily:
            errors.append(f"{sid}: 缺 daily 段（run_daily_all 不会调度它）")
            continue
        # E3：新策略未经允许不得写入每日操作报告 —— 必须显式声明 enabled，
        # 缺省 True 会让「忘了写」变成「偷偷上板」。
        if "enabled" not in daily:
            errors.append(f"{sid}: daily 段缺显式 enabled —— 新策略默认不进每日操作报告，"
                          f"必须显式写 \"enabled\": false；上板须经用户同意")
        enabled = daily.get("enabled") is True   # 缺键按「不上板」算（与 daily_digest._card_on 一致）
        # 上板 = 出卡。card=false 表示「不单独出卡」，也不随每日操作跑（用户 2026-11-24 规则）。
        on_board = enabled and daily.get("card") is not False

        # E4：上板要留凭据（日期 + 谁同意），否则没人知道它为什么在报告里。
        if on_board:
            note = (daily.get("enabled_note") or "").strip()
            if not note:
                errors.append(f"{sid}: daily.enabled=true 但缺 enabled_note —— "
                              f"上板必须写明「YYYY-MM-DD 谁同意」，否则视为未经允许")
            elif not _DATE.search(note):
                errors.append(f"{sid}: daily.enabled_note 里没有日期 → {note[:40]!r}")

        block = daily.get("block")
        if block not in blocks:
            errors.append(f"{sid}: daily.block={block!r} 不在 blocks {blocks}")
        if on_board:
            n_on_board += 1
            if block in enabled_by_block:
                enabled_by_block[block] += 1

        runner = (daily.get("runner") or "").strip()
        if runner:
            if not os.path.isfile(os.path.join(_ROOT, runner)):
                errors.append(f"{sid}: daily.runner={runner} 文件不存在")
            if on_board:
                runner_users.setdefault(runner, []).append((sid, block))
            elif enabled:
                # E5：不出卡（card=false）却配了 runner —— run_daily_all 不调度它，
                # 典型后果是「以为在跑、其实产物一直是旧的」。
                errors.append(f"{sid}: card=false 却配了 daily.runner={runner} —— 不出卡的策略"
                              f"不随每日操作跑；把调度权挂到出卡的家族卡上，本条目 runner 置空")
            else:
                warns.append(f"{sid}: enabled=false 但留了 daily.runner={runner} "
                             f"（不会被 run_daily_all 调度，只能手动跑）")
        elif on_board and not (daily.get("runner_note") or "").strip():
            warns.append(f"{sid}: daily.runner 为空且没有 runner_note（为什么留空？由谁代跑？）")

        for k, v in daily.items():
            if _is_path(k, v) and not os.path.isfile(os.path.join(_ROOT, v)):
                warns.append(f"{sid}: daily.{k}={v} 尚未生成")

    for runner, users in runner_users.items():
        if len(users) > 1:
            sids = [u[0] for u in users]
            blks = {u[1] for u in users}
            if len(blks) > 1:
                errors.append(f"{runner} 被跨板块的 {len(users)} 张卡引用 {sids}{sorted(blks)}"
                              f" —— 产物归属不清，需人工确认")
            else:
                infos.append(f"{runner} 被同板块 {len(users)} 张卡共享 {sids} —— "
                             f"run_daily_all 去重后只执行一次")

    for k, n in enabled_by_block.items():
        if n == 0:
            warns.append(f"block={k} 下没有上板策略（该板块的看板卡片将长期沿用旧数据）")

    card_off = [sid for sid, st in strategies.items()
                if ((st or {}).get("daily") or {}).get("enabled") is True
                and ((st or {}).get("daily") or {}).get("card") is False]
    stats = {"strategies": len(strategies), "enabled": n_on_board,
             "runners": len(runner_users), "blocks": len(blocks),
             "on_board": [sid for sid, st in strategies.items()
                          if ((st or {}).get("daily") or {}).get("enabled") is True
                          and ((st or {}).get("daily") or {}).get("card") is not False],
             "card_off": card_off,
             "off_board": [sid for sid, st in strategies.items()
                           if ((st or {}).get("daily") or {}).get("enabled") is not True]}
    return errors, warns, stats, infos


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    strict = "--strict" in argv

    with open(CFG, encoding="utf-8") as f:
        cfg = json.load(f)
    errors, warns, stats, infos = check(cfg)

    print("每日操作配置自检 · pipeline/strategies.json")
    print(f"  策略 {stats['strategies']} · 上板 {stats['enabled']} · "
          f"调度 runner {stats['runners']} 个 · 板块 {stats['blocks']} 个")
    print(f"  每日操作报告收录 {len(stats['on_board'])} 个：{', '.join(stats['on_board'])}")
    if stats["card_off"]:
        print(f"  不出卡、不参与调度 {len(stats['card_off'])} 个：{', '.join(stats['card_off'])}"
              f"（card=false；产物随出卡的家族卡刷新）")
    print(f"  未上板 {len(stats['off_board'])} 个（enabled=false，不随每日操作跑；"
          f"需刷新手动跑各自 runner）")

    for msg in infos:
        print(f"  [INFO ] {msg}")
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
