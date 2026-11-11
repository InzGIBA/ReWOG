"""Unit tests for the HTML viewer generator."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

from wog_dump.core.glb import GLBWriter, MeshPrimitive
from wog_dump.core.viewer import copy_viewer_assets, render_viewer_html


def tiny_glb(tmp_path: Path) -> bytes:
    """A minimal valid GLB (one triangle mesh, no textures)."""
    writer = GLBWriter(generator="viewer-test")
    mesh = writer.add_mesh(
        "tri",
        [
            MeshPrimitive(
                positions=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                indices=[0, 1, 2],
            )
        ],
    )
    writer.add_node("bolt", mesh=mesh, translation=(0.0, 0.0, 0.0))
    path = tmp_path / "tiny.glb"
    writer.write(path)
    return path.read_bytes()


def manifest() -> dict:
    return {
        "weapon": "ak47",
        "state": "assembled",
        "parts": ["bolt", "magazine"],
        "field_strip": ["magazine"],
        "meshes": 1,
    }


class _Validator(HTMLParser):
    """Feeds the document to catch malformed markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag not in ("meta", "br", "img", "link", "input", "hr"):
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()


class TestRenderViewer:
    def test_embedded_html_is_self_contained(self, tmp_path: Path) -> None:
        glb = tiny_glb(tmp_path)
        html = render_viewer_html(glb, manifest())

        validator = _Validator()
        validator.feed(html)
        assert validator.stack == [], f"unclosed tags: {validator.stack}"

        assert "data:model/gltf-binary;base64," in html
        assert "data:text/javascript;base64," in html
        assert '"bolt"' in html
        assert '"magazine"' in html
        assert "<script src=" not in html
        assert 'href="http' not in html and 'src="http' not in html

    def test_linked_mode_references_relative_paths(self, tmp_path: Path) -> None:
        glb = tiny_glb(tmp_path)
        html = render_viewer_html(glb, manifest(), linked=True)

        assert "./vendor/three.module.min.js" in html
        assert "./ak47.glb" in html
        assert "data:model/gltf-binary" not in html

    def test_script_terminator_escaped(self, tmp_path: Path) -> None:
        glb = tiny_glb(tmp_path)
        tricky = manifest()
        tricky["parts"] = ["bolt</script>"]
        html = render_viewer_html(glb, tricky)
        assert "bolt<\\/script>" in html

    def test_no_external_urls(self, tmp_path: Path) -> None:
        glb = tiny_glb(tmp_path)
        html = render_viewer_html(glb, manifest())
        external = re.findall(r'(?:src|href)="https?://', html)
        assert external == []


class TestCopyViewerAssets:
    def test_vendor_files_landed(self, tmp_path: Path) -> None:
        target = copy_viewer_assets(tmp_path)
        assert (target / "three.module.min.js").stat().st_size > 100000
        assert (target / "addons/loaders/GLTFLoader.js").stat().st_size > 50000
        assert (target / "addons/controls/OrbitControls.js").stat().st_size > 10000
        assert (target / "addons/utils/BufferGeometryUtils.js").stat().st_size > 10000
