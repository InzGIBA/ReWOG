# CLI usage

## Stage by stage

```bash
wog-dump info                          # configuration and status
wog-dump download-weapons              # fetch the weapon catalog
wog-dump download-assets --update-keys # download bundles + fresh keys
wog-dump decrypt-assets                # XOR-decrypt the bundles
wog-dump unpack-assets                 # extract models and textures
wog-dump build-scene ak47              # Blender-ready glTF scene
wog-dump convert-normals ./unpacked --recursive   # fix normal maps
wog-dump render ak47                   # turntable previews
wog-dump catalog                       # per-weapon status table
```

## Common options

| Option | Purpose | Example |
| --- | --- | --- |
| `--weapons` | Limit a command to specific weapons | `--weapons "ak74,m4a1"` |
| `--update-keys` | Fetch fresh decryption keys first | `download-assets --update-keys` |
| `--check-only` | Preview pending downloads | `download-assets --check-only` |
| `--state` | Disassembly pose: assembled, field-strip, exploded, parts | `build-scene ak47 --state exploded` |
| `--format` | Extra output formats: stl, usd, usdz, fbx | `build-scene ak47 --format stl` |
| `--viewer` | Self-contained HTML viewer | `build-scene ak47 --viewer` |
| `--animations` | Embed the operate animation | `build-scene ak47 --animations` |
| `--locale` | Message language (ru) | `--locale ru catalog` |
| `--no-progress` | Disable progress bars | CI and piped runs |

## When everything breaks

Run `wog-dump info --protocol-probe` first. It answers whether the
problem is local or the game protocol moved; any FAIL exits with
code 2 and points at the capture instructions.
