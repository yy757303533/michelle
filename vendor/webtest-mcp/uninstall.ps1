# webtest-mcp-server 卸载（Windows PowerShell）
# 仅适配 Claude Code

$ErrorActionPreference = "Stop"
$Python      = if (Get-Command python -EA 0) { "python" } else { "py" }
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ClaudeJson  = Join-Path $env:USERPROFILE ".claude.json"

Write-Host ""
Write-Host "  webtest-mcp-server 卸载" -ForegroundColor Cyan
Write-Host "  将移除:"
Write-Host "    * ~/.claude/skills 中的 3 个 Skill"
Write-Host "    * ~/.claude.json 中的 webtest/playwright"
Write-Host ""
$confirm = Read-Host "  继续? [y/N]"
if ($confirm -notmatch "^[Yy]") { Write-Host "已取消"; exit 0 }

# 移除 3 个 Skill
foreach ($skill in @("web-test-runner", "case-generator", "case-executor")) {
    $d = Join-Path $env:USERPROFILE ".claude\skills\$skill"
    if (Test-Path $d) {
        Remove-Item -Path $d -Recurse -Force
        Write-Host "  已移除 $d"
    }
}

# 从 ~/.claude.json 移除 webtest、playwright
if (Test-Path $ClaudeJson) {
    & $Python "$ProjectRoot\scripts\remove_mcp_config.py" $ClaudeJson
    Write-Host "  已从 $ClaudeJson 移除 webtest/playwright"
}

# claude cli 注销
if (Get-Command claude -ErrorAction SilentlyContinue) {
    try { claude mcp remove webtest    --scope user 2>$null; Write-Host "  claude mcp remove webtest: ok"    } catch {}
    try { claude mcp remove playwright --scope user 2>$null; Write-Host "  claude mcp remove playwright: ok" } catch {}
}

Write-Host ""
Write-Host "卸载完成。重启 Claude Code 生效。" -ForegroundColor Green
Write-Host "Python 包需手动卸载: pip uninstall webtest-mcp-server"
