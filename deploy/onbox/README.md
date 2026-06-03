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

### Stage 2 — boot chain (board-matched; in progress)

Reuse this board's working kernel + a small initramfs whose `init` mounts the
SD ext4 rootfs and `switch_root`s into it. Copy the firmware's matching
`/lib/modules/<kver>` into the rootfs so kernel modules line up.

> The exact `BOOT.bin` / kernel / devicetree must match this specific Pluto+
> clone (it reports `PlutoSDR Rev.C`, fw `v0.37-dirty`, kernel 5.10.0 SMP).
> Do NOT use ANTSDR artifacts — different board.

### Stage 3 — write the SD card and boot

1. Partition the microSD: `p1` FAT32 (boot, ~256 MB), `p2` ext4 (rootfs, rest).
2. Copy boot artifacts → FAT32; extract `rootfs.tar` → ext4.
3. Set the **SD-H jumper** on the PCB (SD boot; physical step).
4. Insert card, power on. The gateway comes up on the device IP, port 8050.

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
