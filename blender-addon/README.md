# WOG Dump Blender addon

Native Blender extension (4.2+) over the `wog-dump` CLI: browse the
weapon catalog with statuses in a sidebar panel, build missing scenes,
import the GLB straight into the current blend file.

## Install

1. Install the toolkit: `pip install rewog` (check `wog-dump --help`
   runs in a terminal).
2. Blender: Edit > Preferences > Add-ons > Install from Disk, pick the
   `wog_dump_addon.zip` from a release (or build it with
   `python scripts/build_addon.py`).
3. Enable "WOG Dump" in the add-ons list. Set the runtime directory in
   the addon preferences if your `runtime/` is not the Blender working
   directory.

## Usage

N-panel > WOG Dump: Refresh lists the catalog (`wog-dump catalog
--json`). Double-select a weapon, Import scene builds it if needed
(subprocess to the CLI, minutes for a first build) and imports the GLB.
Build missing (selected) builds every non-ready selected weapon
sequentially.

## Architecture

`wog_client.py` is deliberately bpy-free (subprocess + JSON parsing),
so it unit-tests outside Blender; `__init__.py` holds the UI. The
addon talks only to the CLI: no database, no Python deps inside
Blender, no network. The catalog JSON schema is checked
(`wog-dump.catalog/1`); a mismatch tells you to update the toolkit
instead of misbehaving.
