#!/usr/bin/env bash
# Fetch the Pluto+ JTAG bootstrap (ps7_init.tcl + u-boot.elf + system_top.bit)
# from the DeonMarais64 PlutoPlusSDR-FW v0.37+ release. These are the *stock
# 16-bit* bootstrap files; the 32-bit DDR variant (ps7_init_32b2.tcl) lives in
# ./openocd and is derived from this stock ps7_init.tcl (see DDR-FINDINGS.md).
#
# Usage:  ./fetch-bootstrap.sh        # downloads into ./openocd
set -euo pipefail
cd "$(dirname "$0")/openocd"

URL="https://github.com/DeonMarais64/PlutoPlusSDR-FW/releases/download/v0.37%2B/plutosdr-jtag-bootstrap-v0.37-dirty.zip"

echo "Downloading JTAG bootstrap..."
curl -fL --retry 3 -o jtag-bs.zip "$URL"
unzip -o jtag-bs.zip
# keep stock ps7_init.tcl alongside the patched 32-bit one
echo
echo "Files now present:"
ls -la u-boot.elf ps7_init.tcl ps7_init_32b2.tcl system_top.bit 2>/dev/null || true
echo
echo "Stock (16-bit) init  : ps7_init.tcl"
echo "Patched (32-bit) init: ps7_init_32b2.tcl"
echo "Next: BS_DIR=\$PWD openocd -f recover_uboot.cfg   (see ../README.md)"
