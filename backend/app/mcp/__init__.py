"""Michelle's own MCP server.

Exposes platform capabilities to any MCP client (Claude Code, Cursor, ...).

Tools (Day 6+ implements bodies; Day 1 stubs the registration):

  michelle.list_cases(project_id, status?)        -> list[Case]
  michelle.get_case(case_id)                       -> Case
  michelle.execute_case(case_id, env?)             -> {run_id}
  michelle.get_run(run_id)                         -> Run + steps
  michelle.diagnose(run_id)                        -> Diagnosis
  michelle.suggest_cases(description, max_cases)   -> list[CaseDraft]

Mounted by main.py at startup if MCP_ENABLE=true.
"""

from app.mcp.server import build_mcp_server

__all__ = ["build_mcp_server"]
