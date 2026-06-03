"""RX backend selection.

`sdr-docker` supports two interchangeable receive backends that both fill the
same shared-memory IQ ring (see ``processor.py`` for the consumer):

- ``gnuradio_soapy`` — host mode. A host machine talks to the SDR over
  USB/Ethernet via GNU Radio's ``gr-soapy``. Implemented in ``gnuradio_rx.py``
  and re-exported through the ``sdr.py`` shim.
- ``libiio_local`` — on-box mode. ``sdr-docker`` runs natively on the SDR's own
  Linux (e.g. Pluto+ Zynq-7010) and captures via ``pyadi-iio`` against the
  local IIO context. Implemented in ``libiio_rx.py``.

Both backends expose the same blocking entry point::

    rx_loop(state) -> None

which runs until ``state.running`` is cleared (and may exit the process on an
unrecoverable connection loss, relying on a supervisor / systemd / Docker to
restart). ``app.py`` runs whichever entry point this factory returns in a
daemon thread, so the orchestration model is identical for both backends.

Imports of the concrete backends are deferred to call time so that merely
importing this module never requires GNU Radio or pyadi-iio to be installed
(important for CI / ``SDR_TYPE=mock``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from . import config

if TYPE_CHECKING:
    from .app import SharedState

KNOWN_BACKENDS = ("gnuradio_soapy", "libiio_local")

RxLoop = Callable[["SharedState"], None]


def resolve_backend_name(backend: str | None = None) -> str:
    """Return the validated backend name (defaults to ``config.RX_BACKEND``)."""
    name = (backend or config.RX_BACKEND).lower()
    if name not in KNOWN_BACKENDS:
        raise ValueError(
            f"Unknown RX_BACKEND={name!r}; expected one of {KNOWN_BACKENDS}"
        )
    return name


def make_rx_backend(backend: str | None = None) -> RxLoop:
    """Resolve and import the blocking RX entry point for the selected backend.

    The concrete module is imported lazily so this function only pulls in
    GNU Radio (host) or pyadi-iio (on-box) for the backend actually chosen.
    """
    name = resolve_backend_name(backend)
    if name == "gnuradio_soapy":
        from .sdr import rx_loop  # GNU Radio / gr-soapy host path
        return rx_loop
    # libiio_local
    from .libiio_rx import rx_loop  # pyadi-iio on-box path
    return rx_loop
