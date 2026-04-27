"""测试 Excel loader"""

import pytest
from pathlib import Path

from webtest_mcp.loader import load_excel_cases, filter_cases_by_tags, ExcelCase, CaseStep

FIXTURES = Path(__file__).parent.parent / "projects" / "demo"


def test_load_excel_cases():
    excel_path = FIXTURES / "cases.xlsx"
    if not excel_path.exists():
        pytest.skip("cases.xlsx 不存在")
    cases = load_excel_cases(excel_path)
    assert len(cases) >= 1
    c = cases[0]
    assert isinstance(c, ExcelCase)
    assert c.case_id
    assert len(c.steps) >= 1
    assert isinstance(c.steps[0], CaseStep)
    assert c.steps[0].description


def test_filter_by_tags():
    excel_path = FIXTURES / "cases.xlsx"
    if not excel_path.exists():
        pytest.skip("cases.xlsx 不存在")
    cases = load_excel_cases(excel_path)
    filtered = filter_cases_by_tags(cases, ["smoke"])
    # 有 smoke 标签或无标签的用例会保留
    assert isinstance(filtered, list)
    empty = filter_cases_by_tags(cases, ["nonexistent_tag_xyz"])
    assert len(empty) == 0
