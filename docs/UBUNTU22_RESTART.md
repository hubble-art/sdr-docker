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

## 2a. Working with the STOCK `hubble-satnet-decoder` — what to patch

**`sdr-docker` does NOT fork the decoder.** It installs the stock package from
PyPI (`hubble-satnet-decoder>=1.1.1`) and **reconfigures it at runtime**. If you
clone the upstream decoder repo to work on it, you must know the following or
results will silently be wrong.

### (a) Version floor — correct synthesizer frequencies live in ≥ 1.1.1
`pyproject.toml` pins `hubble-satnet-decoder>=1.1.1`. **Do not** use an older
release: 1.1.1 carries the corrected `SYNTH_RES` (chipset symbol rates), namely
`ti=366.2119, nordic=488.28125, silabs=296.0, esp=400.0, atmosic=500.0`.
Earlier versions have wrong frequencies and will mis-decode. If you build/install
from the upstream repo, make sure the checkout is at **≥ v1.1.1**.

### (b) Mandatory runtime reconfiguration (the real "patch")
The decoder ships with default constants. `src/stream_web/config.py` (lines
~146–154) overrides them **at import time** to match this SDR front-end:

```python
import hubble_satnet_decoder.constants as _fdc
_fdc.CHANNEL_SPACING = 25_750.0
_fdc.DEVICE_CHANNEL_SPACING = {
    name: round(_fdc.CHANNEL_SPACING / sr) * sr
    for name, sr in _fdc.SYNTH_RES.items()
}
_fdc.configure(781_250)          # SAMPLE_RATE: 6.25 MHz / 8
```

Any standalone test of the **stock** decoder (outside `sdr-docker`) must
replicate this, or it will use different channelization / sample rate. The
decoder contract is: `decode_signal(iq) -> (packets, detections, attempts)` on a
**1 s complex64 chunk at 781.25 kS/s**; also `compute_spec_chunk`,
`get_chipset_stats`. Preamble detection is an **OpenCV template match**.

### (c) OpenCV dependency — the concrete patch point for on-box ARM
The decoder `import cv2` for preamble template matching. On `armhf`/Debian, PyPI
opencv wheels are unreliable. Order of preference:
1. apt `python3-opencv` (used in `deploy/onbox/Dockerfile.rootfs`);
2. if that fails on the chosen Debian release, **vendor a thin NumPy/SciPy
   replacement of just the template-match call site** upstream in the decoder
   (this is the one place you may need to actually edit the decoder source).
Host (x86) is fine with `opencv-python-headless` from pip.

### (d) Performance: C-ports go UPSTREAM in the decoder repo, not here
If the Zynq dual Cortex-A9 can't decode in real time (likely the main risk),
the plan (see `docs/design/2026-06-03-onbox-decoder.md` §5, §10) is:
1. **Profile first** (`decode_signal` on a representative 1 s chunk).
2. Loosen `DECODE_INTERVAL_S` as a cheap first lever.
3. Only then **C-port the dominant hot loop in `hubble-satnet-decoder` itself**
   (Cython/C extension), release a new version, and bump the pin here.
**Do not add C code to `sdr-docker`** — keep it pure-Python so both host and
on-box benefit and installs stay simple. So: yes, you will patch the *stock
decoder repo* for perf — but as upstream changes + a version bump, not a fork.

### (e) Summary for the next assistant
- Install stock decoder ≥ 1.1.1 from PyPI; don't fork for normal use.
- Always apply the `config.py` runtime reconfig (CHANNEL_SPACING + `configure()`).
- For on-box ARM: replace opencv with apt (or vendor the template-match site).
- For speed: profile, then C-port hot loops **in the decoder repo** + bump pin.

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
