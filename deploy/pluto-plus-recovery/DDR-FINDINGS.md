# Pluto+ (HamGeek clone) DDR findings — register-level

## Hardware (confirmed by reading the chip markings)

- SoC: **Xilinx Zynq-7010** (XC7Z010), silicon ver field `0x3`.
- DDR: **two** Micron **MT41K256M16TW-107:P** chips (FBGA code `D9SHD`).
  - Each chip: 4Gb DDR3L, **256M × 16**, DDR3-1866 (-107), 1.35V → **512 MB each**.
  - Two in parallel ⇒ **32-bit data bus, 1 GB total**.
- This differs from a **standard ADALM-Pluto / reference Pluto+**, which uses a
  **single** x16 chip ⇒ **16-bit bus, 512 MB**.

## Why the board bricked

All public Pluto / Pluto+ firmware (ADI `plutosdr-fw`, `plutoplus`,
DeonMarais64) is built for the **16-bit / single-chip** layout. Flashing it
writes an **FSBL whose `ps7_init` configures DDR as 16-bit**. On this 32-bit
board the FSBL cannot bring up memory → boot hangs → LED solid, no USB, no UART.

The fix is an FSBL/`ps7_init` configured for **32-bit / 2-chip** DDR.

## The 32-bit `ps7_init` patch (stock `ps7_init.tcl` → `ps7_init_32b2.tcl`)

All edits are in the silicon-3.0 path (`ps7_ddr_init_data_3_0`, `ps7_mio_init_data_3_0`)
because `ps_version` returns `0x3` on this part. Register addresses are absolute.

### 1. DDRC data bus width — `0xF8006000` bits[3:2] (01=16-bit → 00=32-bit)
```
0xF8006000 0x0001FFFF 0x00000084  ->  0x00000080
0xF8006000 0x0001FFFF 0x00000085  ->  0x00000081
```

### 2. Enable upper PHY byte lanes DX2/DX3 — DATX8 GCR, bit0 = DXEN
```
0xF8006120 0x7FFFFFCF 0x40000000  ->  0x40000001   ; DX2 enable
0xF8006124 0x7FFFFFCF 0x40000000  ->  0x40000001   ; DX3 enable
; (0xF8006118 / 0xF800611C = DX0/DX1 already 0x40000001)
```

### 3. Power up the upper-lane DDR I/O buffers (DDRIOB, in SLCR)
The 16-bit config disables DATA1/DIFF1 (value 0x800) and clears their VREF bits.
```
0xF8000B4C 0x00000FFF 0x00000800  ->  0x00000672   ; DATA1 = match DATA0 (0xB48)
0xF8000B54 0x00000FFF 0x00000800  ->  0x00000674   ; DIFF1 = match DIFF0 (0xB50)
0xF8000B4C 0x00000180 0x00000000  ->  0x00000180   ; DATA1 VREF enable
0xF8000B54 0x00000180 0x00000000  ->  0x00000180   ; DIFF1 VREF enable
```

### 4. Give lanes 2/3 the same per-lane DQS/training seeds as lanes 0/1
Identical chips + symmetric routing ⇒ mirror lane0/1 values onto lane2/3.
```
0xF8006134 0x000FFFFF 0x00029000  ->  0x00028406   ; (match 0xF8006130)
0xF8006138 0x000FFFFF 0x00029000  ->  0x00028406
0xF800615C 0x000FFFFF 0x00000080  ->  0x00000086   ; (match 0xF8006158)
0xF8006160 0x000FFFFF 0x00000080  ->  0x00000086
0xF8006170 0x001FFFFF 0x000000F9  ->  0x000000F6   ; (match 0xF800616C)
0xF8006174 0x001FFFFF 0x000000F9  ->  0x000000F6
0xF8006184 0x000FFFFF 0x000000C0  ->  0x000000C6   ; (match 0xF8006180)
0xF8006188 0x000FFFFF 0x000000C0  ->  0x000000C6
```

## Status of the patch

- With all four edits, **isolated single-word** DDR writes/reads are bit-perfect.
- **Multi-address / bulk** access (e.g. `load_image` of u-boot, `verify_image`)
  still corrupts **over JTAG on macOS** — but the **same corruption also appears
  with the known-good stock 16-bit config**, which proves the dominant fault is
  the **JTAG↔DDR transfer path on macOS** (persistent `LIBUSB_ERROR_ACCESS`),
  *not* the config. On-chip OCM (`0xFFFF0000`) is always perfect; the global
  timer counts, so DDR3 training delays are not skipped.

Therefore the patch is **promising but unverified on hardware**. It must be
re-validated on Linux (reliable FTDI/libusb) and/or with Xilinx `xsct`.

## Open items before declaring victory

1. Re-run `diag_map.cfg` on Linux. If consecutive addresses come back clean,
   the 32-bit config is correct and `recover_uboot.cfg` should boot u-boot.
2. The DDR **address-map** registers (`0xF8006008/600C/6010`) were NOT changed.
   For full 1 GB correctness the byte→column mapping may need adjustment for the
   doubled width; low addresses (where u-boot lives) appeared usable, but verify.
3. The *correct* long-term fix is a Vivado-generated `ps7_init` / FSBL with the
   PS7 DDR configured as 32-bit, 1 GB, MT41K256M16 — or HamGeek's original FSBL.
