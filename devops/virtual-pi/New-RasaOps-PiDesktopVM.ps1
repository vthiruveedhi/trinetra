#Requires -Version 5.1
<#
.SYNOPSIS
  Create a VirtualBox VM for Raspberry Pi Desktop (x86) for RasaOps edge simulation.

.PARAMETER IsoPath
  Path to Raspberry Pi Desktop ISO.

.PARAMETER VmName
  VirtualBox VM name.

.PARAMETER MemoryMB
  RAM in MB (default 4096).

.PARAMETER Cpus
  vCPU count (default 4).

.PARAMETER DiskGB
  VDI size in GB (default 32).
#>
param(
  [Parameter(Mandatory = $true)]
  [string]$IsoPath,

  [string]$VmName = "RasaOps-PiDesktop",
  [int]$MemoryMB = 4096,
  [int]$Cpus = 4,
  [int]$DiskGB = 32
)

$ErrorActionPreference = "Stop"

function Find-VBoxManage {
  $candidates = @(
    "VBoxManage",
    "${env:ProgramFiles}\Oracle\VirtualBox\VBoxManage.exe",
    "${env:ProgramFiles(x86)}\Oracle\VirtualBox\VBoxManage.exe"
  )
  foreach ($c in $candidates) {
    if (Get-Command $c -ErrorAction SilentlyContinue) { return (Get-Command $c).Source }
    if (Test-Path $c) { return $c }
  }
  throw "VBoxManage not found. Install VirtualBox: winget install --id Oracle.VirtualBox -e"
}

if (-not (Test-Path $IsoPath)) {
  throw "ISO not found: $IsoPath`nDownload from https://www.raspberrypi.com/software/raspberry-pi-desktop/"
}

$vbox = Find-VBoxManage
Write-Host "Using VBoxManage: $vbox"

$vmDir = Join-Path $PSScriptRoot "vms\$VmName"
New-Item -ItemType Directory -Force -Path $vmDir | Out-Null
$diskPath = Join-Path $vmDir "$VmName.vdi"

# Remove existing VM if present (prompt)
$existing = & $vbox list vms 2>$null | Select-String -Pattern "`"$VmName`""
if ($existing) {
  Write-Warning "VM '$VmName' already exists. Unregistering (disk kept if detach fails)..."
  & $vbox controlvm $VmName poweroff 2>$null
  Start-Sleep -Seconds 2
  & $vbox unregistervm $VmName --delete 2>$null
}

Write-Host "Creating VM $VmName ..."
& $vbox createvm --name $VmName --ostype "Debian_64" --register --basefolder $vmDir | Out-Null
& $vbox modifyvm $VmName `
  --memory $MemoryMB `
  --cpus $Cpus `
  --vram 128 `
  --graphicscontroller vmsvga `
  --nic1 nat `
  --audio none `
  --clipboard-mode bidirectional `
  --draganddrop bidirectional

# NAT port forwards: guest services -> host ports (edge talks to host Docker via 10.0.2.2)
$forwards = @(
  @{ Name = "ssh";  Host = 2222; Guest = 22 },
  @{ Name = "http"; Host = 18080; Guest = 8080 },
  @{ Name = "kiosk"; Host = 13000; Guest = 3000 }
)
foreach ($f in $forwards) {
  & $vbox modifyvm $VmName --natpf1 "$($f.Name),tcp,,$($f.Host),,$($f.Guest)"
}

if (-not (Test-Path $diskPath)) {
  & $vbox createmedium disk --filename $diskPath --size ($DiskGB * 1024) --format VDI | Out-Null
}

& $vbox storagectl $VmName --name "SATA" --add sata --controller IntelAhci
& $vbox storageattach $VmName --storagectl "SATA" --port 0 --device 0 --type hdd --medium $diskPath
& $vbox storagectl $VmName --name "IDE" --add ide
& $vbox storageattach $VmName --storagectl "IDE" --port 0 --device 0 --type dvddrive --medium $IsoPath

# Shared folder to monorepo (auto-mount after Guest Additions)
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
& $vbox sharedfolder add $VmName --name "restaurant-ops-ai" --hostpath $repoRoot --automount

Write-Host ""
Write-Host "VM created successfully."
Write-Host "  Name:     $VmName"
Write-Host "  Memory:   ${MemoryMB} MB, CPUs: $Cpus, Disk: ${DiskGB} GB"
Write-Host "  ISO:      $IsoPath"
Write-Host "  Shared:   $repoRoot -> restaurant-ops-ai"
Write-Host "  SSH NAT:  localhost:2222 -> guest:22 (after openssh-server install)"
Write-Host ""
Write-Host "Start with:"
Write-Host "  & `"$vbox`" startvm $VmName"
Write-Host ""
Write-Host "After OS install: install Guest Additions, then:"
Write-Host "  cd /media/sf_restaurant-ops-ai"
Write-Host "  See devops/virtual-pi/README.md for env vars and mock camera."
