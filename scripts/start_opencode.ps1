<#
.SYNOPSIS
    三省六部 OpenCode 一键启动 (Windows PowerShell 版本)
.DESCRIPTION
    生成 OpenCode agent 配置，启动 OpenCode server、dashboard/server.py 和数据刷新循环。
    用法: powershell -ExecutionPolicy Bypass -File .\scripts\start_opencode.ps1
#>
#Requires -Version 5.1

$ErrorActionPreference = "Stop"

$RepoDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$DashboardHost = if ($env:EDICT_DASHBOARD_HOST) { $env:EDICT_DASHBOARD_HOST } else { "127.0.0.1" }
$DashboardPort = if ($env:EDICT_DASHBOARD_PORT) { [int]$env:EDICT_DASHBOARD_PORT } else { 7891 }
$OpenCodeHost = if ($env:OPENCODE_HOST) { $env:OPENCODE_HOST } else { "127.0.0.1" }
$OpenCodePort = if ($env:OPENCODE_PORT) { [int]$env:OPENCODE_PORT } else { 4096 }
$OpenCodeServerUrl = if ($env:OPENCODE_SERVER_URL) { $env:OPENCODE_SERVER_URL.TrimEnd("/") } else { "http://${OpenCodeHost}:${OpenCodePort}" }
$OpenCodeModel = if ($env:OPENCODE_MODEL) { $env:OPENCODE_MODEL } else { "github-copilot/gpt-4o" }

$PidDir = Join-Path $RepoDir ".pids"
$LogDir = Join-Path $RepoDir "logs"
$ServerPidFile = Join-Path $PidDir "server.pid"
$LoopPidFile = Join-Path $PidDir "loop.pid"
$OpenCodePidFile = Join-Path $PidDir "opencode.pid"
$ServerLog = Join-Path $LogDir "server.log"
$ServerErrLog = Join-Path $LogDir "server.err.log"
$LoopLog = Join-Path $LogDir "loop.log"
$LoopErrLog = Join-Path $LogDir "loop.err.log"
$OpenCodeLog = Join-Path $LogDir "opencode.log"
$OpenCodeErrLog = Join-Path $LogDir "opencode.err.log"

function Log($msg) { Write-Host "✅ $msg" -ForegroundColor Green }
function Info($msg) { Write-Host "▶ $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "⚠️  $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "❌ $msg" -ForegroundColor Red; exit 1 }

function Resolve-Python {
    if ($env:EDICT_PYTHON) {
        $cmd = Get-Command $env:EDICT_PYTHON -ErrorAction SilentlyContinue
        if ($cmd) { return @{ File = $cmd.Source; Prefix = @() } }
    }
    foreach ($candidate in @("python", "python3")) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) {
            $version = & $cmd.Source -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')" 2>$null
            if ($LASTEXITCODE -eq 0) {
                $parts = $version.Split(".")
                if ([int]$parts[0] -gt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 10)) {
                    return @{ File = $cmd.Source; Prefix = @() }
                }
            }
        }
    }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        $version = & $py.Source -3 -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')" 2>$null
        if ($LASTEXITCODE -eq 0) { return @{ File = $py.Source; Prefix = @("-3") } }
    }
    throw "未找到 Python 3.10+。"
}

function Resolve-OpenCode {
    if ($env:OPENCODE_BIN) {
        $cmd = Get-Command $env:OPENCODE_BIN -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
        if (Test-Path $env:OPENCODE_BIN) { return $env:OPENCODE_BIN }
    }
    $cmd = Get-Command opencode -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in @(
        (Join-Path $env:USERPROFILE ".opencode\bin\opencode.exe"),
        (Join-Path $env:USERPROFILE ".opencode\bin\opencode.cmd"),
        (Join-Path $env:USERPROFILE ".opencode\bin\opencode")
    )) {
        if (Test-Path $p) { return $p }
    }
    throw "未找到 OpenCode CLI。请先安装 opencode，或设置 OPENCODE_BIN。"
}

function Join-Args([string[]]$Args) {
    return ($Args | ForEach-Object { '"' + ($_ -replace '"', '\"') + '"' }) -join " "
}

function Invoke-Python([string[]]$Args) {
    & $script:Python.File @($script:Python.Prefix + $Args)
}

function Test-HttpOk([string]$Url) {
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500)
    } catch {
        return $false
    }
}

function Test-OpenCodeAgents {
    try {
        $dir = [System.Uri]::EscapeDataString($RepoDir)
        $agents = Invoke-RestMethod -Uri "$OpenCodeServerUrl/agent?directory=$dir" -TimeoutSec 5
        $names = @($agents | ForEach-Object { $_.name })
        return ($names -contains "taizi" -and $names -contains "zhongshu" -and $names -contains "shangshu")
    } catch {
        return $false
    }
}

function Get-PortPids([int]$Port) {
    try {
        return @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
    } catch {
        return @()
    }
}

function Stop-PidFile([string]$PidFile) {
    if (-not (Test-Path $PidFile)) { return }
    $pidText = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($pidText -and ($pidText -match '^\d+$')) {
        $p = Get-Process -Id ([int]$pidText) -ErrorAction SilentlyContinue
        if ($p) {
            try { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } catch {}
        }
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

function Stop-OpenCodeOnPort {
    foreach ($pid in Get-PortPids $OpenCodePort) {
        $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$pid" -ErrorAction SilentlyContinue).CommandLine
        if ($cmd -and $cmd.ToLowerInvariant().Contains("opencode")) {
            Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
        }
    }
}

function Stop-RepoDashboardOnPort {
    foreach ($pid in Get-PortPids $DashboardPort) {
        $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$pid" -ErrorAction SilentlyContinue).CommandLine
        if ($cmd -and $cmd.Contains("dashboard") -and $cmd.Contains("server.py")) {
            Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
        }
    }
}

function Ensure-DataFiles {
    New-Item -ItemType Directory -Path $PidDir, $LogDir, (Join-Path $RepoDir "data") -Force | Out-Null
    foreach ($f in @("live_status.json","agent_config.json","model_change_log.json","sync_status.json","officials_stats.json")) {
        $fp = Join-Path $RepoDir "data\$f"
        if (-not (Test-Path $fp)) { "{}" | Out-File $fp -Encoding UTF8 }
    }
    foreach ($f in @("pending_model_changes.json","tasks_source.json","tasks.json","officials.json")) {
        $fp = Join-Path $RepoDir "data\$f"
        if (-not (Test-Path $fp)) { "[]" | Out-File $fp -Encoding UTF8 }
    }
}

function Start-OpenCodeServer {
    $args = @("serve", "--hostname", $OpenCodeHost, "--port", [string]$OpenCodePort)
    $p = Start-Process -FilePath $OpenCodeBin -ArgumentList (Join-Args $args) `
        -WorkingDirectory $RepoDir `
        -RedirectStandardOutput $OpenCodeLog `
        -RedirectStandardError $OpenCodeErrLog `
        -WindowStyle Hidden `
        -PassThru
    $p.Id | Out-File $OpenCodePidFile -Encoding ASCII
}

function Start-DashboardServer {
    $args = @($Python.Prefix) + @((Join-Path $RepoDir "dashboard\server.py"), "--host", $DashboardHost, "--port", [string]$DashboardPort)
    $p = Start-Process -FilePath $Python.File -ArgumentList (Join-Args $args) `
        -WorkingDirectory $RepoDir `
        -RedirectStandardOutput $ServerLog `
        -RedirectStandardError $ServerErrLog `
        -WindowStyle Hidden `
        -PassThru
    $p.Id | Out-File $ServerPidFile -Encoding ASCII
}

function Start-RefreshLoop {
    $script = Join-Path $RepoDir "scripts\run_loop_opencode.ps1"
    $args = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $script)
    $p = Start-Process -FilePath "powershell" -ArgumentList (Join-Args $args) `
        -WorkingDirectory $RepoDir `
        -RedirectStandardOutput $LoopLog `
        -RedirectStandardError $LoopErrLog `
        -WindowStyle Hidden `
        -PassThru
    $p.Id | Out-File $LoopPidFile -Encoding ASCII
}

Write-Host ""
Write-Host "╔══════════════════════════════════════════╗" -ForegroundColor Blue
Write-Host "║  三省六部 · OpenCode Windows 启动中       ║" -ForegroundColor Blue
Write-Host "╚══════════════════════════════════════════╝" -ForegroundColor Blue
Write-Host ""

try { $script:Python = Resolve-Python } catch { Fail $_ }
try { $script:OpenCodeBin = Resolve-OpenCode } catch { Fail $_ }

$env:EDICT_RUNTIME = "opencode"
$env:EDICT_AGENT_RUNTIME = "opencode"
$env:EDICT_PYTHON = $Python.File
$env:OPENCODE_BIN = $OpenCodeBin
$env:OPENCODE_SERVER_URL = $OpenCodeServerUrl
$env:OPENCODE_MODEL = $OpenCodeModel

Ensure-DataFiles

Write-Host "Python:   $($Python.File)" -ForegroundColor Green
Write-Host "OpenCode: $OpenCodeBin" -ForegroundColor Green
Write-Host "Model:    $OpenCodeModel" -ForegroundColor Green

Info "同步 OpenCode agent 配置..."
Invoke-Python @((Join-Path $RepoDir "scripts\sync_opencode_agents.py"))

if ((Test-HttpOk "$OpenCodeServerUrl/doc") -and (Test-OpenCodeAgents)) {
    Log "复用已运行的 OpenCode server: $OpenCodeServerUrl"
} else {
    if (Test-HttpOk "$OpenCodeServerUrl/doc") {
        Warn "当前 $OpenCodeServerUrl 不是本项目的 OpenCode server，正在切换..."
        Stop-OpenCodeOnPort
        Stop-PidFile $OpenCodePidFile
        Start-Sleep -Milliseconds 500
    }
    Info "启动 OpenCode server..."
    Stop-PidFile $OpenCodePidFile
    Start-OpenCodeServer
    for ($i = 0; $i -lt 20; $i++) {
        if (Test-HttpOk "$OpenCodeServerUrl/doc") { break }
        Start-Sleep -Milliseconds 500
    }
    if (-not ((Test-HttpOk "$OpenCodeServerUrl/doc") -and (Test-OpenCodeAgents))) {
        Fail "OpenCode server 启动失败，请查看日志: $OpenCodeLog / $OpenCodeErrLog"
    }
}

Info "切换看板到 OpenCode 运行时..."
Stop-PidFile $ServerPidFile
Stop-PidFile $LoopPidFile
Stop-RepoDashboardOnPort
Remove-Item (Join-Path $env:TEMP "sansheng_liubu_opencode_refresh.pid") -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 500

Start-DashboardServer
Start-RefreshLoop

for ($i = 0; $i -lt 20; $i++) {
    if (Test-HttpOk "http://${DashboardHost}:${DashboardPort}/healthz") { break }
    Start-Sleep -Milliseconds 500
}
if (-not (Test-HttpOk "http://${DashboardHost}:${DashboardPort}/healthz")) {
    Fail "看板启动失败，请查看日志: $ServerLog / $ServerErrLog"
}

Write-Host ""
Log "OpenCode 模式已启动"
Write-Host "   看板:     http://${DashboardHost}:${DashboardPort}" -ForegroundColor Blue
Write-Host "   OpenCode: $OpenCodeServerUrl" -ForegroundColor Blue
Write-Host "   日志:     $LogDir" -ForegroundColor Blue

try {
    Start-Process "http://${DashboardHost}:${DashboardPort}" | Out-Null
} catch {}
