# On-box deployment (Pluto+ / Zynq-7010)

Run `sdr-docker` natively on the SDR's own Linux so the device is a
self-contained SatNet gateway: it captures, decodes, and serves
`/api/packets` (+ a packets-only Flask UI) on its own IP — no host machine.

See `docs/design/2026-06-03-onbox-decoder.md` for the full design and rationale.

> **Status:** M1 (backend abstraction) and M2 (libiio_local capture) are done and
> hardware-verified. M3 (this directory) is the on-device boot + rootfs. The
> boot chain (Stage 2) needs board-matched artifacts and is in progress.

## Architecture recap

```
AD9363 ──libiio──> libiio_rx (pyadi-iio) ──> shared IQ ring ──> processor ──> Flask :8050
```

`RX_BACKEND=libiio_local`, `DASHBOARD_MODE=packets_only` (no spectrogram render,
so matplotlib/Pillow are never loaded — keeps RAM/CPU low on the 512 MB A9).

## Deployment model (option D: switch_root into a Debian armhf rootfs)

The Pluto+ stock flash (QSPI) stays untouched. We boot from the microSD card
and `switch_root` into a Debian **armhf** rootfs that carries the whole decode
stack. Debian **trixie** is the base because its `libiio 0.26` matches the
Pluto firmware (avoids the libiio version-mismatch pitfalls) and it ships
numpy/scipy/opencv via apt.

### Stage 1 — build the rootfs (no hardware; runs on your dev machine)

Requires Docker with `linux/arm/v7` (qemu) emulation.

```bash
deploy/onbox/build-rootfs.sh
# -> deploy/onbox/out/rootfs.tar
```

This builds `Dockerfile.rootfs` (Debian armhf + apt numpy/scipy/opencv/flask/
libiio + pip pyadi-iio/reedsolo/hubble-satnet-decoder + sdr-docker) and exports
the filesystem as `rootfs.tar`. The image's build-time smoke check imports the
full decode stack under numpy 2.x.

### Target board (confirmed)

HamGeek **ADI Pluto+** clone: Zynq-7010, 512 MB, AD936x, Gigabit Ethernet +
microSD. Reports `Analog Devices PlutoSDR Rev.C (Z7010-AD9363A)`, fw
`v0.37-dirty`, kernel `5.10.0 SMP armv7l`. Firmware lineage =
[`plutoplus/plutoplus`](https://github.com/plutoplus/plutoplus) family.
**Do NOT use ANTSDR/E310 or LibreSDR (Zynq-7020) artifacts — wrong board.**

### Device facts (from the running unit over SSH)

Login: **`root` / `root`** (this HamGeek v0.37-dirty build uses `root`, not the
usual `analog`). Inspection revealed why the chroot path below is the right one:

- **Monolithic kernel, zero loadable modules** (`/lib/modules` empty). AD9361 /
  IIO / SD (`sdhci-of-arasan`) / ext4 / vfat are all built in — the rootfs needs
  **no** matching `/lib/modules`.
- **`/etc/init.d/S98autostart` sources `/mnt/jffs2/autorun.sh`** if present —
  a supported, persistent, reversible boot hook in QSPI flash.
- u-boot has SD-boot, but it's gated on `modeboot=sdboot` (the jumper).

### Stage 2 — deployment via chroot hook (PRIMARY, no jumper, reversible)

Because the stock kernel already provides every driver, we **leave QSPI boot
completely untouched** and run the gateway from the SD rootfs via the autorun
hook:

1. `autorun.sh` (installed to `/mnt/jffs2/autorun.sh`) mounts the SD's ext4
   rootfs, bind-mounts `/dev` + `/sys` + `/proc` (so `libiio local:` sees the
   live IIO devices), and `chroot`s in to launch the gateway on port 8050.
2. To revert: delete `/mnt/jffs2/autorun.sh` and reboot — pure stock firmware.

No `BOOT.bin`, no `switch_root`, no boot-chain risk, no SD-H jumper.

> The `boot/` dir (`uEnv.txt`, `initramfs-init.sh`) and `provision-sd.sh` remain
> as the **alternative full-SD-boot path** (replace the rootfs entirely, needs a
> Pluto+ `BOOT.bin` + jumper). Not needed for the chroot path.

### Stage 3 — build + flash the SD, install the hook

```bash
deploy/onbox/build-rootfs.sh        # -> out/rootfs.tar (Stage 1)
deploy/onbox/build-sdimg.sh         # -> out/sdcard.img (ext4, populated)

# Flash (macOS — VERIFY the disk number first; this erases it):
diskutil list
diskutil unmountDisk /dev/disk5
sudo dd if=deploy/onbox/out/sdcard.img of=/dev/rdisk5 bs=4m && sync

# Install the persistent hook on the Pluto (over SSH, root/root):
scp deploy/onbox/autorun.sh root@192.168.2.1:/mnt/jffs2/autorun.sh
ssh root@192.168.2.1 chmod +x /mnt/jffs2/autorun.sh
```

Then move the SD card into the Pluto+ and reboot. The gateway comes up on the
device IP, port 8050:

```bash
curl http://192.168.2.1:8050/api/status     # deployment=onbox, backend=libiio_local
curl http://192.168.2.1:8050/api/packets    # NDJSON decode stream
```

The ext4 image defaults to 6 GiB; grow it to fill the card later with
`resize2fs` if you want more space for logs / IQ captures.

```bash
curl http://<device-ip>:8050/api/status     # deployment=onbox, backend=libiio_local
curl http://<device-ip>:8050/api/packets    # NDJSON decode stream
```

## Service management

`sdr-gateway.service` runs the app under systemd with `Restart=always`
(mirrors the host's connection-recovery: rx_loop exits code 3 on a libiio
drop, systemd restarts a clean instance).

```bash
cp deploy/onbox/sdr-gateway.service /etc/systemd/system/
systemctl enable --now sdr-gateway
journalctl -u sdr-gateway -f
```

Override defaults via `/etc/default/sdr-gateway` (e.g. `RX_BACKEND`,
`DASHBOARD_MODE`, `LIBIIO_URI`, `PLUTO_URI`).

## Notes / caveats

- **TX is host-only for now.** The TX path uses GNU Radio, which isn't in the
  on-box rootfs; `/api/tx/*` will error on-device. RX/decode/packets work.
- **opencv pulls a large dependency tree on Debian** (VTK, tesseract, GUI libs)
  — fine on the 64 GB SD card, but it's why the rootfs is sizeable. A slimmer
  opencv is a future optimization.
- **Performance on the A9 is unverified** until M4. The 0.5 s decode cadence
  must hold; if not, escape hatches are in the design doc (§7).
