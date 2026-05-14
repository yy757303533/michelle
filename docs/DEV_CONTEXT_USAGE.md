# Michelle DevContext 定位与使用手册

本文说明 Michelle 如何联动 `zstack-workspace`、`zstack-dev-mcp`、GitLab、Jira、Confluence、CI 和服务器日志，用于 PRD 导入和失败诊断。

## 1. 项目定位

Michelle 的定位不是“上传 PRD 后生成测试点”的工具，而是一个面向 QA 和研发协作的智能测试工作台：

```text
需求来源 -> 覆盖建模 -> 用例设计 -> 自动执行 -> 失败诊断 -> 代码/日志定位 -> 回归资产沉淀
```

它的核心价值是把测试活动从一次性的用例生成，升级成一条可追溯、可诊断、可沉淀的工程闭环。

新的 DevContext 能力把 Michelle 和公司已有开发资产打通：

- `zstack-workspace` 提供本地业务代码、PRD 文件、repo 结构和代码搜索上下文；
- `zstack-dev-mcp` 作为企业工具网关，连接 GitLab、Jira、Confluence、CI 等系统；
- SSH 服务器日志提供真实运行环境的失败证据；
- Michelle 负责把这些证据组织成测试设计、失败诊断和回归资产。

最终目标是让 QA 在一个入口里完成这些动作：

- 从 GitLab、workspace 或 Markdown 导入 PRD；
- 基于 PRD 生成并评审 coverage；
- 从 accepted coverage 起草测试用例；
- 执行测试并沉淀回归资产；
- 用例失败后自动结合代码、日志、Jira、Confluence、CI 做 root cause 分析；
- 把诊断结果反向沉淀到 coverage、case、regression asset 或后续报告。

## 2. 解决的问题

DevContext 主要解决四类上下文断裂问题。

### 2.1 PRD 来源断裂

以前 PRD 只能上传 Markdown，容易出现这些问题：

- PRD 文件已经在 Git 上，但还要手动下载或复制；
- 不知道当前测试点对应哪个 repo、哪个文件、哪个 ref；
- PRD 更新后，难判断哪些 coverage 需要重新检查；
- Confluence、GitLab、workspace 文件来源不统一。

现在 PRD 可以从 Markdown、workspace 文件、GitLab URL 导入，并保存 `source_ref`。这让后续 coverage、case、run、diagnosis 都能追溯到需求来源。

### 2.2 测试失败和研发证据断裂

以前用例失败后，系统主要看到的是浏览器侧证据，例如截图、trace、console、network。QA 仍然需要自己去查：

- 失败页面对应哪个前端组件；
- API 报错对应哪个后端服务；
- GitLab CI 有没有相关失败，后续也可以扩展 Jenkins 构建证据；
- Jira 或 Confluence 里有没有需求背景；
- 服务器日志里有没有真实异常。

现在 `Analyze with workspace context` 会把这些证据一起收集，形成一次失败诊断的 evidence pack。

### 2.3 代码定位断裂

以前诊断结果容易停留在“可能是什么问题”的描述层面。接入 workspace 后，Michelle 可以基于失败信息搜索业务代码，例如：

- URL；
- API path；
- 错误栈；
- 异常类名；
- 页面文案；
- i18n key；
- case/module/step intent。

诊断结果会给出 candidate files 和命中片段，让 QA 和研发能更快定位到可能的代码位置。

### 2.4 结果沉淀断裂

测试失败不是终点。Michelle 的产品主线是把诊断结果继续沉淀：

- 如果是测试脚本问题，反馈到 TestCase；
- 如果是需求覆盖不足，反馈到 CoverageItem；
- 如果是稳定通过的路径，沉淀为 RegressionAsset；
- 如果是常见失败模式，沉淀为 Pattern；
- 后续可以回写 Jira、MR 或 Confluence 报告。

这让 Michelle 从“生成测试点”升级为“理解需求、执行测试、定位问题、沉淀回归”的平台。

## 3. 当前功能总览

当前已接入三类能力：

1. PRD 来源导入
   - 粘贴 Markdown 或选择本地 `.md` 文件；
   - 从 `zstack-workspace` 读取 PRD 文件；
   - 通过 `zstack-dev-mcp` 从 GitLab URL 拉取 PRD 文件。

2. 失败诊断增强
   - 失败 run 一键结合 workspace 代码分析；
   - 根据失败信息搜索业务代码候选文件；
   - 识别 Jira key 并通过 `zstack-dev-mcp` 拉 Jira 详情；
   - 识别 Confluence pageId 或页面链接并拉页面内容；
   - 识别 GitLab CI job URL 并拉 job log；
   - 按白名单通过 SSH 读取服务器日志片段。

3. 产品化运维能力
   - Dashboard 展示 DevContext status；
   - Settings 区域展示 workspace、MCP、代码搜索、服务器日志状态；
   - 安全边界和配置问题会显示为 security findings；
   - PRD 导入、诊断生成、DevContext 诊断生成、人工反馈会进入 audit log；
   - 服务器日志进入 evidence 前会做敏感信息脱敏。

Michelle 仍然是独立项目，不需要放进 `zstack-workspace` 目录。推荐保持：

```text
/Users/yy/code/yal/michelle
/Users/yy/code/zstack-workspace
```

这种方式是方案 B：Michelle 和 workspace 分目录部署，通过配置无缝联动。这样不会污染 `zstack-workspace`，也不会把 Michelle 的运行、数据库、前端后端启动方式绑定到某个 super repo。

## 4. 推荐工作流

### 4.1 PRD 到 coverage

```text
GitLab / Workspace / Markdown
  -> Import PRD
  -> 保存 source_ref
  -> 解析章节
  -> 生成 requirements / risks / coverage
  -> 人工评审 coverage
```

这个阶段的重点不是马上生成可执行用例，而是先把需求、风险和覆盖点建模清楚。

### 4.2 Coverage 到执行

```text
Accepted Coverage
  -> Draft Test Cases
  -> Case Review
  -> Agentic Execution
  -> Pass 后进入 Regression Asset 候选
```

Michelle 仍然坚持 coverage-first。新的 DevContext 只是增强需求来源和诊断能力，不改变“先 coverage review，再 case drafting”的主线。

### 4.3 失败到 root cause

```text
Failed Run
  -> Analyze with workspace context
  -> 收集 run / case / coverage / PRD
  -> 收集 Jira / Confluence / CI
  -> 搜索 workspace 代码
  -> 拉取服务器日志
  -> LLM 输出结构化诊断
```

诊断结果应该回答：

- 这更像产品 bug、环境问题、测试数据问题，还是测试脚本问题；
- 哪些证据支持这个判断；
- 可能相关的代码文件有哪些；
- 建议如何修复；
- 建议补充哪些回归覆盖。

## 5. 环境配置

在 Michelle 的 `.env` 中配置：

```bash
MICHELLE_WORKSPACE_ROOT=/Users/yy/code/zstack-workspace
MICHELLE_ZDEV_MCP_COMMAND=node
MICHELLE_ZDEV_MCP_ARGS=/Users/yy/code/zstack-workspace/zstack-dev-mcp/dist/index.js
MICHELLE_ZDEV_MCP_CWD=/Users/yy/code/zstack-workspace/zstack-dev-mcp
MICHELLE_ZDEV_MCP_TIMEOUT_SECONDS=60

MICHELLE_DEV_CONTEXT_REPOS=zstack,zstack-ui-next,premium
MICHELLE_DEV_CONTEXT_MAX_FILES=8
MICHELLE_DEV_CONTEXT_MAX_MATCHES_PER_FILE=3
```

这些变量的含义：

| 配置 | 作用 |
|---|---|
| `MICHELLE_WORKSPACE_ROOT` | 外部 `zstack-workspace` 根目录 |
| `MICHELLE_ZDEV_MCP_COMMAND` | 启动 MCP 的命令，通常是 `node` |
| `MICHELLE_ZDEV_MCP_ARGS` | `zstack-dev-mcp` 编译后的入口文件 |
| `MICHELLE_ZDEV_MCP_CWD` | `zstack-dev-mcp` 工作目录 |
| `MICHELLE_ZDEV_MCP_TIMEOUT_SECONDS` | MCP 调用超时时间，建议 60 秒 |
| `MICHELLE_DEV_CONTEXT_REPOS` | 失败诊断时要搜索的业务 repo |
| `MICHELLE_DEV_CONTEXT_MAX_FILES` | 最多返回多少个候选文件 |
| `MICHELLE_DEV_CONTEXT_MAX_MATCHES_PER_FILE` | 每个文件最多返回多少条命中 |

这些配置也可以由 admin 在 Dashboard 的 `Platform settings / dev_context` 区域修改。运行时配置会写入 `runtime_settings` 表，优先级高于 `.env`，保存后不需要重启后端。

## 6. zstack-dev-mcp 准备

在 workspace 中确认 MCP 已安装并编译：

```bash
cd /Users/yy/code/zstack-workspace/zstack-dev-mcp
npm install
npm run build
```

GitLab、Jira、Confluence、CI 的凭据由 `zstack-dev-mcp` 自己读取。Michelle 不在浏览器 UI 中输入这些 token。

如果 GitLab/Jira/Confluence/CI 工具不可用，先检查 `zstack-dev-mcp` 的环境变量和 `.mcp.json` 配置。

## 7. 检查 DevContext 状态

启动 Michelle：

```bash
make dev
```

登录后访问：

```text
GET /api/dev-context/status
```

这个接口会返回：

- workspace 是否存在；
- `.gitmodules` 中的 repo 是否存在；
- `zstack-dev-mcp` 是否配置；
- 代码搜索 repo 列表；
- 服务器日志配置是否存在。

未登录访问会返回 `401`，非 admin 用户访问会返回 `403`。DevContext 状态会暴露本地 workspace、MCP 和服务器日志路径，所以只给管理员看。

也可以直接在 Dashboard 查看 `DevContext status` 面板。面板会展示：

- workspace 是否存在；
- `.gitmodules` 中 repo 是否 checkout；
- `zstack-dev-mcp` 命令、cwd、entrypoint 是否存在；
- 代码搜索 repo 和返回上限；
- SSH server log 是否配置；
- security boundary 是否健康。

如果 security 显示 `needs attention`，优先修复 findings 中列出的配置项。

## 8. 导入 PRD

进入前端：

```text
http://localhost:5173/prd
```

PRD 页面现在有三个来源。

### 8.1 Markdown

适用于临时导入或本地文件：

1. 选择 `Markdown`。
2. 粘贴 PRD 内容，或选择 `.md` 文件。
3. 点击 `Import + parse`。
4. Michelle 会解析章节，后续照常生成 coverage。

对应 API：

```bash
curl -X POST http://localhost:8000/api/prd/import \
  -H 'Content-Type: application/json' \
  -d '{"project_id":"demo","source":{"type":"markdown","markdown":"# Demo\n\n## Login\nUsers can log in."}}'
```

### 8.2 Workspace File

适用于 PRD 已经在 `/Users/yy/code/zstack-workspace` 里的情况：

1. 选择 `Workspace file`。
2. 填 repo，例如 `zstack`。
3. 填文件路径，例如 `docs/prd/demo.md`。
4. 可选填 ref，例如 `master`、分支名或 commit。
5. 点击 `Import + parse`。

对应 API：

```bash
curl -X POST http://localhost:8000/api/prd/import \
  -H 'Content-Type: application/json' \
  -d '{"project_id":"demo","source":{"type":"workspace","repo":"zstack","file_path":"docs/prd/demo.md","ref":"master"}}'
```

说明：

- 不填 `ref` 时读取当前 workspace 工作区文件；
- 填 `ref` 时通过 `git show <ref>:<file>` 读取；
- 文件路径必须在 repo 内，不能使用 `../`；
- 单个 PRD 文件默认限制 2 MiB。

### 8.3 GitLab URL

适用于直接贴 GitLab 文件链接：

1. 选择 `GitLab URL`。
2. 输入类似：

```text
http://gitlab.zstack.io/zstackio/zstack/-/blob/master/docs/prd/demo.md
```

3. 点击 `Import + parse`。

对应 API：

```bash
curl -X POST http://localhost:8000/api/prd/import \
  -H 'Content-Type: application/json' \
  -d '{"project_id":"demo","source":{"type":"gitlab_mcp","url":"http://gitlab.zstack.io/zstackio/zstack/-/blob/master/docs/prd/demo.md"}}'
```

说明：

- 后端会解析 GitLab URL；
- 调用 `zstack-dev-mcp` 的 `gl_get_file_contents`；
- 支持 `/-/blob/<ref>/<path>` 和 `/-/raw/<ref>/<path>`。

## 9. PRD 来源追踪

每次导入后，PRD 会保存 `source_ref`。

常见字段：

```json
{
  "source_type": "gitlab_mcp",
  "repo": "zstackio/zstack",
  "file_path": "docs/prd/demo.md",
  "ref": "master",
  "url": "http://gitlab.zstack.io/..."
}
```

后续 coverage、case、run、diagnosis 可以基于这个来源继续扩展追溯能力。

## 10. Workspace-aware 失败诊断

当 run 失败后，进入：

```text
/diagnosis/<run_id>
```

页面上有两个诊断按钮：

| 按钮 | 作用 |
|---|---|
| `Run diagnosis` | 使用原有 trace/screenshot/run 证据做诊断 |
| `Analyze with workspace context` | 额外收集代码、Jira、Confluence、CI、服务器日志证据 |

也可以调用 API：

```bash
curl -X POST http://localhost:8000/api/diagnosis/by-run/<run_id>/generate-dev-context \
  -H 'Content-Type: application/json' \
  -d '{"overwrite_existing":true}'
```

诊断结果会保存：

- `evidence_pack`：本次诊断收集到的完整证据包；
- `candidate_files`：疑似相关代码文件；
- LLM 生成的 `category`、`reasoning`、`fix_suggestion`。

前端诊断页会展示：

- root cause 分类；
- reasoning；
- fix suggestion；
- 候选代码文件和命中行；
- Jira/CI/Confluence 线索；
- 服务器日志片段。

## 11. 代码搜索逻辑

workspace-aware diagnosis 会从失败 run 中提取关键词：

- run error message；
- failed step error；
- page URL；
- case name / intent / module；
- step intent。

然后在 `MICHELLE_DEV_CONTEXT_REPOS` 配置的 repo 中用 `rg` 搜索代码。

例子：

```bash
MICHELLE_DEV_CONTEXT_REPOS=zstack,zstack-ui-next,premium
```

如果诊断没有候选文件，优先检查：

- workspace 是否 clone 完整；
- repo 名称是否在 `MICHELLE_DEV_CONTEXT_REPOS` 中；
- 失败信息中是否有可搜索的 API path、异常类名、组件名或文案；
- 本机是否安装 `rg`。

## 12. Jira / Confluence / CI 证据

诊断会从失败信息里自动识别：

| 类型 | 识别方式 | MCP 工具 |
|---|---|---|
| Jira | `ZSTAC-82099`、`QA-12` 这类 key | `jira_get_issue` |
| Confluence | URL 中的 `pageId=12345` 或 `/pages/12345` | `confluence_get_page` |
| CI job | GitLab job URL 中的 `/-/jobs/<id>` | `ci_get_job_logs` |
| Jenkins build | Jenkins URL 中的 `/job/.../<build>` | `jenkins_get_build_log` |

这些调用是 best-effort：

- 成功则写入 `evidence_pack.external_context`；
- 失败不会阻塞诊断；
- 错误信息会作为 evidence 的 `error` 字段保存。

说明：当前 Confluence 已用于失败诊断证据收集，PRD 直接从 Confluence 页面导入还没有开放 UI/API；Jenkins build log 是 best-effort，如果当前 `zstack-dev-mcp` 没有提供 `jenkins_get_build_log` 工具，诊断会记录错误但不会阻塞。

## 13. SSH 服务器日志配置

服务器日志能力默认不启用。要启用，需要配置 `MICHELLE_SERVER_LOGS_JSON`。

示例：

```bash
MICHELLE_SERVER_LOGS_JSON='{"servers":[{"name":"staging-api","host":"10.0.0.1","user":"readonly","env":"staging","roles":["api"],"log_paths":["/var/log/zstack/management-server.log","/var/log/nginx/error.log"]}]}'
```

字段说明：

| 字段 | 说明 |
|---|---|
| `name` | Michelle 展示用名称 |
| `host` | 服务器地址 |
| `user` | SSH 用户，建议只读用户 |
| `env` | 环境，例如 `staging`、`test` |
| `roles` | 服务器角色，例如 `api`、`frontend`、`nginx` |
| `log_paths` | 允许读取的日志路径白名单 |

安全边界：

- Michelle 不提供任意 SSH shell；
- 只会执行 `tail -n 200 -- <log_path>`；
- 只读取 `log_paths` 中配置的绝对路径；
- 禁止 `../`、换行、空字节路径；
- SSH 命令使用 `BatchMode=yes`，不会交互式输入密码。
- 日志输出会脱敏 `Authorization: Bearer ...`、`password=...`、`token=...`、`secret=...`、`api_key=...`、`Cookie: ...` 等常见敏感字段。

如果日志拉不到，检查：

- Michelle 运行用户是否有 SSH key；
- 目标机器是否允许该用户免密登录；
- `log_paths` 是否存在且可读；
- 防火墙或 VPN 是否可达。

## 14. 每个角色怎么用

### 14.1 QA

常用入口：

- PRD 页面导入 GitLab 或 workspace 中的需求文档；
- coverage 页面评审测试覆盖；
- case 页面从 accepted coverage 起草用例；
- run 失败后进入 diagnosis 页面点击 `Analyze with workspace context`；
- 根据 candidate files、日志证据和 fix suggestion 与研发沟通。

QA 不需要关心底层 MCP 工具名，也不需要手动打开多个系统复制信息。Michelle 会尽量把证据收集到一次诊断里。

### 14.2 研发

常用信息：

- diagnosis 的 root cause；
- evidence pack 中的浏览器请求、CI、服务器日志；
- candidate files；
- suggested fix；
- suggested regression。

研发可以把 Michelle 的诊断结果作为定位入口，而不是从截图和一句失败描述开始排查。

### 14.3 平台维护者

主要维护：

- `.env` 中 workspace 和 MCP 路径；
- `zstack-dev-mcp` 的凭据；
- `MICHELLE_DEV_CONTEXT_REPOS` 的 repo 列表；
- `MICHELLE_SERVER_LOGS_JSON` 的服务器日志白名单；
- `/api/dev-context/status` 的健康状态。
- Dashboard 中的 `DevContext status` 和 `Admin ops / Audit log`。

## 15. 诊断闭环和审计

诊断页里的 `human feedback` 不只是评论，它决定诊断结果沉淀到哪里。

| Feedback target | 作用 |
|---|---|
| `pattern` | 将确认后的失败模式沉淀到 Pattern，后续类似失败会命中历史经验 |
| `asset` | 如果诊断关联 regression asset，会把 asset 标为 `needs_repair` |
| `case` | 将用例标记为需要 review，并把诊断写入 case quality notes |
| `coverage` | 将来源 coverage 标记为 `stale`，提示重新评审需求覆盖 |

目前已写入 audit log 的关键动作：

| 动作 | Audit action |
|---|---|
| 上传 PRD | `prd.uploaded` |
| 从 workspace / GitLab / Markdown 导入 PRD | `prd.imported` |
| 普通诊断生成 | `diagnosis.generated` |
| Workspace-aware 诊断生成 | `diagnosis.dev_context_generated` |
| 外部证据收集 | `dev_context.external_evidence_collected` |
| 服务器日志读取 | `dev_context.server_logs_read` |
| 人工反馈诊断结果 | `diagnosis.feedback` |
| 发布诊断结果到外部系统 | `diagnosis.published` |

诊断页也提供 `publish back`：

- Jira comment：填写 Jira issue key，例如 `ZSTAC-12345`；
- Confluence comment：填写 Confluence `pageId`；
- GitLab discussion 回写已有后端能力，API 使用 `gitlab_discussion` target，需要提供 `project`、`mr_iid`、`discussion_id`。

回写通过 `zstack-dev-mcp` 执行，成功后会写入 `diagnosis.published` audit log。

Workspace-aware diagnosis 现在也支持异步 Job：

```text
POST /api/diagnosis/by-run/<run_id>/jobs
GET  /api/diagnosis/jobs/<job_id>
```

前端诊断页会创建 job 并轮询状态，避免 Jira/Confluence/Jenkins/SSH/LLM 调用时间过长时卡住页面请求。

管理员可以在 Dashboard 的 `Admin ops / Audit log` 查看最近操作。后续如果接 Jira/MR/Confluence 回写，也应该继续沿用这套 audit 机制。

## 16. 常见排查

| 问题 | 检查点 |
|---|---|
| GitLab URL 导入失败 | `MICHELLE_ZDEV_MCP_ARGS`、MCP 是否 build、GitLab token |
| Workspace 文件导入失败 | `MICHELLE_WORKSPACE_ROOT`、repo 名、文件路径、ref |
| DevContext status 是 disabled | `.env` 是否配置 `MICHELLE_WORKSPACE_ROOT` |
| 诊断没有候选代码 | `MICHELLE_DEV_CONTEXT_REPOS`、失败关键词、workspace checkout |
| Jira/Confluence 拉不到 | zstack-dev-mcp 凭据和对应工具是否启用 |
| CI 日志拉不到 | 失败文本是否包含 GitLab job URL，CI MCP 配置是否可用 |
| Jenkins 日志拉不到 | 失败文本是否包含 Jenkins build URL，zstack-dev-mcp 是否提供 `jenkins_get_build_log` |
| SSH 日志为空 | `MICHELLE_SERVER_LOGS_JSON`、SSH key、日志权限 |

## 17. 当前边界

已经完成的是 PRD 导入和失败诊断证据接入。

暂未做 Confluence PRD 直接导入、自动修复业务代码、自动创建 MR、自动回写 Jira/MR/Confluence 评论。这些可以作为下一阶段能力继续做。

当前版本的目标是先把上下文链路打通，让 Michelle 能稳定地“拿到需求、看到代码、读取证据、生成诊断”。在这个基础上，再做自动回写和修复建议闭环会更稳。
