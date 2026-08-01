#Requires -Version 5.1
<#
.SYNOPSIS
  Boot a Raspberry Pi OS aarch64 disk image under QEMU (advanced / slow).
  Requires kernel+initrd extract or use of community qemu-rpi images — see README.
#>
param(
  [Parameter(Mandatory = $true)]
  [string]$ImagePath,
  [string]$KernelPath = "",
  [string]$DtbPath = "",
  [int]$MemoryMB = 2048
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $ImagePath)) { throw "Image not found: $ImagePath" }

$qemu = $null
foreach ($n in @("qemu-system-aarch64", "${env:ProgramFiles}\qemu\qemu-system-aarch64.exe")) {
  if (Get-Command $n -ErrorAction SilentlyContinue) { $qemu = (Get-Command $n).Source; break }
  if (Test-Path $n) { $qemu = $n; break }
}
if (-not $qemu) {
  throw "qemu-system-aarch64 not found. Install QEMU via winget or use WSL2."
}

if (-not $KernelPath -or -not (Test-Path $KernelPath)) {
  Write-Host @"
Aarch64 Pi OS under QEMU needs a kernel + DTB (or a pre-bundled qemu image).

Recommended for RasaOps:
  1. Use VirtualBox Pi Desktop for GUI simulation.
  2. Use host Docker Compose for cloud + edge file capture on Windows.
  3. Use a real Pi 5 for L2 hardware exit.

If you have extracted kernel (vmlinuz) and dtb from the image, re-run:
  .\Start-Qemu-Aarch64.ps1 -ImagePath ... -KernelPath path\to\vmlinuz -DtbPath path\to.dtb
"@
  exit 1
}

& $qemu `
  -M virt `
  -cpu cortex-a72 `
  -m $MemoryMB `
  -kernel $KernelPath `
  -dtb $DtbPath `
  -drive if=none,file=$ImagePath,format=raw,id=hd0 `
  -device virtio-blk-device,drive=hd0 `
  -netdev user,id=net0,hostfwd=tcp::2222-:22 `
  -device virtio-net-device,netdev=net0 `
  -nographic `
  -append "console=ttyAMA0 root=/dev/vda2 rw"
