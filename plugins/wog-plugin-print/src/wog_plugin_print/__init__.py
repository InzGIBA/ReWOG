"""WOG Dump plugin: 3D-print preparation (repair, orient, split, estimate).

Registers the ``print-export`` CLI command through the
``wog_dump.commands`` entry-point group:

    pip install ./plugins/wog-plugin-print
    wog-dump print-export ak47 --orient --bed-size 220x220x250 --estimate
"""

from __future__ import annotations

from .printprep import PRINT_SCHEMA, PartReport, prepare_print

__all__ = ["PRINT_SCHEMA", "PartReport", "prepare_print"]
