# Michelle agent-native surface

This directory turns Michelle into a first-class citizen for AI agents
(Claude Code primarily, any MCP client secondarily).

**Principle**: anything a human can do via the Web UI, an agent can do too.

## What's here

| Path | Purpose |
|------|---------|
| `skills/michelle-run/` | Slash command `/michelle-run <case-ids>` — execute selected cases |
| `skills/michelle-diagnose/` | Slash command `/michelle-diagnose <run-id>` — get AI failure analysis |
| `skills/michelle-suggest/` | Slash command `/michelle-suggest <feature>` — propose new cases |
| `agents/michelle-diagnoser.md` | Subagent definition: dedicated failure-diagnosis persona |
| `agents/michelle-reviewer.md` | Subagent definition: AI-generated case review persona |
| `commands/` | Reserved for future namespaced commands |

## How they wire to the platform

```
[Claude Code session]
    ↓ /michelle-run TC-001,TC-002
[Skill loads, runs Bash]
    ↓ POST http://localhost:8000/api/runs
[Michelle backend]
    ↓ spawn `claude -p --mcp-config ...`
[Inner Claude session + @playwright/mcp]
    ↓ drives browser
[Result back to Michelle DB]
    ↓
[Skill reads /api/runs/<id>, prints summary]
```

Same path as the Web UI, just initiated from a Claude Code prompt.

## See also

- `backend/app/mcp/` — Michelle's own MCP server (exposes `michelle.execute_case` etc to ANY MCP client, not just Claude Code)
- `docs/adr/0005-agent-native-surface.md` — design rationale
