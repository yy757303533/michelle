#!/usr/bin/env python3
"""从 MCP 配置中移除 webtest、playwright"""
import json
import sys

def main():
    path = sys.argv[1]
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return
    servers = data.get("mcpServers", {})
    servers.pop("webtest", None)
    servers.pop("playwright", None)
    data["mcpServers"] = servers
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
