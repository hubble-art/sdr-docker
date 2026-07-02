# sdr-docker

Multi-SDR streaming spectrogram + packet decoder web application.

## Supported SDR devices

- **ADALM-PLUTO / PlutoPlus (Pluto+)** — `SDR_TYPE=pluto` (default). Both use the same SoapyPlutoSDR driver and libiio backend. PlutoPlus has Gigabit Ethernet and 2RX/2TX (AD9363) but only channel 0 is used.
- **bladeRF 2.0 Micro** — `SDR_TYPE=bladerf`

## Build & test

```bash
pip install -e ".[dev]"
ruff check src/
python3 run_stream.py          # requires SDR hardware or Docker
```

## Docker

```bash
docker build -t sdr-docker .
docker run -p 8050:8050 sdr-docker
```

## Project structure

- `src/stream_web/config.py` — SDR and display configuration (protocol constants via hubble-satnet-decoder)
- `src/stream_web/gnuradio_rx.py` — GNU Radio RX flowgraph using gr-soapy
- `src/stream_web/gnuradio_tx.py` — GNU Radio TX flowgraph using gr-soapy (full-duplex, tone + IQ file playback)
- `src/stream_web/spectrogram.py` — spectrogram image rendering (PIL/matplotlib)
- `src/stream_web/processor.py` — decode + spectrogram loop (separate OS process)
- `src/stream_web/app.py` — Flask web app, API routes (RX/TX/file upload), process orchestration
- `run_stream.py` — entry point (supervisor: auto-restarts on SDR connection loss)

## Key dependencies

- **hubble-satnet-decoder** — preamble detection, FSK decoding, protocol constants
- **GNU Radio + gr-soapy** — unified SDR RX/TX (system-level, not pip)
- **Flask** — web server and API

## API endpoints

### RX
- `GET /api/status` — system status, RX metrics, peak power; includes `sdr_connected` (bool) indicating whether the SDR hardware is currently open
- `GET /api/packets` — poll-and-drain decoded packets (NDJSON)
- `GET /api/iq_capture?seconds=N` — record N s (1–30, default 10) of raw IQ forward from now; returns a `.npy` (complex64) download with `X-Sample-Rate-Hz` / `X-Center-Freq-Hz` / `X-N-Samples` headers
- `GET /api/record_analyze?seconds=N` — record N s (1–30, default 10) and return a plain-text diagnostic report file: for a representative decoded packet, per-symbol timing (duration/gap/drift), per-symbol frequency vs expected channel window, and per-symbol amplitude/SNR, plus chipset/synth-res, frequency offset, and RS corrections; analyzes decoded packets only; backs `hubblenetwork sat record`

### TX
- `POST /api/tx/start` — start TX (`{"mode":"tone"}` or `{"mode":"packet","file":"<name>"}`)
- `POST /api/tx/stop` — stop TX
- `GET /api/tx/status` — current TX state
- `POST /api/tx/attn` — set TX attenuation in dB, 0=max power 89=min (`{"attn_db": 30}`)

### TX file management
- `POST /api/tx/files` — upload IQ binary file (multipart, max 1 GB), returns `{filename, size_bytes, sha256}`
- `GET /api/tx/files` — list available TX files with size and SHA256
- `DELETE /api/tx/files/<filename>` — delete a TX file

## Conventions

- Hatchling build with `src` layout
- Ruff is the sole linter
- Protocol constants come from `hubble_satnet_decoder.constants`; SDR/display config stays in `config.py`
- `config.py` re-exports protocol constants for backward compatibility
