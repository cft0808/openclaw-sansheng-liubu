#Requires -Version 5.1
$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$ocHome = Join-Path $env:USERPROFILE '.openclaw'
$ocCfg = Join-Path $ocHome 'openclaw.json'
$agents = @('taizi','zhongshu','menxia','shangshu','hubu','libu','bingbu','xingbu','gongbu','libu_hr','zaochao')

function Need($ok, $msg) { if (-not $ok) { throw $msg } }

Write-Host '[INFO] Edict installer for Windows' -ForegroundColor Cyan
Need (Get-Command openclaw -ErrorAction SilentlyContinue) 'openclaw not found in PATH'
$py = Get-Command python3 -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python -ErrorAction SilentlyContinue }
Need $py 'python/python3 not found in PATH'
Need (Test-Path $ocCfg) "openclaw config not found: $ocCfg"

$ts = Get-Date -Format 'yyyyMMdd-HHmmss'
Copy-Item $ocCfg "$ocCfg.bak.sansheng-$ts" -Force

foreach ($a in $agents) {
  $ws = Join-Path $ocHome "workspace-$a"
  New-Item -ItemType Directory -Path (Join-Path $ws 'skills') -Force | Out-Null

  $soulSrc = Join-Path $repo "agents\\$a\\SOUL.md"
  if (Test-Path $soulSrc) {
    $soul = (Get-Content $soulSrc -Raw) -replace '__REPO_DIR__', $repo
    Set-Content -Path (Join-Path $ws 'SOUL.md') -Value $soul -Encoding UTF8
  }

  $agentsMd = @(
    '# AGENTS.md',
    '',
    '1. Acknowledge task first.',
    '2. Return task id/result/evidence path/blocker.',
    '3. Ask shangshu for cross-agent delegation.'
  ) -join "`n"
  Set-Content -Path (Join-Path $ws 'AGENTS.md') -Value $agentsMd -Encoding UTF8
}

$cfg = Get-Content $ocCfg -Raw | ConvertFrom-Json
if (-not $cfg.agents) {
  $cfg | Add-Member -NotePropertyName agents -NotePropertyValue ([pscustomobject]@{})
}
if (-not ($cfg.agents.PSObject.Properties.Name -contains 'list')) {
  $cfg.agents | Add-Member -NotePropertyName list -NotePropertyValue @()
}

$allowMap = @{
  taizi = @('zhongshu')
  zhongshu = @('menxia','shangshu')
  menxia = @('shangshu','zhongshu')
  shangshu = @('zhongshu','menxia','hubu','libu','bingbu','xingbu','gongbu','libu_hr')
  hubu = @('shangshu')
  libu = @('shangshu')
  bingbu = @('shangshu')
  xingbu = @('shangshu')
  gongbu = @('shangshu')
  libu_hr = @('shangshu')
  zaochao = @()
}

$existing = @{}
foreach ($x in $cfg.agents.list) { $existing[$x.id] = $true }
foreach ($a in $agents) {
  if (-not $existing.ContainsKey($a)) {
    $cfg.agents.list += [pscustomobject]@{
      id = $a
      workspace = (Join-Path $ocHome "workspace-$a")
      subagents = [pscustomobject]@{ allowAgents = $allowMap[$a] }
    }
  }
}

if ($cfg.bindings) {
  foreach ($b in $cfg.bindings) {
    if ($b.match -and $b.match.PSObject.Properties.Name -contains 'pattern') {
      $b.match.PSObject.Properties.Remove('pattern')
    }
  }
}

$cfg | ConvertTo-Json -Depth 20 | Set-Content -Path $ocCfg -Encoding UTF8

$dataDir = Join-Path $repo 'data'
New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
foreach ($f in @('live_status.json','agent_config.json','model_change_log.json')) {
  $fp = Join-Path $dataDir $f
  if (-not (Test-Path $fp)) { '{}' | Set-Content -Path $fp -Encoding UTF8 }
}
'[]' | Set-Content -Path (Join-Path $dataDir 'pending_model_changes.json') -Encoding UTF8

foreach ($a in $agents) {
  $ws = Join-Path $ocHome "workspace-$a"
  foreach ($pair in @(@('data', (Join-Path $repo 'data')), @('scripts', (Join-Path $repo 'scripts')))) {
    $name = $pair[0]
    $src = $pair[1]
    $dst = Join-Path $ws $name

    if (Test-Path $dst) {
      $item = Get-Item $dst
      if (-not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        Rename-Item $dst "$dst.bak.$(Get-Date -Format yyyyMMdd-HHmmss)"
      } else {
        continue
      }
    }
    cmd /c mklink /J "$dst" "$src" | Out-Null
  }
}

try { openclaw config set tools.sessions.visibility all | Out-Null } catch {}

$pkg = Join-Path $repo 'edict\\frontend\\package.json'
if ((Get-Command node -ErrorAction SilentlyContinue) -and (Test-Path $pkg)) {
  Push-Location (Join-Path $repo 'edict\\frontend')
  try { npm install; npm run build } finally { Pop-Location }
}

Push-Location $repo
try {
  & $py.Source scripts/sync_agent_config.py
  & $py.Source scripts/sync_officials_stats.py
  & $py.Source scripts/refresh_live_data.py
} finally {
  Pop-Location
}

try { openclaw gateway restart | Out-Null } catch {}

Write-Host '[OK] install finished' -ForegroundColor Green
Write-Host 'Run: python dashboard/server.py'
Write-Host 'Open: http://127.0.0.1:7891'
