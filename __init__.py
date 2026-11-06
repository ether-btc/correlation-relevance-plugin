"""Hermes plugin entrypoint for correlation-relevance.

The plugin root goes on sys.path before anything else imports, so the bundled
`correlation_lib` package resolves whether Hermes loads this directory as a plugin or a test
runner imports it from the repo root.
"""

from __future__ import annotations

import pathlib
import sys

_HERE = str(pathlib.Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from correlation_plugin import register  # noqa: E402  (needs the sys.path bootstrap above)

__all__ = ["register"]
