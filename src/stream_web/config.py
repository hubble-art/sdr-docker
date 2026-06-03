"""Configuration constants for the SDR stream web application.

Protocol constants (RS codes, hopping sequences, preamble patterns, etc.)
live in ``hubble_satnet_decoder.constants`` and are re-exported here for backward
compatibility.  SDR-specific and display-specific values are defined locally.
"""

import os

import hubble_satnet_decoder.constants as _fdc
from hubble_satnet_decoder.constants import (  # noqa: F401 — re-exported
    ADC_FULL_SCALE,
    DATA_LEN_VNEG1,
    DETECTION_THRESHOLD,
    F0_TOL,
    FREQ_STEP_VNEG1,
    GAP_DURATIONS,
    HOPPING_SEQS,
    LO_CHANNEL,
    MAX_RAW,
    MIN_ENERGY_DBFS,
    NFFT_DET,
    NFFT_VIS,
    NMS_FREQ_BINS,
    NMS_TIME_BINS,
    NOVERLAP_DET,
    NOVERLAP_VIS,
    NUM_CHANNELS,
    NUM_FSK_BINS,
    NUM_HEADER_SYMS,
    NUM_SYM_PER_HOP,
    PAYLOAD_LEN_BYTES_V1,
    PREAMBLE_BITS,
    PREAMBLE_CODE_V1,
    PREAMBLE_F0_SNR_MIN,
    PREAMBLE_LEN,
    RS_K_V1,
    RS_K_VNEG1,
    RS_N_V1,
    RS_N_VNEG1,
    SYMBOL_DURATION_S,
    SYMBOLS_PER_PACKET_VNEG1,
    SYNTH_RES,
    TEMPLATE_FREQ_BINS,
    TIME_TOL,
    bins_on,
    fft_freqs,
    off_indices_v1,
    on_indices_v1,
    preamble_off_idx,
    preamble_on_idx,
    samples_per_symbol,
    slot_samples,
    templates,
    time_step_s,
)

# -- SDR selection (override with environment variables) --------------------
# "pluto" (ADALM-PLUTO & PlutoPlus) or "bladerf"
SDR_TYPE = os.environ.get("SDR_TYPE", "pluto").lower()


def _on_pluto() -> bool:
    """Best-effort detection of running natively on a Pluto-class device.

    True when a local IIO device exposes the AD936x phy driver.  Used only to
    pick a sensible *default* backend; always overridable via ``RX_BACKEND``.
    """
    try:
        for dev in ("iio:device0", "iio:device1", "iio:device2"):
            name_path = f"/sys/bus/iio/devices/{dev}/name"
            try:
                with open(name_path) as f:
                    if "ad9361" in f.read().strip():
                        return True
            except OSError:
                continue
    except Exception:
        pass
    return False


# -- RX backend & deployment mode -------------------------------------------
# "gnuradio_soapy" (host, default) or "libiio_local" (on-box, e.g. Pluto+).
RX_BACKEND = os.environ.get(
    "RX_BACKEND", "libiio_local" if _on_pluto() else "gnuradio_soapy"
).lower()

# Deployment label surfaced in /api/status. Derived from RX_BACKEND but
# overridable for clarity in mixed setups.
DEPLOYMENT = os.environ.get(
    "DEPLOYMENT", "onbox" if RX_BACKEND == "libiio_local" else "host"
).lower()

# "full" (spectrogram + time-domain dashboard) or "packets_only" (no image
# rendering — saves matplotlib/Pillow/CPU/RAM on constrained on-box hardware).
DASHBOARD_MODE = os.environ.get(
    "DASHBOARD_MODE", "packets_only" if DEPLOYMENT == "onbox" else "full"
).lower()

# -- PlutoSDR connection (ignored when SDR_TYPE != "pluto") -----------------
PLUTO_URI = os.environ.get("PLUTO_URI", "ip:192.168.2.1")

# -- Radio parameters (shared across SDR backends) -------------------------
CENTER_FREQ_HZ = 2_482_440_375
SAMPLE_RATE = 781_250  # 6.25 MHz / 8
RX_BUFFER_SIZE = 2 ** 16  # ~84 ms per read
RF_BANDWIDTH = int(SAMPLE_RATE)
RX_GAIN_MODE = "manual"
RX_INITIAL_GAIN_DB = 20
RX_GAIN_MIN_DB = 0
RX_GAIN_STEP_DB = 2

if SDR_TYPE == "bladerf":
    RX_GAIN_MAX_DB = 60
else:
    RX_GAIN_MAX_DB = 71

# -- Spectrogram (visualisation) -------------------------------------------
SPEC_DURATION_S = 10.0
SPEC_CHUNK_S = 0.5
SPEC_CHUNK_SAMPLES = int(SPEC_CHUNK_S * SAMPLE_RATE)
MAX_SPEC_CHUNKS = int(SPEC_DURATION_S / SPEC_CHUNK_S)

# IQ circular buffer: ~2 s for decode + headroom
IQ_BUFFER_SIZE = int(2.0 * SAMPLE_RATE)

# Target image size for web display
SPEC_IMG_WIDTH = 1200
SPEC_IMG_HEIGHT = 200

# -- Decoder scheduling ----------------------------------------------------
DECODE_WINDOW_S = 1.0
DECODE_INTERVAL_S = 0.5
DECODE_SAMPLES = int(DECODE_WINDOW_S * SAMPLE_RATE)

# -- Web server & app behaviour --------------------------------------------
FLASK_PORT = 8050
VERBOSE = False
MAX_DECODE_HISTORY = 200
SDR_RETRY_INTERVAL_S = 3

# -- Time-domain viewer ----------------------------------------------------
TD_WINDOW_S = 0.5

# -- Sync hubble_satnet_decoder with this SDR config -----------------------
_fdc.CHANNEL_SPACING = 25_750.0
_fdc.DEVICE_CHANNEL_SPACING = {
    name: round(_fdc.CHANNEL_SPACING / sr) * sr
    for name, sr in _fdc.SYNTH_RES.items()
}
CHANNEL_SPACING = _fdc.CHANNEL_SPACING
DEVICE_CHANNEL_SPACING = _fdc.DEVICE_CHANNEL_SPACING
_fdc.configure(SAMPLE_RATE)
