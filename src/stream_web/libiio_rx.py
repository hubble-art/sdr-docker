"""On-box RX backend (pyadi-iio) — *skeleton* (M2 deliverable).

This backend runs natively on the SDR's own Linux (Pluto+ Zynq-7010 first
target) and captures IQ via ``pyadi-iio`` against the local IIO context,
writing into the same shared-memory ring the GNU Radio host backend uses.

M1 (backend abstraction) ships this as a stub so backend selection is complete
and testable. The real capture loop lands in M2 — see
``docs/design/2026-06-03-onbox-decoder.md``.

Do NOT import ``adi`` (pyadi-iio) at module load: the module must be importable
on hosts without pyadi-iio so CI and backend-selection tests work. Import it
inside ``rx_loop`` once the loop actually runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import SharedState


def rx_loop(state: SharedState) -> None:  # noqa: ARG001
    """Blocking RX entry point (parity with ``gnuradio_rx.rx_loop``).

    Not yet implemented — the libiio capture loop is the M2 milestone.
    """
    raise NotImplementedError(
        "libiio_local backend (pyadi-iio capture) is the M2 milestone; "
        "see docs/design/2026-06-03-onbox-decoder.md"
    )
