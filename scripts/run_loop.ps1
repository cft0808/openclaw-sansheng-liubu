<#
.SYNOPSIS
    三省六部数据刷新循环 (Windows PowerShell 版本)
.DESCRIPTION
    run_loop.sh 的 Windows 等效脚本。
    用法: .\run_loop.ps1 [-Interval 15] [-ScanInterval 120]
.NOTES
    源自 GitHub Issue #245 (感谢 @Vip4pt 贡献)
#>
param(
    [int]$Interval = 15,
    [int]$ScanInterval = 120
)

# ── 基础配置 ──
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $env:OPENCLAW_HOME) {
    $env:OPENCLAW_HOME = Split-Path -Parent $ScriptDir
}

$Log = "$env:TEMP\sansheng_liubu_refresh.log"
$PidFile = "$env:TEMP\sansheng_liubu_refresh.pid"
$MaxLogSize = 10MB
$ScriptTimeout = 30
$DashboardPort = $env:EDICT_DASHBOARD_PORT
if (-not $DashboardPort) { $DashboardPort = 7891 }

# ── 单实例保护 ──
if (Test-Path $PidFile) {
    $OldPid = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($OldPid -and (Get-Process -Id $OldPid -ErrorAction SilentlyContinue)) {
        Write-Host "Already running (PID=$OldPid), exiting."
        exit 1
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}
$PID | Out-File $PidFile

# ── 优雅退出 ──
$cleanup = {
    "$(Get-Date -Format HH:mm:ss) [loop] Shutting down..." | Out-File $Log -Append
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    exit
}
Register-EngineEvent PowerShell.Exiting -Action $cleanup | Out-Null

# ── 日志轮转 ──
function Rotate-Log {
    if (Test-Path $Log) {
        $size = (Get-Item $Log).Length
        if ($size -gt $MaxLogSize) {
            Move-Item $Log "$Log.1" -Force
            "$(Get-Date -Format HH:mm:ss) [loop] Log rotated" | Out-File $Log
        }
    }
}

# ── 安全执行（带超时）──
function Safe-Run($script) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "python"
    $psi.Arguments = "`"$script`""
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $psi
    $process.Start() | Out-Null

    if (-not $process.WaitForExit($ScriptTimeout * 1000)) {
        try {
            $process.Kill()
            "$(Get-Date -Format HH:mm:ss) [loop] Script timeout (${ScriptTimeout}s): $script" | Out-File $Log -Append
        } catch {}
    }

    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()

    if ($stdout) { $stdout | Out-File $Log -Append }
    if ($stderr) { $stderr | Out-File $Log -Append }
}

# ── 启动信息 ──
Write-Host "Data refresh loop started (PID=$PID)"
Write-Host "  Script dir: $ScriptDir"
Write-Host "  Interval: ${Interval}s  Scan: ${ScanInterval}s  Timeout: ${ScriptTimeout}s"
Write-Host "  Log: $Log"
Write-Host "  Ctrl+C to stop"

$ScanCounter = 0

# ── 主循环 ──
while ($true) {
    Rotate-Log

    Safe-Run "$ScriptDir\sync_from_openclaw_runtime.py"
    Safe-Run "$ScriptDir\sync_agent_config.py"
    Safe-Run "$ScriptDir\apply_model_changes.py"
    Safe-Run "$ScriptDir\sync_officials_stats.py"
    Safe-Run "$ScriptDir\refresh_live_data.py"

    # ── 巡检任务 ──
    $ScanCounter += $Interval
    if ($ScanCounter -ge $ScanInterval) {
        $ScanCounter = 0
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:$DashboardPort/api/scheduler-scan" `
                -Method POST `
                -ContentType "application/json" `
                -Body '{"thresholdSec":180}' | Out-Null
        } catch {
            $_ | Out-File $Log -Append
        }
    }

    Start-Sleep -Seconds $Interval
}
