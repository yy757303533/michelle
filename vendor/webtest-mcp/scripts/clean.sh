#!/usr/bin/env sh
# 清理 __pycache__、.pytest_cache 等，打包前建议执行
cd "$(dirname "$0")/.."
rm -rf src/webtest_mcp/__pycache__ tests/__pycache__
rm -rf .pytest_cache
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
echo "已清理 __pycache__、.pytest_cache"
