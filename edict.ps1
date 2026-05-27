<#
.SYNOPSIS
    三省六部 Windows 服务管理脚本
.DESCRIPTION
    PowerShell 入口，对应 edict.sh。
    用法: powershell -ExecutionPolicy Bypass -File .\edict.ps1 {opencode|stop|status|logs}
#>
#Requires -Version 5.1
param(
    [ValidateSet("opencode", "stop", "status", "logs")]
    [string]$Command,
    [ValidateSet("server", "loop", "opencode", "all")]
    [string]$Target = "all"
)

$ErrorActionPreference = "Continue"

$RepoDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidDir = Join-Path $RepoDir ".pids"
$LogDir = Join-Path $RepoDir "logs"
$ServerPidFile = Join-Path $PidDir "server.pid"
$LoopPidFile = Join-Path $PidDir "loop.pid"
$OpenCodePidFile = Join-Path $PidDir "opencode.pid"
$DashboardHost = if ($env:EDICT_DASHBOARD_HOST) { $env:EDICT_DASHBOARD_HOST } else { "127.0.0.1" }
$DashboardPort = if ($env:EDICT_DASHBOARD_PORT) { [int]$env:EDICT_DASHBOARD_PORT } else { 7891 }

function Is-Running([string]$PidFile) {
    if (-not (Test-Path $PidFile)) { return $false }
    $pidText = Get-Content $PidFile -ErrorAction SilentlyContinue
    if (-not ($pidText -match '^\d+$')) {
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
        return $false
    }
    $p = Get-Process -Id ([int]$pidText) -ErrorAction SilentlyContinue
    if ($p) { return $true }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    return $false
}

function Get-PidText([string]$PidFile) {
    if (Test-Path $PidFile) { return (Get-Content $PidFile -ErrorAction SilentlyContinue) }
    return ""
}

function Stop-PidFile([string]$PidFile) {
    if (-not (Test-Path $PidFile)) { return $false }
    $pidText = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($pidText -and ($pidText -match '^\d+$')) {
        try { Stop-Process -Id ([int]$pidText) -Force -ErrorAction SilentlyContinue } catch {}
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    return $true
}

function Show-Status {
    Write-Host "三省六部 · Windows 服务状态" -ForegroundColor Blue
    Write-Host ""
    foreach ($item in @(
        @{ Name = "看板服务器"; File = $ServerPidFile },
        @{ Name = "数据刷新循环"; File = $LoopPidFile },
        @{ Name = "OpenCode server"; File = $OpenCodePidFile }
    )) {
        if (Is-Running $item.File) {
            Write-Host "  ● $($item.Name) PID=$(Get-PidText $item.File) 运行中" -ForegroundColor Green
        } else {
            Write-Host "  ○ $($item.Name) 未运行" -ForegroundColor Red
        }
    }
    Write-Host ""
    try {
        $health = Invoke-RestMethod -Uri "http://${DashboardHost}:${DashboardPort}/healthz" -TimeoutSec 3
        if ($health.status -eq "ok") {
            Write-Host "  健康检查: 正常" -ForegroundColor Green
        } else {
            Write-Host "  健康检查: degraded" -ForegroundColor Yellow
        }
        Write-Host "  看板地址: http://${DashboardHost}:${DashboardPort}" -ForegroundColor Blue
    } catch {
        Write-Host "  健康检查: 无法连接" -ForegroundColor Red
    }
}

function Stop-All {
    Write-Host "正在关闭服务..." -ForegroundColor Yellow
    $stopped = 0
    foreach ($item in @(
        @{ Name = "看板服务器"; File = $ServerPidFile },
        @{ Name = "数据刷新循环"; File = $LoopPidFile },
        @{ Name = "OpenCode server"; File = $OpenCodePidFile }
    )) {
        if (Stop-PidFile $item.File) {
            Write-Host "  已停止 $($item.Name)" -ForegroundColor Green
            $stopped += 1
        }
    }
    Remove-Item (Join-Path $env:TEMP "sansheng_liubu_opencode_refresh.pid") -Force -ErrorAction SilentlyContinue
    if ($stopped -eq 0) {
        Write-Host "  没有发现由本脚本管理的服务" -ForegroundColor Yellow
    }
}

function Show-Logs([string]$Which) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    $files = switch ($Which) {
        "server" { @("server.log", "server.err.log") }
        "loop" { @("loop.log", "loop.err.log") }
        "opencode" { @("opencode.log", "opencode.err.log") }
        default { @("server.log", "server.err.log", "loop.log", "loop.err.log", "opencode.log", "opencode.err.log") }
    }
    $paths = $files | ForEach-Object { Join-Path $LogDir $_ } | Where-Object { Test-Path $_ }
    if (-not $paths) {
        Write-Host "暂无日志文件: $LogDir" -ForegroundColor Yellow
        return
    }
    Get-Content -Path $paths -Wait -Tail 80
}

switch ($Command) {
    "opencode" {
        powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $RepoDir "scripts\start_opencode.ps1")
    }
    "stop" { Stop-All }
    "status" { Show-Status }
    "logs" { Show-Logs $Target }
    default {
        Write-Host "用法: powershell -ExecutionPolicy Bypass -File .\edict.ps1 {opencode|stop|status|logs}" -ForegroundColor Yellow
        Write-Host "  opencode  启动 OpenCode 模式"
        Write-Host "  stop      停止脚本管理的服务"
        Write-Host "  status    查看状态"
        Write-Host "  logs      查看日志，可选 server|loop|opencode|all"
        exit 1
    }
}
