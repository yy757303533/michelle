# Michelle 内部试点指南

## 1. 启动

```bash
cp .env.example .env
make setup
make postgres
make dev
```

`.env.example` 默认使用 PostgreSQL：

```bash
DATABASE_URL=postgresql+asyncpg://michelle:michelle@127.0.0.1:5432/michelle
```

启动前需要准备本机或共享 Postgres，并确认库和账号已创建。

默认登录：

- username: `admin`
- password: 首次启动时查看 `backend/artifacts/bootstrap-admin.txt`

如果你显式设置了 `DEFAULT_ADMIN_PASSWORD`，则使用该密码创建首个 admin。密码哈希保存在数据库中，`.env.example` 不放通用默认密码。

生产或共享环境必须修改 `.env`。`APP_ENV=shared/staging/production` 时继续使用历史默认密码会阻止后端启动：

```bash
APP_ENV=shared
DATABASE_URL=postgresql+asyncpg://michelle:<password>@postgres:5432/michelle
DEFAULT_ADMIN_USERNAME=<admin-user>
DEFAULT_ADMIN_PASSWORD=<strong-bootstrap-password>
ADMIN_TOKEN=<break-glass-token>
```

SQLite 只建议本机试用。多人试点建议使用 Postgres，并把数据库纳入备份。

Artifacts 会保存在 `backend/artifacts/`。Dashboard → Platform settings 中可以配置
`artifact_retention_days`，先点 `dry run` 查看将清理多少 run 和空间，再点
`clean now` 执行；pending/running run 会被跳过。

## 2. 试点验收流程

1. 创建项目，填写 `base_url` 和测试账号。
2. 上传 PRD，生成用例。
3. 由 reviewer/admin 审核并 approve 用例。
4. 批量执行用例，在 Queue 页面观察 pending/running。
5. 失败后进入 AI diagnosis，确认分类、证据和修复建议。
6. 对正确诊断点 confirmed；错误诊断选择原因并备注。
7. 检查 Dashboard 的 pass rate、flaky rate、环境自检。
8. 导出 cases/report/diagnosis 给项目组复盘。
9. Dashboard → Environment self-check 中检查 `admin_security`、`database`、`llm_probe`、`playwright_mcp_probe`。

## 3. 真实 E2E

```bash
DEFAULT_TARGET_URL=http://localhost:5000/ make e2e-smoke
```

如果开启了 admin token：

```bash
cd backend
uv run python ../scripts/day13_e2e_smoke.py \
  --target-url http://localhost:5000/ \
  --admin-token "$ADMIN_TOKEN"
```

## 4. 诊断可信度边界

AI diagnosis 不能替代人工判断。以下情况必须人工复核：

- confidence < 0.7
- screenshot 缺失或页面空白
- 失败来自环境、账号、网络、弹窗或登录态
- 涉及权限、支付、生产数据、批量写入或破坏性操作
- 模型给出的 fix 无法直接执行
- pattern 命中但产品近期有大改版

## 5. 常见排查

- Executor 不 ready：Dashboard self-check 查看 provider、npx、runner status。
- Run 卡住：Queue 页面 cancel，然后检查 run detail timeline。
- 邮件/Webhook 不发：先在 Platform settings 点 test email / test webhook。
- 登录失败：查看 `backend/artifacts/bootstrap-admin.txt`，或检查 `.env` 中 `DEFAULT_ADMIN_PASSWORD`。
