#!/usr/bin/env bash
# Build a flashable SD-card image for the chroot deployment.
#
# Consumes deploy/onbox/out/rootfs.tar (from build-rootfs.sh) and produces
# deploy/onbox/out/sdcard.img: an MBR disk image with a single ext4 partition
# (label sdrroot) populated with the Debian armhf rootfs. Flash it to the card,
# then move the card to the Pluto+ and install autorun.sh (see README).
#
# Uses `mke2fs -d` to populate ext4 directly — no loop devices or privileged
# mounts — so it runs in a plain Docker container (native arch; no emulation).
#
# Usage:
#   deploy/onbox/build-sdimg.sh [PART_SIZE_MiB]   # default 6144 (6 GiB)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="$SCRIPT_DIR/out"
PART_MIB="${1:-6144}"

[ -f "$OUT_DIR/rootfs.tar" ] || { echo "ERROR: missing $OUT_DIR/rootfs.tar (run build-rootfs.sh first)" >&2; exit 1; }

echo "[build-sdimg] building sdcard.img (ext4 partition ${PART_MIB} MiB)..."
docker run --rm -v "$OUT_DIR:/out" -e PART_MIB="$PART_MIB" debian:trixie-slim bash -euc '
  apt-get update -qq >/dev/null && apt-get install -y -qq e2fsprogs parted >/dev/null
  mkdir -p /work/rootfs
  echo "  extracting rootfs..."
  tar -xf /out/rootfs.tar -C /work/rootfs
  echo "  creating ext4 (${PART_MIB} MiB)..."
  mke2fs -q -t ext4 -L sdrroot -d /work/rootfs /work/root.ext4 "${PART_MIB}M"
  DISK_MIB=$((PART_MIB + 1))
  truncate -s "${DISK_MIB}M" /out/sdcard.img
  parted -s /out/sdcard.img mklabel msdos
  parted -s /out/sdcard.img mkpart primary ext4 1MiB 100%
  dd if=/work/root.ext4 of=/out/sdcard.img bs=1M seek=1 conv=notrunc status=none
  rm -f /work/root.ext4
'
SIZE="$(du -h "$OUT_DIR/sdcard.img" | cut -f1)"
echo "[build-sdimg] done: $OUT_DIR/sdcard.img ($SIZE)"
echo
echo "Flash it (macOS — verify the disk first!):"
echo "  diskutil list                       # confirm the SD is /dev/disk5"
echo "  diskutil unmountDisk /dev/disk5"
echo "  sudo dd if=$OUT_DIR/sdcard.img of=/dev/rdisk5 bs=4m && sync"
echo "Then move the card to the Pluto+ and install autorun.sh (see README)."
