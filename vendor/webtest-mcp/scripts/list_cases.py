#!/usr/bin/env python3
"""列出 Excel 中的自然语言用例（本地验证）"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from webtest_mcp.loader import load_excel_cases
from webtest_mcp.config import load_project_config


def main():
    if len(sys.argv) < 3:
        print("用法: python scripts/list_cases.py <project> <excel_path>")
        print("  例: python scripts/list_cases.py demo login_cases.xlsx")
        sys.exit(1)
    project, excel_name = sys.argv[1], sys.argv[2]
    root = Path(__file__).resolve().parent.parent
    excel_path = root / "projects" / project / excel_name
    if not excel_path.exists():
        print(f"错误: 文件不存在 {excel_path}")
        sys.exit(1)
    cases = load_excel_cases(excel_path)
    config = load_project_config(project)
    base_url = config.get("base_url", "")
    out = {
        "project": project,
        "base_url": base_url,
        "count": len(cases),
        "cases": [
            {
                "case_id": c.case_id,
                "title": c.title,
                "module": c.module,
                "precondition": c.precondition,
                "priority": c.priority,
                "tags": c.tags,
                "test_type": c.test_type,
                "steps": [{"step_no": s.step_no, "description": s.description} for s in c.steps],
                "expected": [
                    {"expect_no": e.expect_no, "description": e.description}
                    for e in c.expected
                ],
            }
            for c in cases
        ],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
