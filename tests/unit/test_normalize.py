"""``app.utils.normalize`` 单元测试。"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.utils.normalize import extract_number, normalize_text, parse_date


# ---------------------------------------------------------------------------
# normalize_text
# ---------------------------------------------------------------------------

class TestNormalizeText:
    def test_whitespace_stripped(self):
        assert normalize_text("  2024-001 ") == "2024-001"

    def test_case_folded(self):
        assert normalize_text("ABC") == "abc"

    def test_full_to_half(self):
        # 全角 'ＡＢＣ１２３' 经 NFKC 后变 'abc123'（叠加 ignore_case）
        assert normalize_text("ＡＢＣ１２３") == "abc123"

    def test_full_half_mix(self):
        # 全角空格 + 全角数字
        assert normalize_text("ＡＢＣ　１２３") == "abc123"

    def test_keep_case_when_disabled(self):
        assert normalize_text("ABC", ignore_case=False) == "ABC"

    def test_keep_whitespace_when_disabled(self):
        assert normalize_text("a b c", ignore_whitespace=False) == "a b c"

    def test_none_input(self):
        assert normalize_text(None) == ""

    def test_chinese_kept(self):
        # 中文字符不应被破坏
        assert normalize_text("项目代码A-01") == "项目代码a-01"


# ---------------------------------------------------------------------------
# extract_number
# ---------------------------------------------------------------------------

UNIT_MAP = {"亿": 1e8, "万元": 1e4, "万": 1e4, "元": 1.0}


class TestExtractNumber:
    def test_yi(self):
        assert extract_number("1.2亿", UNIT_MAP) == pytest.approx(1.2e8)

    def test_wan_yuan(self):
        # "12000万元" 应该按 "万元" 匹配（最长优先），不能按 "万"
        assert extract_number("12000万元", UNIT_MAP) == pytest.approx(1.2e8)

    def test_yi_equals_wan_yuan(self):
        # 业务核心场景：1.2亿 ≡ 12000万元
        assert extract_number("1.2亿", UNIT_MAP) == extract_number("12000万元", UNIT_MAP)

    def test_comma_thousand_sep(self):
        assert extract_number("1,200,000元", UNIT_MAP) == pytest.approx(1.2e6)

    def test_with_space(self):
        assert extract_number("1.2 亿", UNIT_MAP) == pytest.approx(1.2e8)

    def test_no_unit(self):
        assert extract_number("100", UNIT_MAP) == pytest.approx(100.0)

    def test_unparseable(self):
        assert extract_number("非数字", UNIT_MAP) is None

    def test_none(self):
        assert extract_number(None, UNIT_MAP) is None

    def test_unknown_unit_treated_as_unit_one(self):
        # 未知单位（如 "ABC"）应当作倍率 1
        assert extract_number("100ABC", UNIT_MAP) == pytest.approx(100.0)

    def test_negative(self):
        assert extract_number("-5万", UNIT_MAP) == pytest.approx(-5e4)


# ---------------------------------------------------------------------------
# parse_date
# ---------------------------------------------------------------------------

class TestParseDate:
    def test_iso_dash(self):
        assert parse_date("2024-01-10") == datetime(2024, 1, 10)

    def test_iso_slash(self):
        assert parse_date("2024/01/10") == datetime(2024, 1, 10)

    def test_cn_full(self):
        assert parse_date("2024年1月10日") == datetime(2024, 1, 10)

    def test_cn_zero_padded(self):
        assert parse_date("2024年01月10日") == datetime(2024, 1, 10)

    def test_cn_no_day_suffix(self):
        assert parse_date("2024年1月10") == datetime(2024, 1, 10)

    def test_equivalence_across_formats(self):
        # 业务核心场景：多种格式应被解析为同一日期
        target = datetime(2024, 1, 10)
        for s in ("2024-01-10", "2024/1/10", "2024年1月10日", "2024.01.10"):
            assert parse_date(s) == target

    def test_unparseable_returns_none(self):
        assert parse_date("not a date") is None

    def test_empty_returns_none(self):
        assert parse_date("") is None
        assert parse_date(None) is None
