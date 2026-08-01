#Requires -Version 5.1
# Generates a simple test MP4 for FileVideoSource if ffmpeg is available.
param(
  [string]$OutPath = (Join-Path $PSScriptRoot "fixtures\sample_dining.mp4"),
  [int]$Seconds = 30
)

$ErrorActionPreference = "Stop"
$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not $ffmpeg) {
  Write-Warning "ffmpeg not found on PATH. Place any short MP4 at:`n  $OutPath"
  New-Item -ItemType Directory -Force -Path (Split-Path $OutPath) | Out-Null
  # Write a placeholder note
  Set-Content -Path (Join-Path (Split-Path $OutPath) "README.txt") -Value @"
Put sample_dining.mp4 here (royalty-free dining/CCTV-style floor video).
Or install ffmpeg and re-run Generate-TestPattern.ps1.
"@
  exit 0
}

New-Item -ItemType Directory -Force -Path (Split-Path $OutPath) | Out-Null
# Synthetic color bars + timer — good enough for pipeline smoke
& ffmpeg -y -f lavfi -i "testsrc=size=640x480:rate=5" -t $Seconds -c:v libx264 -pix_fmt yuv420p $OutPath
Write-Host "Wrote $OutPath"
