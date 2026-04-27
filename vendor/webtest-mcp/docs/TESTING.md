# 单元测试说明

本文档面向开发者，说明如何在本地运行单元测试来验证代码正确性。

> 如果你是第一次使用本项目，请先阅读 [GETTING_STARTED.md](GETTING_STARTED.md)。

---

## 运行单元测试

### 前置

```bash
# 安装开发依赖
pip install -e ".[dev,xls]"
```

### 设置 PYTHONPATH

每次新开终端都需要执行：

| 平台 | 命令 |
|------|------|
| Windows CMD | `set PYTHONPATH=src` |
| Windows PowerShell | `$env:PYTHONPATH = "src"` |
| macOS / Linux | `export PYTHONPATH=src` |

### 运行全部测试

```bash
PYTHONPATH=src pytest tests/ -v
```

**Windows PowerShell：**
```powershell
$env:PYTHONPATH = "src"
pytest tests/ -v
```

---

## 测试文件说明

| 文件 | 覆盖范围 |
|------|----------|
| `tests/test_config.py` | 项目配置加载（list_projects、load_project_config） |
| `tests/test_loader.py` | Excel 读取（load_excel_cases、filter_cases_by_tags） |
| `tests/test_server.py` | MCP 工具（save_test_results、generate_cases、list/grouped/excel） |

### test_server.py 覆盖的测试用例

- `test_save_basic` — PASS/FAIL/SKIP 统计正确，文件全部生成
- `test_save_with_screenshot` — 截图以 base64 嵌入 HTML
- `test_save_screenshot_missing_file` — 截图文件不存在时不报错
- `test_save_cumulative` — 分批两次调用，累计报告包含全部结果
- `test_save_invalid_project` — 不存在的项目返回 success=False
- `test_generate_cases_basic` — 写入 Excel，loader 能读回
- `test_generate_cases_skip_incomplete` — 缺必填字段的用例被跳过
- `test_generate_cases_auto_filename` — 自动生成带时间戳的文件名
- `test_generate_cases_path_traversal` — 路径穿越攻击被拦截
- `test_list_projects` — 列出项目包含 demo
- `test_get_grouped_cases` — 分组返回正确
- `test_get_excel_cases_empty_tag_filter` — 不存在的标签返回空列表

---

## 预期结果

```
tests/test_config.py::test_list_projects       PASSED
tests/test_config.py::test_load_project_config PASSED
tests/test_loader.py::test_load_excel_cases    PASSED
tests/test_loader.py::test_filter_by_tags      PASSED
tests/test_server.py::test_save_basic          PASSED
tests/test_server.py::test_save_with_screenshot PASSED
...（共 16 个测试）
```

`test_server.py` 中的测试需要 `mcp` 包，已通过 `pip install -e .` 安装则全部通过；否则标记为 `SKIP`。

---

## 创建示例数据

`projects/demo/` 的 xlsx 文件用于 test_loader 测试，如不存在可生成：

```bash
python scripts/create_demo_excel.py
```
