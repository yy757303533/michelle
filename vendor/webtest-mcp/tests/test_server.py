"""测试 MCP 工具"""

import asyncio
import json
import shutil
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
DEMO_DIR = PROJECT_ROOT / "projects" / "demo"

try:
    from webtest_mcp.server import (
        save_test_results,
        generate_cases,
        list_projects_tool,
        get_grouped_cases,
        get_excel_cases,
    )
    HAS_MCP = True
except ImportError:
    HAS_MCP = False

pytestmark = pytest.mark.skipif(not HAS_MCP, reason="mcp 未安装，需 pip install -e .")
skip_no_demo = pytest.mark.skipif(
    not (DEMO_DIR / "project.yaml").exists(), reason="projects/demo 不存在"
)


# ─── save_test_results ───────────────────────────────────────────────────────

@skip_no_demo
def test_save_basic():
    """PASS/FAIL/SKIP 统计正确，必要文件全部生成"""
    out = asyncio.run(save_test_results(
        project="demo",
        results=[
            {"case_id": "T001", "module": "登录", "title": "正常登录", "passed": True},
            {"case_id": "T002", "module": "登录", "title": "密码错误", "passed": False, "error": "E1 失败"},
            {"case_id": "T003", "module": "登录", "title": "时间条件", "passed": None,  "error": "SKIP: 无法模拟"},
        ],
        excel_path="login.xlsx",
    ))
    r = json.loads(out)
    assert r["success"] is True, r.get("error")
    for key in ("run_id", "run_dir", "report_html_path", "report_md_path",
                "latest_report_html_path", "result_path"):
        assert key in r, f"缺少字段: {key}"

    s = r["summary"]
    assert s["total"] == 3 and s["passed"] == 1 and s["failed"] == 1 and s["skipped"] == 1

    for key in ("result_path", "report_html_path", "report_md_path", "latest_report_html_path"):
        assert Path(r[key]).exists(), f"文件不存在: {r[key]}"

    html = Path(r["report_html_path"]).read_text(encoding="utf-8")
    for keyword in ("PASS", "FAIL", "SKIP", "登录"):
        assert keyword in html, f"HTML 缺少: {keyword}"

    shutil.rmtree(r["run_dir"], ignore_errors=True)


@skip_no_demo
def test_save_with_screenshot(tmp_path):
    """截图嵌入 HTML 作为 base64 data URI"""
    # 最小合法 PNG (1x1 白色)
    png = (
        b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
        b'\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff'
        b'\x3f\x00\x05\xfe\x02\xfe\xdc\xccY\xe7\x00\x00\x00\x00IEND\xaeB`\x82'
    )
    ss = tmp_path / "fail_T001.png"
    ss.write_bytes(png)

    out = asyncio.run(save_test_results(
        project="demo",
        results=[{"case_id": "T001", "module": "M", "title": "截图测试", "passed": False}],
        screenshots=[{"case_id": "T001", "path": str(ss)}],
    ))
    r = json.loads(out)
    assert r["success"] is True, r.get("error")
    assert r["screenshots_copied"] == 1
    assert "data:image/png;base64," in Path(r["report_html_path"]).read_text(encoding="utf-8")
    assert (Path(r["run_dir"]) / "screenshots").exists()
    shutil.rmtree(r["run_dir"], ignore_errors=True)


@skip_no_demo
def test_save_screenshot_missing_file():
    """截图文件不存在不报错，只是不嵌入"""
    out = asyncio.run(save_test_results(
        project="demo",
        results=[{"case_id": "T001", "module": "M", "title": "t", "passed": False}],
        screenshots=[{"case_id": "T001", "path": "/nonexistent/path.png"}],
    ))
    r = json.loads(out)
    assert r["success"] is True
    assert r["screenshots_copied"] == 0
    shutil.rmtree(r["run_dir"], ignore_errors=True)


@skip_no_demo
def test_save_cumulative():
    """分批两次调用，latest 报告累计全部结果"""
    # 先清空可能残留的累计文件
    from webtest_mcp.server import _get_artifacts_root
    from webtest_mcp.config import load_project_config
    config = load_project_config("demo")
    arts = Path(config["_project_dir"]) / "artifacts" / "demo"
    cum_json = arts / "result.json"
    if cum_json.exists():
        cum_json.unlink()

    r1 = json.loads(asyncio.run(save_test_results(
        project="demo",
        results=[
            {"case_id": "C001", "module": "A", "title": "a1", "passed": True},
            {"case_id": "C002", "module": "A", "title": "a2", "passed": False},
        ],
        excel_path="x.xlsx",
    )))
    assert r1["success"]

    r2 = json.loads(asyncio.run(save_test_results(
        project="demo",
        results=[{"case_id": "C003", "module": "B", "title": "b1", "passed": True}],
        excel_path="x.xlsx",
    )))
    assert r2["success"]

    cum = r2["cumulative_summary"]
    assert cum["total"] == 3
    assert cum["passed"] == 2
    assert cum["failed"] == 1

    html = Path(r2["latest_report_html_path"]).read_text(encoding="utf-8")
    assert "C001" in html and "C003" in html

    shutil.rmtree(r1["run_dir"], ignore_errors=True)
    shutil.rmtree(r2["run_dir"], ignore_errors=True)
    if cum_json.exists():
        cum_json.unlink()


@skip_no_demo
def test_save_invalid_project():
    out = asyncio.run(save_test_results(
        project="__nonexistent__",
        results=[{"case_id": "T1", "title": "t", "passed": True}],
    ))
    r = json.loads(out)
    assert r["success"] is False
    assert "不存在" in r["error"]


# ─── generate_cases ──────────────────────────────────────────────────────────

@skip_no_demo
def test_generate_cases_basic():
    from webtest_mcp.loader import load_excel_cases
    out = asyncio.run(generate_cases(
        project="demo",
        cases=[
            {
                "case_id": "GEN-T001", "module": "注册", "test_type": "功能测试",
                "title": "空值拒绝", "precondition": "P1. 用户在注册页",
                "steps": "S1. 留空\nS2. 点击提交",
                "expected": "E1. 显示错误\nE2. 无法提交",
                "priority": "高", "tags": "输入校验",
            },
            {
                "case_id": "GEN-T002", "module": "注册",
                "title": "最小长度", "steps": "S1. 输入1字符", "expected": "E1. 接受",
            },
        ],
        output_filename="_test_gen.xlsx",
    ))
    r = json.loads(out)
    assert r["success"] is True, r.get("error")
    assert r["written"] == 2 and r["skipped"] == 0

    cases = load_excel_cases(r["output_path"])
    assert len(cases) == 2
    assert cases[0].case_id == "GEN-T001"
    assert len(cases[0].steps) == 2
    assert len(cases[0].expected) == 2
    Path(r["output_path"]).unlink(missing_ok=True)


@skip_no_demo
def test_generate_cases_skip_incomplete():
    out = asyncio.run(generate_cases(
        project="demo",
        cases=[
            {"case_id": "G001", "title": "缺steps", "steps": "", "expected": "E1. ok"},
            {"case_id": "G002", "title": "正常", "steps": "S1. 做", "expected": "E1. 结果"},
        ],
        output_filename="_test_skip.xlsx",
    ))
    r = json.loads(out)
    assert r["success"] is True
    assert r["written"] == 1 and r["skipped"] == 1
    Path(r["output_path"]).unlink(missing_ok=True)


@skip_no_demo
def test_generate_cases_auto_filename():
    out = asyncio.run(generate_cases(
        project="demo",
        cases=[{"case_id": "A001", "title": "t", "steps": "S1. x", "expected": "E1. y"}],
    ))
    r = json.loads(out)
    assert r["success"] is True
    assert r["output_file"].startswith("cases_") and r["output_file"].endswith(".xlsx")
    Path(r["output_path"]).unlink(missing_ok=True)


@skip_no_demo
def test_generate_cases_path_traversal():
    out = asyncio.run(generate_cases(
        project="demo",
        cases=[{"case_id": "T1", "title": "t", "steps": "S1. x", "expected": "E1. y"}],
        output_filename="../../../evil.xlsx",
    ))
    r = json.loads(out)
    assert r["success"] is False
    assert "穿越" in r["error"]


# ─── list / grouped / excel ──────────────────────────────────────────────────

def test_list_projects():
    out = asyncio.run(list_projects_tool())
    r = json.loads(out)
    assert r["success"] is True
    assert "demo" in r["projects"]


@skip_no_demo
def test_get_grouped_cases():
    excel = DEMO_DIR / "cases.xlsx"
    if not excel.exists():
        pytest.skip("cases.xlsx 不存在")
    out = asyncio.run(get_grouped_cases(project="demo", excel_path="cases.xlsx"))
    r = json.loads(out)
    assert r["success"] is True
    assert r["total_cases"] >= 1
    assert isinstance(r["groups"], list)


@skip_no_demo
def test_get_excel_cases_empty_tag_filter():
    excel = DEMO_DIR / "cases.xlsx"
    if not excel.exists():
        pytest.skip("cases.xlsx 不存在")
    out = asyncio.run(get_excel_cases(
        project="demo", excel_path="cases.xlsx",
        tags=["__nonexistent_xyz__"]
    ))
    r = json.loads(out)
    assert r["success"] is True
    assert r["count"] == 0
