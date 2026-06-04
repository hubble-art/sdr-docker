#!/bin/sh
# Persistent on-box launcher for the SDR gateway (chroot deployment).
#
# Install to /mnt/jffs2/autorun.sh on the Pluto+ (persistent flash). The stock
# firmware's /etc/init.d/S98autostart sources this at boot. It mounts the SD
# card's Debian rootfs and chroots into it to run the decoder against the
# device's built-in IIO drivers. Stock QSPI boot is untouched — delete this
# file (and reboot) to fully revert.
#
# Requires the SD card to carry the Debian armhf rootfs on its first partition
# (see deploy/onbox/build-sdimg.sh). Runs under the firmware's BusyBox /bin/sh.

SD_PART=/dev/mmcblk0p1
ROOT=/mnt/sdroot
LOG=/mnt/jffs2/autorun.log

{
echo "[autorun] $(date) starting"

# Wait for the SD partition to enumerate.
i=0
while [ ! -b "$SD_PART" ] && [ "$i" -lt 15 ]; do sleep 1; i=$((i + 1)); done
if [ ! -b "$SD_PART" ]; then
    echo "[autorun] SD rootfs $SD_PART not found; leaving stock firmware running"
    exit 0
fi

mkdir -p "$ROOT"
if ! mount -t ext4 "$SD_PART" "$ROOT"; then
    echo "[autorun] mount of $SD_PART failed"
    exit 0
fi

if [ ! -x "$ROOT/usr/bin/python3" ]; then
    echo "[autorun] no python3 in SD rootfs; aborting (stock firmware stays up)"
    exit 0
fi

# Share the live kernel interfaces so libiio local: + IIO sysfs work in chroot.
for m in /dev /dev/shm /proc /sys /run; do
    mkdir -p "$ROOT$m"
    mount --bind "$m" "$ROOT$m" 2>/dev/null
done

mkdir -p "$ROOT/var/log"
# Launch in background so device init isn't blocked.
chroot "$ROOT" /usr/bin/env \
    RX_BACKEND=libiio_local \
    DEPLOYMENT=onbox \
    DASHBOARD_MODE=packets_only \
    LIBIIO_URI=local: \
    /usr/bin/python3 -c "import stream_web.app as a; a.main()" \
    >> "$ROOT/var/log/sdr-gateway.log" 2>&1 &

echo "[autorun] gateway launched (pid $!) on port 8050"
} >> "$LOG" 2>&1
