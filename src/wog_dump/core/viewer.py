"""Self-contained HTML viewer generation for exported scenes.

``render_viewer_html`` inlines the GLB (base64 data URI) and the
vendored three.js runtime (import-map data URIs) into one HTML file
that works offline from a file:// URL. ``linked`` mode writes a plain
HTML that references ``./vendor/`` and the GLB next to it instead;
``copy_viewer_assets`` supplies the files for that variant.
"""

from __future__ import annotations

import base64
import json
import string
from importlib import resources
from importlib.abc import Traversable
from pathlib import Path

from ..utils.logging import get_logger

__all__ = ["copy_viewer_assets", "render_viewer_html"]

_VENDOR_BASE = "three/addons"
# Vendored runtime files, copied verbatim into linked-mode output
_VENDOR_FILES = (
    "three.module.min.js",
    "addons/loaders/GLTFLoader.js",
    "addons/controls/OrbitControls.js",
    "addons/utils/BufferGeometryUtils.js",
)


def _vendor_file(relative: str) -> Traversable:
    return resources.files("wog_dump") / "resources" / "viewer" / "vendor" / relative


def _resource(relative: str) -> Traversable:
    return resources.files("wog_dump") / "resources" / "viewer" / relative


def _data_uri(data: bytes, mime: str = "text/javascript") -> str:
    payload = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{payload}"


def _import_map(linked: bool) -> str:
    """Import map wiring the vendored modules for either output mode."""
    if linked:
        mapping: dict[str, str] = {
            "three": "./vendor/three.module.min.js",
            f"{_VENDOR_BASE}/": "./vendor/addons/",
        }
    else:
        mapping = {
            "three": _data_uri(_vendor_file("three.module.min.js").read_bytes()),
            f"{_VENDOR_BASE}/loaders/GLTFLoader.js": _data_uri(
                _vendor_file("addons/loaders/GLTFLoader.js").read_bytes()
            ),
            f"{_VENDOR_BASE}/controls/OrbitControls.js": _data_uri(
                _vendor_file("addons/controls/OrbitControls.js").read_bytes()
            ),
            f"{_VENDOR_BASE}/utils/BufferGeometryUtils.js": _data_uri(
                _vendor_file("addons/utils/BufferGeometryUtils.js").read_bytes()
            ),
        }
    return json.dumps({"imports": mapping}, indent=2)


def render_viewer_html(glb: bytes, manifest: dict, *, linked: bool = False) -> str:
    """Build the viewer HTML for one exported scene.

    ``linked=False`` embeds the GLB and the whole three.js runtime so a
    single file opens offline. ``linked=True`` references the GLB and
    ``./vendor/`` as relative paths (see ``copy_viewer_assets``).
    """
    logger = get_logger()
    if linked:
        glb_url = f"./{manifest.get('weapon', 'weapon')}.glb"
    else:
        glb_url = _data_uri(glb, "model/gltf-binary")

    config = json.dumps(
        {
            "glbUrl": glb_url,
            "manifest": {
                "weapon": manifest.get("weapon", ""),
                "state": manifest.get("state", "assembled"),
                "parts": manifest.get("parts", []),
                "field_strip": manifest.get("field_strip", []),
                "annotations": manifest.get("annotations", []),
                "meshes": manifest.get("meshes", 0),
            },
        }
    )
    # JSON inside a script tag must not terminate the element
    config = config.replace("</", "<\\/")

    template = _resource("template.html").read_text(encoding="utf-8")
    viewer_js = _resource("viewer.js").read_text(encoding="utf-8")

    html = string.Template(template).substitute(
        TITLE=manifest.get("weapon", "weapon"),
        IMPORT_MAP=_import_map(linked),
        CONFIG_JSON=config,
        VIEWER_JS=viewer_js,
    )
    size_mb = len(html.encode("utf-8")) / 1e6
    glb_mb = max(len(glb) / 1e6, 0.001)
    if not linked and size_mb > glb_mb * 1.5 + 1.5:
        logger.warning(
            f"viewer.html is {size_mb:.1f} MB (vendor runtime overhead "
            f"on a {glb_mb:.1f} MB scene); consider --viewer-link"
        )
    return html


def copy_viewer_assets(output_dir: Path) -> Path:
    """Copy the vendored three.js runtime next to a linked viewer."""
    target = output_dir / "vendor"
    target.mkdir(parents=True, exist_ok=True)
    for relative in _VENDOR_FILES:
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(_vendor_file(relative).read_bytes())
    return target
