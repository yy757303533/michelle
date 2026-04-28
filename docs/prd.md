# AI-Native Web 测试平台 PRD

> **文档状态**:Draft v0.5
> **作者**:测试开发
> **创建日期**:2026-04-27
> **最近修订**:2026-04-27(v0.5:执行引擎换 `@playwright/mcp` + fork webtest-mcp + 移除 Docker 依赖)
> **目标**:面试 Portfolio 项目 + 内部 MVP 验证

---

## 0. 已确认事项

以下事项已由 PI 在 2026-04-27 确认:

| # | 事项 | 确认值 |
|---|------|-------|
| A1 | 项目名 | **Michelle**(代号 + 正式名) |
| A2 | 投入强度 | **全力投入,2 周交付 MVP** |
| A3 | Staging 目标 | **已具备** —— the demo Web app at `http://localhost:5000/`,账号 admin/password,中文 SPA |
| A4 | LLM 接入 | **Claude Max 订阅**(主),`api.minimax.chat` MiniMax 模型(备)。Flywheel 网关(Opus 4.7 / GPT-5.4-pro)配额恢复后作高端备份。无 API key 路径必需,但 MiniMax 作为 rate-limit fallback。 |
| A5 | 前端框架 | **Vite + React 19 + TypeScript**(由 Next.js 改为 Vite,见 §16.1 决策记录) |
| A6 | **执行引擎** | **`@playwright/mcp`**(微软官方 Playwright MCP,ARIA tree)由 Claude CLI 子进程驱动。**fork webtest-mcp-server 作为执行内核** —— 复用其 Excel schema、HTML 报告生成、`@playwright/mcp` 集成、`projects/<key>/` 多项目目录。Michelle 在其上加 Web UI、Review 工作流、SQLite 版本化、AI 诊断、可观测性、沉淀闭环。 |
| A7 | 存储 | MVP 用 **本地文件系统**(`./data/` + `./artifacts/<project>/`)+ SQLite。MinIO / S3 推迟到 Phase 2。**MVP 阶段不依赖 Docker**。 |

---

## 1. 概述

**Michelle** 是一个 AI-native 的 Web E2E 测试平台。区别于把 AI 当代码生成器的传统平台,Michelle 把 AI 贯穿到**生成 → 执行 → 诊断 → 沉淀**的全链路:

- **生成**:导入 PRD,LLM 生成自然语言形式的 UI 测试用例
- **执行**:Vision LLM 驱动的浏览器 agent 实时识别页面并执行
- **诊断**:AI 自动读取 trace、截图、决策日志,定位失败原因
- **沉淀**:每次失败的诊断与人工反馈回流到 prompt 与规则库,平台越用越准

**一句话定位**:**Not a tool, an agent that gets smarter the more it runs.**

---

## 2. 问题背景

### 2.1 现状痛点

1. **测试用例编写慢**:测试同学读 PRD → 手工拆 → 写用例 → 转代码,一份 PRD 要 1-3 天
2. **E2E 维护成本高**:UI 改版,Selector 大量失效,维护占测试组 60%+ 工时
3. **失败 triage 拖慢回归**:CI 一红,需要人工逐条看截图 / 日志判断"真 bug"还是"误报",每条 2-5 分钟,200 条用例就是几小时
4. **经验无沉淀**:同一类问题(如"登录态过期"导致连环挂)每个版本都重新踩一遍,无系统记忆

### 2.2 为什么 2026 年是做这个的好时机

- Vision LLM(Claude / GPT-4V)成熟,能稳定识别一般 Web UI
- 浏览器 agent 框架(`@playwright/mcp` / Midscene / Browser Use 等)开源可用,不用从零造
- LLM observability 工具链(Langfuse / Logfire)已标准化

---

## 3. 目标用户

| 用户角色 | 主要诉求 | 高频动作 |
|---------|---------|---------|
| **测试开发(主力)** | 用 AI 把重复劳动自动化,聚焦设计与质量 | 导 PRD、review 用例、看报告、调诊断 |
| **手工测试** | 不写代码也能跑 E2E | 触发执行、看报告 |
| **业务测试 lead** | 看回归健康度、知道哪些用例不稳 | 看趋势、确认诊断结论 |
| **开发(次要)** | CI 集成,失败时知道原因 | 看 PR 上的检查报告 |

**MVP 阶段只服务"测试开发"角色**,其他角色在 P1+ 接入。

---

## 4. 范围与不做的事

### 4.1 MVP(P0)做的

只做这一条主链路,做透:

```
PRD 上传 → AI 生成草稿用例 → 人工 Review → 一键执行 → AI 诊断报告
```

### 4.2 MVP 不做的(明确砍掉)

| 砍掉项 | 原因 |
|--------|------|
| 用户 / 权限 / 多租户 | MVP 单人单项目够用 |
| 接口(API)测试 | PRD 不含 API 协议,需求与 UI 测试链路差异大,稀释焦点 |
| 测试数据工厂 / Mock 服务 | 阶段性绕开,用真实 staging 数据 |
| 用例集 / 复杂编排 / 依赖图 | 单 case 跑通先 |
| CI/CD 集成 | 加分项,Phase 2 |
| 缺陷系统联动(Jira 等) | Phase 2 |
| 移动端 / 接口 / 性能测试 | 非本平台范围 |

### 4.3 永远不做的

- 不替代单元测试(那是开发的活)
- 不做"全自动无人值守 24h 跑测试"——AI 决策永远要有人工兜底入口

---

## 5. 核心场景(用户旅程)

### 场景 1:第一次导入 PRD

> 我拿到一份新功能 PRD(markdown),贴进 Michelle。
> 平台分章节调 LLM 生成 6 条草稿用例,每条标 `pending` 状态。
> 我打开 review 页,3 条直接 approve,2 条改了断言后 approve,1 条不合理 reject。
> 选中 5 条 approved 用例,点"执行",平台拉起浏览器跑。
> 5 分钟后回来看报告:4 条 pass,1 条 fail。

### 场景 2:失败用例 AI 诊断

> 那条 fail 用例我点开,看到失败步骤是"点击购买按钮"。
> 平台已经显示了 AI 诊断结论:`vision_misjudge | confidence 0.78`,
> 推理是"vision LLM 把'立即购买'识别成了'立即预订',因为这两个按钮颜色相同位置相邻"。
> 建议是"在 step intent 中加上'红色按钮'限定词"。
> 我同意诊断,标记 confirmed。这条诊断进入沉淀库。

### 场景 3:PRD 二次导入(变更场景)

> 一周后 PRD 改了第三章。我重新上传。
> 平台 diff 出"第三章变更",只对该章节重新生成用例,旧用例标 `stale` 等我决定。
> 已 approved 且与变更无关的章节用例**完全不动**。

---

## 6. 功能需求

### 6.1 P0 功能(MVP 必做)

#### F1. PRD 导入与解析
- **F1.1** 支持 markdown 文件上传与文本粘贴
- **F1.2** 按章节(`##` / `###` 标题)切分,标识每个章节边界
- **F1.3** 二次导入做章节级 diff,只对变更章节重生成

#### F2. AI 用例生成
- **F2.1** 调用 LLM(Claude Max 订阅)按章节生成草稿用例
- **F2.2** 用例数据模型(详见 §8)结构化:`name / intent / steps[] / assertions[] / 元数据`
- **F2.3** 生成时强制分桶:happy path / 边界 / 异常 / 安全敏感(如可)
- **F2.4** 每条用例打 `source: ai-generated` + `prompt_version` + `model_version`
- **F2.5** 失败重试 + 章节级失败隔离(单章节失败不影响其他)

#### F3. Review 工作流
- **F3.1** 草稿列表页:批量 approve / reject / 加 tag
- **F3.2** 单条编辑:修 intent / 加 step / 改断言
- **F3.3** 状态机:`pending → approved / rejected`,**人工编辑过的字段不被 LLM 重生成覆盖**
- **F3.4** 用例版本:每次修改产生新 version,可回看历史

#### F4. AI Agent 执行
- **F4.1** 后端 spawn `claude -p --mcp-config <conf>`,Claude session 通过 `@playwright/mcp` 操作浏览器执行用例
- **F4.2** 选用例 + 选环境 → 创建执行任务
- **F4.3** 单 case 串行执行,case 间可并发(并发数可配)
- **F4.4** 实时记录每步:Claude 的 `tool_use`(MCP 工具名 + 入参)、`tool_result`、截图、延迟、tokens
- **F4.5** 自动重试机制:整个 case 失败可配置重试次数(claude session 级,不是步骤级),记录每次结果

#### F5. 报告与失败回放
- **F5.1** 执行结果聚合:pass / fail / flaky 统计
- **F5.2** 失败用例详情页:时间线 + 每步截图缩略图 + 决策日志
- **F5.3** 一键复跑(只跑失败的)

#### F6. AI 诊断(平台杀手功能)
- **F6.1** 失败用例旁边的 `[AI 诊断]` 按钮
- **F6.2** 诊断输出结构化:`category / confidence / reasoning / fix_suggestion`
- **F6.3** category 枚举:`real_bug / flaky / selector_drift / vision_misjudge / env_issue / data_issue`
- **F6.4** 人工反馈入口:`confirmed / wrong / partially_correct`
- **F6.5** 反馈数据落库,用于后续 prompt 优化(沉淀)

### 6.2 P1 功能(MVP 后)

- F7 用户与权限(项目隔离)
- F8 CI/CD Webhook(GitLab MR / GitHub PR)
- F9 用例集 / 场景编排
- F10 用例间依赖关系
- F11 缺陷系统联动(Jira / 禅道)
- F12 多环境管理(dev / staging / prod 变量隔离)

### 6.3 P2 功能(以后再说)

- F13 PRD 质量评分与建议
- F14 Selector 缓存与混合执行(成本优化)
- F15 历史趋势看板
- F16 团队协作(评论、@提醒)
- F17 Self-healing 用例库

---

## 7. 非功能需求

### 7.1 可观测性(P0,与功能同等优先级)

**核心原则**:**日志为 AI 设计,不为人设计。** 所有日志必须 AI 可消费。

#### 7.1.1 三层架构

```
Layer 3: AI 诊断层  ←  消费 Layer 1 + 2,产出诊断结论
Layer 2: 业务语义层 ←  agent.step.executed / llm.case.generated / review.case.approved 等
Layer 1: 基础设施层 ←  OpenTelemetry traces / metrics / logs
```

#### 7.1.2 日志规范

- 全栈结构化 JSON 日志,字段名跨服务统一
- 每条日志必有 `trace_id` + `span_id` + `event` + `ts`
- 大对象(截图、DOM、prompt)以 URL 引用,不内嵌
- 事件名用命名空间:`<domain>.<entity>.<action>`(例 `agent.step.executed`)
- 维护一份 **Event Catalog**(事件目录),AI 诊断按事件名过滤

#### 7.1.3 必须采集的关键事件

| 事件 | 字段 |
|------|------|
| `llm.case.generated` | prompt_version, model, input_tokens, output_tokens, latency_ms, output_cases |
| `review.case.action` | case_id, action, before_state, after_state, user_id |
| `agent.step.executed` | case_id, step_index, step_intent, vision_model, chosen_action, confidence, alternatives, screenshot_urls, result |
| `agent.assertion.evaluated` | case_id, assertion_type, expected, actual, passed |
| `diagnosis.generated` | case_id, run_id, category, confidence, reasoning_url |
| `diagnosis.feedback` | diagnosis_id, feedback, user_id |

#### 7.1.4 工具选型

- **OpenTelemetry**:trace / log / metric 三合一标准
- **Logfire**(首选):LLM 调用与业务事件存储,Pydantic 出品,对 Python LLM 应用原生友好,免费档够 MVP
- **Langfuse**(备选):自托管开源方案,如果数据需留本地或公司禁外部云
- **本地文件系统**(`./artifacts/<project>/<run_id>/`):截图、DOM 快照、报告 HTML、trace JSONL。**MVP 不引入 MinIO / S3**(Phase 2 升级)。

### 7.2 可回滚性

闭环要求所有可变对象都能回滚:

| 对象 | 回滚机制 |
|------|---------|
| 测试用例 | 版本号 + UI 一键回到上一版 |
| Prompt | git-managed,每次提交记录 + 自动跑黄金回归集 |
| 模型版本 | 每条 case 钉死生成时模型版本,可强制重生成 |
| AI 诊断 | 人工反馈 `wrong` 后,该诊断失效不计入沉淀 |
| 执行运行 | 失败 run 自动清理脏状态,不污染统计 |

### 7.3 性能

MVP 阶段不做强约束,但必须监控:

| 指标 | MVP 目标 | 说明 |
|------|---------|------|
| PRD 章节生成耗时 | P95 < 30s | 单章节 LLM 调用 |
| Agent 单步执行 | P95 < 15s | vision LLM 一次决策 + 浏览器动作 |
| 单 case 总耗时 | P95 < 5min | 假设 10 步 |
| 报告页加载 | P95 < 2s | 失败回放含截图 |

### 7.4 安全

- LLM 订阅凭证仅本地子进程调用,不进数据库,不出本机
- 上传的 PRD 仅限当前用户访问(MVP 单用户阶段无差别)
- 无外部网络写入,执行 agent 限制在白名单域名(staging)

### 7.5 平台自测试性(平台对自己的测试)

> "测试平台自己没测试"是常见反讽。Michelle 必须避免。

#### 7.5.1 单元测试(必做)
- 所有核心模块写单测:PRD 章节切分、用例 schema 校验、prompt 拼接、诊断结果解析、状态机转换
- 目标覆盖率:核心服务层 > 70%,工具函数 > 90%
- CI 中每次提交跑

#### 7.5.2 黄金回归集(Golden Regression Set)
- 维护一组**固定用例 + 固定 demo 站**(例 `playwright.dev/todomvc`、自建 demo)
- 每次 prompt 变更或 LLM 模型升级前,必须在黄金集上跑一轮回归
- 通过率不能低于上一版本基线
- **作用**:防止"改一个 prompt 让 80% 用例好了,却让另外 5% 退化了"被忽略

#### 7.5.3 诊断回归测试
- 历史失败用例 + 已 confirmed 的人工反馈 → 入诊断回归集
- 新诊断 prompt 上线前,必须在该集上达到正确率阈值

#### 7.5.4 集成测试
- E2E 跑通"上传 PRD → 生成 → review → 执行 → 报告 → 诊断"全链路
- Mock LLM 调用(避免每次 CI 烧 token),只测平台编排逻辑
- 真实 LLM 集成测试每周一次,不进每次提交

---

## 8. 数据模型(关键)

### 8.1 测试用例(TestCase)

```yaml
case_id: TC-20260427-001
project_id: proj_demo
name: 用户名密码正确登录成功
intent: 验证使用合法账号能成功登录并跳转首页
module: 认证/登录
tags: [smoke, regression, happy-path]
priority: P0

# —— 执行部分(自然语言步骤,vision LLM 解释执行)——
preconditions:
  - 用户处于退出登录状态
  - 测试账号 test@x.com / pwd123 在数据库存在
steps:
  - intent: 打开登录页
    expected: 页面标题包含"登录"
  - intent: 在邮箱输入框填入 test@x.com
  - intent: 在密码输入框填入 pwd123
  - intent: 点击登录提交按钮
    expected: 页面跳转到 /home
assertions:
  - description: URL 路径为 /home
  - description: 页面顶部出现欢迎语 "欢迎,test"

# —— 元数据 ——
source: ai-generated   # ai-generated / manual / imported
prompt_version: v17
model_version: claude-sonnet-4-7
generated_at: 2026-04-27T10:00:00Z
generated_from: prd_chapter_3
review_status: pending  # pending / approved / rejected
manual_edited_fields: []  # 一旦人工改过 steps[2],下次重生成不覆盖该字段
version: 1
prev_version_id: null
```

### 8.2 执行运行(Run)

```yaml
run_id: run_a1b2c3
trace_id: tr_a1b2c3
case_id: TC-20260427-001
case_version: 1
env: staging
started_at: ...
ended_at: ...
status: failed  # passed / failed / flaky / aborted
step_results: [ ... ]
artifacts:
  screenshots: [s3://...]
  dom_snapshots: [s3://...]
  trace_url: ...
```

### 8.3 诊断(Diagnosis)

```yaml
diagnosis_id: diag_x1y2
run_id: run_a1b2c3
generated_at: ...
diagnoser_prompt_version: v9
category: vision_misjudge
confidence: 0.78
reasoning: |
  Step 4 的 vision LLM 在置信度 0.62 选择了"立即预订"按钮,
  备选是 0.71 的"立即购买"按钮。截图显示两按钮颜色相同位置相邻。
fix_suggestion: 在 step 4 的 intent 中追加"红色按钮"限定词
human_feedback: null  # confirmed / wrong / partially_correct
```

---

## 9. 技术架构

### 9.1 整体

```
┌──────────────────────────────────────────────────────────┐
│              Frontend (Vite + React 19 + TS)              │
│   PRD Upload  │  Review  │  Run  │  Report  │  Diagnosis  │
└──────────────────────┬───────────────────────────────────┘
                       │  REST + SSE
┌──────────────────────▼───────────────────────────────────┐
│                  Backend (FastAPI, Python 3.12+)          │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐  │
│  │ PRD Svc  │ │ Case Svc │ │ Run Svc  │ │Diagnosis Svc │  │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └──────┬───────┘  │
│       │            │            │              │          │
│       │            │     ┌──────▼──────┐       │          │
│       │            │     │  webtest-   │       │          │
│       │            │     │  mcp loader │  ← fork 复用      │
│       │            │     └──────┬──────┘       │          │
│       │            │            │              │          │
│  ┌────▼────────────▼────────────▼──────────────▼──────┐   │
│  │   LLM Gateway (provider-agnostic)                  │   │
│  │   主: claude -p (订阅)  备: MiniMax  备2: Flywheel │   │
│  └────────────────────┬───────────────────────────────┘   │
│                       │                                    │
│  ┌──────────┐  ┌──────▼──────┐  ┌──────────┐              │
│  │ SQLite   │  │ Local FS    │  │ Logfire  │              │
│  │ (data)   │  │ artifacts/  │  │ (obs)    │              │
│  └──────────┘  └─────────────┘  └──────────┘              │
└──────────────────────┬────────────────────────────────────┘
                       │ (Run Svc 触发执行时)
                       ▼
       ┌────────────────────────────────────────┐
       │   subprocess: claude -p --mcp-config   │
       │   ┌─────────────────────────────────┐  │
       │   │  Claude session (orchestrator)  │  │
       │   │  调用 @playwright/mcp tools     │  │
       │   └────────────┬────────────────────┘  │
       │                │  MCP stdio            │
       │   ┌────────────▼────────────────────┐  │
       │   │  @playwright/mcp (Node)          │  │
       │   │  ARIA tree + Playwright API      │  │
       │   └────────────┬────────────────────┘  │
       └────────────────┼───────────────────────┘
                        │
                        ▼
                ┌────────────────────┐
                │   the demo Web app     │
                │  localhost:5000│
                └────────────────────┘
```

### 9.2 关键选型与理由

| 模块 | 选型 | 理由 | 备选 |
|------|------|------|------|
| 前端 | Vite + React 19 + TypeScript + Tailwind 4 + shadcn/ui | 后端是 Python,Next.js 全栈特性零价值;SPA 模型最简,2 周冲刺优先 | Next.js(若需 SSR/SEO 才换) |
| 后端 | Python FastAPI | LLM 生态原生,async 友好 | Node Express 等价可选 |
| **执行内核** | **Fork webtest-mcp-server** | 用户已有项目,Excel schema + HTML 报告 + `@playwright/mcp` 集成成熟,~70% 重叠功能直接复用 | 重写一份(浪费时间) |
| **执行引擎** | **`@playwright/mcp`**(Microsoft 官方 Playwright MCP) | ARIA tree 确定性操作浏览器,~100ms/步,无 vision LLM 调用,demo 稳定 | DIY DOM-augmented vision agent(慢、贵、需要 LLM JSON 输出稳定) |
| 存储 | SQLite + 本地文件系统(`./artifacts/`) | MVP 单机,零运维,Docker 不依赖 | MinIO 留给 Phase 2 |
| LLM 主通道 | `claude -p` CLI 子进程 | Claude Max 订阅,无 API key,生成 + 执行编排 + 诊断 一把通吃 | - |
| LLM 备用通道 | MiniMax-Text-01 / MiniMax-M2.7 via `api.minimax.chat` | rate-limit fallback,价格便宜 | Flywheel(配额恢复后)|
| 可观测性 | OpenTelemetry + Logfire | 标准 + AI 友好 | Langfuse 自托管 |
| 任务队列 | AsyncIO in-process | MVP 不上 Celery,够用 | Phase 2 上 Redis Queue |

---

## 10. 关键设计决策

### D1. 为什么选 `@playwright/mcp` 而非 DIY vision agent?

(本决策于 v0.5 修订。原方案为 DIY DOM-augmented vision agent,因发现用户已有 webtest-mcp-server 而重新评估。)

**对比**:

| 维度 | DIY vision agent(原案) | `@playwright/mcp`(终选) |
|------|----------------------|----------------------|
| 单步延迟 | 1.8s(每步调 vision LLM) | ~100ms(ARIA 确定性) |
| 单步成本 | 6.5k tokens × N 步 | 接近零(LLM 仅在编排层) |
| Demo 稳定性 | LLM JSON 偶尔出错就翻车 | 高(ARIA 确定性) |
| 抗 UI 变更 | 中(语义识别) | 中(依赖 ARIA 标签质量) |
| AI 决策位置 | 每步 | 编排层(选 tool 调哪个) |
| 故事强度 | "自研视觉 agent" | "工业级 MCP + AI 编排" |

**选 `@playwright/mcp` 的核心理由**:14 天 MVP + 真实 demo 场景,**稳定性 > 故事炫技**。AI 算力集中在真正决策的两端 —— **生成**(创造性)和 **诊断**(深度推理),执行层确定性化。

**抗 UI 变更**:ARIA tree 在 React + Ant Design 等主流框架上覆盖良好;the demo Web app 已实测可用。如局部 ARIA 缺失,可用 MiniMax-Text-01 视觉作为退路(architecture 已预留)。

### D1.5. 为什么 fork webtest-mcp-server 而非重写?

- 用户**已经写过**该项目,代码所有权清晰,可放心 fork
- 复用价值估算:**节省 3-4 天**(执行编排 + HTML 报告 + Excel schema + `@playwright/mcp` 集成)
- Michelle 的差异化点(Web UI / Review / 诊断 / 沉淀)webtest-mcp 完全没有,**两者非竞争关系**
- 抄成熟代码是工程美德,不是耻辱

### D2. 为什么 PRD 优先生成 UI 用例,不做 API 用例?

PRD 描述用户视角行为,天然是 UI 语言。API 用例需要 Swagger 等接口契约,PRD 不含。强行生成只会得到无法执行的占位用例。

### D3. 为什么把可观测性提到 P0,与功能同优先级?

- 平台核心差异化(AI 自诊断)依赖结构化日志
- 日志格式后期改成本极高(全链路重写)
- 没有 trace_id 贯穿 → 调试困难 → 平台不能进化

### D4. 为什么用例存储自然语言步骤而不是 selector?

- 自然语言步骤抗 UI 变更
- 与 vision LLM 执行模型一致
- 人工 review 成本低(测试同学能看懂业务步骤)
- selector 由 vision 实时解析,失败可 fallback / 自愈

### D5. 为什么 LLM 主通道走 Claude CLI 子进程,而非 OpenAI/Anthropic API?

用户当前只有 Claude Max 月订阅,无 API key。CLI 子进程是合规复用订阅的唯一稳定路径。Phase 2 扩展 API key 路径是简单替换(LLM Gateway 已设计为 provider-agnostic)。

### D6. 为什么有备用通道(MiniMax / Flywheel),却仍以 Claude 订阅为主?

- Claude Max 订阅本身免费(对用户),边际成本为零
- 主通道走订阅 = **所有正常路径不烧 API key 的钱**
- 备用通道仅在 rate-limit 触发时降级,**烧的是少量 MiniMax 余额**(几元到几十元级)
- Flywheel 网关(Opus 4.7 / GPT-5.4-pro)配额恢复后可作"诊断阶段升级"路径,**用更强的 reasoning 模型分析复杂失败**

LLM Gateway 设计为 provider-agnostic,新增通道只需实现 `BaseChatClient` 接口,业务代码不动。

---

## 11. 度量指标(Success Metrics)

| 类别 | 指标 | MVP 目标 | 长期目标 |
|------|------|---------|---------|
| **效率** | PRD 一章节 → 草稿用例时间 | < 60s | < 30s |
| **效率** | 一份完整 PRD 生成总耗时 | < 10min | < 5min |
| **质量** | AI 草稿 review 通过率(approved / total) | > 50% | > 80% |
| **质量** | 用例首次执行成功率 | > 60% | > 85% |
| **稳定性** | Flaky 率(同 case 重跑结果不一致) | < 15% | < 5% |
| **诊断准确率** | AI 诊断 category 正确率(人工判定) | > 60% | > 85% |
| **沉淀效果** | 月度新增 flaky 模式自动识别数 | > 0 | > 5 |
| **成本** | 单 case 平均 LLM 成本(订阅约束下记 token 数) | < 10k tokens | < 5k tokens |

---

## 12. 风险与应对

| 风险 | 等级 | 应对 |
|------|------|------|
| LLM 订阅 rate limit 撞墙 | 高 | 章节级生成 + 失败队列 + 模型降级(haiku/4o-mini) |
| Vision LLM 误判率高 | 高 | 多置信度阈值 + alternatives 记录 + 自愈重试 + 人工兜底 |
| Staging 环境不稳定 | 中 | 区分 env_issue 与 real_bug,批量同因失败自动暂停 |
| 用户对 AI 诊断不信任 | 中 | 显示推理过程 + 置信度 + 反馈入口,渐进建立信任 |
| MVP 范围过大 2 周做不完 | 中 | 已严格砍到 6 个 P0 模块,任何 P1 推后 |
| `@playwright/mcp` 升级破坏接口 | 低 | 钉版本(`@playwright/mcp@<sha>`)+ 抽象 ExecutorEngine 接口,必要时回滚版本或换 Midscene |
| 目标页面 ARIA 标签不全 | 中 | 验证用 `browser_snapshot` 看 ARIA 树覆盖度;局部缺失时用 MiniMax 视觉作步骤级 fallback |
| 截图/DOM 存储爆炸 | 低 | 默认保留 30 天,失败用例延长至 90 天,过期自动清理 |

---

## 13. 时间表(2 周 MVP)

> 详细到日的里程碑见 §16.11(v0.5 后以该章节为准)。本节仅作概览。

```
Week 1: 骨架 + 核心可行性 + 生成链路
  Day 1: 项目骨架 + make dev + ADR
  Day 2: claude CLI + @playwright/mcp 登录 the demo target(可行性闸门)
  Day 3: LLM Gateway 三件套 + 可观测性
  Day 4: PRD 上传/diff/生成
  Day 5: Vendor webtest-mcp + SQLite + 报告生成
  Day 6: Run Orchestrator 端到端
  Day 7: 前端 5 页面联调

Week 2: 用户体验 + AI 诊断 + Demo
  Day 8: Review 工作流 + 用例版本
  Day 9: 真实场景 + 失败分类
  Day 10: Trace Viewer
  Day 11: AI 诊断 + 沉淀
  Day 12: Demo 视频 + README
  Day 13: 面试话术
  Day 14: Buffer
```

---

## 14. 沉淀机制(Compound Loop)

> 这是 Michelle 与传统测试平台的本质差异:**每一次失败都让平台更聪明一点。**

### 14.1 闭环示意

```
       ┌─── AI 生成用例 (code)
       │           ↓
       │    AI 执行 + 全量记录 (tests + logs)
       │           ↓
       │    失败 → AI 自诊断 → 人工反馈
       │           ↓
       │    沉淀:失败模式入库 / prompt 更新 / 规则提炼
       │           ↓
       └─── 下一轮生成与诊断更准 (process improved)
```

### 14.2 五类沉淀对象

| 沉淀对象 | 触发条件 | 作用 |
|---------|---------|------|
| **Flaky 模式库** | 同 case 三次重跑两次成功 | 自动打 flaky 标,先重试再报警 |
| **Selector drift 模式库** | AI 诊断为 selector_drift | 同类失败二次出现时秒识别 |
| **PRD 缺陷模式库** | 某章节生成的 case 大批失败 | 反向标记章节质量,提示作者补 |
| **环境抖动模式库** | 批量同因失败 | 自动识别 env_issue,暂停后续 |
| **Vision 误判模式库** | 反复看错同一类 UI 元素 | 累积入 vision prompt 优化材料 |

### 14.3 反馈数据如何使用

人工对 AI 诊断的 `confirmed / wrong` 反馈是**最值钱的数据**:

- 累积满 N 条 `wrong` 反馈 → 触发诊断 prompt 优化任务
- 累积满 M 条 `confirmed` → 同类模式入沉淀库
- 月度回顾:对比新旧 prompt 在历史用例上的诊断准确率

---

## 15. 参考与致谢

- **Compound Engineering** 理念(由 Every 团队 Dan Shipper / Kieran Klaassen 等推广)
- **`@playwright/mcp`** —— Microsoft 官方 Playwright MCP server(执行引擎)
- **webtest-mcp-server** —— 本项目作者自有 prior 项目,fork 进 vendor/ 作执行内核
- **Midscene.js** —— 国内开源的 AI 浏览器 agent,设计上启发我们但本项目不直接用
- **Sprint Flow** —— Claude Code 插件,启发了"AI agent 工作流"思路
- **OpenTelemetry / Logfire / Langfuse** —— 可观测性工具链

---

## 附录 A:Event Catalog 初稿

| 事件名 | 触发时机 | 关键字段 |
|--------|---------|---------|
| `prd.uploaded` | 用户上传 PRD | prd_id, chapter_count, hash |
| `prd.chapter.diff` | 二次上传 | prd_id, changed_chapters[] |
| `llm.case.generated` | LLM 返回用例 | prompt_version, model, tokens, case_count |
| `llm.case.failed` | 生成失败 | error, retry_count |
| `review.case.action` | 用户操作用例 | action, before_state, after_state |
| `run.created` | 触发执行 | run_id, case_ids[] |
| `agent.step.executed` | 每个执行步骤 | step_intent, chosen_action, confidence, alternatives |
| `agent.assertion.evaluated` | 断言判断 | type, expected, actual, passed |
| `run.completed` | 执行结束 | run_id, status, duration_ms |
| `diagnosis.generated` | AI 诊断完成 | category, confidence |
| `diagnosis.feedback` | 人工反馈 | feedback |
| `pattern.matched` | 沉淀模式命中 | pattern_id, pattern_type |

---

## 16. 实现规约(Tech Spec)

> 本章把 §9 "技术架构"细化到**可直接开工**的程度:版本钉死、目录定形、关键代码骨架、命令固定。
> 出现在本章和 §9 矛盾时,以本章为准。

### 16.1 完整技术栈(锁版本)

#### 后端(Python)

| 组件 | 选型 | 版本 / 说明 |
|------|------|------------|
| 语言 | Python | **3.12+**(实测 3.14.3 兼容,uv 自动 resolve) |
| 包管理 | uv | 0.11+,比 poetry 快,2026 年事实标准 |
| Web 框架 | FastAPI | 0.115+ |
| ORM/Schema | SQLModel | SQLAlchemy + Pydantic 二合一 |
| 异步 SQLite | aiosqlite | async 接 SQLModel |
| 迁移 | Alembic | SQLAlchemy 标配 |
| HTTP 客户端 | httpx | async-first |
| Excel 读写 | openpyxl + xlrd[xls] | 复用 webtest-mcp 的 case schema |
| 结构化日志 | structlog + Logfire SDK | Layer 1+2 日志 |
| 追踪 | opentelemetry-api/sdk | trace_id 贯穿 |
| 测试 | pytest + pytest-asyncio + respx | mock httpx |
| Lint/Format | ruff | 一个工具替 black + isort + flake8 |
| 服务器 | uvicorn | dev 用 `--reload` |

#### 执行层(被后端 spawn)

| 组件 | 选型 | 版本 / 说明 |
|------|------|------------|
| **MCP 服务器** | **`@playwright/mcp`**(微软官方) | 通过 `npx -y @playwright/mcp@latest` 调用 |
| 运行时(为 MCP) | Node | **22+**(实测 25.8.1 兼容) |
| 包管理 | pnpm(仅前端用) | 10.x |
| 浏览器引擎 | Playwright Chromium | 1217(已缓存) |
| 编排器 | `claude -p --mcp-config <conf>` | 由后端 subprocess 启动 |
| 执行内核 | webtest-mcp-server(fork 进 vendor) | 复用 loader / generator / report |

#### 前端(Web)

| 组件 | 选型 | 版本 / 说明 |
|------|------|------------|
| 构建工具 | **Vite** | 6.x,dev server 极快 |
| 框架 | React | 19 |
| 语言 | TypeScript | 5.x,strict mode |
| 路由 | **TanStack Router** | 类型安全文件路由 |
| 样式 | Tailwind CSS | 4.x |
| UI 组件 | shadcn/ui | 复制即用,Vite 兼容 |
| 数据获取 | TanStack Query | v5 |
| 状态 | Zustand | 轻量,不上 Redux |
| 表单 | React Hook Form + Zod | 与后端 schema 对齐 |
| API 类型同步 | openapi-typescript | 从 FastAPI 自动生成 |
| 包管理 | pnpm | 10.x |
| 测试 | Vitest + Playwright | 单测 + e2e |

**前端选型决策(ADR)**:从 Next.js 改为 Vite + React,原因:
1. 后端是 Python,Next.js 全栈特性(API Routes / Server Actions / RSC)**零价值**
2. 内部测试平台**无 SEO 需求**,SSR 是纯成本
3. 2 周冲刺,**架构最简**优先
4. 纯 SPA 模型与"前后端分离"的实际架构一致,心智负担最小
5. shadcn/ui 在 Vite 下完美工作,Tailwind / TanStack 系列全部框架无关

#### 存储与基础设施

| 组件 | 选型 |
|------|------|
| 主数据库 | SQLite 3(单文件,MVP 够) |
| 大对象存储 | **本地文件系统**(`./artifacts/<project>/<run_id>/`)—— MVP 不依赖 Docker |
| LLM observability | Logfire(免费档),备选自托管 Langfuse |
| 任务队列 | AsyncIO in-process(MVP),Phase 2 切 Redis Queue |
| Phase 2 升级路径 | Postgres + MinIO + Redis Queue + 多用户/权限 |

#### LLM 接入(provider-agnostic gateway)

| 通道 | 端点 / 命令 | 用途 | 优先级 |
|------|------------|------|-------|
| Claude Code CLI | `claude -p ... --output-format json [--mcp-config X]` | 主通道,所有正常路径 | P0 主 |
| MiniMax-Text-01 | `https://api.minimax.chat/v1/text/chatcompletion_v2` | 备用,Claude rate-limit 时降级,视觉 fallback | P1 备 |
| MiniMax-M2.7 | 同上 | reasoning 模型,可选用于诊断阶段升级 | P2 可选 |
| Flywheel 网关 | `https://your-proxy.example.com/v1/chat/completions` | 配额恢复后接入,Opus 4.7 / GPT-5.4-pro 用于诊断升级 | P3 升级 |
| ~~Codex CLI~~ | ~~`codex exec`~~ | 不再使用 | - |

#### 部署 / DX

| 组件 | 选型 |
|------|------|
| 容器编排 | **MVP 不需要**(本地文件 + SQLite 替代 MinIO) |
| Backend/Frontend dev | 本地起 |
| 命令入口 | Makefile(`make dev` / `make test` / `make lint`) |
| Pre-commit | pre-commit 框架 + ruff/eslint |
| Phase 2 升级 | docker-compose.yml(Postgres + MinIO + Redis) |

### 16.2 项目目录结构

```
michelle/
├── README.md
├── Makefile                       # make dev / test / lint
├── .pre-commit-config.yaml
├── .env.example                   # FLYWHEEL_TOKEN / MINIMAX_API_KEY 等占位
├── pyproject.toml                 # uv 管理(workspace root)
│
├── backend/                       # FastAPI 后端(主战场)
│   ├── pyproject.toml
│   ├── alembic.ini
│   ├── alembic/versions/
│   ├── app/
│   │   ├── main.py                # FastAPI 入口
│   │   ├── config.py              # Settings via pydantic-settings
│   │   ├── deps.py                # 依赖注入
│   │   ├── api/                   # 路由层
│   │   │   ├── prd.py
│   │   │   ├── cases.py
│   │   │   ├── runs.py
│   │   │   └── diagnosis.py
│   │   ├── services/              # 业务逻辑
│   │   │   ├── prd_parser.py
│   │   │   ├── case_generator.py  # 调 LLM 生成,沿用 webtest-mcp.generate_cases 思路
│   │   │   ├── run_orchestrator.py# 调 claude CLI + @playwright/mcp
│   │   │   ├── diagnoser.py       # AI 诊断
│   │   │   ├── pattern_store.py   # 沉淀
│   │   │   └── report_html.py     # 复用 webtest-mcp.save_test_results 的 HTML 生成
│   │   ├── agent/                 # 执行编排
│   │   │   ├── claude_runner.py   # subprocess: claude -p --mcp-config
│   │   │   ├── mcp_config.py      # 临时生成 @playwright/mcp 配置 JSON
│   │   │   └── trace_parser.py    # 解析 claude 输出 + MCP 工具调用日志
│   │   ├── llm/                   # LLM Gateway(provider-agnostic)
│   │   │   ├── base.py            # BaseChatClient 抽象基类
│   │   │   ├── claude_cli.py      # 主通道
│   │   │   ├── minimax.py         # 备通道
│   │   │   ├── flywheel.py        # 升级通道(占位,可禁用)
│   │   │   ├── gateway.py         # 路由 + 失败降级
│   │   │   └── prompts/           # 所有 prompt 模板(版本化)
│   │   │       ├── case_gen_v1.txt
│   │   │       ├── execute_v1.txt
│   │   │       └── diagnose_v1.txt
│   │   ├── models/                # SQLModel 定义
│   │   │   ├── case.py
│   │   │   ├── run.py
│   │   │   ├── diagnosis.py
│   │   │   └── pattern.py
│   │   ├── obs/                   # 可观测性
│   │   │   ├── logger.py          # structlog 配置
│   │   │   ├── tracer.py          # OTel
│   │   │   └── events.py          # Event Catalog 定义
│   │   └── storage/
│   │       └── local_fs.py        # MVP 用本地 FS,接口可换 MinIO
│   ├── data/                      # SQLite + uploaded PRDs(gitignore)
│   ├── artifacts/                 # 大对象:截图/HTML 报告/trace JSONL(gitignore)
│   │   └── <project>/<run_id>/
│   │       ├── trace.jsonl
│   │       ├── screenshots/
│   │       └── report.html
│   └── tests/
│       ├── unit/
│       ├── integration/
│       └── golden/                # 黄金回归集
│
├── vendor/
│   └── webtest-mcp/               # ← Fork 进来作为子模块/git subtree
│       └── (从 /Users/yy/code/yal/webtest-mcp-server 同步)
│
├── frontend/                      # Vite + React 前端
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── tailwind.config.ts
│   ├── index.html
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx                # 根组件 + Router Provider
│   │   ├── routes/                # TanStack Router 文件路由
│   │   │   ├── __root.tsx
│   │   │   ├── index.tsx          # 仪表盘
│   │   │   ├── prd.tsx            # PRD 上传
│   │   │   ├── cases.tsx          # 用例列表 + review
│   │   │   ├── runs.$id.tsx       # 运行报告 + 失败回放
│   │   │   └── diagnosis.$id.tsx
│   │   ├── components/
│   │   │   └── ui/                # shadcn 原子组件
│   │   ├── lib/
│   │   │   ├── api.ts             # TanStack Query hooks
│   │   │   └── api-types.ts       # openapi-typescript 自动生成
│   │   └── stores/                # Zustand
│   └── tests/
│
└── docs/
    ├── prd.md                     # 本文档
    ├── adr/                       # 架构决策记录
    │   ├── 0001-vite-over-nextjs.md
    │   ├── 0002-playwright-mcp-over-vision-agent.md
    │   ├── 0003-fork-webtest-mcp.md
    │   └── 0004-claude-cli-subprocess.md
    ├── event-catalog.md
    └── prompts/                   # 各版本 prompt 历史
```

### 16.3 关键决策:执行编排(Claude CLI + `@playwright/mcp`)

**问题**:后端是 Python 单语言栈,如何驱动浏览器执行自然语言形式的用例?

**方案**:**Python 后端 spawn `claude -p` 子进程,该 Claude session 通过 `--mcp-config` 加载 `@playwright/mcp`,Claude 当编排者解释每个用例步骤并调 MCP 工具操作浏览器。**

**为什么这条路**:
- 没有 Python ↔ Node 双向 IPC,只有单向 spawn
- Claude session 自动处理"步骤理解 → tool 选择 → 异常处理"——免写编排逻辑
- `@playwright/mcp` 由 Claude 自动 spawn 为 MCP 子子进程,Python 不需要直接管它
- trace 通过解析 Claude 输出 + 拦截 MCP 工具调用日志获得

**核心代码**:

```python
# backend/app/agent/claude_runner.py
import asyncio, json, tempfile
from pathlib import Path

MCP_CONFIG_TEMPLATE = {
    "mcpServers": {
        "playwright": {
            "command": "npx",
            "args": ["-y", "@playwright/mcp@latest", "--browser", "chromium"]
        }
    }
}

async def execute_case(case: TestCase, run_id: str, trace_id: str) -> RunResult:
    log = logger.bind(case_id=case.case_id, run_id=run_id, trace_id=trace_id)
    run_dir = Path(f"./artifacts/{case.project_id}/{run_id}")
    run_dir.mkdir(parents=True, exist_ok=True)

    # 1. 临时 MCP 配置(每次执行独立)
    mcp_conf = run_dir / "mcp.json"
    mcp_conf.write_text(json.dumps(MCP_CONFIG_TEMPLATE))

    # 2. 拼装 prompt:目标网站 + 用例步骤 + 期望输出格式
    prompt = render_prompt("execute_v1.txt", case=case, base_url=case.project.base_url)

    # 3. spawn claude CLI
    log.info("agent.run.started")
    proc = await asyncio.create_subprocess_exec(
        "claude", "-p", prompt,
        "--mcp-config", str(mcp_conf),
        "--output-format", "json",
        env={**os.environ, "TRACE_ID": trace_id},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # 4. 流式读取并解析(每个 MCP tool call 都打 layer 2 事件)
    raw_lines = []
    async for line in proc.stdout:
        raw_lines.append(line)
        # claude --output-format=stream-json 模式可逐条解析 tool_use / tool_result
        # 这里简化为完整读取后解析

    await proc.wait()
    final = json.loads(b"".join(raw_lines))

    # 5. 解析 tool 调用序列还原成步骤事件
    steps = parse_mcp_tool_calls(final)
    for s in steps:
        log.info("agent.step.executed", **s.dict())

    # 6. 落地 trace + 截图
    (run_dir / "trace.jsonl").write_text(
        "\n".join(json.dumps(s.dict()) for s in steps)
    )

    return RunResult(
        run_id=run_id,
        status=infer_status(steps),
        steps=steps,
        usage=final.get("usage"),
    )
```

**Trace 数据来源**:
- `claude -p --output-format json` 返回的完整 conversation,包含每个 `tool_use`(MCP 工具调用)和 `tool_result`(工具返回)
- `@playwright/mcp` 工具有 `browser_navigate / browser_click / browser_type / browser_snapshot / browser_take_screenshot` 等
- 解析这些 tool_use 还原成业务事件 `agent.step.executed`(intent、target、result)

**收益**:
- 整套执行链路 Python 单语言驱动,不需要维护 Node 代码
- `@playwright/mcp` 升级、bug 修复 = `npx -y @playwright/mcp@latest` 自动跟进
- Claude 处理重试、错误恢复、截图保存 —— 全是它内置的 agent 能力
- Token 消耗可见(`--output-format json` 返回 usage 字段)

### 16.4 LLM Gateway(provider-agnostic)

**接口**:

```python
# backend/app/llm/base.py
from abc import ABC, abstractmethod
from pydantic import BaseModel

class LLMResult(BaseModel):
    text: str
    input_tokens: int
    output_tokens: int
    model: str
    provider: str

class LLMError(Exception): ...
class RateLimitError(LLMError): ...
class QuotaExceededError(LLMError): ...

class BaseChatClient(ABC):
    name: str
    @abstractmethod
    async def chat(self, prompt: str, *, prompt_version: str, image: bytes | None = None) -> LLMResult: ...
```

**Gateway 路由**:

```python
# backend/app/llm/gateway.py
class LLMGateway:
    def __init__(self, primary: BaseChatClient, fallbacks: list[BaseChatClient]):
        self.primary = primary
        self.fallbacks = fallbacks

    async def chat(self, prompt: str, *, prompt_version: str, image: bytes | None = None) -> LLMResult:
        log = logger.bind(prompt_version=prompt_version)
        clients = [self.primary, *self.fallbacks]
        last_err = None
        for client in clients:
            try:
                t0 = time.monotonic()
                result = await client.chat(prompt, prompt_version=prompt_version, image=image)
                log.info("llm.completion", provider=client.name, model=result.model,
                    input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                    latency_ms=int((time.monotonic() - t0) * 1000))
                return result
            except (RateLimitError, QuotaExceededError) as e:
                log.warning("llm.fallback", from_provider=client.name, reason=type(e).__name__)
                last_err = e
                continue
            except LLMError as e:
                log.error("llm.failed", provider=client.name, error=str(e)[:300])
                raise
        raise last_err or LLMError("all providers exhausted")
```

**Claude CLI 主通道**:

```python
# backend/app/llm/claude_cli.py
class ClaudeCLIClient(BaseChatClient):
    name = "claude-cli"

    async def chat(self, prompt, *, prompt_version, image=None) -> LLMResult:
        args = ["claude", "-p", prompt, "--output-format", "json"]
        if image:
            tmp = Path(tempfile.mkstemp(suffix=".png")[1])
            tmp.write_bytes(image)
            args += ["--image", str(tmp)]

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)

        if proc.returncode != 0:
            err = stderr.decode()[:500]
            if "rate" in err.lower() or "limit" in err.lower():
                raise RateLimitError(err)
            raise LLMError(err)

        data = json.loads(stdout)
        return LLMResult(
            text=data["result"],
            input_tokens=data["usage"]["input_tokens"],
            output_tokens=data["usage"]["output_tokens"],
            model=data.get("model", "claude-opus"),
            provider=self.name,
        )
```

**MiniMax 备通道**:

```python
# backend/app/llm/minimax.py
class MiniMaxClient(BaseChatClient):
    name = "minimax"
    BASE_URL = "https://api.minimax.chat/v1/text/chatcompletion_v2"

    def __init__(self, api_key: str, model: str = "MiniMax-Text-01"):
        self.api_key = api_key
        self.model = model

    async def chat(self, prompt, *, prompt_version, image=None) -> LLMResult:
        content = [{"type": "text", "text": prompt}]
        if image:
            b64 = base64.b64encode(image).decode()
            content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
        async with httpx.AsyncClient() as client:
            r = await client.post(
                self.BASE_URL,
                json={"model": self.model, "messages": [{"role": "user", "content": content}], "max_tokens": 2000},
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=60,
            )
        data = r.json()
        if data.get("base_resp", {}).get("status_code", 0) != 0:
            msg = data["base_resp"].get("status_msg", "")
            if "quota" in msg.lower(): raise QuotaExceededError(msg)
            raise LLMError(msg)
        choice = data["choices"][0]["message"]
        return LLMResult(
            text=choice["content"],
            input_tokens=data["usage"]["prompt_tokens"],
            output_tokens=data["usage"]["completion_tokens"],
            model=self.model,
            provider=self.name,
        )
```

**关键约定**:
- 每次调用必须提供 `prompt_version`(沉淀追溯)
- 超时硬上限:Claude 180s,MiniMax 60s
- 主通道触发 `RateLimitError` / `QuotaExceededError` 时自动 fallback,不打断业务
- 所有调用都打 `llm.completion` 事件,token 用量可见

### 16.5 数据库 Schema(SQLModel)

```python
# backend/app/models/case.py
class TestCase(SQLModel, table=True):
    case_id: str = Field(primary_key=True)
    project_id: str = Field(index=True)
    name: str
    intent: str
    module: str
    tags: list[str] = Field(sa_column=Column(JSON))
    priority: str  # P0/P1/P2
    
    preconditions: list[str] = Field(sa_column=Column(JSON))
    steps: list[dict] = Field(sa_column=Column(JSON))
    assertions: list[dict] = Field(sa_column=Column(JSON))
    
    source: str  # ai-generated / manual / imported
    prompt_version: str | None
    model_version: str | None
    generated_at: datetime
    generated_from: str | None  # prd_chapter_id
    
    review_status: str  # pending / approved / rejected
    manual_edited_fields: list[str] = Field(sa_column=Column(JSON), default=[])
    version: int = 1
    prev_version_id: str | None = None
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
```

类似定义 `Run`、`Diagnosis`、`Pattern`(沉淀模式)。**所有表全程不删行,只改 status / version**——回滚靠版本号。

### 16.6 REST API 表(MVP)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/prd/upload` | 上传 PRD,返回 prd_id |
| POST | `/api/prd/{prd_id}/generate` | 触发用例生成(异步) |
| GET | `/api/cases?status=pending` | 用例列表 |
| GET | `/api/cases/{case_id}` | 用例详情(含历史版本) |
| PATCH | `/api/cases/{case_id}` | 编辑用例(进 manual_edited_fields) |
| POST | `/api/cases/{case_id}/approve` | review 通过 |
| POST | `/api/cases/{case_id}/reject` | review 拒绝 |
| POST | `/api/runs` | 创建运行(body 含 case_ids[]) |
| GET | `/api/runs/{run_id}` | 运行详情 + 步骤事件 |
| GET | `/api/runs/{run_id}/stream` | SSE 实时推送步骤事件 |
| POST | `/api/diagnosis/{run_id}/generate` | 触发 AI 诊断 |
| POST | `/api/diagnosis/{diag_id}/feedback` | 人工反馈 |

**通用约定**:
- 所有响应包 `{data, trace_id}`
- 错误统一 `{error: {code, message, trace_id}}`
- OpenAPI schema 自动从 FastAPI 生成,前端 `lib/types.ts` 用 `openapi-typescript` 自动同步

### 16.7 PRD 章节 diff 算法

```python
# backend/app/services/prd_parser.py
def split_chapters(markdown: str) -> list[Chapter]:
    """按 ## / ### 标题切,每章保留 标题/正文/原始位置/SHA-256 哈希"""

def diff_prds(old: list[Chapter], new: list[Chapter]) -> ChapterDiff:
    """
    用 (heading_level, normalized_title) 作为对齐 key:
    - 双方都有 + hash 相同 → unchanged
    - 双方都有 + hash 不同 → modified
    - 仅老有 → removed
    - 仅新有 → added
    第二版优化用 difflib.SequenceMatcher 检测 moved
    """
```

**MVP 不做 moved 检测**(实现复杂、edge case 多),demo 不影响。

### 16.8 错误处理与重试

| 场景 | 策略 |
|------|------|
| LLM 调用失败 | 指数退避重试 3 次(2s/8s/32s),仍失败标 `llm.case.failed` |
| Claude CLI 子进程崩 | 任务标 `aborted`,artifacts 保留供调试,可手动重跑 |
| Vision LLM confidence < 0.5 | 单步内自动重试 1 次,仍低标 `low_confidence` 提交诊断 |
| Staging 网络不通 | 标 `env_issue`,本批次后续 case 暂停 |
| Trace 写失败 | log warning,业务不阻塞(可观测性是辅助,不是关键路径) |

### 16.9 测试组织(配合 §7.5)

```
backend/tests/
├── unit/                   # 单测,Mock LLM
│   ├── test_prd_parser.py
│   ├── test_case_generator.py  # mock claude_cli
│   ├── test_diagnoser.py
│   └── test_pattern_store.py
├── integration/
│   ├── test_api_flow.py    # FastAPI TestClient,mock worker subprocess
│   └── test_worker_bridge.py # 真实 spawn worker(无浏览器,smoke)
└── golden/
    ├── prds/               # 固定 PRD 样本
    ├── expected_cases/     # 期望生成的 case shape(不比对文本,比对 schema)
    └── test_golden_set.py  # 周级 CI 跑(用真实 LLM)
```

**单测目标**:每次 commit 跑,< 30s 全过。
**集成测试**:每 PR 跑,~ 2min。
**黄金回归**:每周一次 + prompt 变更触发,~ 15min。

### 16.10 部署与本地运行

**MVP 阶段不依赖 Docker。** 所有大对象(截图 / 报告 / trace)落本地文件系统。

```Makefile
.PHONY: dev test lint setup

setup:
	cd backend && uv sync
	cd frontend && pnpm install
	# Playwright 浏览器(已缓存,这一步通常 noop)
	cd backend && uv run playwright install chromium
	# 验证 claude CLI 可用
	claude --version
	# 验证 @playwright/mcp 可拉取
	npx -y @playwright/mcp@latest --version

dev:
	cd backend && uv run uvicorn app.main:app --reload &
	cd frontend && pnpm dev      # Vite dev server, 默认 :5173

test:
	cd backend && uv run pytest -x
	cd frontend && pnpm test

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .
	cd frontend && pnpm lint
```

`make dev` 一键起:FastAPI(8000)+ Vite(5173)。**全部本地进程,零 Docker。**

Phase 2 当需要 MinIO / Postgres / Redis Queue 时再加 `docker-compose.yml`。

### 16.11 实施顺序与里程碑(对齐 §13 时间表)

| Day | 交付物 | 验收 |
|-----|--------|------|
| 1 | 项目骨架(`michelle/`) + uv/pnpm 依赖装好 + `make dev` 起前后端 + git init + ADR 0001-0004 落地 | 访问 :5173(Vite) + :8000(FastAPI) 都正常,`/healthz` 返回 200 |
| 2 | **核心可行性验证**:claude CLI + `@playwright/mcp` 跑通 <demo creds> 登录 + 截图 + 输出步骤 trace | 手工跑一条 prompt,Claude 调 `@playwright/mcp` 工具登录成功,得到一份步骤 JSON |
| 3 | LLM Gateway 三件套(claude_cli + minimax + gateway 路由 + 单测覆盖 fallback) + Event Catalog 定义 + structlog/Logfire 接通 | gateway 单测全过(包括 mock rate-limit 自动降级);Logfire 看到事件流 |
| 4 | PRD 上传 + 章节切分 + 二次 diff + 用例生成 prompt v1 | 喂 Michelle 自己的 PRD,产出 ≥ 8 条 schema 合法 case(dogfooding 强故事) |
| 5 | Vendor webtest-mcp 集成:loader / report HTML 复用,SQLite 模型 + Alembic 初始迁移 | 后端能从 SQLite 读用例,能生成累计 HTML 报告 |
| 6 | Run Orchestrator:`claude_runner.py` 把 case 转 prompt → spawn claude → 解析 tool_use → 落 trace.jsonl | 一条 case 能从后端 API 触发,跑完得到 pass/fail 结果 |
| 7 | 前端 5 页面骨架 + TanStack Query hooks + 与后端联调 | 端到端点点点能走完 P0 主链路 |
| 8 | Review 工作流(pending/approved/rejected 状态机 + 用例版本 + 人工编辑保护) | 草稿用例可批量审核,改过的字段下次重生成不被覆盖 |
| 9 | 真实跑 目标场景 + 失败重试 + 错误分类(env/flaky/real_bug) | 跑 10+ 条 case,生成报告 |
| 10 | Trace Viewer 页(失败回放,时间线 + 截图缩略图) | 任一失败 case 能看到每步截图 + Claude 的 tool_use 决策 |
| 11 | **AI 诊断**(diagnoser.py + 反馈入口) + 沉淀模式库 schema + 黄金回归集 | 5 条历史失败诊断准确 ≥ 3 条;诊断 prompt 改版前后能在黄金集对比 |
| 12 | Demo 视频(≤ 90s) + README(架构图 + 决策摘要) + ADR 完善 | 视频展示完整闭环;README 5 分钟内让面试官看懂 |
| 13 | 面试话术 + Q&A 演练 + cleanup(dead code / 临时文件 / TODO) | 5 个核心问题各有 30s pitch |
| 14 | Buffer | 修 demo 阶段发现的问题 |

**关键变化**:Day 2 不再是"写自己的 vision agent",而是"验证 Claude + `@playwright/mcp` 能驱动 the demo target"——**风险更低、价值更高**(直接证明可行性)。

---

**End of PRD v0.5**
