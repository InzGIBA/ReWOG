"""WOG Dump plugin: local web UI over the core pipeline.

Registers the ``serve`` CLI command through the ``wog_dump.commands``
entry-point group:

    pip install ./plugins/wog-plugin-web
    wog-dump serve
"""

from __future__ import annotations

__version__ = "0.1.0"
