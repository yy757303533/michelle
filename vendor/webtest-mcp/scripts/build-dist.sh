#!/usr/bin/env sh
# 打包：先清理，再构建 PyPI 包。务必在项目根目录执行。
# 若需源码发布 zip，用 git archive：git archive -o webtest-mcp-server.zip HEAD
# （git archive 自动排除 .gitignore，避免 __pycache__、.pytest_cache、artifacts 打入）
set -e
cd "$(dirname "$0")/.."
sh scripts/clean.sh
pip install build -q 2>/dev/null || true
python -m build
echo "构建完成，输出在 dist/"
