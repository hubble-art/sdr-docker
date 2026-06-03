# Hubble SDR Gateway — Plan (v2, grounded in the repos)

**Goal:** a standalone SDR gateway that scans regular **BLE beacons ~99% of the time** and captures + decodes **Hubble SatNet (STP)** during two scheduled ~5-minute windows per day (satellite passes). Hardware proof point: **Zynq-7010 + AD9363 (Pluto+)**.

> **Key finding after reading the repos:** `sdr-docker` already implements the *entire* SatNet receive + decode + dashboard path, and `hubble-satnet-decoder` is the pip-installable decode library it calls. **We are not building the SatNet side — it exists and runs.** This plan is about **adding a BLE-scan mode and a scheduler** on top of `sdr-docker`, reusing its GNU Radio RX path, config, processor pattern, and API.

---

## 0. What already exists (so we don't rebuild it)

`sdr-docker` = "live rolling spectrogram + packet decoder," Flask dashboard on **:8050**, built on GNU Radio `gr-soapy` (so any SoapySDR-supported radio works through one code path). Already supports **PlutoSDR (Ethernet/USB)** and **bladeRF 2.0 Micro A4**.

Data flow (today):
```
SDR ──gr-soapy──> BufferSink ──> 2 s shared-mem IQ ring ──> Processor process
                                                              every 0.5 s: spectrogram chunk
                                                              every 1.0 s: detect + decode (hubble-satnet-decoder)
                                                                    │
                                                              result_queue ──> Flask /api/status, /api/packets
```
Real files (in `src/stream_web/`): `config.py`, `gnuradio_rx.py` (soapy.source → BufferSink), `gnuradio_tx.py`, `spectrogram.py`, `processor.py` (0.5 s loop, separate OS process), `app.py` (Flask + orchestration). Native entrypoint: `run_stream.py`.

SatNet RF params (from `config.py`, confirmed): `CENTER_FREQ_HZ = 2_482_440_375`, `SAMPLE_RATE = 781_250` (6.25 MHz/8), `RF_BANDWIDTH = SAMPLE_RATE`, `DECODE_WINDOW_S = 1.0`, `DECODE_INTERVAL_S = 0.5`, 2 s IQ ring. The full SatNet hopping band (`CHANNEL_SPACING = 25_750 Hz` × channels) fits inside that single 781.25 kHz window — that's why one capture + 2-D spectrogram template match (OpenCV/NMS, dual PHY v-1/v1) catches every hop.

Decoder contract (`hubble_satnet_decoder`): `decode_signal(iq) -> (packets, detections, attempts)` on a **1 s complex64 chunk at 781.25 kS/s**; `configure(sample_rate=...)`; preamble detection = OpenCV template match; FSK demod + Reed-Solomon inside the lib.

Output schema already defined — `/api/packets` (NDJSON, poll-and-drain): `device_id`, `seq_num`, `device_type` (chipset), `timestamp`, `rssi_dB`, `channel_num`, `freq_offset_hz`, `payload_b64`.

---

## 1. Hardware recommendation: Pluto+

It's `sdr-docker`'s default, it *is* the target architecture (Zynq-7010 + AD9363), it runs standalone (Linux on the on-chip A9s), it has Gigabit Ethernet for backhaul, and both RX are broken out for a later AoA path. You already have it at `ip:192.168.2.1`.

| SDR | Transceiver | FPGA/SoC | In `sdr-docker`? | Standalone | Backhaul | Verdict |
|---|---|---|---|---|---|---|
| **Pluto+** | AD9363 | **Zynq-7010 (2× A9)** | **Yes (default)** | **Yes** | **GbE** | **Use this** — on-target, default, Ethernet, AoA-ready |
| Pluto (base) | AD9363 | Zynq-7010 | Yes | Yes | USB | Fallback; no Ethernet, 1 RX |
| bladeRF 2.0 µA4 | AD9361 | Cyclone V | Yes (`SDR_TYPE=bladerf`) | No (USB host) | via host | Better RF, but **their README flags USB-streaming instability on Apple Silicon/ARM** — use on Linux x86 only; off-target (non-Zynq) |
| LimeSDR | LMS7002M | Cyclone IV/MAX10 | Drops in via a SoapySDR module, **zero app-code changes** | No (USB host) | via host | Bench option; off-target |

Note: `sdr-docker` runs on a **host** talking to the radio (Ethernet/USB) — it does **not** run on the Pluto itself (small DRAM + buildroot; confirmed: don't run Docker on the Pluto). For the proof point that's fine. True on-box standalone (native binaries / PL offload / companion SBC) is a later phase.

---

## 2. What we're adding

1. **`BLE_SCAN` mode** — a second capture+decode pipeline parallel to the SatNet one: retune to a BLE advertising channel, capture wider, GFSK-demod, parse advertising/beacon packets.
2. **Scheduler / mode router** — default to `BLE_SCAN`; switch to `SATNET` for two ~5-minute pass windows/day; switch back.

Everything else (radio access, ring buffer, processor pattern, Flask API, connection recovery) is reused.

### 2.1 BLE RF params
- **Tune:** park one adv channel — default **ch37 = 2_402_000_000** (configurable to ch38 2_426e6 / ch39 2_480e6). Single channel is fine: our endpoints TX every adv event on all three channels.
- **Sample rate:** BLE 1M GFSK occupies ~1 MHz (±250 kHz deviation), so **≥ 2 MS/s** (suggest `BLE_SAMPLE_RATE = 2_000_000`, or 4 MS/s and decimate). This **differs from SatNet's 781.25 kS/s** — important for mode switching (see §4).
- **Decode:** GFSK discriminator → clock/bit recovery → access-address correlation (adv AA `0x8E89BED6`) → de-whiten (channel-indexed LFSR) → CRC-24 → parse `AdvA` + AD structures (iBeacon/Eddystone/Hubble payload). Port from BTLE/BLESDR (§6).

---

## 3. Integration plan (against the real files)

| Where | Change |
|---|---|
| `config.py` | Add `MODE` (env), `BLE_CENTER_FREQ_HZ` (default 2_402_000_000), `BLE_SAMPLE_RATE` (2_000_000), `BLE_RF_BANDWIDTH`, and the schedule (window times / source). Keep SatNet constants as-is. |
| `gnuradio_rx.py` | Parameterize the soapy source on (center_freq, sample_rate, bandwidth, gain) instead of importing fixed `CENTER_FREQ_HZ`/`SAMPLE_RATE`. Lets the same flowgraph serve both modes. |
| `ble/` (new) | `gfsk_demod.py` (discriminator + clock recovery) and `adv_parser.py` (AA corr, dewhiten, CRC-24, AD parse). Mirror `processor.py`'s separate-process pattern: pull 1 MHz-ish chunks from the ring, emit packets to a result queue. |
| `processor.py` | Leave the SatNet processor; add a sibling BLE processor (or a `mode`-switch at the top of the loop) reading the same ring. |
| `app.py` / new `scheduler.py` | Mode state machine: default `BLE_SCAN`; enter `SATNET` on pass windows; tear down + rebuild the flowgraph on switch (see §4). |
| `/api/packets` | Reuse the schema. Add a `source` field (`"ble"` vs `"satnet"`) so consumers can tell them apart; map BLE fields onto the existing keys (`device_id` = AdvA, `payload_b64` = adv data, `rssi_dB`, `channel_num` = 37/38/39). |
| Dashboard (optional) | A mode indicator + a BLE decode tab; not required for the proof point. |

### 3.1 Pass-window source
Two ~5-min windows/day. Either propagate TLEs on the host (`skyfield`/`sgp4`) **or** ingest a precomputed schedule from the ground system / `satellite.passes` (simpler, authoritative). Make it a pluggable `schedule.next_window()` so we can swap sources.

---

## 4. Mode-switch mechanics (the one real gotcha)

BLE and SatNet use **different sample rates** (2 MS/s vs 781.25 kS/s). In GNU Radio the soapy source's sample rate is a **construction-time** parameter — center frequency can be retuned live, but sample rate can't. So a mode switch = **stop the flowgraph, rebuild it with the new rate/freq, restart** — exactly the teardown `sdr-docker` already does for connection-recovery (process exit → clean restart). Reuse that machinery:
- Wrap "build flowgraph for mode M" so the scheduler can stop/rebuild cleanly.
- Budget a few hundred ms for AD9363 retune + AGC settle at each window edge; start the SatNet window a few seconds early so we don't clip the pass start.
- If staying in one process proves flaky (libiio state), the connection-recovery pattern (exit + supervised restart with a `MODE`/window env) is an acceptable fallback.

---

## 5. Milestones (revised — SatNet already works)

- [ ] **M0** — Stand up `sdr-docker` against Pluto+ at `192.168.2.1`; confirm the existing SatNet dashboard + `/api/packets` work end-to-end (baseline).
- [ ] **M1** — `gnuradio_rx.py` parameterized on (freq, rate, bw); SatNet path still works through the parameterized source.
- [ ] **M2** — BLE capture: retune to 2402 MHz @ 2 MS/s, dump IQ, confirm a clean BLE channel spectrum + a visible advertising burst.
- [ ] **M3** — BLE decode (host): GFSK demod + adv parser (ported from BTLE/BLESDR); decode real beacons + a Hubble endpoint; emit on `/api/packets` with `source:"ble"`.
- [ ] **M4** — Scheduler: default BLE; flip to SatNet for a scheduled window; flip back. Flowgraph rebuild clean; no clipped pass starts.
- [ ] **M5** — 24 h soak (host-tethered): beacon throughput steady, both daily SatNet windows captured + decoded, recovery on drop works.
- [ ] *(later)* **M6** standalone packaging (native on PS / companion SBC / PL offload); **M7** 2-antenna AoA on Pluto+ RX2.

Proof point = **M5**.

---

## 6. Open questions

1. **Pass-window source** — TLE-on-host vs ground-system schedule ingest. (Leaning ingest.)
2. **Single process vs restart-on-switch** — does retune+rate-rebuild stay stable in one process, or do we lean on the exit+restart pattern per switch? Test in M4.
3. **BLE channel choice** — ch37 (2402) vs ch39 (2480, adjacent to the SatNet window so smaller retune). Either works; 2480 minimizes LO jump.
4. **Shared ring vs second ring** — reuse the 2 s ring at the BLE rate, or a separate BLE ring sized for 2 MS/s. Minor.
5. **`/api/packets` schema** — confirm adding `source` doesn't break existing consumers; otherwise a parallel `/api/ble` endpoint.

---

## 7. References

- **BTLE — Xianjun Jiao** (`github.com/JiaoXianjun/BTLE`) — canonical SDR BLE baseband (GFSK demod, AA corr, dewhiten, CRC); start the BLE demod here.
- **BLESDR — jocover** (`github.com/jocover/BLESDR`) — C++ IQ→BLE + sniffer + iBeacon parse; cross-ref for `adv_parser.py`.
- **KU Leuven BLE tracking, arXiv 2509.03979** — PlutoSDR + GNU Radio, PN-correlation detection + beam-sweep AoA; closest public analog to the SatNet detection/AoA approach.
- **PySDR (pysdr.org)** — GFSK/decimation/clock-recovery recipes.
- In-repo: `hubble-satnet-decoder` README (API + constants) and `sdr-docker` README (architecture, config, `/api/packets`, TX API, connection recovery).

---

## 8. Dev environment (Cursor)

- Work inside `sdr-docker` (Python + GNU Radio + SoapySDR via `--system-site-packages` venv, per its README). Decoder via `pip install -e` of `hubble-satnet-decoder`.
- Talk to Pluto+ over Ethernet (`PLUTO_URI=ip:192.168.2.1`); no Docker-on-Pluto.
- Keep the front end (tune/rate/IQ ring) decoupled from the two decoders so a recorded-IQ file source can stand in for live RF in tests (both `decode_signal` and the BLE parser should accept array/file input for CI).
