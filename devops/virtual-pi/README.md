# Virtual Raspberry Pi environment (Windows PC)

**Purpose:** Logic and integration simulator for RasaOps edge software.  
**Not a substitute for:** CSI camera, Pi 5 thermal behavior, real RTSP jitter, ARM-native wheels under load, or kiosk touch performance.

| Path | When to use |
|------|-------------|
| **Host Docker Compose** (`../docker-compose.yml`) | **Primary** daily DevEx |
| **VirtualBox + Raspberry Pi Desktop (x86)** | Optional Debian-like desktop / kiosk UX |
| **QEMU aarch64** | Occasional ARM wheel import checks |
| **Real Pi 5** | Hardware Exit Criteria (L2) before pilot OTA |

---

## Prerequisites

- Windows 10/11 with **virtualization enabled** (BIOS + Windows Features: Hyper-V *or* VirtualBox/Hypervisor)
- ~20 GB free disk for images
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (already recommended for compose)

### Install VirtualBox (recommended for Pi Desktop)

```powershell
# Run PowerShell as Administrator if needed
winget install --id Oracle.VirtualBox -e
```

### Install QEMU (optional ARM)

```powershell
winget install --id SoftwareFreedomConservancy.QEMU -e
```

Or use WSL2 + `sudo apt install qemu-system-arm qemu-system-aarch64`.

---

## Option A — VirtualBox + Raspberry Pi Desktop (x86)

### 1. Download image

Official: [Raspberry Pi Desktop for PC and Mac](https://www.raspberrypi.com/software/raspberry-pi-desktop/)

Save ISO under (example):

```text
C:\Users\tvikr\Downloads\rpd-debian.iso
```

### 2. Create VM (scripted)

```powershell
cd C:\Users\tvikr\restaurant-ops-ai\devops\virtual-pi
.\New-RasaOps-PiDesktopVM.ps1 -IsoPath "C:\Users\tvikr\Downloads\rpd-debian.iso"
```

Defaults: **4 vCPU, 4096 MB RAM, 32 GB VDI**, NAT + host-only optional.

### 3. Install OS

Boot the ISO, install Raspberry Pi Desktop to the virtual disk, reboot, remove ISO.

### 4. Guest setup

Inside the VM:

```bash
sudo apt update
sudo apt install -y git python3-pip python3-venv curl ca-certificates
# Optional: Docker-in-VM only if you want full compose inside guest (heavy)
```

Clone monorepo (from host share or git):

```bash
# Shared folder example (VirtualBox Guest Additions)
# Host: C:\Users\tvikr\restaurant-ops-ai  -> Guest: /media/sf_restaurant-ops-ai
cd /media/sf_restaurant-ops-ai
python3 -m venv .venv
source .venv/bin/activate
pip install -e edge/ -e shared/python/rasaops_shared  # when packages exist
```

### 5. Mock camera

Use sample video or synthetic frames — **not** CSI:

```bash
export RASAOPS_CAPTURE_SOURCE=file
export RASAOPS_VIDEO_PATH=/media/sf_restaurant-ops-ai/devops/virtual-pi/fixtures/sample_dining.mp4
export RASAOPS_ZONES_PATH=/media/sf_restaurant-ops-ai/shared/schemas/examples/zones.v1.sample.json
```

Place a short royalty-free dining-floor clip at `fixtures/sample_dining.mp4`, or generate a test pattern:

```powershell
# From host (requires ffmpeg)
.\Generate-TestPattern.ps1
```

### 6. Point edge at host cloud stubs

If cloud services run on the Windows host via Docker Compose, use VirtualBox **NAT** port forwards (script sets 5432, 6379, 1883, 4222, 9000, 8080) or host-only adapter IP.

```bash
export RASAOPS_CLOUD_BASE_URL=http://10.0.2.2:8080   # VirtualBox NAT gateway to host
export RASAOPS_MQTT_HOST=10.0.2.2
export RASAOPS_NATS_URL=nats://10.0.2.2:4222
```

---

## Option B — QEMU (x86 quick / aarch64 advanced)

### x86_64 Pi Desktop-like (simpler)

Prefer VirtualBox for GUI. QEMU headless:

```powershell
.\Start-Qemu-PiDesktop.ps1 -IsoPath "C:\Users\tvikr\Downloads\rpd-debian.iso"
```

### aarch64 Raspberry Pi OS (closer to real Pi OS)

1. Download [Raspberry Pi OS Lite 64-bit](https://www.raspberrypi.com/software/operating-systems/) image.
2. Expand `.img` and run:

```powershell
.\Start-Qemu-Aarch64.ps1 -ImagePath "C:\Users\tvikr\Downloads\raspios-lite-arm64.img"
```

Expect slow GUI; use for **wheel import / apt package** smoke tests only.

---

## What to validate in the VM vs real Pi

| Check | Virtual Pi | Real Pi 5 |
|-------|------------|-----------|
| State machines, queue, scrubber goldens | Yes | Yes |
| Compose / API contracts | Prefer host Docker | Optional |
| ONNX Runtime aarch64 wheels | QEMU/ARM CI | Yes |
| CSI open, thermal throttle, power-loss | No | **Required L2** |
| Multi-cam RTSP soak | Limited | **Required for multi-cam SKU** |
| Touch kiosk latency | Approximate | Required |

See: `docs/runbooks/hardware-exit-criteria.md`.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| VT-x/AMD-V not available | Enable virtualization in BIOS; disable conflicting Hyper-V if VirtualBox fails |
| No network from guest to host Docker | Use `10.0.2.2` (VBox NAT) or bridge adapter; ensure Windows firewall allows Docker ports |
| ISO won't boot | Confirm 64-bit ISO; enable EFI if required by image |
| Slow inference in VM | Expected — use lighter mock detector or run inference on host |

---

## Related

- Architecture: `docs/architecture/RasaOps-System-Architecture.md` § Local Development / Virtual Pi
- Compose: `devops/docker-compose.yml`
- Zones fixture: `shared/schemas/examples/zones.v1.sample.json`
