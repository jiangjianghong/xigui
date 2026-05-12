"""文本/数值/日期归一化工具。

被规则引擎用来在比较前抹平噪声差异：
- ``normalize_text``：处理全半角、空白、大小写。
- ``extract_number``：从带单位的中文金额字符串中抽出 ``float`` 数值。
- ``parse_date``：解析多种中英文日期表达，返回 ``datetime``。
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime

from dateutil import parser as _dtp


def normalize_text(
    s: str | None,
    *,
    ignore_whitespace: bool = True,
    ignore_case: bool = True,
    full_to_half: bool = True,
) -> str:
    """归一化文本以便严格比较。

    参数都是 keyword-only，方便 yaml 配置直接 ``**params`` 解包。
    """
    s = s or ""
    if full_to_half:
        # NFKC 同时处理全角字母/数字/标点 → 半角
        s = unicodedata.normalize("NFKC", s)
    if ignore_case:
        s = s.lower()
    if ignore_whitespace:
        s = re.sub(r"\s+", "", s)
    return s


# 匹配可能带千分位/小数点的数字 + 紧随其后的中文/字母单位
# 例:  "1,234.5亿"  -> ("1,234.5", "亿")
#      "100万元"    -> ("100", "万元")
#      "1.2 亿"     -> ("1.2", "亿")  # 中间允许空白
_NUMBER_PATTERN = re.compile(r"(-?[\d,]+(?:\.\d+)?)\s*([\u4e00-\u9fffA-Za-z]*)")


def extract_number(s: str | None, unit_map: dict[str, float] | None = None) -> float | None:
    """从字符串中抽取数值并按单位换算。

    - 输入示例："1.2亿"、"12000万元"、"1,200,000 元"、"100"
    - 找不到数字 → 返回 None
    - 单位不在 ``unit_map`` 内（含空单位） → 视为倍率 1
    - ``unit_map`` 中以最长匹配优先（避免 "万元" 被 "万" 误匹配）
    """
    if not s:
        return None
    m = _NUMBER_PATTERN.search(s)
    if not m:
        return None
    try:
        num = float(m.group(1).replace(",", ""))
    except ValueError:
        return None

    unit_text = (m.group(2) or "").strip()
    factor = 1.0
    if unit_map and unit_text:
        # 按 key 长度倒序匹配，"万元" 优先于 "万"
        for k in sorted(unit_map.keys(), key=lambda x: -len(x)):
            if k and k in unit_text:
                factor = float(unit_map[k])
                break
    return num * factor


# 中文日期格式 "2024年1月10日" / "2024年01月10日" 提前转成 "2024-01-10"
# 这样 dateutil.parser 才能正确解析（它对中文日期支持不佳）
_CN_DATE_PATTERN = re.compile(
    r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?"
)


def _preprocess_cn_date(s: str) -> str:
    def _repl(m: re.Match[str]) -> str:
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        return f"{y}-{mo:02d}-{d:02d}"

    return _CN_DATE_PATTERN.sub(_repl, s)


def parse_date(s: str | None) -> datetime | None:
    """解析日期字符串。支持常见格式：
    - ``2024-01-10`` / ``2024/01/10`` / ``2024.01.10``
    - ``2024年1月10日`` / ``2024年01月10日``
    - ``Jan 10, 2024`` 等英文写法（依赖 dateutil）

    解析失败返回 None（而不是抛异常），便于规则上层把它转成
    ``match=False, reason="无法解析日期"`` 的业务结果。
    """
    if not s:
        return None
    text = _preprocess_cn_date(s).strip()
    if not text:
        return None
    try:
        return _dtp.parse(text, fuzzy=True)
    except (ValueError, TypeError, OverflowError):
        return None
