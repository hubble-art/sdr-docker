"""Unit tests for the on-box libiio_rx backend, with pyadi-iio mocked out.

No hardware and no pyadi-iio install required: we inject a fake ``adi`` module
so ``LibiioSource`` and the ring-write helpers can be exercised in CI.
"""

import sys
import types

import numpy as np
import pytest

from stream_web import config, libiio_rx

# ---------------------------------------------------------------------------
# Fake adi / ad9361
# ---------------------------------------------------------------------------

class _FakeAD9361:
    """Minimal stand-in for adi.ad9361 returning deterministic IQ buffers."""

    def __init__(self, uri=""):
        self.uri = uri
        self.rx_lo = 0
        self.sample_rate = 0
        self.rx_rf_bandwidth = 0
        self.rx_enabled_channels = []
        self.rx_buffer_size = 0
        self.gain_control_mode_chan0 = ""
        self.rx_hardwaregain_chan0 = 0.0
        self._counter = 0

    def rx(self):
        # Full-scale ramp so we can verify ±1.0 normalization (2048 -> 1.0).
        n = self.rx_buffer_size or 8
        self._counter += 1
        return np.full(n, 2048 + 2048j, dtype=np.complex128)


@pytest.fixture
def fake_adi(monkeypatch):
    mod = types.ModuleType("adi")
    mod.ad9361 = _FakeAD9361
    monkeypatch.setitem(sys.modules, "adi", mod)
    return mod


# ---------------------------------------------------------------------------
# LibiioSource
# ---------------------------------------------------------------------------

class TestLibiioSource:
    def test_configures_device_from_config(self, fake_adi):
        src = libiio_rx.LibiioSource("ip:192.168.2.1")
        sdr = src._sdr
        assert sdr.rx_lo == int(config.CENTER_FREQ_HZ)
        assert sdr.sample_rate == int(config.SAMPLE_RATE)
        assert sdr.rx_rf_bandwidth == int(config.RF_BANDWIDTH)
        assert sdr.rx_enabled_channels == [0]
        assert sdr.rx_buffer_size == int(config.RX_BUFFER_SIZE)

    def test_read_normalizes_to_unit_scale(self, fake_adi):
        src = libiio_rx.LibiioSource("ip:test")
        out = src.read()
        assert out.dtype == np.complex64
        # 2048 raw counts -> 1.0 after /2048
        assert np.isclose(out.real.max(), 1.0)
        assert np.isclose(out.imag.max(), 1.0)

    def test_set_gain_switches_to_manual(self, fake_adi):
        src = libiio_rx.LibiioSource("ip:test")
        src.set_gain(42)
        assert src._sdr.gain_control_mode_chan0 == "manual"
        assert src._sdr.rx_hardwaregain_chan0 == 42.0

    def test_set_frequency(self, fake_adi):
        src = libiio_rx.LibiioSource("ip:test")
        src.set_frequency(2_400_000_000)
        assert src._sdr.rx_lo == 2_400_000_000


# ---------------------------------------------------------------------------
# Ring-write helper (mirrors gnuradio_rx wrap-around semantics)
# ---------------------------------------------------------------------------

class _FakeState:
    def __init__(self, size):
        self.iq_buffer = np.zeros(size, dtype=np.complex64)
        self._wi = 0

    @property
    def buf_write_idx(self):
        return self._wi

    @buf_write_idx.setter
    def buf_write_idx(self, v):
        self._wi = v


class TestRingWrite:
    def test_simple_write_advances_index(self, monkeypatch):
        monkeypatch.setattr(config, "IQ_BUFFER_SIZE", 16)
        st = _FakeState(16)
        samples = np.arange(4, dtype=np.complex64)
        libiio_rx._write_ring(st, samples)
        assert st.buf_write_idx == 4
        assert np.array_equal(st.iq_buffer[:4], samples)

    def test_wraparound(self, monkeypatch):
        monkeypatch.setattr(config, "IQ_BUFFER_SIZE", 8)
        st = _FakeState(8)
        st.buf_write_idx = 6
        samples = np.array([1, 2, 3, 4], dtype=np.complex64)
        libiio_rx._write_ring(st, samples)
        # 2 samples at tail (idx 6,7), 2 wrap to front (idx 0,1)
        assert st.buf_write_idx == 2
        assert st.iq_buffer[6] == 1
        assert st.iq_buffer[7] == 2
        assert st.iq_buffer[0] == 3
        assert st.iq_buffer[1] == 4


# ---------------------------------------------------------------------------
# URI resolution
# ---------------------------------------------------------------------------

class TestUriResolution:
    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("LIBIIO_URI", "ip:10.0.0.5")
        assert libiio_rx._libiio_uri() == "ip:10.0.0.5"

    def test_local_when_on_pluto(self, monkeypatch):
        monkeypatch.delenv("LIBIIO_URI", raising=False)
        monkeypatch.setattr(config, "_on_pluto", lambda: True)
        assert libiio_rx._libiio_uri() == "local:"

    def test_falls_back_to_pluto_uri(self, monkeypatch):
        monkeypatch.delenv("LIBIIO_URI", raising=False)
        monkeypatch.setattr(config, "_on_pluto", lambda: False)
        monkeypatch.setattr(config, "PLUTO_URI", "ip:192.168.2.1")
        assert libiio_rx._libiio_uri() == "ip:192.168.2.1"
