"""Tests for RX backend selection (config + factory dispatch).

These never import GNU Radio or pyadi-iio: they exercise the name-resolution
and dispatch logic only, so they run in CI / SDR_TYPE=mock.
"""

import builtins

import pytest

from stream_web import config, rx_backend


class TestResolveBackendName:
    def test_defaults_to_config(self, monkeypatch):
        monkeypatch.setattr(config, "RX_BACKEND", "gnuradio_soapy")
        assert rx_backend.resolve_backend_name() == "gnuradio_soapy"

    def test_explicit_override(self):
        assert rx_backend.resolve_backend_name("libiio_local") == "libiio_local"

    def test_case_insensitive(self):
        assert rx_backend.resolve_backend_name("LibIIO_Local") == "libiio_local"

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown RX_BACKEND"):
            rx_backend.resolve_backend_name("totally_made_up")

    def test_known_backends_contract(self):
        # /api/status and docs depend on exactly these two names.
        assert rx_backend.KNOWN_BACKENDS == ("gnuradio_soapy", "libiio_local")


class TestMakeRxBackend:
    def test_libiio_dispatch_returns_callable(self):
        # libiio backend imports cleanly (no adi import at module load) and is
        # the entry point the factory should hand back for on-box mode.
        from stream_web import libiio_rx
        rx_loop = rx_backend.make_rx_backend("libiio_local")
        assert rx_loop is libiio_rx.rx_loop

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown RX_BACKEND"):
            rx_backend.make_rx_backend("nope")

    def test_gnuradio_dispatch_imports_sdr_shim(self, monkeypatch):
        # We don't have GNU Radio in CI, so assert the factory tries to import
        # the host shim (.sdr) for gnuradio_soapy rather than the libiio path.
        real_import = builtins.__import__
        attempted: list[str] = []

        def tracking_import(name, *args, **kwargs):
            attempted.append(name)
            if name.endswith("sdr") or name == "stream_web.sdr":
                raise ImportError("simulated: GNU Radio absent in CI")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", tracking_import)
        with pytest.raises(ImportError, match="GNU Radio absent"):
            rx_backend.make_rx_backend("gnuradio_soapy")


class TestOnPlutoHeuristic:
    def test_returns_bool(self):
        assert isinstance(config._on_pluto(), bool)

    def test_false_when_no_iio_devices(self, monkeypatch):
        def no_iio(path, *args, **kwargs):
            raise OSError("no such file")

        monkeypatch.setattr("builtins.open", no_iio)
        assert config._on_pluto() is False

    def test_true_when_ad9361_present(self, monkeypatch):
        import io

        def fake_open(path, *args, **kwargs):
            if "iio:device0/name" in str(path):
                return io.StringIO("ad9361-phy")
            raise OSError("no such file")

        monkeypatch.setattr("builtins.open", fake_open)
        assert config._on_pluto() is True
