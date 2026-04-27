#!/usr/bin/env python3
"""本地测试 save_test_results 接口"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from webtest_mcp.server import save_test_results


def main():
    async def run():
        return await save_test_results(
            "demo",
            [
                {"case_id": "TC001", "title": "登录", "passed": True},
                {"case_id": "TC002", "title": "失败用例", "passed": False, "error": "示例"},
            ],
            excel_path="cases.xlsx",
        )

    r = asyncio.run(run())
    out = json.loads(r)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
