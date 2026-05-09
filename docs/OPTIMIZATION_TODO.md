# 待优化项

## 真实端到端压测

Status: scripted via `make e2e-smoke` / `scripts/day13_e2e_smoke.py`.

在真实 target app 上完整跑一遍新执行器链路：

1. 生成或选择已有 approved case。
2. 点击 Run，确认 Michelle generic loop 能完成真实 MCP 通信。
3. 在 run detail 查看 timeline，确认工具调用、截图、URL/title、错误信息都正常落库。
4. 对失败 run 触发 diagnosis，确认模型 JSON action、run trace、截图输入和诊断结果稳定。

验收标准：至少 3 条真实 case 跑通，其中包含 1 条失败 case，并能生成可读 diagnosis。

运行方式：

```bash
# terminal 1
make dev

# terminal 2
DEFAULT_TARGET_URL=http://localhost:5000/ make e2e-smoke
```

也可以传参覆盖默认地址：

```bash
cd backend
uv run python ../scripts/day13_e2e_smoke.py --target-url http://localhost:5000/
```
