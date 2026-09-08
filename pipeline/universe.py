# -*- coding: utf-8 -*-
"""唯一标的池来源：解析 data/_universe.md 的场外清单 + 场内映射。

约定（2026-09 起）：
- 标的的「增删改」只改 _universe.md；回测 / 每日扫描 / 推荐均由本模块派生，禁止再硬编码池。
- 有「场内对应代码」的行才进入场内信号回测与每日操作推荐（内池）；
- 无场内对应的行只作为场外清单 / 定投配置用途。

用法：
    python pipeline/universe.py          # 打印汇总（行数/场内池/场外代码数）
    from pipeline import universe
    rows = universe.inner_rows()         # 场内映射行 [{idx, code, cat, theme, off_code, off_name, inner_name}]
    codes = universe.inner_codes()       # 场内池代码列表（md 行序）
"""
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNIVERSE = os.path.join(_ROOT, "data", "_universe.md")

# 六类 -> 报告/扫描用的分类标签
LABEL = [
    (("宽基",), "宽基/另类"),
    (("全球", "QDII", "跨境"), "全球/QDII"),
    (("行业",), "A股行业"),
    (("策略", "商品", "因子"), "策略/商品"),
    (("主动", "量化"), "主动/量化"),
    (("债券",), "债券"),
]
_HEAD = re.compile(r"^#+\s*(.+)$")
_ROW = re.compile(r"^\|\s*\d+\s*\|")


def _cat_of(title):
    for keys, label in LABEL:
        if any(k in title for k in keys):
            return label
    return "其他"


def _clean(s):
    return (s or "").strip()


def parse(path=None):
    """_universe.md -> list[dict]，含 num/cat/theme/off_code/off_name/inner_code/inner_name。"""
    path = path or UNIVERSE
    rows, cat = [], None
    for ln in open(path, encoding="utf-8"):
        if not _ROW.match(ln):
            m = _HEAD.match(ln)
            if m:
                cat = _cat_of(m.group(1))
            continue
        cells = [_clean(x) for x in ln.strip().strip("|").split("|")]
        # 期望至少: num,theme,off_code,off_name[,inner_code,inner_name,...]
        if len(cells) < 5:
            continue
        rows.append(dict(
            num=int(cells[0]), cat=cat,
            theme=cells[1], off_code=cells[2], off_name=cells[3],
            inner_code=cells[4] if len(cells) > 4 else "",
            inner_name=cells[5] if len(cells) > 5 else "",
        ))
    return rows


def offshore_rows(rows=None):
    return rows or parse()


def inner_rows(rows=None):
    """仅含场内映射的行（唯一池内池），带 code/cat/theme/off 等输出字段。"""
    rows = rows or parse()
    out = []
    for r in rows:
        if not r["inner_code"]:
            continue
        out.append(dict(
            idx=r["num"], code=r["inner_code"], cat=r["cat"],
            theme=r["theme"], off_code=r["off_code"], off_name=r["off_name"],
            inner_name=r["inner_name"] or r["inner_code"],
        ))
    return out


def offshore_codes(rows=None):
    return [r["off_code"] for r in (rows or parse())]


def inner_codes(rows=None):
    return [r["code"] for r in inner_rows(rows)]


def _main():
    rows = parse()
    inn = inner_rows(rows)
    print(f"_universe.md 唯一池: {UNIVERSE}")
    print(f"总行数 {len(rows)} (offshore {len(offshore_codes(rows))})")
    print(f"场内内池 {len(inn)} 只: {' '.join(r['code'] for r in inn)}")


if __name__ == "__main__":
    _main()
