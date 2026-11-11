# Getting started

## Install

```bash
pip install rewog
# USD/USDZ export support:
pip install "rewog[usd]"
```

From source for development:

```bash
git clone https://github.com/InzGIBA/ReWOG
cd ReWOG
uv pip install -e .
```

## First extraction

```bash
# 1. Create a game account and log in (once)
wog-dump register

# 2. Weapon list, keys, download, decrypt, unpack, normal maps
wog-dump full-pipeline --update-keys --convert-normals

# 3. Blender-ready scene
wog-dump build-scene ak47
```

Open `runtime/scenes/ak47/ak47.glb` in Blender, or run the generated
setup script:

```bash
blender --background --python runtime/scenes/ak47/blender_setup.py
```

## Disassembly states and more

```bash
wog-dump build-scene ak47 --state field-strip   # game removal positions
wog-dump build-scene ak47 --state exploded      # pushed-apart view
wog-dump build-scene ak47 --viewer              # offline HTML viewer
wog-dump build-scene ak47 --animations          # operate animation
wog-dump build-scene ak47 --format stl,usdz     # extra formats
wog-dump render ak47                            # turntable via Blender
wog-dump catalog                                # what do I have?
```

Every command documents itself: `wog-dump --help`,
`wog-dump <command> --help`.
