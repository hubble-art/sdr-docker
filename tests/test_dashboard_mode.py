"""Regression test for the DASHBOARD_MODE lazy-import contract.

In packets_only mode (on-box default), importing the processor must NOT pull in
matplotlib or Pillow — that's the whole point of the mode on constrained
hardware. We check in a fresh subprocess so unrelated test imports can't
pollute sys.modules.
"""

import subprocess
import sys


def _import_check(dashboard_mode: str) -> set[str]:
    """Return which heavy modules are loaded after importing the processor."""
    code = (
        "import sys, stream_web.processor;"
        "print('matplotlib' in sys.modules, 'PIL' in sys.modules)"
    )
    env = {"DASHBOARD_MODE": dashboard_mode, "SDR_TYPE": "mock", "PATH": "/usr/bin:/bin"}
    # Preserve venv/site-packages resolution.
    import os
    full_env = dict(os.environ)
    full_env.update(env)
    out = subprocess.check_output(
        [sys.executable, "-c", code], env=full_env, text=True
    ).strip()
    mpl, pil = out.split()
    loaded = set()
    if mpl == "True":
        loaded.add("matplotlib")
    if pil == "True":
        loaded.add("PIL")
    return loaded


def test_packets_only_does_not_import_matplotlib_or_pil():
    loaded = _import_check("packets_only")
    assert loaded == set(), f"packets_only must not load heavy deps, got: {loaded}"
