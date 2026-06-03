"""On-box RX backend (pyadi-iio) for the libiio_local path.

Runs natively on the SDR's own Linux (Pluto+ Zynq-7010 first target) and
captures IQ via ``pyadi-iio`` against the local IIO context, writing into the
same shared-memory ring the GNU Radio host backend uses. The consumer
(``processor.py``) is identical for both backends.

Design parity with ``gnuradio_rx.py``:

- Same circular-buffer write semantics (single-producer / single-consumer,
  ``state.buf_write_idx`` advanced after each write).
- Same ±1.0 normalization the host path gets from gr-soapy, so
  ``config.ADC_FULL_SCALE = 1.0`` and all energy thresholds stay valid.
  pyadi-iio returns raw 12-bit AD936x counts (±2048), so we divide by 2048.
- Same recovery contract: retry the *initial* connection, but exit the process
  (code 3) on a mid-stream connection loss so a supervisor / systemd / Docker
  restarts a clean instance (libiio cannot recover an IIO context in-process).
- Same live controls: gain (``state.rx_gain_dB``) and LO (``state.lo_freq_hz``).

``adi`` is imported lazily inside ``rx_loop`` so this module stays importable on
hosts without pyadi-iio (CI, SDR_TYPE=mock, backend-selection tests).

Capture is a *pull* loop here (``sdr.rx()`` blocks until a buffer is ready),
versus GNU Radio's push model — but the ring contract is identical.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

import numpy as np

from . import config

if TYPE_CHECKING:
    from .app import SharedState

# AD936x is a 12-bit transceiver; pyadi-iio returns sign-extended raw counts in
# roughly ±2048. Dividing by this yields the same ±1.0 CF32 range gr-soapy
# produces on the host path.
_AD936X_FULL_SCALE = 2048.0

_STALE_TIMEOUT_S = 5.0
_EXIT_CODE_CONNECTION_LOST = 3


def _libiio_uri() -> str:
    """Resolve the IIO context URI for the on-box backend.

    Priority: explicit ``LIBIIO_URI`` env > ``local:`` when running on the
    device > ``config.PLUTO_URI`` (lets a dev laptop drive a remote Pluto+ via
    ``ip:192.168.2.1`` for iteration before microSD-Debian exists).
    """
    env_uri = os.environ.get("LIBIIO_URI", "").strip()
    if env_uri:
        return env_uri
    if config._on_pluto():
        return "local:"
    return config.PLUTO_URI


class LibiioSource:
    """Thin pyadi-iio wrapper exposing the controls ``rx_loop`` needs."""

    def __init__(self, uri: str):
        import adi  # lazy: only needed when this backend actually runs

        self._uri = uri
        self._sdr = adi.ad9361(uri=uri)
        sdr = self._sdr

        sdr.rx_lo = int(config.CENTER_FREQ_HZ)
        sdr.sample_rate = int(config.SAMPLE_RATE)
        sdr.rx_rf_bandwidth = int(config.RF_BANDWIDTH)
        sdr.rx_enabled_channels = [0]
        sdr.rx_buffer_size = int(config.RX_BUFFER_SIZE)

        if config.RX_GAIN_MODE == "manual":
            sdr.gain_control_mode_chan0 = "manual"
            sdr.rx_hardwaregain_chan0 = float(config.RX_INITIAL_GAIN_DB)
        else:
            sdr.gain_control_mode_chan0 = "slow_attack"

        self.last_sample_time: float = time.monotonic()

    @property
    def info_string(self) -> str:
        return (
            f"{config.SDR_TYPE} via pyadi-iio ({self._uri}) -- "
            f"LO={config.CENTER_FREQ_HZ / 1e9:.5f} GHz, "
            f"fs={config.SAMPLE_RATE:,} Hz"
        )

    def read(self) -> np.ndarray:
        """Blocking read of one buffer, normalized to complex64 ±1.0."""
        raw = self._sdr.rx()
        self.last_sample_time = time.monotonic()
        return (raw / _AD936X_FULL_SCALE).astype(np.complex64)

    def set_gain(self, gain_db: float) -> None:
        self._sdr.gain_control_mode_chan0 = "manual"
        self._sdr.rx_hardwaregain_chan0 = float(gain_db)

    def set_frequency(self, freq_hz: int) -> None:
        self._sdr.rx_lo = int(freq_hz)

    def seconds_since_last_sample(self) -> float:
        return time.monotonic() - self.last_sample_time

    def close(self) -> None:
        try:
            # Drop references so libiio tears down the context/buffers.
            del self._sdr
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Circular buffer write (mirrors gnuradio_rx._BufferSink.work)
# ---------------------------------------------------------------------------

def _write_ring(state: SharedState, samples: np.ndarray) -> None:
    n = len(samples)
    buf = state.iq_buffer
    wi = state.buf_write_idx
    space = config.IQ_BUFFER_SIZE - wi
    if n <= space:
        buf[wi:wi + n] = samples
        state.buf_write_idx = wi + n
    else:
        buf[wi:] = samples[:space]
        remainder = n - space
        buf[:remainder] = samples[space:]
        state.buf_write_idx = remainder


def _update_peak(state: SharedState, samples: np.ndarray, running_peak: float) -> float:
    peak = float(max(np.max(np.abs(samples.real)), np.max(np.abs(samples.imag))))
    scaled = peak / config.ADC_FULL_SCALE
    if scaled > running_peak:
        running_peak = scaled
    state.rx_peak_frac = running_peak
    return running_peak * 0.999


# ---------------------------------------------------------------------------
# Public entry point (parity with gnuradio_rx.rx_loop)
# ---------------------------------------------------------------------------

def rx_loop(state: SharedState) -> None:
    """Capture from the local SDR into the shared ring until ``state.running``
    is cleared. Exits the process on mid-stream connection loss.
    """
    src = _connect(state)
    if src is None:
        return

    cur_gain = config.RX_INITIAL_GAIN_DB
    cur_freq = config.CENTER_FREQ_HZ
    running_peak = 0.0

    # Expected wall-clock duration of one buffer; a longer gap means we fell
    # behind and the kernel/libiio dropped samples.
    buf_dur_s = config.RX_BUFFER_SIZE / config.SAMPLE_RATE
    drop_gap_s = buf_dur_s * 1.5
    prev_read_end = time.monotonic()

    while state.running.is_set():
        try:
            samples = src.read()
        except Exception as e:
            print(f"[RX] libiio read error ({e}) -- connection lost.  "
                  "libiio cannot recover in-process; exiting so "
                  "supervisor/systemd/Docker can restart.", flush=True)
            _teardown(src, state)
            os._exit(_EXIT_CODE_CONNECTION_LOST)

        now = time.monotonic()
        gap = now - prev_read_end
        prev_read_end = now
        if gap > drop_gap_s:
            state.rx_overflows += 1
            try:
                state.drop_queue.put_nowait(state.buf_write_idx)
            except Exception:
                pass
            if state.rx_overflows <= 20 or state.rx_overflows % 100 == 0:
                print(f"[RX] WARNING: probable sample drop #{state.rx_overflows} "
                      f"(gap={gap * 1000:.1f}ms > {drop_gap_s * 1000:.1f}ms)")

        running_peak = _update_peak(state, samples, running_peak)
        _write_ring(state, samples)

        if config.RX_GAIN_MODE == "manual" and state.rx_gain_dB != cur_gain:
            new_gain = int(max(config.RX_GAIN_MIN_DB,
                               min(config.RX_GAIN_MAX_DB, state.rx_gain_dB)))
            try:
                src.set_gain(new_gain)
                cur_gain = new_gain
                state.rx_gain_dB = cur_gain
                print(f"[GAIN] Set to {cur_gain} dB")
            except Exception as e:
                print(f"[GAIN] Failed to set {new_gain} dB: {e}")
                state.rx_gain_dB = cur_gain

        if state.lo_freq_hz != cur_freq:
            new_freq = state.lo_freq_hz
            try:
                src.set_frequency(new_freq)
                cur_freq = new_freq
                print(f"[LO] Tuned to {cur_freq / 1e9:.6f} GHz")
            except Exception as e:
                print(f"[LO] Failed to set {new_freq}: {e}")
                state.lo_freq_hz = cur_freq

    _teardown(src, state)
    if config.VERBOSE:
        print("[RX] Stopped.")


def _connect(state: SharedState) -> LibiioSource | None:
    """Create the source, retrying until success or ``state.running`` clears."""
    uri = _libiio_uri()
    while state.running.is_set():
        try:
            src = LibiioSource(uri)
            state.rx_connected.set()
            state.rx_gain_dB = config.RX_INITIAL_GAIN_DB
            print(f"[RX] Connected -- {src.info_string}, "
                  f"gain_mode={config.RX_GAIN_MODE}, "
                  f"gain={config.RX_INITIAL_GAIN_DB} dB")
            return src
        except Exception as e:
            print(f"[RX] SDR not found ({e}), "
                  f"retrying in {config.SDR_RETRY_INTERVAL_S}s...")
            for _ in range(config.SDR_RETRY_INTERVAL_S * 10):
                if not state.running.is_set():
                    print("[RX] Stopped (never connected).")
                    return None
                time.sleep(0.1)
    return None


def _teardown(src: LibiioSource, state: SharedState) -> None:
    state.rx_connected.clear()
    try:
        src.close()
    except Exception:
        pass
