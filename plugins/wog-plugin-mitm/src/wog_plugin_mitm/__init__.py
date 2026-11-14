"""WOG Dump plugin: key/identity import from captured game traffic.

Registers the ``import-keys`` CLI command through the
``wog_dump.commands`` entry-point group:

    pip install ./plugins/wog-plugin-mitm
    wog-dump import-keys capture.flows --merge
"""

from __future__ import annotations

from .cli import import_keys

__all__ = ["import_keys"]
