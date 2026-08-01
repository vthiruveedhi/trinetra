#Requires -Version 5.1
param(
  [Parameter(Mandatory = $true)]
  [string]$IsoPath,
  [int]$MemoryMB = 4096,
  [int]$Cpus = 4
)

$ErrorActionPreference = "Stop"

function Find-Qemu {
  $names = @("qemu-system-x86_64", "${env:ProgramFiles}\qemu\qemu-system-x86_64.exe")
  foreach ($n in $names) {
    if (Get-Command $n -ErrorAction SilentlyContinue) { return (Get-Command $n).Source }
    if (Test-Path $n) { return $n }
  }
  throw "qemu-system-x86_64 not found. Install: winget install --id SoftwareFreedomConservancy.QEMU -e"
}

if (-not (Test-Path $IsoPath)) { throw "ISO not found: $IsoPath" }

$qemu = Find-Qemu
Write-Host "Starting QEMU with $IsoPath (live/install). Prefer VirtualBox for GUI installs."
& $qemu `
  -m $MemoryMB `
  -smp $Cpus `
  -cdrom $IsoPath `
  -boot d `
  -netdev user,id=net0,hostfwd=tcp::2222-:22 `
  -device e1000,netdev=net0 `
  -display gtk
