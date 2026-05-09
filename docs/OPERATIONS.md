# Michelle 操作手册

> 作者：**smarty**
>
> 面向「拿到代码就要把平台跑起来」的同学。架构和理念见 [`README.md`](../README.md) 与 [`docs/prd.md`](prd.md)，本文只讲怎么用。

---

## 1. 环境准备

| 依赖 | 版本 | 安装 |
|---|---|---|
| Python | 3.12+ | `brew install python@3.12` 或 [uv](https://github.com/astral-sh/uv) 自带 toolchain |
| uv | 最新 | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Node | 22+ | `brew install node@22` 或 nvm |
| pnpm | 9+ | `npm i -g pnpm` |
| claude CLI | 已登录订阅 | [Claude Code 安装](https://docs.claude.com/en/docs/claude-code) + `claude login` |
| Chromium | 系统装一份即可 | `@playwright/mcp` 通过 `npx` 自动拉取 |

> Claude Max 订阅是必备前提。其他 LLM 都是可选 fallback；只有 claude 可用平台也能正常跑（PRD → 用例 → 执行 → 诊断 全链路都不缺）。

### 拉代码 + 装依赖

```bash
git clone https://github.com/yy757303533/michelle.git
cd michelle
make setup        # backend uv sync + frontend pnpm install + 校验 CLI
```

### 配置 .env

```bash
cp .env.example .env
```

`.env` 默认空也能跑（claude CLI 是兜底）。当前产品只支持
`claude-cli` 和 `codex-cli`，要启用 Codex 按下面表格填：

| 变量 | 作用 | 拿到方式 |
|---|---|---|
| `CLAUDE_CLI_PATH` | Claude CLI 路径 | 默认 `claude` |
| `CODEX_ENABLED` / `CODEX_CLI_PATH` | 启用 Codex CLI provider | `codex` 已安装且登录 |
| `LOGFIRE_TOKEN` | 把日志送到 Logfire 看 trace 视图 | logfire.pydantic.dev |
| `DEFAULT_TARGET_URL` / `_USERNAME` / `_PASSWORD` | Day 2 smoke 默认目标，也是 demo 用的 the demo target | 自填 |

**未启用 Codex 时，平台只会使用 Claude CLI。**

### 启动

```bash
make dev           # backend :8000 + frontend :5173 一起起，Ctrl+C 同时停
```

也可以分开起：

```bash
make backend       # 只起 FastAPI
make frontend      # 只起 Vite
```

打开 http://localhost:5173 就是 Web UI。

---

## 2. 五分钟完整流程（happy path）

平台的设计动线：**PRD → AI 生成用例 → 人工 review → 执行 → AI 诊断 → 沉淀**。

### Step 1 创建项目

不需要单独建。第一次上传 PRD 时若 `project_id` 不存在，平台会自动建一个同名项目。  
要预先建项目（带 base_url、默认账号等），用 REST：

```bash
curl -X POST http://localhost:8000/api/projects/ \
  -H 'Content-Type: application/json' \
  -d '{"project_id":"demo","name":"Demo App","base_url":"http://example.com"}'
```

### Step 2 上传 PRD

Web UI：进入 **PRD** 页 → 粘贴 markdown → Upload。  
平台会按 H2/H3 切章节，给每章算 hash，与上一版做 diff，UI 上能看到 `added / removed / modified / moved / unchanged` 摘要。

REST：

```bash
curl -X POST http://localhost:8000/api/prd/upload \
  -H 'Content-Type: application/json' \
  -d @- <<EOF
{
  "project_id": "demo",
  "name": "Demo PRD",
  "markdown": "# Demo\n\n## 登录\n用户使用账号密码登录。\n"
}
EOF
```

### Step 3 生成用例

PRD 上传成功后，UI 自动列出章节并默认全选。点 **Generate cases for selected**。  
后端会：跳过 prev 已有 approved 的章节、跳过未变更章节、调 LLM 给 added/modified 章节生成 5–8 条用例。

REST：

```bash
curl -X POST http://localhost:8000/api/prd/<prd_id>/generate \
  -H 'Content-Type: application/json' \
  -d '{"chapter_indices":[0,1,2],"max_cases_per_chapter":8}'
```

### Step 4 Review 用例

进入 **Cases** 页：

- 顶部 filter pills（all / pending / approved / rejected / stale）切换列表。**切换会清空 selected**，避免误操作隐藏行。
- 单条 → `approve` / `reject` / `edit`（行内展开编辑表单）。
- 多条 → 勾选 checkbox → 顶部黑条 bulk approve / reject。
- 编辑过的字段会进 `manual_edited_fields`，下次 LLM regen 永远不会覆盖这些字段。
- approved 行上有 `▶ Run` 按钮可以直接执行；旁边的 `edit` 按钮会把它改回 pending（这是平台的不变量：approved 改了必须人工重新 confirm）。

### Step 5 执行用例

approved case → 点 `▶ Run`。后台启 `claude -p --mcp-config <ours>` 子进程加载 `@playwright/mcp`，全程 ARIA 树驱动浏览器，**不是视觉模型逐步看截图**（确定性 + 速度）。  
跳到 **Run detail** 页：

- 步骤实时打回（每 1.5s 拉一次），缩略图随每步出来。
- 终态后自动停止轮询，一次补刷确保最后一张截图不漏。
- 失败/aborted/flaky 的 run 上方会出现 `AI diagnose →` 按钮。

REST 起 run：

```bash
curl -X POST http://localhost:8000/api/runs/ \
  -H 'Content-Type: application/json' \
  -d '{"case_ids":["TC-20260427-0001"],"env":"default"}'
```

### Step 6 失败诊断 + 沉淀（killer feature）

失败 run → 点 `AI diagnose →`。后台：渲染 `diagnose_v1` prompt（trace 尾段 + 失败步 + 截图）→ 送视觉模型 → 返回 `category / confidence / reasoning / fix_suggestion / evidence`。

诊断页有三个反馈按钮：

| 按钮 | 行为 |
|---|---|
| **confirmed** | 调 `pattern_store.absorb`，把这次诊断的特征签名（intent / error / tool keywords）折进 Pattern 库，`hit_count++`。下次类似失败先跑关键词匹配，命中就直接展示 "we've seen this before"，**不再调 LLM**。 |
| **wrong** | 标记诊断不对，不污染 Pattern 库。 |
| **partially_correct** | 接受但不沉淀。 |

每次 `confirmed` 都让下次诊断更便宜——这是 compound engineering 闭环的实操点。

---

## 3. 三种入口（agent-native）

任何一个 Web UI 操作都有等价的命令行/API/MCP 路径。

### 3.1 REST（最通用）

文档：启动后访问 http://localhost:8000/docs（FastAPI 自动 OpenAPI）。  
常用：

```bash
# 列项目
curl http://localhost:8000/api/projects/

# 列 cases（按状态过滤）
curl 'http://localhost:8000/api/cases/?status=approved&limit=50'

# 编辑 case（manual_edited_fields 会自动 merge）
curl -X PATCH http://localhost:8000/api/cases/TC-20260427-0001 \
  -H 'Content-Type: application/json' \
  -d '{"priority":"P0","assertions":[{"description":"URL 跳到 /home"}]}'

# 看 run 详情（含 step events）
curl http://localhost:8000/api/runs/<run_id>

# 跑诊断
curl -X POST http://localhost:8000/api/diagnosis/by-run/<run_id>/generate \
  -H 'Content-Type: application/json' -d '{}'

# LLM 通道健康
curl http://localhost:8000/api/llm/health

# 1-token "ok" 探活某通道
curl -X POST http://localhost:8000/api/llm/probe \
  -H 'Content-Type: application/json' -d '{"prefer":"codex-cli"}'
```

### 3.2 Claude Code Skills（终端最快）

`.claude/skills/` 下三个 skill：

| 命令 | 说明 |
|---|---|
| `/michelle-run TC-XXX` | 跑指定 case，等结果 |
| `/michelle-diagnose <run_id>` | 触发诊断，打印结果 |
| `/michelle-suggest "登录模块"` | 让 AI 给某个领域建议测试点（不落库） |

Skill 内部走 REST，不直接 import 后端，所以平台不在跑也不会污染数据。

### 3.3 MCP server（给其他 AI 客户端）

把 `backend/app/mcp/server.py` 注册成你 Cursor / Windsurf / 自定义 agent 的 MCP server，6 个 tool 直接可用：`list_cases / execute_case / diagnose_run / list_runs / list_patterns / approve_case`。

---

## 4. 数据和文件在哪

```
backend/
  data/michelle.db                 SQLite（全部业务表 + 7 张表 in 1 alembic revision）
  artifacts/<project>/<run_id>/    每次 run 的现场
    prompt.txt                     送给 claude 的完整 prompt（密码已脱敏）
    claude.stream.jsonl            stream-json 原文（含每步工具调用、tool_result）
    claude.err.log                 stderr 尾部
    trace.jsonl                    解析后的 step 摘要（写库前的 sanity dump）
    report.html                    可单独打开的自包含报告（截图内嵌为 base64）
    result.json                    报告的 JSON 边车
    step-N.png                     每步截图
    final.png / after-login.png    业务截图（playwright_take_screenshot 生成）
```

> **artifacts 只读取 artifacts root 下的截图**——前端 `report.html` 渲染时做了沙箱（防路径穿越）+ 大小上限（5MB）+ 扩展名白名单。

清空：

```bash
rm -rf backend/artifacts/<project_id>/<run_id>   # 删单次 run
rm -rf backend/artifacts/*                       # 清全部 artifacts
rm backend/data/michelle.db                      # 清库（彻底重置）
cd backend && uv run alembic upgrade head        # 重建表
```

---

## 5. 常见任务 cheatsheet

### 不想手敲 `/api/...`，直接用 dashboard

打开 http://localhost:5173/，最上面是 LLM provider 状态 + Probe 面板，点 `probe` 就能看到任意通道的活性、延迟、token 数。

### 看实时日志

启动后 backend 终端持续打 JSON 行，每条是一条业务事件：

```json
{"event":"agent.step.executed","case_id":"TC-...","step_index":3,"tool_name":"browser_click","trace_id":"a1b2c3..."}
```

事件名都在 `backend/app/obs/events.py` 的 `EVENTS` 常量里，按 `<domain>.<entity>.<action>` 命名。`trace_id` 串起一次请求里所有日志。

如果配了 `LOGFIRE_TOKEN`，同一份日志会自动到 Logfire 上有 trace 视图。

### 失败 run 自动诊断

`run.failed` hook 已经默认装好——只要 run 状态变 `failed/aborted/flaky`，会自动调 `diagnose_run`，不用手点。`AI diagnose →` 按钮在已经诊断过的情况下打开的就是结果页。

要关掉自动诊断，改 `backend/app/agent/hooks.py` 的 `install_default_hooks`，注释掉 `register("run.failed", _on_run_failed_auto_diagnose)` 即可。

### 切换 LLM 路由

Dashboard → Platform settings → model_routing 里配置三件事：

- Generate cases
- Execute cases
- Diagnose failures

可选 provider 只有 `auto` / `claude-cli` / `codex-cli`。其中
Execute cases 选 `claude-cli` 会走 Claude CLI Loop；选 `codex-cli`
会走 Michelle Loop，由 Codex 输出 JSON action，Michelle 调 Playwright MCP。

### 重新生成已 approved 章节的用例

平台**不会自动覆盖** approved——这是硬不变量。  
两种办法：

1. 把那些 approved case 改回 pending（Cases 页 → 编辑 → 任意改一下 → 保存），再触发生成；
2. 直接 reject + 删除（暂未提供 UI 删除，可以在 SQLite 里 `DELETE FROM testcase WHERE case_id IN (...)`）。

### 把 PRD 的某些章节临时移出生成

Generate 接口 `chapter_indices` 是显式数组——只传你想生成的章节索引就行，不传就是全部。

### 重跑某个 case 的 retry

直接再点一次 `▶ Run`。每次 run 是独立的 Run 行，不共享 step_events。如果你想看 attempt 1 + attempt 2 在同一 run 里（比如 transient 重试），run_orchestrator 自带的 retry-on-transient 已经做了 step_index offset，不会冲突。

### 看历史 run 的 HTML 报告

```
http://localhost:8000/api/runs/<run_id>/report.html
```

或者整个项目的最新 latest-per-case 聚合报告：

```
http://localhost:8000/api/projects/<project_id>/report.html
```

---

## 6. 故障排查（常见问题）

| 现象 | 原因 / 对策 |
|---|---|
| `make dev` 起不来，端口冲突 | 8000 / 5173 被占。`lsof -i:8000` 查进程或改 `.env` 里 `BACKEND_PORT` |
| 启动报 `claude CLI not found` | `which claude` 看路径，`.env` 里改 `CLAUDE_CLI_PATH=/绝对路径/claude` |
| Run 长时间 pending 不动 | 多半是 `claude -p` 鉴权挂了。开终端跑 `claude -p "ok" --output-format json` 单独验证 |
| Run 跑完但 status=`aborted` `no playwright tool calls observed` | claude 没成功调用 MCP 工具——通常是 prompt 问题或 MCP 没起来。看 `artifacts/.../claude.stream.jsonl` 第一行能不能看到 `mcpServers ready` |
| Run 跑完但截图全是空白 | Chromium headless 模式 + 网站异常。把 `RunRequest.headless` 改成 `False` 看真实窗口 |
| 诊断按钮点了没反应 / 一直 loading | 看后端日志 `diagnoser.llm_failed*`。先在 Dashboard Probe 里测 `claude-cli` / `codex-cli` |
| 上传同一份 PRD 不停涨 version | 这是当前默认行为（每次都新版本）；要去重需要在 `api/prd.py:upload_prd` 加 content_hash 比对，自己改一下 |
| `data/michelle.db is locked` | SQLite 同时被多个进程写。退出所有 `uvicorn` / `pytest`，再起 |

---

## 7. 测试和质量

```bash
cd backend
uv run pytest tests/unit -q              # 152 测试，~2 秒
uv run pytest tests/unit -k diagnoser    # 跑诊断相关
uv run ruff check app tests              # lint
uv run ruff format app tests             # 自动格式化
```

```bash
cd frontend
pnpm tsc -b                              # 类型检查
pnpm build                               # 生产构建（顺带类型检查）
pnpm dev                                 # 开发服务器
```

CI 在 `.github/workflows/ci.yml`，每次 push 都跑 backend ruff + pytest + frontend tsc + build。Branch protection 要求 PR 通过 CI 才能 merge。

### End-to-end smoke（真 LLM + 真浏览器）

```bash
cd backend
uv run python ../scripts/day2_smoke.py            # claude + playwright 跑 demo 登录
uv run python ../scripts/day4_dogfood.py          # PRD → AI 用例（用 Michelle 自己的 PRD）
uv run python ../scripts/day7_visual_smoke.py     # 给前端每页拍照
uv run python ../scripts/day12_demo_capture.py    # 走完整流程并录屏
```

这些脚本会真调订阅、起真 Chromium，跑一次大约 30s–2 分钟。

---

## 8. 升级 / 维护

### 升级依赖

```bash
cd backend && uv sync --upgrade          # Python deps
cd frontend && pnpm update               # JS deps
```

### 新增 LLM 通道

当前内部试点只保留 `claude-cli` 和 `codex-cli`。后续确实需要第三方
provider 时，再实现 `app/llm/base.py:BaseChatClient`，注册到
`app/llm/gateway.py`，补配置项和单测。

### 新增业务事件名

1. 在 `app/obs/events.py:EVENTS` 加常量（`<domain>.<entity>.<action>`）
2. 调用处 `log.info(EVENTS.X, **fields)`，不要硬编码字符串
3. structlog 的 `_normalize_event_catalog` processor 会自动把 `Event` dataclass 转成字符串，并把缺失的 `key_fields` 标到 `event_missing_fields`

### Prompt 版本

每个 prompt（execute / case_gen / diagnose）在 `app/llm/prompts/` 里都是 `<name>_v<N>.txt`。改语义就 `_v(N+1).txt` 新建一份，旧的保留——这样 sediment 复现历史诊断时不会因为 prompt 漂移被坑。

### 数据库迁移

```bash
cd backend
uv run alembic revision --autogenerate -m "add column foo"
uv run alembic upgrade head
```

> 注意：SQLModel 模型变更后必须生成 migration；CI 里没有 migrations check，但 schema drift 会让 model 和库对不上。

---

## 9. 安全注意

- **凭证不进 logs / artifacts / DB**。`RunRequest.secrets` + `trace_parser.redact_*` 已经把 password 从 stream-json、stdout、stderr、`StepEvent.tool_args` 里都替换成 `***`。新加 hook 或 service 写到 artifacts 时记得继承这条链路。
- **artifact 文件服务有沙箱**。`/api/runs/{id}/artifacts/{path}` 通过 `target.relative_to(base)` 阻止路径穿越；`report_html._safe_screenshot_path` 把内嵌截图限制在 artifacts root。
- **`/api/llm/probe` 没鉴权**。它会真打 LLM，烧 token。生产环境务必加 auth 或限频。
- **`bypassPermissions` 会让 claude 执行所有 MCP 工具调用而不询问**。这是为了 demo 顺畅。生产改成 `acceptEdits` 或加 allowlist。
- **MCP server 默认监听 stdio，谁连都能调**。要暴露成网络服务前先加 token / IP allowlist。

---

## 10. 引用 / 链接

- README：[`../README.md`](../README.md)
- PRD：[`prd.md`](prd.md)
- ADR：[`adr/`](adr/)
- 5-min 走查：[`STORY.md`](STORY.md)
- 经验复盘：[`lessons-learned.md`](lessons-learned.md)
- 面试讲稿：[`INTERVIEW.md`](INTERVIEW.md)
- 事件目录：[`../backend/app/obs/events.py`](../backend/app/obs/events.py)
- CI：[`.github/workflows/ci.yml`](../.github/workflows/ci.yml)

有问题先看 `backend/app/obs/events.py` 里相关事件的注释，再看对应 service 的 docstring，都没说清楚再翻 daily findings（`docs/day*-findings.md`）。
