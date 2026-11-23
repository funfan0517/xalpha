# -*- coding: utf-8 -*-
"""波动资产 · **近五年窗口**下各标的的「策略表现 × 前三名」清单。

本文件只是一个**薄壳**：逻辑全部在 `research/best_by_leg/_best_by_leg_3y.py`，
这里只把窗口参数从 3 年换成 5 年、输出改到本目录。为什么这么做：

- 两个窗口若各维护一份 600 行脚本，改一处口径（费率、切窗基点、收益币种）必然漏改另一份，
  而两份产物长得一模一样，**漏改不会报错、只会静默给出两套数**；
- 薄壳只覆盖 7 个模块级变量（`WINDOW_YEARS` / `WINDOW_WORD` / `SE_TXT` /
  `MARKET_NOTE` / `SIBLING_NOTE` / `SELF_PY` / `OUT_*`），口径天然一致。

## 与近三年版的关键区别

| | 近三年（`best_by_leg`） | **近五年（本目录）** |
|---|---|---|
| 窗口 | 2023-11 起 | **2021-11 起** |
| 行情 | 不含 2022 熊市 | **含 2022 年 A 股与美股同步大跌、港股续跌** |
| 样本 | 3 年（夏普标准误 ≈ ±0.3） | 5 年（≈ ±0.22） |

5 年窗口多出来的 2021-11 ~ 2023-11 那段，恰好是「所有择时策略最该证明自己」的熊市段
—— 因此同一支策略在两个窗口的排名**很可能相反**，这不是数据错，是风格暴露不同。
两份榜单必须一起看：**两个窗口都进前三的才是稳的**。

产出（本目录）：
- `_best_by_leg_5y.md`    近五年榜（一页前三名 + 分标的全量明细 + 与全历史榜的差异）
- `_best_by_leg_5y.jsonl` 机读结果

跑一次：`python strategies/vol_legs/research/best_by_leg_5y/_best_by_leg_5y.py`

> 模拟结果，非投资建议。
"""
import importlib.util
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))     # .../research/best_by_leg_5y
_RES = os.path.dirname(_HERE)                          # .../research
_SRC = os.path.join(_RES, "best_by_leg", "_best_by_leg_3y.py")


def _load_src():
    """按路径加载 3y 模块（文件名以下划线开头，不能直接 `import`）。"""
    spec = importlib.util.spec_from_file_location("_best_by_leg_3y", _SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    m = _load_src()

    # ------------------------------------------------ 只覆盖窗口参数
    m.WINDOW_YEARS = 5
    m.WINDOW_WORD = "近五年"
    m.SE_TXT = "0.22"                     # 5 年 ≈ 0.5/sqrt(5)
    m.MARKET_NOTE = ("覆盖 2021 年末以来的一段行情（含 2022 年 A 股与美股同步大跌、"
                     "港股续跌，以及 2023~2026 年的反弹与黄金大涨）")
    m.SIBLING_NOTE = ("另一个窗口的同一份榜单：`research/best_by_leg/_best_by_leg_3y.md`"
                      "（近三年，不含 2022 年熊市）—— 窗口不同结论可能相反，两份对照看。"
                      "在本榜前三之上再找组合优化：`research/beat_best_5y/_beat_best_5y.md`。")
    m.SELF_PY = "strategies/vol_legs/research/best_by_leg_5y/_best_by_leg_5y.py"
    m.OUT_MD = os.path.join(_HERE, "_best_by_leg_5y.md")
    m.OUT_JSONL = os.path.join(_HERE, "_best_by_leg_5y.jsonl")

    m.main()


if __name__ == "__main__":
    main()
