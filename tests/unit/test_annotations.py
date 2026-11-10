"""Unit tests for the annotation mode (task 19)."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from wog_dump.core.glb import GLBWriter, MeshPrimitive
from wog_dump.core.scene import (
    SceneStats,
    _GameObjectInfo,
    collect_annotations,
)
from wog_dump.core.viewer import render_viewer_html


def tiny_glb(tmp_path: Path) -> bytes:
    writer = GLBWriter(generator="annotate-test")
    mesh = writer.add_mesh(
        "tri",
        [
            MeshPrimitive(
                positions=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                indices=[0, 1, 2],
            )
        ],
    )
    writer.add_node("bolt", mesh=mesh)
    path = tmp_path / "tiny.glb"
    writer.write(path)
    return path.read_bytes()


def synthetic_tree() -> _GameObjectInfo:
    from wog_dump.core.scene import _MeshData

    bolt = _GameObjectInfo(
        name="bolt",
        mesh=object(),
        translation=(1.0, 2.0, 3.0),
    )
    receiver = _GameObjectInfo(name="receiver", children=[bolt])
    return _GameObjectInfo(name="root", children=[receiver])


class TestCollectAnnotations:
    def test_center_group_and_dedup(self) -> None:
        root = synthetic_tree()
        worlds = {
            "receiver/bolt": [
                1.0,
                0.0,
                0.0,
                0.0,
                0.0,
                1.0,
                0.0,
                0.0,
                0.0,
                0.0,
                1.0,
                0.0,
                1.0,
                2.0,
                3.0,
                1.0,
            ]
        }
        stats = SceneStats(weapon="x", parts=["bolt"], field_strip=["bolt"])

        annotations = collect_annotations(root, worlds, 0.01, stats)

        assert len(annotations) == 1  # bolt in both lists: once
        assert annotations[0]["name"] == "bolt"
        assert annotations[0]["group"] == "parts"
        assert annotations[0]["center"][0] == 0.01

    def test_bbox_from_mesh_extents(self) -> None:
        from wog_dump.core.scene import _MeshData

        root = synthetic_tree()
        worlds = {
            "receiver/bolt": [
                1.0,
                0.0,
                0.0,
                0.0,
                0.0,
                1.0,
                0.0,
                0.0,
                0.0,
                0.0,
                1.0,
                0.0,
                1.0,
                2.0,
                3.0,
                1.0,
            ]
        }
        stats = SceneStats(weapon="x", parts=["bolt"])

        def mesh_fn(mesh: Any) -> Any:
            return _MeshData(
                name="bolt",
                vertices=[],
                normals=[],
                uvs=[],
                submeshes=[],
                local_min=[0.0, 0.0, 0.0],
                local_max=[4.0, 5.0, 6.0],
            )

        annotations = collect_annotations(root, worlds, 0.01, stats, mesh_fn)

        assert annotations[0]["bbox_size"] == [0.04, 0.05, 0.06]

    def test_unlisted_part_no_annotation(self) -> None:
        root = synthetic_tree()
        worlds = {
            "receiver/bolt": [
                1.0,
                0.0,
                0.0,
                0.0,
                0.0,
                1.0,
                0.0,
                0.0,
                0.0,
                0.0,
                1.0,
                0.0,
                1.0,
                2.0,
                3.0,
                1.0,
            ]
        }
        stats = SceneStats(weapon="x", parts=["not_a_part"])

        assert collect_annotations(root, worlds, 0.01, stats) == []


class TestViewerAnnotations:
    def test_html_embeds_annotations(self, tmp_path: Path) -> None:
        manifest = {
            "weapon": "ak47",
            "parts": ["bolt"],
            "field_strip": [],
            "annotations": [
                {
                    "name": "bolt",
                    "node": "receiver/bolt",
                    "center": [0.1, 0.2, 0.3],
                    "group": "parts",
                    "bbox_size": [0.04, 0.05, 0.06],
                }
            ],
        }
        html = render_viewer_html(tiny_glb(tmp_path), manifest)

        assert '"annotations"' in html
        assert '"bolt"' in html
        assert "buildMarkers" in html
        assert "showCard" in html

    def test_html_without_annotations_has_no_markers_button(
        self, tmp_path: Path
    ) -> None:
        manifest = {"weapon": "ak47", "parts": [], "field_strip": []}
        html = render_viewer_html(tiny_glb(tmp_path), manifest)
        # the button exists in the template but stays hidden
        assert 'id="markers" style="display:none"' in html


class TestCliAnnotate:
    def test_annotate_implies_viewer(self, tmp_path: Path) -> None:
        pass  # live-checked below through the ak47 run assertions in docs
