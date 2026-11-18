# -*- coding: utf-8 -*-
"""唯一标的池来源：解析 data/_universe.md 的场外清单 + 场内映射。

约定（2026-09 起）：
- 标的的「增删改」只改 _universe.md；回测 / 每日扫描 / 推荐均由本模块派生，禁止再硬编码池。
- 有「场内代码」（第 5 列）的行才进入场内信号回测与每日操作推荐（内池）；
- 无场内对应的行只作为场外清单 / 定投配置用途。

用法：
    python pipeline/universe.py          # 打印汇总（行数/场内池/场外代码数/代理错配清单）
    from pipeline import universe
    rows = universe.inner_rows()         # 场内映射行 [{idx, code, cat, theme, off_code, off_name, inner_name}]
    codes = universe.inner_codes()       # 场内池代码列表（md 行序）
    universe.proxy_mismatch()            # 场内外跟踪指数不同源的行（基差风险清单）
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
# 指数代码列（0 基）：0 # / 1 主题 / 2 场外代码 / 3 场外名称 / 4 场内代码 / 5 场内名称
#                   / 6 跟踪指数 / 7 指数代码（2026-11-18 新增）
_IDX_COL = 7
# 指数代码：6 位数字 / 字母数字混合 + 数据源后缀（中证 .CSI、海外 .GI/.HI/.SPI、中债 .CS、
# 商品 .SGE/.SHF、中信 .CI、申万 .SWI、定存利率 .IR）
_IDX = re.compile(r"[0-9A-Z]{3,}\.(?:SH|SZ|CSI|BJ|GI|HI|SPI|SPT|SWI|CI|CS|SGE|SHF|IR)")


def _cat_of(title):
    for keys, label in LABEL:
        if any(k in title for k in keys):
            return label
    return "其他"


def _clean(s):
    return (s or "").strip()


def parse(path=None):
    """_universe.md -> list[dict]，含 num/cat/theme/off_code/off_name/inner_code/inner_name/index_code。"""
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
            index_code=cells[_IDX_COL] if len(cells) > _IDX_COL else "",
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


# 2026-10-28：技术指标家族（strategies/tech_indicators 的 v1~v15）只在前四类里回测/扫描。
#   第五类「主动/量化」没有场内代理（0 只），第六类「债券」风险属性与权益择时不同源（1 只 511260），
#   两类都不参与技术指标择时。
INNER_CATS = (1, 2, 3, 4)


def inner_rows_cats(prefixes=INNER_CATS, rows=None):
    """只取编号首位属于 prefixes 的场内映射行（默认一~四类）。"""
    keep = {str(p) for p in prefixes}
    return [r for r in inner_rows(rows) if str(r["idx"])[0] in keep]


def inner_codes_cats(prefixes=INNER_CATS, rows=None):
    """只取编号首位属于 prefixes 的场内代码列表（md 行序）。"""
    return [r["code"] for r in inner_rows_cats(prefixes, rows)]


def offshore_codes(rows=None):
    return [r["off_code"] for r in (rows or parse())]


def inner_codes(rows=None):
    return [r["code"] for r in inner_rows(rows)]


def index_codes(cell):
    """「指数代码」单元格 -> 跟踪指数代码列表（md 行序）。

    单元格写法（见 _universe.md 维护规则）：
    - 单指数 ``000300.SH``                       -> ``['000300.SH']``
    - 场内外不同源 ``399809.SZ(场外) / H30035.CSI(场内 …)`` -> 两个代码，前者场外、后者场内
    - 主动管理 ``—(基准 000906.SH)``             -> ``[]``（带「基准」的是业绩基准，不是跟踪标的）
    """
    if "基准" in (cell or ""):
        return []
    return _IDX.findall(cell or "")


def proxy_mismatch(rows=None, prefixes=INNER_CATS):
    """场内信号代理与场外执行标的**跟踪指数不同源**的行（基差风险清单）。

    判定：指数代码列解析出 ≥2 个不同代码，即 md 里按「A(场外) / B(场内)」分列。
    默认只看参与择时的前四类（INNER_CATS）—— 第六类债券的 511260 只是利率久期通道、
    本来就不当信号腿，混进来会让清单失真（该差异已写在 _universe.md 的对应说明里）。
    """
    keep = {str(p) for p in prefixes}
    out = []
    for r in (rows or parse()):
        if not r["inner_code"] or str(r["num"])[0] not in keep:
            continue
        cs = index_codes(r["index_code"])
        if len(set(cs)) < 2:
            continue
        out.append(dict(
            idx=r["num"], cat=r["cat"], theme=r["theme"],
            off_code=r["off_code"], off_name=r["off_name"], off_index=cs[0],
            inner_code=r["inner_code"], inner_name=r["inner_name"], inner_index=cs[1],
        ))
    return out


def _main():
    rows = parse()
    inn = inner_rows(rows)
    print(f"_universe.md 唯一池: {UNIVERSE}")
    print(f"总行数 {len(rows)} (offshore {len(offshore_codes(rows))})")
    print(f"场内内池 {len(inn)} 只: {' '.join(r['code'] for r in inn)}")
    mis = proxy_mismatch(rows)
    print(f"场内外跟踪指数不同源 {len(mis)} 行:")
    for m in mis:
        print(f"  #{m['idx']} {m['theme']}: 场外 {m['off_code']} {m['off_index']}"
              f" / 场内 {m['inner_code']} {m['inner_index']}")


if __name__ == "__main__":
    _main()
