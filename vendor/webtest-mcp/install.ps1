# webtest-mcp-server 一键安装（Windows PowerShell）
# 仅适配 Claude Code

$ErrorActionPreference = "Stop"
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = $ScriptDir
$ProjectsDir = Join-Path $ProjectRoot "projects"
$ClaudeJson  = Join-Path $env:USERPROFILE ".claude.json"

# 检测 Python
$Python = $null
if (Get-Command python  -ErrorAction SilentlyContinue) { $Python = "python"  }
elseif (Get-Command python3 -ErrorAction SilentlyContinue) { $Python = "python3" }
elseif (Get-Command py      -ErrorAction SilentlyContinue) { $Python = "py"      }
if (-not $Python) {
    Write-Host "错误: 未找到 python、python3 或 py" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "  webtest-mcp-server x Claude Code" -ForegroundColor White
Write-Host "  Skill + MCP 一键安装" -ForegroundColor White
Write-Host ""
Write-Host "  将安装:"
Write-Host "    * 3 个 Skill  -> ~/.claude/skills"
Write-Host "    * webtest + playwright -> ~/.claude.json (Claude Code)"
Write-Host ""

# 1. 安装 Python 包
Write-Host "[1/3] 安装 Python 依赖..." -ForegroundColor Yellow
Set-Location $ProjectRoot
& $Python -m pip install -e . -q
Write-Host "  ok" -ForegroundColor Green

# 2. 安装 Skill
Write-Host "[2/3] 安装 Skill..." -ForegroundColor Yellow
$ClaudeSkills = Join-Path $env:USERPROFILE ".claude\skills"
New-Item -ItemType Directory -Force -Path $ClaudeSkills | Out-Null
foreach ($skill in @("web-test-runner", "case-generator", "case-executor")) {
    Copy-Item -Path "$ProjectRoot\.claude\skills\$skill" -Destination $ClaudeSkills -Recurse -Force
    Write-Host "  ~/.claude/skills/$skill"
}

# 3. 注册 MCP 到 Claude Code
Write-Host "[3/3] 注册 MCP..." -ForegroundColor Yellow

# 写入 ~/.claude.json（带 WEBTEST_PROJECTS_DIR）
$dir = Split-Path -Parent $ClaudeJson
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
& $Python "$ProjectRoot\scripts\merge_mcp_config.py" $ClaudeJson $ProjectsDir
Write-Host "  已配置 $ClaudeJson (含 WEBTEST_PROJECTS_DIR=$ProjectsDir)"

# 同时用 claude cli 注册（如果已安装）
if (Get-Command claude -ErrorAction SilentlyContinue) {
    try {
        claude mcp add webtest --scope user -e "WEBTEST_PROJECTS_DIR=$ProjectsDir" -- webtest-mcp 2>$null
        Write-Host "  claude mcp add webtest: ok"
    } catch {
        Write-Host "  claude mcp add 跳过（已存在，~/.claude.json 已写入）"
    }
    try {
        claude mcp add playwright --scope user -- npx @playwright/mcp@latest 2>$null
        Write-Host "  claude mcp add playwright: ok"
    } catch {
        Write-Host "  claude mcp add playwright 跳过（已存在）"
    }
}

Write-Host ""
Write-Host "═══════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "  安装完成！" -ForegroundColor Green
Write-Host "═══════════════════════════════════════════════════" -ForegroundColor Green
Write-Host ""
Write-Host "  1. 重启 Claude Code 使 MCP 生效"
Write-Host "  2. 确保已安装 Node.js（playwright-mcp 需要 npx）"
Write-Host "  3. 如需 .xls 格式支持: pip install -e `".[xls]`""
Write-Host "  4. 使用: 对 AI 说「根据需求文档给 xxx 项目生成用例」"
Write-Host ""
