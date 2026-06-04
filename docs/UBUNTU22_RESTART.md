# Restarting this work on Ubuntu 22.04

This is the pickup guide for continuing the **on-box decoder** effort (and the
Pluto+ recovery) on a fresh Ubuntu 22.04 box, after development moved off macOS.
It assumes you cloned this fork (`hubble-art/sdr-docker`) and are on the
`feat/onbox-decoder` branch.

```bash
git clone https://github.com/hubble-art/sdr-docker.git
cd sdr-docker
git checkout feat/onbox-decoder
```

## 0. Where things stand

| Track | State |
|---|---|
| Host decoder + dashboard (`run_stream.py`) | Works; PASSED on x86 Linux per `.cursor/rules/project-context.mdc`. |
| On-box decoder (run decoder on the Pluto itself) | Design + scaffolding committed under `deploy/onbox/` and `docs/design/2026-06-03-onbox-decoder.md`. Not yet validated on hardware. |
| HamGeek Pluto+ unit | **Bricked** (32-bit/1 GB DDR vs 16-bit firmware). Recovery artifacts in `deploy/pluto-plus-recovery/`. See `docs/recovery/pluto-plus-brick-recovery.md`. |
| Decision | De-risk decoding on a **standard ADALM-Pluto** first (no SD slot — different on-box strategy), return to Pluto+ later. |

## 1. Host SDR toolchain (Ubuntu — the easy path)

On Linux these are packages, not from-source builds (contrast with macOS):

```bash
sudo apt update
sudo apt install -y \
  gnuradio gnuradio-dev gr-osmosdr \
  libiio-dev libiio-utils libad9361-dev \
  soapysdr-tools libsoapysdr-dev \
  cmake build-essential git python3-venv python3-pip \
  dfu-util openocd
```

SoapyPlutoSDR (the only from-source piece) — build + install:
```bash
git clone https://github.com/pothosware/SoapyPlutoSDR.git
cd SoapyPlutoSDR && mkdir build && cd build
cmake .. && make -j"$(nproc)" && sudo make install && sudo ldconfig
SoapySDRUtil --info        # should list the plutosdr module
```

Project venv (GNU Radio is system-installed, so use system site-packages):
```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -e ".[dev]"     # numpy is pinned >=1.26,<2 — do not relax (gnuradio needs numpy 1.x)
ruff check src/
```

Plug in a **standard ADALM-Pluto** (USB), then:
```bash
iio_info -u ip:192.168.2.1     # or `iio_info -s` to discover
python3 run_stream.py          # dashboard on http://localhost:8050
```

## 2. On-box decoder de-risking (the actual goal)

Read `docs/design/2026-06-03-onbox-decoder.md` first. The big open question is
whether the Zynq-7010 (dual Cortex-A9) can run the decoder fast enough.

Suggested order on Ubuntu:
1. **Estimate feasibility** — profile `hubble_satnet_decoder` on x86, then scale
   to A9. `qemu-user-static` lets you run the armhf build to sanity-check, not
   to benchmark.
   ```bash
   sudo apt install -y qemu-user-static binfmt-support \
        gcc-arm-linux-gnueabihf g++-arm-linux-gnueabihf
   ```
2. **Cross-compile any C hot paths** for `armhf` (Zynq is `arm-linux-gnueabihf`).
3. **Pick an on-box rootfs strategy** (standard Pluto has only ~32 MB flash, no
   SD): NFS-root, USB-gadget storage, or a slim tmpfs Python env. (The committed
   `deploy/onbox/` SD/chroot approach assumes a Pluto+ SD slot.)
4. Deploy over the Pluto USB-net link (`192.168.2.1`), launch via
   `/mnt/jffs2/autorun.sh` (`deploy/onbox/autorun.sh`).

> Reminder: keep `numpy>=1.26,<2`. GNU Radio (apt + Homebrew) is built against
> NumPy 1.x; relaxing this breaks `import gnuradio` with `_ARRAY_API not found`.

## 3. Pluto+ recovery (optional, when you want the 1 GB unit back)

Everything is in `deploy/pluto-plus-recovery/` with its own README. Summary:

```bash
sudo apt install -y openocd dfu-util libusb-1.0-0 unzip curl
cd deploy/pluto-plus-recovery && ./fetch-bootstrap.sh && cd openocd
BS_DIR=$PWD openocd -f diag_map.cfg        # should be CLEAN on Linux (was not on macOS)
BS_DIR=$PWD openocd -f recover_uboot.cfg   # bring up u-boot; watch UART @115200
```
The permanent fix is a **32-bit / 1 GB / MT41K256M16 FSBL** (Vivado PS7 DDR
config or HamGeek's firmware) flashed to QSPI. See
`deploy/pluto-plus-recovery/DDR-FINDINGS.md`.

### udev (non-root USB/JTAG access)
```bash
# FTDI debug/JTAG + Pluto USB
echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="0403", MODE="0666"' | sudo tee /etc/udev/rules.d/52-ftdi.rules
echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="0456", MODE="0666"' | sudo tee /etc/udev/rules.d/53-pluto.rules
sudo udevadm control --reload && sudo udevadm trigger
```

## 4. Key references in this repo

- `.cursor/rules/project-context.mdc` — architecture, threads, known issues.
- `.cursor/rules/native-sdr-setup.mdc` — SDR setup pitfalls (mostly macOS).
- `.cursor/rules/sdr-gateway-plan.md` — the on-box gateway plan.
- `docs/design/2026-06-03-onbox-decoder.md` — on-box decoder design.
- `docs/recovery/pluto-plus-brick-recovery.md` — the brick story + root cause.
- `deploy/pluto-plus-recovery/` — JTAG recovery artifacts (32-bit `ps7_init`).
