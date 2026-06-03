# On-box Decoder — Design

**Status:** draft / awaiting review
**Date:** 2026-06-03
**Branch:** `feat/onbox-decoder`
**Scope:** Add an "on-box" mode where `sdr-docker` runs natively on the SDR's
own Linux (Pluto+ Zynq-7010 as the first target), with the existing
host-machine mode preserved unchanged.

---

## 1. Goal

Let a customer flash a microSD, drop it into a Pluto+, plug in Ethernet, and
get a self-contained SatNet gateway: the SDR does its own RX, runs the
decoder, and serves `/api/packets` (and a packets-only Flask UI) on its GbE
IP. No host machine required.

Long-term, the same on-box path should work for other SDRs that expose a
suitable Linux runtime (e.g. bladeRF host SBC, custom Zynq carriers, future
boards). On-box is a **second supported deployment target**, not a
replacement for the host-machine path.

Explicitly **out of scope** for this design:

- BLE scanner / scheduler (covered in the broader gateway plan, deferred).
- AoA / second-RX work on the Pluto+.
- True bake-into-flash production firmware (separate later milestone — see §10).

---

## 2. Constraints and requirements

### Hardware (first target: Pluto+)

- Zynq-7010: dual Cortex-A9 @ 667 MHz, 512 MB DDR3, microSD slot, GbE.
- AD9363 transceiver (same RF front-end already supported in `sdr-docker`).
- Stock firmware is `plutosdr-fw` (buildroot, no Python, no GNU Radio).

### Functional

- New: `/api/packets`, `/api/status`, `/api/tx/*` available on the Pluto+'s
  IP, port `8050`. Schema **identical** to host mode.
- No spectrogram PNG, no time-domain plot served by the device (UI is
  packets-only).
- Existing host-mode deployment unchanged: same `run_stream.py`, same Flask
  routes, same dashboard, same `docker run` works on x86 Linux / macOS.
- One repo, one Python package (`stream_web`), two backends selected at
  runtime by config.

### Non-functional

- Decode loop must keep up with the existing schedule on the A9: 1.0 s decode
  window every 0.5 s (current `DECODE_INTERVAL_S`). If we can't hit that, we
  degrade gracefully (longer interval) rather than corrupt the IQ ring.
- Auto-restart on libiio failure (mirrors today's host behavior, but via
  `systemd` instead of Docker).
- No regression in the 11-test pytest suite, ruff clean.

### Constraints chosen (not negotiable for v1)

- **Rootfs:** Debian armhf (or equivalent) booted from microSD on the Pluto+.
  Stock plutosdr-fw stays in flash, untouched. (Production buildroot bake-in
  is §10 future work.)
- **Same repo and branch model:** all work on `feat/onbox-decoder`, merging
  into `main` when the on-box smoke test passes.
- **Same Python package:** no `stream_web_onbox` fork. Backend selection via
  `config.RX_BACKEND`.

---

## 3. Architecture

```
┌─────────────────────────  Pluto+ (microSD Debian armhf)  ─────────────────────────┐
│                                                                                    │
│   AD9363 ──IIO──> libiio_rx (Python, this repo)                                    │
│                       │                                                            │
│                       ▼ (complex64, ±1.0 normalized)                               │
│                  ┌────────────────┐                                                │
│                  │  IQ ring       │  POSIX shmem, 2 s @ 781.25 kS/s                │
│                  │  (existing)    │                                                │
│                  └────────────────┘                                                │
│                       │                                                            │
│                       ▼                                                            │
│              ┌─────────────────────┐                                               │
│              │ processor (existing)│  decode_signal every DECODE_INTERVAL_S        │
│              │  - NO spec render   │  (compute_spec_chunk / matplotlib / Pillow    │
│              │  - NO td render     │   never imported when DASHBOARD_MODE=packets) │
│              └─────────────────────┘                                               │
│                       │                                                            │
│                       ▼ result_queue                                               │
│              ┌─────────────────────┐                                               │
│              │ Flask app.py        │  GET /api/packets, /api/status, /api/tx/*    │
│              │ (existing, trimmed) │  bind 0.0.0.0:8050                            │
│              └─────────────────────┘                                               │
│                                                                                    │
└────────────────────────────────────────────────────────────────────────────────────┘

                ┌────────────────────  Host machine (today, unchanged)  ────────────┐
                │                                                                   │
                │  AD9363 ──gr-soapy──> gnuradio_rx (existing)                      │
                │                ─> IQ ring ─> processor (full, with spectrogram)   │
                │                                  ─> Flask (full dashboard)        │
                └───────────────────────────────────────────────────────────────────┘
```

The IQ ring, the processor process, the result queue, and `app.py` are the
same code paths in both deployments. The only swappable piece is the RX
front-end module, plus a config flag that gates spectrogram rendering.

### 3.1 RX backend abstraction

New file `src/stream_web/rx_backend.py` defining the minimal interface both
backends satisfy:

```python
class RxBackend(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    @property
    def is_running(self) -> bool: ...
    # No data API here — backends write directly into the shmem ring + advance
    # buf_write_idx (same convention gr-soapy backend uses today).
```

Existing GNU Radio path moves behind this protocol with no behavioral
change: a thin wrapper class `SoapyHostBackend` whose `start()` calls into
`rx_loop()` from `gnuradio_rx.py`.

New `LibiioLocalBackend` in `src/stream_web/libiio_rx.py` uses
`pyadi-iio`'s `adi.ad9361(uri="local:")` (on-box) or `adi.ad9361(uri=PLUTO_URI)`
(remote-test from a dev machine, useful before we have microSD-Debian booted).

Backend selection:

```python
# config.py
RX_BACKEND = os.environ.get("RX_BACKEND",
                            "libiio_local" if _on_pluto() else "gnuradio_soapy")
```

`_on_pluto()` heuristic: presence of `/sys/bus/iio/devices/iio:device0` AND
the device name matches `ad9361-phy`. Easy to override with the env var.

### 3.2 Processor changes

`processor.py` gets a single new config check:

```python
if config.DASHBOARD_MODE == "full":
    from .spectrogram import render_spec_image, render_td_plot
    from hubble_satnet_decoder import compute_spec_chunk
    # ...existing spectrogram render loop
else:  # packets_only
    pass  # skip spec/td entirely
```

`config.DASHBOARD_MODE` defaults to `"full"` on host, `"packets_only"`
on-box. Saves matplotlib + Pillow imports and ~0.5 s/cycle of CPU when
packets-only.

### 3.3 Flask changes

`app.py` keeps every existing route. When `DASHBOARD_MODE == "packets_only"`:

- Routes that return the spectrogram PNG or time-domain plot return 404.
- The HTML dashboard template is swapped for a minimal "packets table"
  template (server-rendered + small fetch loop against `/api/packets`).
- TX routes (`/api/tx/*`) stay — Pluto+ can transmit too, no reason to drop
  them.

`/api/status` adds two fields: `deployment` (`"host"` | `"onbox"`) and
`backend` (`"gnuradio_soapy"` | `"libiio_local"`). Additive only — existing
consumers unaffected.

### 3.4 Deployment

- A `deploy/onbox/` directory containing:
  - `bootstrap.sh` — first-boot script that `apt install`s the deps, creates a
    `sdr` user, clones the repo, sets up the venv with `--system-site-packages`,
    installs `sdr-docker` editable.
  - `sdr-gateway.service` — systemd unit that runs `python3 -m stream_web` (we
    add a `__main__.py`) with `Restart=always`, `RestartSec=5s`. Replaces
    today's `--restart unless-stopped` Docker behavior.
  - `README.md` — step-by-step: flash Debian armhf to microSD → boot Pluto+ →
    SSH in → `curl … | bash bootstrap.sh`.
- No on-box Docker. The whole point of microSD-Debian is direct execution.

### 3.5 Dependency strategy (on-box)

| Dep | Source on Pluto+ |
|---|---|
| python3.11 (or 3.10+) | Debian apt |
| numpy 1.26.x | apt `python3-numpy` if available at the right pin, else pip |
| scipy 1.13+ | pip (apt versions often too old) |
| opencv-python-headless | **apt `python3-opencv`** (PyPI armhf wheels are inconsistent — flagged risk) |
| reedsolo | pip |
| flask | apt or pip |
| libiio + iio-utils | apt `libiio-dev libiio-utils` |
| pyadi-iio | pip |
| hubble-satnet-decoder | pip (PyPI) |

We codify all of this in `deploy/onbox/bootstrap.sh` so the dev workflow is
reproducible and the production buildroot recipe later (§10) has a precise
list to mirror.

---

## 4. File-by-file change plan

| File | Change |
|---|---|
| `src/stream_web/config.py` | Add `RX_BACKEND`, `DASHBOARD_MODE`, defaults via small `_on_pluto()` heuristic. No removed constants. |
| `src/stream_web/rx_backend.py` | **New.** `RxBackend` Protocol; factory `make_rx_backend()`. |
| `src/stream_web/gnuradio_rx.py` | Wrap existing `rx_loop()` behind a `SoapyHostBackend` class. Public API of `rx_loop` preserved. |
| `src/stream_web/libiio_rx.py` | **New.** `LibiioLocalBackend` using `pyadi-iio`. Writes complex64 (±1.0) into the same shmem ring with the same `buf_write_idx` semantics. |
| `src/stream_web/processor.py` | Gate spectrogram + td render behind `DASHBOARD_MODE`. No structural change. |
| `src/stream_web/app.py` | (a) select backend via `make_rx_backend()`; (b) gate spectrogram routes by `DASHBOARD_MODE`; (c) add `deployment` + `backend` to `/api/status`; (d) pick minimal template when packets-only. |
| `src/stream_web/__main__.py` | **New.** Tiny entrypoint so `python3 -m stream_web` works (used by the systemd unit). |
| `src/stream_web/templates/dashboard_packets.html` | **New.** Minimal HTML for packets-only mode. |
| `deploy/onbox/bootstrap.sh` | **New.** First-boot installer. |
| `deploy/onbox/sdr-gateway.service` | **New.** systemd unit. |
| `deploy/onbox/README.md` | **New.** Setup walkthrough. |
| `docs/design/2026-06-03-onbox-decoder.md` | **New.** This document. |
| `pyproject.toml` | Add `pyadi-iio` to an optional `[project.optional-dependencies] onbox`. Keeps host install lean. |
| `tests/test_libiio_rx.py` | **New.** Mock pyadi-iio; unit-test the ring-fill semantics. (No real hardware in CI.) |
| `tests/test_backend_selection.py` | **New.** Verify `RX_BACKEND` env var and `_on_pluto()` heuristic both select correctly. |
| `Dockerfile` | Unchanged. Docker remains a host-mode deployment artifact. |
| `README.md` | Add an "On-box deployment (Pluto+)" section pointing at `deploy/onbox/README.md`. |
| `CLAUDE.md` | Note the two deployment targets and the backend abstraction. |

---

## 5. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| opencv on armhf via pip is broken/missing | Medium | High (decoder won't import) | Use apt `python3-opencv`; document in bootstrap. If even apt fails on the Debian release we pick, escalate to building from source or vendor a thin replacement of just the template-match call site. |
| Decode can't keep up real-time on A9 | Medium-High | High (dropped packets) | (a) Measure first (M2 below); (b) loosen `DECODE_INTERVAL_S`; (c) escalate to C port of the hot loops **upstream in `hubble-satnet-decoder`** so host + on-box both benefit. **Do not** put C code in `sdr-docker`. |
| libiio EPIPE / connection loss inside the same box | Low | Medium | Same recovery pattern as today: process exits non-zero, systemd restarts in <5s. Local IIO context drop is rarer than network IIO context drop, but we handle it the same way. |
| 512 MB DDR3 RAM exhaustion | Medium | High (OOM kill) | Skip matplotlib/Pillow on-box (already in design). Monitor RSS in `/api/status`. Pre-load decoder once at boot so we know real RSS before serving. |
| Dev velocity loss because every change needs a Pluto+ to test | Medium | Medium | `LibiioLocalBackend` accepts `uri=ip:192.168.2.1` so dev laptops can run "on-box mode" against a remote Pluto+ for iteration. Final smoke test happens on real microSD-booted hardware. |
| Customer flashes wrong rootfs / breaks Pluto+ flash | Low | Medium | We don't touch flash. Whole approach is "boot from microSD." Worst case = pull microSD, stock firmware boots normally. |

---

## 6. Testing strategy

- **Unit tests (CI, x86):** existing 11 pass; add `tests/test_libiio_rx.py`
  (mock pyadi-iio) and `tests/test_backend_selection.py`. CI continues to
  run `ruff check src/` + `pytest tests/` on x86.
- **Host integration test:** existing `run_stream.py` against a real Pluto+
  over Ethernet. Confirms zero regression from the backend abstraction.
- **On-box smoke test (manual, M3):** SSH into the microSD-booted Pluto+,
  `systemctl status sdr-gateway`, `curl http://<pluto-ip>:8050/api/status`,
  watch `/api/packets` stream for 5 min, confirm we see decodes.
- **On-box soak test (manual, M5):** 30-minute run, log decode counts, RSS,
  CPU%, drop counts. Compare to host-mode baseline on the same RF environment.
- No on-box CI in v1 (would require armhf runners / QEMU + an iio shim).
  Acceptable trade-off; revisit if regressions land.

---

## 7. Performance budget and escape hatches

Target on Pluto+ A9:

- RX core (libiio + numpy memcpy + ring write): < 30% of one A9 core average.
- Decoder core (`decode_signal` + result emit): < 80% of the other A9 core
  average; < 0.5 s wallclock per `DECODE_INTERVAL_S`.
- Flask + UI traffic: negligible.
- Total RSS at steady state: < 250 MB (half of DRAM, leaves headroom for OS).

Escape hatches, in order we'd reach for them:

1. Bump `DECODE_INTERVAL_S` from 0.5 s to 1.0 s (1 s window, 1 Hz cadence).
2. Disable `get_chipset_stats()` per-cycle and emit only on drain.
3. **Profile + C-port the dominant hot loop upstream in `hubble-satnet-decoder`**
   (likely the OpenCV template match or the FSK demod). Released as a new
   decoder version, pinned here. Host path benefits too.
4. Architecture switch to a native libiio capture binary (option "C" from
   brainstorm). The shmem ring contract makes this swap local to `libiio_rx`.

We will not pre-emptively start (3) or (4); they're triggered by measurement.

---

## 8. Milestones

- **M0 — Branch + design (this doc).** Merged on review.
- **M1 — Backend abstraction.** Refactor: introduce `RxBackend`, move
  existing `rx_loop` behind `SoapyHostBackend`. Host path unchanged, all
  tests green.
- **M2 — `LibiioLocalBackend` working remotely.** From a dev laptop, run
  `RX_BACKEND=libiio_local PLUTO_URI=ip:192.168.2.1 python3 -m stream_web`.
  Same decodes as the host path. (Doesn't yet require microSD-Debian.)
- **M3 — microSD-Debian + bootstrap.** Boot Debian armhf on Pluto+ from
  microSD, run `bootstrap.sh`, get the systemd unit serving `/api/packets`.
  Perf measurement run.
- **M4 — Performance pass.** Tune `DECODE_INTERVAL_S` etc. If we don't hit
  the perf budget, decide between escape hatches (1)–(4).
- **M5 — 30-min on-box soak.** Decode counts and RSS stable. Ready to merge.
- **M6 (later, not this design) — production firmware.** See §10.

---

## 9. Open questions

1. **Which Debian release for the microSD image?** Debian 12 (bookworm)
   armhf is the default candidate; Ubuntu armhf images for Zynq-7000 are
   harder to source. Will confirm during M3.
2. **Auto-update path?** For v1, none — `git pull && systemctl restart
   sdr-gateway` is the upgrade procedure. Revisit pre-production.
3. **TLS / auth on the device's Flask?** Not in v1 — the gateway is assumed
   to live on a customer's LAN behind their firewall. Same as today's host
   mode. Flag for customers; offer a reverse-proxy recipe if asked.
4. **Multi-SDR generalization sequencing.** The abstraction is in place
   from M1, but the second on-box target (probably bladeRF on a small SBC)
   is its own milestone after M5 and out of scope here.

---

## 10. Production migration (post-merge, not this design)

Once M5 is stable, the production-grade story is to bake the same Python
stack into a custom Pluto+ firmware. Approach: extend `plutosdr-fw`'s
buildroot config with the dep list from `bootstrap.sh`, generate a SquashFS
overlay that fits in flash, and re-sign the firmware bundle. The architecture
above is designed so this is a **packaging** exercise (find/build armhf
packages for the same dep list) rather than a code refactor.

C-ports of decoder hot loops, if needed, land in `hubble-satnet-decoder`
*before* this step — buildroot recipes pin a specific decoder version that
includes them.

---

## 11. Decisions log

| Decision | Why |
|---|---|
| Same repo, runtime-selected backend | Customers/contributors discover both paths in one place; future SDRs land as new backends, not new repos. |
| Debian armhf on microSD for v1 (not buildroot bake-in) | Dev velocity. Buildroot iteration time would dominate the project. |
| Multi-process design preserved on-box | Dual A9 core: RX on one, decode on the other. Single-process Python would re-introduce the GIL contention this project already designed away. |
| Spectrogram dropped on-box | Saves matplotlib/Pillow (~tens of MB RAM, significant CPU). UI is packets-only by user choice. |
| C ports, if needed, go upstream in `hubble-satnet-decoder` | Both deployments benefit; `sdr-docker` stays pure-Python and easy to install. |
| systemd, not Docker, on-box | A Pluto+-class Linux can't host Docker reasonably. systemd is native and gives us the same restart semantics. |
