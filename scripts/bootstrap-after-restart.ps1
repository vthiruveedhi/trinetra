#Requires -Version 5.1
<#
.SYNOPSIS
  Rehydrate RasaOps dev environment after a Windows reboot.

.EXAMPLE
  cd C:\Users\tvikr\restaurant-ops-ai
  .\scripts\bootstrap-after-restart.ps1
#>

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

Write-Host "=== RasaOps bootstrap (after restart) ===" -ForegroundColor Cyan
Write-Host "Repo: $RepoRoot"
Write-Host ""

function Test-Cmd([string]$Name) {
  return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

$py = $null
foreach ($c in @("python", "py")) {
  if (Test-Cmd $c) { $py = $c; break }
}
if (-not $py) {
  throw "Python not found on PATH. Install Python 3.11+ and re-run."
}

$venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$venvPip = Join-Path $RepoRoot ".venv\Scripts\pip.exe"

if (-not (Test-Path $venvPython)) {
  Write-Host "Creating .venv ..." -ForegroundColor Yellow
  & $py -m venv (Join-Path $RepoRoot ".venv")
}

Write-Host "Upgrading pip / installing package [dev,edge] ..."
& $venvPython -m pip install --upgrade pip -q
& $venvPip install -e ".[dev,edge]" -q

$tiny = Join-Path $RepoRoot "edge\models\tiny_yolo_like_320.onnx"
if (-not (Test-Path $tiny)) {
  Write-Host "Generating tiny ONNX smoke model ..."
  & $venvPython (Join-Path $RepoRoot "edge\scripts\make_tiny_onnx.py")
} else {
  Write-Host "Tiny ONNX present: $tiny"
}

Write-Host ""
Write-Host "Running pytest ..." -ForegroundColor Cyan
& $venvPython -m pytest -q
if ($LASTEXITCODE -ne 0) {
  Write-Host "FAIL: pytest" -ForegroundColor Red
  exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Running pipeline smoke ..." -ForegroundColor Cyan
& $venvPython -m rasaops_edge.scripts.run_pipeline_smoke --frames 12
if ($LASTEXITCODE -ne 0) {
  Write-Host "FAIL: pipeline smoke" -ForegroundColor Red
  exit $LASTEXITCODE
}

Write-Host ""
Write-Host "=== Optional tooling ===" -ForegroundColor Cyan

if (Test-Cmd "docker") {
  $dockerOk = $false
  $prevEap = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  docker info 1>$null 2>$null
  if ($LASTEXITCODE -eq 0) { $dockerOk = $true }
  $ErrorActionPreference = $prevEap

  if ($dockerOk) {
    Write-Host "Docker: daemon reachable"
    Write-Host "  Start stubs: docker compose -f devops/docker-compose.yml up -d"
  } else {
    Write-Host "Docker: CLI present but daemon not running - start Docker Desktop" -ForegroundColor Yellow
  }
} else {
  Write-Host "Docker: not installed"
}

$vboxCandidates = @(
  (Join-Path ${env:ProgramFiles} "Oracle\VirtualBox\VBoxManage.exe"),
  (Join-Path ${env:ProgramFiles(x86)} "Oracle\VirtualBox\VBoxManage.exe")
)
$vbox = $vboxCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if ($vbox) {
  Write-Host "VirtualBox: $vbox"
} else {
  Write-Host "VirtualBox: not installed (optional - see devops/virtual-pi/README.md)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== Next work (from RESUME.md) ===" -ForegroundColor Green
Write-Host "  1. PR-06  edge/rasaops_edge/privacy/  fail-closed scrubber"
Write-Host "  2. PR-07  edge/rasaops_edge/queue/    SQLite offline queue"
Write-Host "  3. PR-08  edge/rasaops_edge/dashboard_api/"
Write-Host ""
Write-Host "Paste prompt from RESUME.md into Grok to continue."
Write-Host "bootstrap-after-restart: OK" -ForegroundColor Green
exit 0
