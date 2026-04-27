---
name: web-test-runner
description: Web 测试全流程入口。当用户提到"生成用例"、"分析需求"、"写测试用例"时触发生成流程；当用户提到"执行测试"、"跑用例"、"自动化测试"、"run cases"时触发执行流程。
---

# Web 测试入口

两个子流程，根据用户意图选择其一：

## 流程 A：需求文档 → 生成用例

**触发词**：生成用例、分析需求、写测试用例、根据文档生成、generate cases

→ 读取并执行 `.claude/skills/case-generator/SKILL.md`

## 流程 B：执行用例 → HTML 报告

**触发词**：执行测试、跑用例、自动化测试、run cases、测试 xxx 项目

→ 读取并执行 `.claude/skills/case-executor/SKILL.md`

## 流程 A+B：完整流水线

**触发词**：从需求到测试、端到端、全流程

→ 先执行流程 A，完成后执行流程 B，Excel 文件名作为衔接参数。

---

**注意**：遇到不确定时，询问用户："您是要生成用例还是执行用例？"
