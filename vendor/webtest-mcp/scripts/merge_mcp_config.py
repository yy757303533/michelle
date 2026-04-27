#!/usr/bin/env python3
"""
合并 webtest + playwright 到 Claude Code MCP 配置文件。

用法：
  python merge_mcp_config.py <config_path> [projects_dir]

  config_path   : ~/.claude.json（Claude Code 读取的配置文件）
  projects_dir  : 可选，webtest-mcp 的 projects 目录绝对路径。
                  传入后 webtest 条目会带 WEBTEST_PROJECTS_DIR 环境变量。
"""
import json
import sys
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        print("用法: merge_mcp_config.py <config_path> [projects_dir]", file=sys.stderr)
        sys.exit(1)

    path = sys.argv[1]
    projects_dir = sys.argv[2] if len(sys.argv) >= 3 else ""

    webtest_entry: dict = {"command": "webtest-mcp"}
    if projects_dir:
        webtest_entry["env"] = {"WEBTEST_PROJECTS_DIR": projects_dir}

    entry = {
        "webtest": webtest_entry,
        "playwright": {"command": "npx", "args": ["@playwright/mcp@latest"]},
    }

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}

    data.setdefault("mcpServers", {})
    data["mcpServers"].update(entry)

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


if __name__ == "__main__":
    main()
