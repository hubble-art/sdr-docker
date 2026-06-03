#!/usr/bin/env bash
# Build the on-box Debian armhf rootfs image and export it as a tarball.
#
# Produces deploy/onbox/out/rootfs.tar — extract this into the SD card's ext4
# rootfs partition (Stage 3). Run from anywhere; paths are resolved relative to
# the repo root.
#
# Requires: Docker with linux/arm/v7 (qemu) emulation.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUT_DIR="$SCRIPT_DIR/out"
IMAGE_TAG="sdr-onbox-rootfs"

mkdir -p "$OUT_DIR"

echo "[build-rootfs] Building $IMAGE_TAG (linux/arm/v7)..."
docker build --platform linux/arm/v7 \
    -f "$SCRIPT_DIR/Dockerfile.rootfs" \
    -t "$IMAGE_TAG" \
    "$REPO_ROOT"

echo "[build-rootfs] Exporting rootfs tarball..."
CID="$(docker create --platform linux/arm/v7 "$IMAGE_TAG")"
trap 'docker rm -f "$CID" >/dev/null 2>&1 || true' EXIT
docker export "$CID" -o "$OUT_DIR/rootfs.tar"

SIZE="$(du -h "$OUT_DIR/rootfs.tar" | cut -f1)"
echo "[build-rootfs] Done: $OUT_DIR/rootfs.tar ($SIZE)"
