# Pluto+ brick recovery — full write-up

_Context: this happened while trying to run the decoder **on-box** (see
`docs/design/2026-06-03-onbox-decoder.md` and `.cursor/rules/sdr-gateway-plan.md`).
The HamGeek Pluto+ was bricked during firmware experiments; this documents the
root cause and how to finish recovery on Linux._

## What happened (timeline)

1. Goal: deploy the Hubble decoder onto a **HamGeek ADALM-Pluto+ clone** via a
   Debian rootfs on its microSD card, launched from a persistent `autorun.sh`.
2. The SD card wasn't enumerating in the stock OS (MMC nodes `disabled` in the
   device tree). Attempts to enable it via U-Boot env `fdt set` didn't persist.
3. To get newer SD support we flashed **plutoplus v1.33** firmware
   (`boot.frm` + `pluto.frm`) via the mass-storage method. After reboot the
   device went dark: **LED1 solid, no USB enumeration, no UART, no DFU.** Bricked.
4. Recovery via DFU failed (bootloader too damaged to enter DFU), so we went to
   **JTAG** through the debug port (FT4232H, `0403:6011`) with OpenOCD.

## Root cause (the important finding)

JTAG worked and on-chip OCM read/write was perfect, but **every** `ps7_init`
config produced broken DDR. Reading the markings off the two memory chips
explained it:

- The board has **two Micron MT41K256M16TW-107 (D9SHD)** DDR3 chips
  = **32-bit bus, 1 GB total**.
- A standard ADALM-Pluto / reference Pluto+ has **one** x16 chip
  = **16-bit bus, 512 MB**.
- All public firmware (ADI, plutoplus, DeonMarais64) is **16-bit**. Its FSBL
  configures DDR as 16-bit, which **cannot initialise this 32-bit board** → the
  bootloader hangs before UART/USB come up. That is the brick.

So this was never a software/QSPI-image bug — it's a **DDR width mismatch** baked
into the FSBL.

## What was derived

A 32-bit `ps7_init` ([`deploy/pluto-plus-recovery/openocd/ps7_init_32b2.tcl`](../../deploy/pluto-plus-recovery/openocd/ps7_init_32b2.tcl)),
patched from the stock 16-bit one. The exact register diffs (bus width, DX2/DX3
byte-lane enables, upper-lane DDR I/O buffers, per-lane DQS seeds) are in
[`deploy/pluto-plus-recovery/DDR-FINDINGS.md`](../../deploy/pluto-plus-recovery/DDR-FINDINGS.md).

Result: isolated single-word DDR access became bit-perfect; **bulk/multi-address
access still corrupted over JTAG**. Crucially the **stock 16-bit config corrupted
the same way** — and OCM was always fine and the A9 global timer counted (training
delays not skipped) — which points the finger at the **JTAG↔DDR transfer path on
macOS** (persistent `LIBUSB_ERROR_ACCESS` from the FTDI/libusb conflict), not the
DDR config itself.

## Why we stopped on macOS

macOS makes three things fight us at once, all documented in
`.cursor/rules/native-sdr-setup.mdc`:
- FTDI/libusb access is unreliable for sustained JTAG↔DDR transfers.
- No native Xilinx tooling path that "just works".
- The host SDR libs (libiio/libad9361/SoapyPlutoSDR) need from-source builds.

The decision was to **continue on Ubuntu 22**, where FTDI/JTAG, `dfu-util`,
Xilinx tools, and the SDR libraries are all first-class — and which matches the
project's Ubuntu-based Docker target.

## How to finish on Ubuntu 22

See [`deploy/pluto-plus-recovery/README.md`](../../deploy/pluto-plus-recovery/README.md).
Order of preference:

1. **Re-validate the 32-bit init over reliable JTAG.** Run `diag_map.cfg`; if
   multi-address reads are clean, run `recover_uboot.cfg` and watch UART for a
   u-boot banner. From u-boot, reflash QSPI or switch to SD boot.
2. **Use Xilinx `xsct` / `program_flash`** (Vivado / Vitis / Vivado Lab Edition)
   — the proper Zynq JTAG-DDR + QSPI flow. Use `ps7_init_32b2.tcl`.
3. **Get a correct FSBL** for 32-bit / 1 GB / MT41K256M16 (Vivado PS7 DDR config
   or HamGeek's original firmware) and flash it to QSPI. This is the permanent fix.

## Lessons / guardrails

- **Before flashing any Pluto firmware, read the DDR chip markings.** Two chips
  ⇒ 32-bit ⇒ stock firmware will brick it.
- The Pluto **debug** port can back-power the board, so unplugging only OTG does
  not power-cycle the Zynq. Pull **both** cables for a true cold boot.
- OCM-only success does **not** prove DDR works; always test multi-address DDR.
- Standard ADALM-Pluto has **no SD slot** — the on-box SD/chroot plan needs a
  Pluto+ (or an alternative rootfs strategy). De-risk decoding first, then return
  to Pluto+.
