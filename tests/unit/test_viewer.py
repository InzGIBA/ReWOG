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

    def test_font_face_only_when_served(self, tmp_path: Path) -> None:
        """Offline files cannot reach the web static mount.

        The @font-face for the theme font is injected only when the
        caller provides font_url_base (the web UI does); embedded and
        CLI-linked viewers fall back to the system monospace stack.
        """
        glb = tiny_glb(tmp_path)
        offline = render_viewer_html(glb, manifest())
        assert "@font-face" not in offline
        linked = render_viewer_html(
            glb, manifest(), linked=True, font_url_base="/t/x/static"
        )
        assert "@font-face" in linked
        assert "/t/x/static/fonts/source-code-pro.woff2" in linked


class TestCopyViewerAssets:
    def test_vendor_files_landed(self, tmp_path: Path) -> None:
        target = copy_viewer_assets(tmp_path)
        assert (target / "three.module.min.js").stat().st_size > 100000
        assert (target / "addons/loaders/GLTFLoader.js").stat().st_size > 50000
        assert (target / "addons/controls/OrbitControls.js").stat().st_size > 10000
        assert (target / "addons/utils/BufferGeometryUtils.js").stat().st_size > 10000


class TestInteractiveViewer:
    def test_states_payload_reaches_config(self, tmp_path: Path) -> None:
        glb = tiny_glb(tmp_path)
        data = manifest()
        data["viewer_states"] = {
            "assembled": {"bolt": [0.0, 0.0, 0.0]},
            "exploded": {"bolt": [0.1, 0.0, 0.0]},
        }
        html = render_viewer_html(glb, data)
        config = re.search(
            r'<script type="application/json" id="wog-config">(.*?)</script>',
            html,
            re.DOTALL,
        )
        assert config is not None
        import json

        payload = json.loads(config.group(1).replace("<\\/", "</"))
        assert payload["states"]["exploded"]["bolt"] == [0.1, 0.0, 0.0]

    def test_interactive_controls_present(self, tmp_path: Path) -> None:
        html = render_viewer_html(tiny_glb(tmp_path), manifest())
        for element_id in (
            "state-select",
            "explode",
            "search",
            "hide",
            "isolate",
            "show-all",
            "screenshot",
            "clip-select",
            "play",
            "timeline",
            "lang",
        ):
            assert f'id="{element_id}"' in html

    def test_document_still_valid(self, tmp_path: Path) -> None:
        html = render_viewer_html(tiny_glb(tmp_path), manifest())
        validator = _Validator()
        validator.feed(html)
        assert validator.stack == []


class TestViewerStatePayload:
    def test_compute_viewer_states_covers_all_states(self) -> None:
        from types import SimpleNamespace

        from wog_dump.core.scene import SceneBuilder, _GameObjectInfo

        barrel = _GameObjectInfo(
            name="barrel",
            mesh=SimpleNamespace(m_Name="barrel_mesh"),
            translation=(-15.0, 21.0, 0.0),
        )
        root = _GameObjectInfo(name="root", children=[barrel])
        root.children.append(
            _GameObjectInfo(name="barrel_out", translation=(-53.0, 21.5, 0.0))
        )
        builder = SceneBuilder()
        builder._mesh_data = lambda mesh: SimpleNamespace(
            local_min=[-1.0, -1.0, -1.0], local_max=[1.0, 1.0, 1.0]
        )
        payload = builder._compute_viewer_states(root, 0.01, 0.35, ["barrel"])

        assert set(payload) == {"assembled", "field-strip", "exploded", "parts"}
        assert payload["assembled"]["barrel"] == [-0.15, 0.21, 0.0]
        assert payload["field-strip"]["barrel"] == [-0.53, 0.215, 0.0]
        # Exploded pushes along the removal direction (-X) by 0.35 m
        assert payload["exploded"]["barrel"][0] < -0.15
        assert "barrel" in payload["parts"]
        # The source tree stays assembled
        assert barrel.translation == (-15.0, 21.0, 0.0)
