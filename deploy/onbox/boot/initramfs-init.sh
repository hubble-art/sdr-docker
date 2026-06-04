#!/bin/sh
# Fallback initramfs init: mount the SD ext4 rootfs and switch_root into it.
#
# Use this ONLY if you must reuse the firmware's ramdisk to boot (i.e. u-boot
# can't set root= directly). Inject this as /init into a custom initramfs
# (rebuild rootfs.cpio.gz with this as PID 1; it needs busybox + switch_root).
# The preferred path is uEnv.txt booting straight to ext4 (no ramdisk).
#
# On any failure it drops to a rescue shell so the device stays inspectable.

ROOTDEV=/dev/mmcblk0p2
NEWROOT=/newroot

/bin/mount -t proc proc /proc 2>/dev/null
/bin/mount -t sysfs sys /sys 2>/dev/null
/bin/mount -t devtmpfs dev /dev 2>/dev/null

rescue() {
    echo "[init] ERROR: $1"
    echo "[init] dropping to rescue shell"
    exec /bin/sh
}

mkdir -p "$NEWROOT"

# Wait for the SD rootfs partition to enumerate.
i=0
while [ ! -b "$ROOTDEV" ] && [ "$i" -lt 10 ]; do
    sleep 1
    i=$((i + 1))
done
[ -b "$ROOTDEV" ] || rescue "rootfs device $ROOTDEV not found"

/bin/mount -t ext4 -o rw "$ROOTDEV" "$NEWROOT" || rescue "mount $ROOTDEV failed"

if [ ! -x "$NEWROOT/sbin/init" ] && [ ! -e "$NEWROOT/usr/lib/systemd/systemd" ]; then
    rescue "no init found in $NEWROOT"
fi

# Carry the early mounts into the new root.
/bin/mount --move /proc "$NEWROOT/proc" 2>/dev/null
/bin/mount --move /sys  "$NEWROOT/sys"  2>/dev/null
/bin/mount --move /dev  "$NEWROOT/dev"  2>/dev/null

echo "[init] switching root to $ROOTDEV"
exec switch_root "$NEWROOT" /sbin/init
