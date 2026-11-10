"""Build the Blender addon zip (blender-addon/wog_dump_addon).

Validates the manifest fields first; exits nonzero on anything missing.

Usage: python scripts/build_addon.py [--out DIR]
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ADDON_DIR = REPO_ROOT / "blender-addon"
PACKAGE = ADDON_DIR / "wog_dump_addon"
MANIFEST = ADDON_DIR / "blender_manifest.toml"


def validate_manifest() -> list[str]:
    required = ("schema_version", "id", "version", "name", "type", "license")
    text = MANIFEST.read_text(encoding="utf-8")
    missing = [key for key in required if f"{key} =" not in text]
    return missing


def build(out_dir: Path) -> Path:
    missing = validate_manifest()
    if missing:
        print(f"manifest missing fields: {missing}")
        sys.exit(1)
    if not (PACKAGE / "__init__.py").exists():
        print("addon package incomplete")
        sys.exit(1)

    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / "wog_dump_addon.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(MANIFEST, "wog_dump_addon/blender_manifest.toml")
        for path in sorted(PACKAGE.rglob("*.py")):
            archive.write(path, f"wog_dump_addon/{path.relative_to(PACKAGE)}")
    print(f"built {zip_path}")
    return zip_path


if __name__ == "__main__":
    out = (
        Path(sys.argv[sys.argv.index("--out") + 1])
        if "--out" in sys.argv
        else ADDON_DIR
    )
    sys.exit(0 if build(out) else 1)
