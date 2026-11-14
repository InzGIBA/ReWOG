"""Optional GLB optimization through gltfpack (meshopt compression).

Follows the Blender discovery pattern: ``WOG_GLTFPACK_PATH`` first,
then PATH. When gltfpack is absent the scene stays uncompressed, so
the flag never hard-fails a build.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

__all__ = ["find_gltfpack", "optimize_glb"]


def find_gltfpack() -> Path | None:
    """Locate gltfpack: WOG_GLTFPACK_PATH first, then PATH."""
    configured = os.environ.get("WOG_GLTFPACK_PATH")
    if configured:
        path = Path(configured)
        return path if path.is_file() else None
    found = shutil.which("gltfpack")
    return Path(found) if found else None


def optimize_glb(glb_path: Path, logger: Any) -> bool:
    """Compress a GLB in place; False when gltfpack is missing or fails.

    ``-kn -ke`` keep named nodes and extras (the viewer addresses parts
    by node name), ``-cc`` applies meshopt compression, which the
    vendored viewer decodes through MeshoptDecoder.
    """
    binary = find_gltfpack()
    if binary is None:
        logger.warning(
            "gltfpack not found: set WOG_GLTFPACK_PATH or install gltfpack; "
            "scene left uncompressed"
        )
        return False

    tmp_path = glb_path.with_name(glb_path.name + ".opt.tmp")
    try:
        result = subprocess.run(
            [
                str(binary),
                "-i",
                str(glb_path),
                "-o",
                str(tmp_path),
                "-kn",
                "-ke",
                "-cc",
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0 or not tmp_path.is_file():
            detail = result.stderr.strip()[:200]
            logger.warning(f"gltfpack failed (exit {result.returncode}): {detail}")
            return False
        os.replace(tmp_path, glb_path)
        return True
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning(f"gltfpack optimization failed: {e}")
        return False
    finally:
        tmp_path.unlink(missing_ok=True)
