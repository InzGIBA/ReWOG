"""WOG Dump plugin: turntable previews and videos via headless Blender.

Registers the ``render`` CLI command through the ``wog_dump.commands``
entry-point group:

    pip install ./plugins/wog-plugin-render
    wog-dump render ak47

Blender itself is an external binary located through ``WOG_BLENDER_PATH``
or PATH; it is not a pip dependency.
"""

from __future__ import annotations
