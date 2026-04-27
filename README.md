# Michelle

> AI-native Web 测试平台:**PRD → AI 生成用例 → 人工 review → 一键执行 → AI 诊断 → 沉淀闭环**。

设计文档:[`docs/prd.md`](docs/prd.md) · 架构决策:[`docs/adr/`](docs/adr/)

## 核心差异化

不是把 AI 当代码生成器,是把 AI 贯穿全流程:

- **生成**:Claude 读 PRD,产出自然语言形式的用例
- **执行**:Claude session + `@playwright/mcp`(微软官方 ARIA tree)驱动浏览器
- **诊断**:用例失败时 AI 读 trace + 截图,输出根因 + 修复建议
- **沉淀**:每次失败让平台更准 —— `compound engineering` 闭环

## 技术栈

后端 **Python 3.12+ / FastAPI / SQLModel / structlog / OpenTelemetry**
前端 **Vite + React 19 + TypeScript / TanStack Router / Tailwind / shadcn-ui**
执行 **claude CLI(Claude Max 订阅)+ `@playwright/mcp`**
LLM Gateway 主备多通道:Claude(主) → MiniMax(备) → Flywheel(升级)

## 快速开始

```bash
make setup       # 装依赖 + 验证 CLI
cp .env.example .env   # 填 MINIMAX_API_KEY (可选)
make dev         # 起 backend(8000) + frontend(5173)
```

## 项目结构

```
backend/          FastAPI + agent 编排 + LLM Gateway
frontend/         Vite + React 5 页面骨架
vendor/webtest-mcp/   Fork 自 webtest-mcp-server,作执行内核
docs/             PRD + ADR + Event Catalog + Prompt 历史
```
