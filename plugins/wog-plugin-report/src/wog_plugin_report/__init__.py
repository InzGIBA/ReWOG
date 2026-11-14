"""WOG Dump plugin: readable reports for built scenes.

Registers the ``scene-report`` CLI command through the
``wog_dump.commands`` entry-point group:

    pip install ./plugins/wog-plugin-report
    wog-dump scene-report ak47
"""

from __future__ import annotations

from .command import scene_report

__all__ = ["scene_report"]
