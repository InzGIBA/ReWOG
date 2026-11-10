# Disassembly data research (task 01)

Findings from inspecting decrypted weapon bundles for the disassembly
states feature. All checks ran against `runtime/decrypted/` with UnityPy
in November 2026. Bundles inspected in depth: ak47, ak74n, aks_74u.
Coverage sample: 12 random weapons from the 286 stored bundles.

## What the bundles contain

### Per-part removal anchors (`_in` / `_out` nodes)

Every weapon keeps a flat set of placeholder transforms as direct
children of the weapon root. Names come in pairs: `<part>_in` and
`<part>_out` (the `<part>` part sometimes carries a leading underscore,
example `_trigger_group_out`). The `_out` node sits at the position
where the game puts the part after field-strip removal.

Verified examples (ak47, Unity centimeters, root space):

| anchor | `_in` position | `_out` position | reading |
| --- | --- | --- | --- |
| barrel | (-15.51, 21.46, 0.00) | (-53.86, 21.53, 0.01) | barrel slides forward, out of the receiver |
| pistol_grip | (10.04, 12.57, 0.01) | (13.02, 5.65, 0.00) | grip drops below the weapon |
| _trigger_group | (4.89, 24.57, -1.49) | (4.72, 21.27, -1.71) | trigger group drops slightly |
| _gas_port | (0.00, 0.00, 0.00) | (-39.89, 21.91, -1.14) | gas tube slides forward |

Coverage across the 12-weapon sample: 36 to 114 anchor pairs per
weapon, present in every sampled weapon. The `_in` positions do not
reliably equal the part's assembled world position (nested parents,
name reuse), so the pipeline uses only `_out` positions as removal
targets. Some anchors sit at the origin (0, 0, 0), these are unused
slots and get skipped.

The `_in`/`_out` anchors carry no meshes, so the assembled export
ignores them (they land in the `empty` skip bucket of the manifest).

### Parts list (`<weapon>_parts` TextAsset)

Present in every sampled weapon. Numbered lines (`1: buttstock_base`),
the numbering is the game's removal order. ak47 lists 96 entries and
all 96 match a GameObject name in the hierarchy exactly. The existing
parser in `core/scene.py` already keeps file order, so the list order
is the removal order for layout purposes.

### Field strip list (`<weapon>_field_strip` TextAsset)

Rare: only 1 of 12 sampled weapons has it (ak47). Names are unreliable:
6 of the 10 ak47 entries do not match any hierarchy name
(`reciver_cover`, `recoi_spring`, `upper_hand_guard_assembly`, ...).
Decision: the anchors are the source of truth for removal positions,
the text list stays as manifest metadata only.

### AnimationClip (`Take 001`)

Exactly one clip per weapon, legacy format. It drives the operate
animation (fire, reload, safety), not the disassembly: 33 of 177
position curves move, and the moving paths are magazine follower and
bullet cases (displacements 1.2 to 17.5 Unity units). No assembly
sequence exists in the bundle.

Consequence for later tasks: T-02 (glTF animations) exports the
operate animation. T-17 (assembly steps) builds step positions from
the anchors, not from animation data.

### Non-findings

`ak47_hd_scaled` lists HD-upgrade node groups, not removal data.
`ak47_hd_edges_bin` is binary edge geometry for HD rendering.
`<weapon>_script_operate` maps operate actions to parts, confirms the
clip interpretation above. glock17 and m4a1 bundles were not present
in the local cache (not downloaded), not empty files.

## Decisions per scene state

- **assembled**: untouched pipeline, byte-identical output.
- **field-strip**: every part matched to a valid `_out` anchor moves so
  its world position lands exactly on the anchor position. Parts
  nested inside an already moved part ride along (one offset per
  containment chain, keeps the game hierarchy intact).
- **exploded**: every top-level mesh part is displaced, uniform
  magnitude `--explode-distance` (meters, default 0.35). Anchored
  parts move along their removal direction (anchor position minus
  assembled position), parts without an anchor get a radial direction
  from the parts centroid.
- **parts**: grid layout over the `<weapon>_parts` list order.
  Containment-filtered top-level parts get grid cells sized from their
  subtree bounds, bottoms on the y=0 plane.

Matching normalizes names: lowercase, leading underscores stripped,
exact match first, then prefix match of at least 4 characters.
Anchor bases never match the anchor nodes themselves.

## Known limits

- Nested parts (example: trigger inside `_trigger_group`) ride with
  their parent in field-strip and exploded states instead of taking
  an independent position. Recorded in the manifest as rode-along.
- Name reuse across the hierarchy (ak47 has 540 nodes, short names
  repeat) means a match takes the first mesh-bearing node in tree
  order. The `_parts` list matched without collisions on the
  inspected weapons.
- The `_in` anchors are ignored. A future pass could use them to
  verify assembled positions per part.
