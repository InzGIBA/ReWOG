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

On Windows the CLI is usually not on Blender's PATH: pip puts
`wog-dump.exe` into a Scripts folder that only a terminal sees. Set
"wog-dump path" in the add-on preferences to the full path of that file.
Find the folder with
`python -c "import sysconfig; print(sysconfig.get_path('scripts'))"`.
Refresh catalog reports a clear error when the path is wrong.

## Usage

N-panel > WOG Dump:

- Refresh (the button next to the list) runs `wog-dump catalog --json`
  and lists every weapon with its status. The Filter box narrows the
  list by name.
- Animations (on by default) builds scenes with the weapon's operate
  animation (`build-scene --animations`): fire, reload and safety
  switch, as one action on the moving parts.
- Import scene builds the selected weapon when it has no scene yet,
  then imports the GLB. Blender waits while the CLI runs.
- Rebuild and import runs `build-scene` again for the selected weapon
  (this applies a changed Animations option), then imports the GLB.
- Build missing (selected) builds every listed weapon without a scene,
  one after another.

With Animations on, the import sets the scene frame range to the clip
(about 75 seconds for an AK-47). Play it from the Timeline or with
Space, and scrub with the timeline. Without Animations the scene is
static.

## Architecture

`wog_client.py` is deliberately bpy-free (subprocess + JSON parsing),
so it unit-tests outside Blender; `__init__.py` holds the UI. The
addon talks only to the CLI: no database, no Python deps inside
Blender, no network. The catalog JSON schema is checked
(`wog-dump.catalog/3`); a mismatch tells you to update the toolkit
instead of misbehaving.
