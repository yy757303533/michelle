# 快速验证（Windows / macOS / Linux）

完整步骤见 [TESTING.md](TESTING.md)。

## 快速验证

```bash
cd webtest-mcp-server
pip install -e .
# Windows CMD: set PYTHONPATH=src
# macOS/Linux: export PYTHONPATH=src

python scripts/create_demo_excel.py
python scripts/list_cases.py demo cases.xlsx
python -m pytest tests/test_config.py tests/test_loader.py -v
```
