#!/usr/bin/env bash
# Provision a microSD card for the on-box decoder (Stage 3).
#
# Layout:
#   p1  FAT32  256 MB   boot  (BOOT.bin, kernel, devicetree, uEnv.txt)
#   p2  ext4   rest     rootfs (extracted from deploy/onbox/out/rootfs.tar)
#
# SAFETY: this ERASES the target device. It is DRY-RUN by default and prints
# the commands it *would* run. Pass --commit to actually execute. It refuses
# non-removable devices and anything that looks like a system disk.
#
# Run on Linux (needs parted, mkfs.vfat, mkfs.ext4). On macOS, ext4 is not
# native — partition with `diskutil` and create ext4 via a privileged Linux
# container or a Linux host. See deploy/onbox/README.md.
#
# Usage:
#   deploy/onbox/provision-sd.sh /dev/sdX            # dry run (default)
#   deploy/onbox/provision-sd.sh /dev/sdX --commit   # actually write

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOTFS_TAR="$SCRIPT_DIR/out/rootfs.tar"
BOOT_DIR="$SCRIPT_DIR/boot"

DEV="${1:-}"
COMMIT="no"
[ "${2:-}" = "--commit" ] && COMMIT="yes"

die() { echo "ERROR: $*" >&2; exit 1; }

[ -n "$DEV" ] || die "usage: $0 /dev/sdX [--commit]"
[ -b "$DEV" ] || die "$DEV is not a block device"
[ -f "$ROOTFS_TAR" ] || die "missing $ROOTFS_TAR (run build-rootfs.sh first)"

BASE="$(basename "$DEV")"
# Refuse non-removable devices (guards against wiping an internal disk).
if [ -r "/sys/block/$BASE/removable" ]; then
    [ "$(cat "/sys/block/$BASE/removable")" = "1" ] || die "$DEV is not removable; refusing"
else
    die "cannot verify $DEV is removable (no /sys/block/$BASE/removable); refusing"
fi
# Refuse the disk that hosts the running root filesystem.
ROOT_SRC="$(findmnt -no SOURCE / 2>/dev/null || true)"
case "$ROOT_SRC" in
    *"$BASE"*) die "$DEV appears to host the running root fs ($ROOT_SRC); refusing" ;;
esac

SIZE_BYTES="$(blockdev --getsize64 "$DEV" 2>/dev/null || echo 0)"
SIZE_GB=$((SIZE_BYTES / 1000000000))
echo "Target: $DEV (${SIZE_GB} GB, removable)"
[ "$SIZE_GB" -le 0 ] && die "could not read device size"
[ "$SIZE_GB" -gt 256 ] && die "device is ${SIZE_GB} GB (>256 GB) — too large to be the SD card; refusing"

P1="${DEV}1"; P2="${DEV}2"
[ -b "${DEV}p1" ] && { P1="${DEV}p1"; P2="${DEV}p2"; }  # mmcblk/nvme naming

run() {
    echo "  + $*"
    if [ "$COMMIT" = "yes" ]; then eval "$@"; fi
}

echo
if [ "$COMMIT" = "yes" ]; then
    echo "*** COMMIT MODE — $DEV WILL BE ERASED ***"
    read -r -p "Type the device name ($DEV) to confirm: " confirm
    [ "$confirm" = "$DEV" ] || die "confirmation mismatch; aborting"
else
    echo "--- DRY RUN (pass --commit to execute) ---"
fi
echo

run "umount ${DEV}* 2>/dev/null || true"
run "wipefs -a $DEV"
run "parted -s $DEV mklabel msdos"
run "parted -s $DEV mkpart primary fat32 1MiB 257MiB"
run "parted -s $DEV mkpart primary ext4 257MiB 100%"
run "parted -s $DEV set 1 boot on"
run "mkfs.vfat -F 32 -n BOOT $P1"
run "mkfs.ext4 -F -L rootfs $P2"

run "mkdir -p /mnt/sdboot /mnt/sdroot"
run "mount $P1 /mnt/sdboot"
run "mount $P2 /mnt/sdroot"
run "cp -v $BOOT_DIR/uEnv.txt /mnt/sdboot/ 2>/dev/null || true"
echo "  (also copy BOOT.bin + kernel(uImage) + devicetree to /mnt/sdboot — Stage 2 artifacts)"
run "tar -xf $ROOTFS_TAR -C /mnt/sdroot"
echo "  (copy the firmware's matching /lib/modules/<kver> into /mnt/sdroot/lib/modules — Stage 2)"
run "cp -v $SCRIPT_DIR/sdr-gateway.service /mnt/sdroot/etc/systemd/system/ 2>/dev/null || true"
run "ln -sf /etc/systemd/system/sdr-gateway.service /mnt/sdroot/etc/systemd/system/multi-user.target.wants/sdr-gateway.service"
run "sync"
run "umount /mnt/sdboot /mnt/sdroot"

echo
echo "Done${COMMIT:+ (dry run)}. Remaining manual Stage 2 items: BOOT.bin + kernel + modules."
