# Pluto+ (HamGeek 1 GB clone) brick recovery

Artifacts and notes for unbricking a **HamGeek ADALM-Pluto+ clone** that was
bricked by flashing 16-bit firmware onto its **32-bit / 1 GB** DDR. Full story
and root cause in [`../../docs/recovery/pluto-plus-brick-recovery.md`](../../docs/recovery/pluto-plus-brick-recovery.md);
register-level detail in [`DDR-FINDINGS.md`](DDR-FINDINGS.md).

**TL;DR** — the board has two Micron MT41K256M16 (D9SHD) DDR chips = 32-bit/1 GB.
Stock Pluto firmware assumes one chip (16-bit/512 MB), so its FSBL can't bring up
RAM. The fix is a 32-bit `ps7_init` (`openocd/ps7_init_32b2.tcl`). It was derived
and partially validated on macOS but **JTAG↔DDR is unreliable on macOS**, so
finish on **Linux**.

## Contents

```
openocd/
  ps7_init_32b2.tcl   patched 32-bit DDR init (THE deliverable)
  recover_uboot.cfg   init DDR + load u-boot.elf + run (target: u-boot/DFU)
  diag_ddr.cfg        isolated single-word DDR integrity test
  diag_map.cfg        multi-address bulk/aliasing test
fetch-bootstrap.sh    downloads stock ps7_init.tcl + u-boot.elf + bitstream
DDR-FINDINGS.md       exact register diffs + status
```

## Hardware setup

- Use the Pluto+ **debug** USB-C port — it's an FT4232H exposing JTAG (channel 0)
  + UART. The **OTG** port is for USB/DFU.
- `lsusb` should show `0403:6011` (FTDI FT4232H) for the debug port.

## Quick start on Ubuntu 22

```bash
sudo apt install -y openocd dfu-util libusb-1.0-0 unzip curl

cd deploy/pluto-plus-recovery
./fetch-bootstrap.sh                       # gets u-boot.elf + stock ps7_init.tcl
cd openocd

# 1) Sanity: JTAG + OCM + DDR single-word
BS_DIR=$PWD openocd -f diag_ddr.cfg

# 2) The real test: does DDR survive multi-address access?
BS_DIR=$PWD openocd -f diag_map.cfg
#    Expect each address to read back its own value. On Linux this should be
#    clean (it was NOT on macOS). If clean -> proceed.

# 3) Bring up u-boot in DDR and run it (watch UART on another channel)
BS_DIR=$PWD openocd -f recover_uboot.cfg
```

Capture UART while step 3 runs (115200 8N1), e.g.:
```bash
for d in /dev/ttyUSB1 /dev/ttyUSB2 /dev/ttyUSB3; do
  (stty -F $d 115200 raw -echo; cat $d) & done
```
A u-boot banner ⇒ DDR config is correct. From u-boot you can reflash QSPI
(`sf` / `dfu`) or set boot mode to SD.

## Preferred alternative on Linux: Xilinx tools

`openocd` re-implements `ps7_init` primitives by hand. Xilinx's own flow is more
reliable for Zynq DDR + QSPI:

```bash
# Vivado / Vitis (or the smaller Vivado Lab Edition) provides xsct + program_flash
xsct
  connect
  targets -set -filter {name =~ "*Cortex-A9*0"}
  rst -processor
  source ps7_init_32b2.tcl ; ps7_init ; ps7_post_config   # use the 32-bit init
  dow u-boot.elf
  con
# then in u-boot: reflash QSPI, or use program_flash to write a full boot image.
```

> If you can build/obtain a **32-bit, 1 GB, MT41K256M16** FSBL (Vivado PS7 DDR
> config, or HamGeek's original firmware), flashing that to QSPI is the clean,
> permanent fix and avoids all of the above.
