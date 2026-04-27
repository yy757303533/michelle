#!/usr/bin/env sh
# webtest-mcp-server 卸载（macOS / Linux）
# 仅适配 Claude Code

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
PYTHON="${PYTHON:-python3}"
command -v python3 >/dev/null 2>&1 || PYTHON=python

echo ""
echo "  webtest-mcp-server 卸载"
echo "  将移除:"
echo "    • ~/.claude/skills 中的 3 个 Skill"
echo "    • ~/.claude.json 中的 webtest/playwright"
echo ""
read -r -p "  继续? [y/N] " CONFIRM
case "$CONFIRM" in [Yy]*) ;; *) echo "已取消"; exit 0 ;; esac

# 移除 3 个 Skill
for skill in web-test-runner case-generator case-executor; do
  d="$HOME/.claude/skills/$skill"
  [ -d "$d" ] && rm -rf "$d" && echo "  已移除 $d"
done

# 从 ~/.claude.json 移除 webtest、playwright
f="$HOME/.claude.json"
[ -f "$f" ] && \
  "$PYTHON" "$PROJECT_ROOT/scripts/remove_mcp_config.py" "$f" && \
  echo "  已从 $f 移除 webtest/playwright" || true

# claude cli 注销
if command -v claude >/dev/null 2>&1; then
  claude mcp remove webtest    --scope user 2>/dev/null && echo "  claude mcp remove webtest: ok"    || true
  claude mcp remove playwright --scope user 2>/dev/null && echo "  claude mcp remove playwright: ok" || true
fi

echo ""
echo "卸载完成。重启 Claude Code 生效。"
echo "Python 包需手动卸载: pip uninstall webtest-mcp-server"
echo ""
