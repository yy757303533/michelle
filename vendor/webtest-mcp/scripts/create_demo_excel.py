#!/usr/bin/env python3
"""创建 demo 示例 Excel，便于本地测试"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = ROOT / "projects" / "demo"


def main():
    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    if ws is None:
        ws = wb.create_sheet("Sheet1")
    ws.title = "cases"

    # 表头：自然语言格式
    ws.append(["用例ID", "标题", "步骤描述", "期望结果", "标签"])
    ws.append(["TC001", "登录成功", "打开登录页，输入 tomsmith / SuperSecretPassword!，点击登录", "显示 Secure Area", "smoke"])
    ws.append(["TC002", "登录失败", "打开登录页，输入错误密码，点击登录", "显示错误提示", ""])

    path = DEMO_DIR / "cases.xlsx"
    wb.save(path)
    print(f"已创建: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
