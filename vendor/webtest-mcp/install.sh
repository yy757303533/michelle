#!/usr/bin/env sh
# webtest-mcp-server 一键安装（macOS / Linux）
# 仅适配 Claude Code

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
CLAUDE_JSON="$HOME/.claude.json"
PROJECTS_DIR="$PROJECT_ROOT/projects"

# 检测 Python
if command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON=python
else
  echo "错误: 未找到 python 或 python3"
  exit 1
fi

echo ""
echo "  ╔═══════════════════════════════════════════════════════╗"
echo "  ║     webtest-mcp-server × Claude Code                 ║"
echo "  ║     Skill + MCP 一键安装                              ║"
echo "  ╚═══════════════════════════════════════════════════════╝"
echo ""
echo "  将安装："
echo "    • 3 个 Skill  → ~/.claude/skills"
echo "    • webtest + playwright → ~/.claude.json（Claude Code）"
echo ""

# 1. 安装 Python 包
echo "[1/3] 安装 Python 依赖..."
cd "$PROJECT_ROOT"
"$PYTHON" -m pip install -e . -q
echo "  ok"

# 2. 安装 Skill
echo "[2/3] 安装 Skill..."
mkdir -p "$HOME/.claude/skills"
for skill in web-test-runner case-generator case-executor; do
  cp -r "$PROJECT_ROOT/.claude/skills/$skill" "$HOME/.claude/skills/"
  echo "  ~/.claude/skills/$skill"
done

# 3. 注册 MCP 到 Claude Code
echo "[3/3] 注册 MCP..."

# 写入 ~/.claude.json（带 WEBTEST_PROJECTS_DIR）
mkdir -p "$(dirname "$CLAUDE_JSON")"
"$PYTHON" "$PROJECT_ROOT/scripts/merge_mcp_config.py" "$CLAUDE_JSON" "$PROJECTS_DIR"
echo "  已配置 $CLAUDE_JSON（含 WEBTEST_PROJECTS_DIR=$PROJECTS_DIR）"

# 同时用 claude cli 注册（如果已安装）
if command -v claude >/dev/null 2>&1; then
  claude mcp add webtest --scope user \
    -e "WEBTEST_PROJECTS_DIR=$PROJECTS_DIR" \
    -- webtest-mcp 2>/dev/null && echo "  claude mcp add webtest: ok" || \
    echo "  claude mcp add 跳过（已存在，~/.claude.json 已写入）"
  claude mcp add playwright --scope user \
    -- npx @playwright/mcp@latest 2>/dev/null && echo "  claude mcp add playwright: ok" || \
    echo "  claude mcp add playwright 跳过（已存在）"
fi

echo ""
echo "═══════════════════════════════════════════════════════"
echo "  安装完成！"
echo "═══════════════════════════════════════════════════════"
echo ""
echo "  1. 重启 Claude Code 使 MCP 生效"
echo "  2. 确保已安装 Node.js（playwright-mcp 需要 npx）"
echo "  3. 如需 .xls 格式支持：pip install -e \".[xls]\""
echo "  4. 使用：对 AI 说「根据需求文档给 xxx 项目生成用例」"
echo ""
