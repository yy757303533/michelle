# 快速上手指南

从零开始，完成"环境安装 → 生成用例 → 执行测试 → 查看报告"的完整流程。

---

## 目录

1. [前置条件](#1-前置条件)
2. [安装步骤](#2-安装步骤)
3. [配置你的项目](#3-配置你的项目)
4. [怎么提问（Prompt 指南）](#4-怎么提问prompt-指南)
5. [查看测试结果](#5-查看测试结果)
6. [常见问题](#6-常见问题)

---

## 1. 前置条件

在安装本项目之前，确保以下工具已就绪：

### 必须安装

| 工具 | 版本要求 | 检查命令 | 下载 |
|------|----------|----------|------|
| Python | 3.10 或以上 | `python --version` | [python.org](https://www.python.org/downloads/) |
| Node.js | 18 或以上 | `node --version` | [nodejs.org](https://nodejs.org/) |
| Claude Code | 最新版 | — | [claude.ai/code](https://claude.ai/code) |

### Python 版本检查

**Windows（PowerShell）：**
```powershell
python --version
# 或
py -3 --version
```

**macOS / Linux：**
```bash
python3 --version
```

> 如果输出 `Python 2.x`，请使用 `python3` 代替 `python`。

### Node.js 检查

```bash
node --version   # 应显示 v18.x.x 或以上
npx --version    # 应正常输出版本号
```

> playwright-mcp 通过 `npx` 启动，Node.js 是必须的。

---

## 2. 安装步骤

### 第一步：获取项目

```bash
# 解压下载的 zip 包，或 git clone
unzip webtest-mcp-server.zip
cd webtest-mcp-server-master
```

### 第二步：一键安装

根据你的操作系统选择命令：

**Windows（PowerShell）：**
```powershell
.\install.ps1
```

> 如果提示"执行策略"错误，先运行：
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```

**macOS / Linux：**
```bash
sh install.sh
```

安装脚本会自动完成以下操作：
1. 安装 Python 依赖（`mcp`、`openpyxl`、`pyyaml`）
2. 部署 3 个 Skill 到 `~/.claude/skills`
3. 注册 `webtest-mcp` 和 `@playwright/mcp` 到 Claude Code 全局配置

**如果需要读取 `.xls` 格式的旧版 Excel（如从其他测试工具导出的用例）：**
```bash
# Windows
pip install -e ".[xls]"

# macOS / Linux
pip install -e ".[xls]"
```

### 第三步：重启 IDE

安装完成后，**必须重启** Claude Code，MCP 配置才会生效。

### 第四步：验证安装

重启后，在 Claude Code 的设置页面确认 MCP 工具已加载：

- 打开 Settings → MCP
- 应看到 `webtest` 和 `playwright` 两个 MCP 服务
- 状态应为绿色（已连接）

或者直接问 AI：
```
列出当前可用的 webtest 项目
```
AI 应该能回复项目列表，说明 MCP 已正常连接。

---

## 3. 配置你的项目

### 目录结构

每个被测项目需要在 `projects/` 下创建独立目录：

```
projects/
  你的项目名/           ← 项目 key，英文小写，无空格
    project.yaml       ← 项目配置（base_url + 登录信息）
    project.yaml.example  ← 模板（可提交到 git）
```

### 创建 project.yaml

参考模板 `project.yaml.example`，复制并填写真实信息：

```yaml
# 被测网站的根地址（必填）
base_url: "https://your-site.com"

# 登录配置（如果测试需要登录才能访问，则必填）
login:
  url: "https://your-site.com/login"      # 登录页 URL
  account: "test@example.com"             # 测试账号
  password_value: "your-test-password"    # 测试密码
```

> ⚠️ `project.yaml` 含账号密码，已在 `.gitignore` 中忽略，不会被提交到 git。

### 已有 Excel 用例的情况

如果你已有手工测试用例的 Excel 文件，直接放到项目目录下：

```
projects/
  your-project/
    project.yaml
    your_cases.xlsx     ← 直接放这里
```

Excel 支持的列头（中英文均可）：

| 必填列 | 可选列 |
|--------|--------|
| 用例编号 / case_id | 模块 / module |
| 用例说明（名称）/ title | 测试类型 / test_type |
| 测试步骤 / 步骤描述 | 预置条件 / precondition |
| 预期结果 / 期望结果 | 等级 / priority |
| | 场景标签 / tags |

步骤格式：`S1. 点击登录按钮\nS2. 输入邮箱`（每步换行分隔）

预期格式：`E1. 跳转到首页\nE2. 显示用户名`

---

## 4. 怎么提问（Prompt 指南）

打开 Claude Code，直接用自然语言描述你要做的事。

### 场景 A：从需求文档生成用例

适用于：你有产品需求文档（.md / .txt / 直接粘贴），希望 AI 帮你拆解测试用例并写入 Excel。

**把需求文档上传或粘贴后，说：**

```
根据这份需求文档，给 huudi 项目生成测试用例
```

```
分析这个需求文档，生成覆盖注册、登录、KYC 三个模块的测试用例，写入 huudi 项目
```

```
帮我把 huudi_specs_EN.md 里的需求转成手工测试用例，保存到 huudi 项目
```

AI 会：
1. 逐章节分析需求文档
2. 拆解功能测试、输入校验、边界值、状态机、安全性 5 类用例
3. 调用 `generate_cases` 写入 Excel
4. 输出各模块用例数量的摘要

生成完成后，AI 会告诉你 Excel 文件的位置，例如：
```
已生成 85 条用例 → projects/huudi/huudi_cases_20260319.xlsx
  用户注册和登录：31 条
  KYC验证：28 条
  投资流程：26 条
```

---

### 场景 B：执行已有用例

适用于：你已经有 Excel 用例文件（手工编写或 AI 生成的），希望 AI 自动跑测试。

**说：**

```
执行 huudi 项目的 huudi_cases_20260319.xlsx
```

```
跑 huudi 项目的测试用例，用例文件是 huudi_cases_v1.xlsx
```

```
帮我测试 huudi 项目，用 KYC验证 模块的用例
```

（只跑某个模块时加上模块名）

AI 会：
1. 读取用例分组，告诉你各模块有多少条
2. 自动打开浏览器并登录（如果 project.yaml 配了 login）
3. 按模块逐条执行，失败时自动截图
4. 每完成一个模块立即保存结果
5. 输出总汇总

---

### 场景 C：完整流水线（需求文档直接到测试报告）

**说：**

```
拿这份需求文档，给 huudi 项目生成用例并执行，最后给我看报告
```

```
全流程：分析 huudi_specs_EN.md，生成测试用例，然后执行，生成 HTML 报告
```

AI 会自动完成生成 + 执行 + 报告的完整链路。

---

### 其他有用的提问方式

```
# 查看项目列表
列出当前有哪些测试项目

# 只生成某个模块的用例
根据需求文档，只生成 KYC验证 模块的测试用例

# 按优先级过滤执行
只执行 huudi 项目高优先级的用例

# 查看用例概要（不执行）
查看 huudi_cases_v1.xlsx 有哪些模块，每个模块多少条
```

---

## 5. 查看测试结果

### 结果文件位置

每次执行后，结果保存在：

```
projects/
  你的项目名/
    artifacts/
      你的项目名/
        report.html      ← 最新累计报告（主要看这个）
        result.json      ← 原始数据（机器可读）
        report.md        ← Markdown 格式报告
        20260319T123456Z-abc12345/   ← 本次运行存档目录
          report.html    ← 本次报告
          result.json    ← 本次数据
          screenshots/   ← 失败截图（如有）
```

### 打开 HTML 报告

**Windows：**
```powershell
# 在文件资源管理器中双击，或：
start projects\huudi\artifacts\huudi\report.html
```

**macOS：**
```bash
open projects/huudi/artifacts/huudi/report.html
```

**Linux：**
```bash
xdg-open projects/huudi/artifacts/huudi/report.html
```

### 报告功能说明

打开 `report.html` 后，你会看到：

```
┌─────────────────────────────────────────────────────┐
│  huudi 测试报告                                      │
│  2026-03-19T12:34:56Z | huudi_cases_v1.xlsx         │
├──────────┬──────────┬──────────┬──────────┐         │
│  总计 85 │  通过 72 │  失败 5  │  跳过 8  │         │
└──────────┴──────────┴──────────┴──────────┘         │
│  ████████████████████████░░░░ 85%                    │
│                                                      │
│  [全部 85] [通过 72] [失败 5] [跳过 8]  [搜索框...]  │
│                                                      │
│  用例ID    模块     标题              结果  备注      │
│  T0001    注册     First Name-空值   PASS            │
│  T0002    注册     Email格式错误     FAIL  E1失败:…  │
│           [缩略截图]                                  │
└─────────────────────────────────────────────────────┘
```

**筛选按钮：** 点击「失败」只显示未通过的用例，方便快速定位问题。

**搜索框：** 输入用例ID、模块名或标题关键词，实时过滤。

**失败截图：** FAIL 用例如果有截图，会直接在备注列显示缩略图，点击可放大查看失败现场。

### 结果状态说明

| 状态 | 含义 |
|------|------|
| ✅ PASS | 所有步骤执行成功，所有预期结果验证通过 |
| ❌ FAIL | 某步骤执行出错，或某条预期结果不符 |
| ⏭️ SKIP | 无法自动执行（时间条件、需要预置数据、视觉验证等） |

SKIP 不是测试失败，是 AI 判断该用例需要人工介入，会在备注中说明原因。

### 多次执行的累计

如果你分模块执行（例如先跑注册模块，再跑 KYC 模块），`report.html` 会自动累计所有模块的结果。每次执行都有独立的存档目录，不会互相覆盖。

---

## 6. 常见问题

### 安装相关

**Q：运行 `.\install.ps1` 提示"无法加载文件，因为在此系统上禁止运行脚本"**

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
.\install.ps1
```

**Q：`pip install` 报错 `externally-managed-environment`（常见于 macOS Homebrew Python）**

```bash
pip install -e . --break-system-packages
# 或创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

**Q：安装完重启 IDE 后 MCP 还是显示未连接**

检查 MCP 配置文件是否存在：`~/.claude.json`

打开文件确认包含 `webtest` 和 `playwright` 两项，然后再次重启 IDE。

---

### 执行相关

**Q：AI 说"项目 xxx 不存在"**

确认 `projects/xxx/project.yaml` 文件存在。项目 key 区分大小写，与目录名完全一致。

**Q：AI 执行到登录步骤就停止了**

检查 `project.yaml` 中的 `login.url` 是否是登录页而非注册页，账号密码是否正确。

**Q：用例执行全部是 SKIP**

多数情况是因为用例的 `precondition` 包含无法自动满足的条件（如"需要 3 条历史数据"），这类用例 AI 会合理地标记为 SKIP。可以通过手工预置数据后重跑。

**Q：`.xls` 文件报错 `读取 .xls 文件需要 xlrd 库`**

```bash
pip install -e ".[xls]"
```

**Q：想只跑部分用例怎么办**

在提问时加上模块名或优先级：
```
只执行 huudi 项目 KYC验证 模块的高优先级用例
```

---

### 报告相关

**Q：报告在哪里**

```
projects/<项目名>/artifacts/<项目名>/report.html
```

**Q：想保留每次执行的历史报告**

每次执行都会在 `artifacts/<项目名>/<run_id>/` 下生成独立存档，历史数据不会丢失，只有根目录的 `report.html` 会被更新为最新累计结果。

**Q：截图没有出现在报告里**

截图只有在用例 FAIL 时才会被截取和嵌入。PASS 和 SKIP 的用例不截图。

---

## 附录：目录结构速查

```
webtest-mcp-server-master/
├── install.sh          ← macOS/Linux 一键安装
├── install.ps1         ← Windows 一键安装
├── src/
│   └── webtest_mcp/
│       ├── server.py   ← MCP 工具实现
│       ├── loader.py   ← Excel 读取
│       └── config.py   ← 项目配置加载
├── projects/
│   ├── demo/           ← 示例项目（the-internet.herokuapp.com）
│   └── your-project/  ← 你的项目（自己创建）
│       ├── project.yaml
│       ├── project.yaml.example
│       ├── cases.xlsx
│       └── artifacts/  ← 执行结果（自动生成）
├── .claude/
│   └── skills/
│       ├── web-test-runner/  ← 总入口 Skill
│       ├── case-generator/   ← 生成用例 Skill
│       └── case-executor/    ← 执行用例 Skill
└── docs/
    ├── GETTING_STARTED.md  ← 本文件
    └── TESTING.md          ← 单元测试说明
```
