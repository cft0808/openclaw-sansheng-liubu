<#
.SYNOPSIS
    三省六部 OpenCode 数据刷新循环 (Windows PowerShell 版本)
.DESCRIPTION
    run_loop_opencode.sh 的 Windows 等效脚本。
    用法: .\scripts\run_loop_opencode.ps1 [-Interval 15] [-ScanInterval 120]
#>
#Requires -Version 5.1
param(
    [int]$Interval = 15,
    [int]$ScanInterval = 120
)

$ErrorActionPreference = "Continue"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoDir = Split-Path -Parent $ScriptDir
$env:EDICT_HOME = if ($env:EDICT_HOME) { $env:EDICT_HOME } else { $RepoDir }
$env:EDICT_RUNTIME = "opencode"
$env:EDICT_AGENT_RUNTIME = "opencode"
if (-not $env:OPENCODE_MODEL) { $env:OPENCODE_MODEL = "github-copilot/gpt-4o" }

$Log = Join-Path $env:TEMP "sansheng_liubu_opencode_refresh.log"
$PidFile = Join-Path $env:TEMP "sansheng_liubu_opencode_refresh.pid"
$MaxLogSize = 10MB
$ScriptTimeout = 30
$DashboardPort = if ($env:EDICT_DASHBOARD_PORT) { $env:EDICT_DASHBOARD_PORT } else { "7891" }

function Resolve-Python {
    if ($env:EDICT_PYTHON) {
        $cmd = Get-Command $env:EDICT_PYTHON -ErrorAction SilentlyContinue
        if ($cmd) { return @{ File = $cmd.Source; Prefix = @() } }
    }
    foreach ($candidate in @("python", "python3")) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) { return @{ File = $cmd.Source; Prefix = @() } }
    }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { return @{ File = $py.Source; Prefix = @("-3") } }
    throw "未找到 python / python3 / py。请先安装 Python 3.10+。"
}

$Python = Resolve-Python

if (Test-Path $PidFile) {
    $OldPid = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($OldPid -and (Get-Process -Id $OldPid -ErrorAction SilentlyContinue)) {
        Write-Host "已有 OpenCode 刷新循环运行中 (PID=$OldPid)，退出"
        exit 1
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}
$PID | Out-File $PidFile -Encoding ASCII

$cleanup = {
    "$(Get-Date -Format HH:mm:ss) [opencode-loop] Shutting down..." | Out-File $Log -Append -Encoding UTF8
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}
Register-EngineEvent PowerShell.Exiting -Action $cleanup | Out-Null

function Rotate-Log {
    if (Test-Path $Log) {
        $size = (Get-Item $Log).Length
        if ($size -gt $MaxLogSize) {
            Move-Item $Log "$Log.1" -Force
            "$(Get-Date -Format HH:mm:ss) [opencode-loop] Log rotated" | Out-File $Log -Encoding UTF8
        }
    }
}

function Safe-Run([string]$Script) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Python.File
    $args = @($Python.Prefix) + @($Script)
    $psi.Arguments = ($args | ForEach-Object { '"' + ($_ -replace '"', '\"') + '"' }) -join " "
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $psi.WorkingDirectory = $RepoDir

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $psi
    $process.Start() | Out-Null

    if (-not $process.WaitForExit($ScriptTimeout * 1000)) {
        try {
            $process.Kill()
            "$(Get-Date -Format HH:mm:ss) [opencode-loop] Script timeout (${ScriptTimeout}s): $Script" | Out-File $Log -Append -Encoding UTF8
        } catch {}
    }

    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    if ($stdout) { $stdout | Out-File $Log -Append -Encoding UTF8 }
    if ($stderr) { $stderr | Out-File $Log -Append -Encoding UTF8 }
}

function Invoke-SchedulerScan {
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:$DashboardPort/api/scheduler-scan" `
            -Method POST `
            -ContentType "application/json" `
            -Body '{"thresholdSec":180}' `
            -TimeoutSec 5 | Out-Null
    } catch {
        $_ | Out-File $Log -Append -Encoding UTF8
    }
}

Write-Host "三省六部 OpenCode 数据刷新循环启动 (PID=$PID)"
Write-Host "  Script dir: $ScriptDir"
Write-Host "  Interval: ${Interval}s  Scan: ${ScanInterval}s  Timeout: ${ScriptTimeout}s"
Write-Host "  Log: $Log"
Write-Host "  Ctrl+C to stop"

$ScanCounter = 0
while ($true) {
    Rotate-Log
    Safe-Run (Join-Path $ScriptDir "sync_opencode_agents.py")
    Safe-Run (Join-Path $ScriptDir "refresh_live_data.py")

    $ScanCounter += $Interval
    if ($ScanCounter -ge $ScanInterval) {
        $ScanCounter = 0
        Invoke-SchedulerScan
    }

    Start-Sleep -Seconds $Interval
}
