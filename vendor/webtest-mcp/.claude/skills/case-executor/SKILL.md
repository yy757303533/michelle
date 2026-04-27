---
name: case-executor
description: 读取 Excel 测试用例，配合 playwright-mcp 执行，生成 HTML 测试报告。工具：get_grouped_cases、get_excel_cases、save_test_results（webtest-mcp）+ browser_* （playwright-mcp）。
---

# 子流程 B：Excel 用例 → 执行 → HTML 报告

## 工具
| 工具 | 来源 | 用途 |
|------|------|------|
| `get_grouped_cases` | webtest-mcp | 查看模块分组 |
| `get_excel_cases` | webtest-mcp | 获取模块用例 |
| `save_test_results` | webtest-mcp | 保存结果（生成 HTML 报告） |
| `browser_navigate` | playwright-mcp | 页面跳转 |
| `browser_snapshot` | playwright-mcp | 获取页面状态 |
| `browser_click` | playwright-mcp | 点击元素 |
| `browser_type` | playwright-mcp | 输入文本 |
| `browser_screenshot` | playwright-mcp | 截图留证 |

---

## 执行流程

```
Step 1: get_grouped_cases   → 了解全貌，规划顺序
Step 2: 登录（如需）        → 建立 session
Step 3: 按模块循环           → get_excel_cases → 逐条执行
Step 4: save_test_results   → 每模块完成后立即保存（含 HTML 报告）
```

---

## Step 1：查看分组

```python
get_grouped_cases(project="huudi", excel_path="huudi_cases_v1.xlsx")
```

返回各模块用例数。**执行顺序**：高优先级模块先跑，用例数最多的模块最后（防止 context 不够）。

---

## Step 2：登录前置

从 `project.yaml` 的 `login` 段读取：

```yaml
login:
  url: "https://..."
  account: "user@example.com"
  password_value: "Password123"
```

**登录操作**：
1. `browser_navigate(login.url)`
2. `browser_snapshot()` → 确认登录页加载
3. 找邮箱框 → `browser_type(account)`
4. 找密码框 → `browser_type(password_value)`
5. 找登录按钮 → `browser_click()`
6. `browser_snapshot()` → 确认已登录（URL 变化 或 出现用户名/头像）

登录成功后**复用同一 session**，整个执行过程不重复登录。
登录失败 → 停止执行，报告原因，不继续。

---

## Step 3：逐条执行

### 执行顺序（每条用例）

```
precondition → 全部 S → 统一验证所有 E
```

S 和 E **不是**一一对应的。先把所有 S 做完，再统一验证所有 E。

### 处理 precondition

| precondition 类型 | 处理方式 |
|-------------------|----------|
| `P1. 用户在 xxx 页面` | `browser_navigate` 到对应 URL |
| `P1. 用户已登录` | 确认 session 有效；已登录跳过 |
| `P1. 用户已有 N 条记录` | `SKIP: 需要预置数据` |
| `P1. 3天内未操作` | `SKIP: 时间条件无法模拟` |

### 执行 S 步骤

每步**必须先 snapshot**，用 snapshot 返回的 `ref` 定位元素：

| 描述关键词 | playwright-mcp 操作 |
|-----------|---------------------|
| 点击「xxx」/ 点击 xxx 按钮 | `browser_click(ref=...)` |
| 输入 / 填写 / 在 xxx 中输入 | `browser_type(ref=..., text=...)` |
| 进入 / 跳转到 xxx 页面 | `browser_navigate(url=base_url+path)` |
| 留空 / 不填写 | 跳过，不操作 |
| 等待 / 加载 | `browser_snapshot()` 轮询，最多 3 次，间隔 2s |
| 上传文件 | `browser_type(ref=input[type=file], text=path)` |

**S 执行失败**（元素找不到 / 超时）：
1. `browser_screenshot(path="/abs/path/fail_{case_id}.png")` 截图，记录绝对路径
2. 记录 `passed: false, error: "S{n} 失败: {原因}"`
3. 跳过该用例剩余所有 S 和 E

### 验证所有 E

全部 S 完成后，做一次 `browser_snapshot()`，然后逐条验证：

| E 类型 | 验证方式 |
|--------|----------|
| `显示 xxx 文字` | 检查 snapshot 文本包含该内容 |
| `按钮禁用 / 不可点击` | 检查元素 `disabled` 或 `aria-disabled` |
| `跳转到 xxx 页面` | 检查当前 URL 或页面标题 |
| `字段显示错误状态` | 检查 `aria-invalid=true` 或 error class |
| `弹窗 / Modal 出现` | 检查 snapshot 中是否有 dialog/modal 元素 |
| `布局 / 颜色 / 视觉样式` | `SKIP: 需人工视觉验证` |
| `邮件发送` | `SKIP: 需邮箱环境` |

**E 验证结果**：
- 所有 E 通过 → `passed: true`
- 任一 E 不通过 → `passed: false, error: "E{n} 失败: 期望 xxx，实际 yyy"`
- 无法自动验证 → `passed: null, error: "SKIP: {原因}"`

---

## Step 4：保存结果（每模块）

每个模块执行完后**立即**保存：

```python
save_test_results(
    project="huudi",
    results=[
        {"case_id": "HUUDI-T0001", "module": "用户注册和登录", "title": "...", "passed": True},
        {"case_id": "HUUDI-T0002", "module": "用户注册和登录", "title": "...", "passed": False, "error": "E2 失败: ..."},
        {"case_id": "HUUDI-T0003", "module": "用户注册和登录", "title": "...", "passed": None, "error": "SKIP: 需要预置数据"},
    ],
    excel_path="huudi_cases_v1.xlsx",
    screenshots=[
        # 只传 FAIL 用例的截图，路径为 browser_screenshot 返回的绝对路径
        {"case_id": "HUUDI-T0002", "path": "/abs/path/fail_HUUDI-T0002.png"},
    ]
)
```

返回中包含 `report_html_path`（HTML 文件路径），向用户展示：

```
✅ 用户注册和登录 执行完成
   结果已保存：projects/huudi/artifacts/huudi/report.html
   通过 28 / 失败 1 / 跳过 2 — 共 31 条
```

---

## 结束：输出总汇总

所有模块执行完后，汇总输出：

```
═══════════════════════════════════
  Huudi 测试执行报告
═══════════════════════════════════
  用户注册和登录    ✅ 29/31  (1 FAIL, 1 SKIP)
  KYC验证          ✅ 22/28  (3 FAIL, 3 SKIP)
  投资流程         ✅ 24/26  (0 FAIL, 2 SKIP)
───────────────────────────────────
  总计: 85 条  通过 75  失败 4  跳过 6
  报告: projects/huudi/artifacts/huudi/report.html
═══════════════════════════════════
```

---

## 关键规则

1. **每步操作前必须 snapshot** — 用 ref 定位，不猜 CSS selector
2. **失败立即截图** — `browser_screenshot()` 保存现场
3. **按模块分批** — 不要一次加载全部用例
4. **模块完成即保存** — 不要等全部跑完才 save
5. **法语/英语界面文本照搬** — 不翻译按钮名
6. **登录 session 全程复用** — 不重复登录
