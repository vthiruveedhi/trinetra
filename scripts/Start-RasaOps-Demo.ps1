# Clean start of RasaOps L1 demo: edge agent + kiosk + optional live preview.
# Examples:
#   .\scripts\Start-RasaOps-Demo.ps1
#   .\scripts\Start-RasaOps-Demo.ps1 -Youtube "https://www.youtube.com/watch?v=0JGQo-vAgwQ" -Scene pub_bar
#   .\scripts\Start-RasaOps-Demo.ps1 -Youtube "https://www.youtube.com/watch?v=WSCyQJH_5TU" -Scene kitchen_line -NoPreview
param(
  [string]$Youtube = "https://www.youtube.com/watch?v=0JGQo-vAgwQ",
  [ValidateSet("auto", "pub_bar", "kitchen_line", "dining_restaurant")]
  [string]$Scene = "auto",
  [switch]$NoPreview,
  [switch]$NoCloud,
  [double]$AgentFps = 0.5,
  [double]$PreviewFps = 6,
  [int]$MaxHeight = 480,
  [int]$Port = 8090,
  [int]$CloudPort = 18080
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
  Write-Host "Missing .venv - create with: python -m venv .venv ; .\.venv\Scripts\pip install -e `".[dev,edge]`"" -ForegroundColor Red
  exit 1
}

Write-Host "Stopping previous RasaOps python processes..." -ForegroundColor Yellow
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -match 'rasaops_edge|run_edge_agent|run_live_view|run_webcam_dogfood' } |
  ForEach-Object {
    Write-Host "  kill PID $($_.ProcessId)"
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
Start-Sleep -Seconds 1

$cloudFlag = @()
if (-not $NoCloud) { $cloudFlag = @("--with-cloud", "--cloud-port", "$CloudPort") }

$agentArgs = @(
  "-m", "rasaops_edge.scripts.run_edge_agent",
  "--youtube", $Youtube,
  "--scene", $Scene,
  "--fps", "$AgentFps",
  "--port", "$Port",
  "--improve-minutes", "5"
) + $cloudFlag

Write-Host "Starting edge agent..." -ForegroundColor Cyan
Write-Host "  $Youtube  scene=$Scene  fps=$AgentFps"
$env:RASAOPS_ORT_THREADS = "4"
Start-Process -FilePath $py -ArgumentList $agentArgs -WorkingDirectory $Root -WindowStyle Minimized

if (-not $NoPreview) {
  $previewArgs = @(
    "-m", "rasaops_edge.scripts.run_live_view",
    "--youtube", $Youtube,
    "--scene", $Scene,
    "--fps", "$PreviewFps",
    "--max-height", "$MaxHeight",
    "--high-priority"
  )
  Write-Host "Starting live preview (HIGH priority, $PreviewFps fps)..." -ForegroundColor Cyan
  $env:RASAOPS_ORT_THREADS = "16"
  Start-Process -FilePath $py -ArgumentList $previewArgs -WorkingDirectory $Root
}

$ok = $false
for ($i = 0; $i -lt 40; $i++) {
  try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 2
    if ($r.StatusCode -eq 200) { $ok = $true; break }
  } catch {
    Start-Sleep -Seconds 1
  }
}

if ($ok) {
  Write-Host "Kiosk ready - opening browser" -ForegroundColor Green
  Start-Process "http://127.0.0.1:$Port/"
} else {
  Write-Host "Kiosk not ready yet - open http://127.0.0.1:$Port/ shortly" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "URLs:" -ForegroundColor Green
Write-Host "  Kiosk:   http://127.0.0.1:$Port/"
Write-Host "  Metrics: http://127.0.0.1:$Port/local/metrics"
Write-Host "  Scene:   http://127.0.0.1:$Port/local/scene"
if (-not $NoCloud) {
  Write-Host "  Cloud:   http://127.0.0.1:$CloudPort/health"
}
Write-Host "  Stream:  $Youtube"
Write-Host "  Scene:   $Scene"
