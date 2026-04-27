# webtest-mcp-server

从需求文档到自动化测试的完整流水线：**需求文档 → 生成用例 → 执行测试 → HTML 报告**。

AI 作为决策者，读取自然语言用例后，实时调用 `@playwright/mcp` 操作浏览器执行测试。

**→ 新手从这里开始：[docs/GETTING_STARTED.md](docs/GETTING_STARTED.md)**

---

## 架构

```
双 MCP 架构
  webtest-mcp     ← Python，读写 Excel、保存结果
  @playwright/mcp ← Node.js，操作浏览器

3 个 Skill
  web-test-runner   ← 总入口，判断意图，路由到子 Skill
  case-generator    ← 子 Skill A：需求文档 → Excel 用例
  case-executor     ← 子 Skill B：Excel 用例 → 执行 → HTML 报告
```

---

## 安装

**一键安装**（Windows/macOS/Linux 均支持）：

| 平台 | 命令 |
|------|------|
| macOS / Linux | `sh install.sh` |
| Windows (PowerShell) | `.\install.ps1` |

安装脚本完成：
1. `pip install -e .`（含 xlrd 可选依赖）
2. 部署全部 3 个 Skill 到 `~/.claude/skills`
3. 合并 webtest + playwright MCP 到 Claude Code 全局配置（不覆盖已有项）

如需读取 `.xls` 老格式文件：

```bash
pip install -e ".[xls]"
```

---

## 项目配置

每个项目在 `projects/<key>/` 下：

```
projects/huudi/
  project.yaml         ← 配置（base_url、login，不要提交到 git）
  project.yaml.example ← 模板（可提交）
  huudi_cases_v1.xlsx  ← 用例文件（AI 生成或手工维护）
  artifacts/           ← 执行结果（自动生成，不提交）
    huudi/
      report.html      ← 最新累计报告
      result.json      ← 最新累计数据
      20260319T.../    ← 每次运行的独立存档
```

`project.yaml` 示例：

```yaml
base_url: "https://huudi.chintec.net"
login:
  url: "https://huudi.chintec.net/login"
  account: "user@example.com"
  password_value: "your-password"
```

---

## 使用

### 流程 A：需求文档 → 生成用例

对 AI 说：

```
根据 huudi_specs_EN.md 给 huudi 项目生成测试用例
```

AI 会分析需求文档，调用 `generate_cases` 写入 Excel，输出模块分布摘要。

### 流程 B：执行已有用例

```
执行 huudi 项目的 huudi_cases_v1.xlsx
```

AI 按模块分批执行，每模块完成后调用 `save_test_results` 保存结果，最终在 `artifacts/huudi/report.html` 生成可交互 HTML 报告（含进度条、筛选器、FAIL 截图）。

### 完整流水线（A + B）

```
从需求文档到测试结果，给 huudi 项目全流程跑一遍
```

---

## MCP 工具

| 工具 | 用途 |
|------|------|
| `list_projects_tool` | 列出已配置项目 |
| `get_grouped_cases` | 按模块分组查看用例概要 |
| `get_excel_cases` | 获取指定模块完整用例（含过滤） |
| `generate_cases` | 需求文档分析结果 → 写入 Excel |
| `save_test_results` | 保存结果，生成 JSON + MD + HTML 报告，支持截图嵌入，多次调用自动累计 |

---

## Excel 格式

**推荐列头**（中文）：

| 用例编号 | 模块 | 测试类型 | 用例说明（名称）| 预置条件 | 测试步骤 | 预期结果 | 等级 | 场景标签 |
|----------|------|----------|----------------|----------|----------|----------|------|----------|

**步骤格式**：`S1. 点击登录按钮\nS2. 输入邮箱`

**预期格式**：`E1. 跳转到首页\nE2. 显示用户名`

支持 `.xlsx` 和 `.xls`（`.xls` 需安装 `[xls]` 可选依赖）。

---

## 本地验证

```bash
# 语法检查
python3 -c "import ast; ast.parse(open('src/webtest_mcp/server.py').read()); print('OK')"

# 单元测试（需 pip install -e ".[dev]"）
PYTHONPATH=src pytest tests/ -v
```

---

## 安全注意

- `project.yaml` 含账号密码，**不要提交到 git**（已在 `.gitignore` 中忽略）
- 使用 `project.yaml.example` 作为模板提交
- `artifacts/` 目录含测试结果，也已忽略
