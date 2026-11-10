"""Integration coverage for states, steps and annotations on a real bundle.

Runs against ``runtime/decrypted/ak47.unity3d`` when present (skipped
otherwise). Exercises the full export paths that unit tests only mock:
disassembly states, assembly steps, animated build and annotations.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from wog_dump.core.config import get_config, set_config
from wog_dump.core.scene import SceneBuilder

REPO_ROOT = Path(__file__).resolve().parents[2]
AK47_BUNDLE = REPO_ROOT / "runtime" / "decrypted" / "ak47.unity3d"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not AK47_BUNDLE.exists(), reason="runtime/decrypted/ak47.unity3d not present"
    ),
]


@pytest.fixture(scope="module")
def builder(tmp_path_factory: pytest.TempPathFactory) -> SceneBuilder:
    set_config(base_dir=REPO_ROOT, decrypted_dir=REPO_ROOT / "runtime" / "decrypted")
    return SceneBuilder(get_config())


class TestStatesIntegration:
    def test_field_strip_moves_parts(
        self, builder: SceneBuilder, tmp_path: Path
    ) -> None:
        out = tmp_path / "ak47-field-strip"
        glb = builder.build_scene("ak47", state="field-strip", output_dir=out)
        manifest = json.loads((out / "scene.json").read_text())
        assert manifest["state"] == "field-strip"
        assert len(manifest["placements"]) > 0
        assert glb.exists()

    def test_exploded_distance_honored(
        self, builder: SceneBuilder, tmp_path: Path
    ) -> None:
        out = tmp_path / "ak47-exploded"
        builder.build_scene(
            "ak47", state="exploded", explode_distance=0.5, output_dir=out
        )
        manifest = json.loads((out / "scene.json").read_text())
        offsets = [p["offset"] for p in manifest["placements"]]
        assert offsets
        # every anchored part pushed at least ~0.4 m along its direction
        magnitudes = [max(abs(c) for c in offset) for offset in offsets]
        assert min(magnitudes) > 0.3

    def test_parts_grid_grounding(self, builder: SceneBuilder, tmp_path: Path) -> None:
        out = tmp_path / "ak47-parts"
        builder.build_scene("ak47", state="parts", output_dir=out)
        manifest = json.loads((out / "scene.json").read_text())
        assert manifest["state"] == "parts"
        assert len(manifest["placements"]) >= 10


class TestStepsIntegration:
    def test_steps_and_animated_build(
        self, builder: SceneBuilder, tmp_path: Path
    ) -> None:
        out = tmp_path / "ak47-steps"
        builder.build_scene("ak47", assembly_steps=3, animated=True, output_dir=out)
        steps = json.loads((out / "steps.json").read_text())
        assert steps["steps"] == 3
        assert steps["parts"] > 0
        assert steps["placed_per_step"][-1] == steps["parts"]
        for index in range(1, 4):
            assert (out / f"step_{index:02d}.glb").exists()
        assembly = out / "assembly.glb"
        assert assembly.exists()
        data = assembly.read_bytes()
        (json_len,) = struct.unpack_from("<I", data, 12)
        document = json.loads(data[20 : 20 + json_len])
        animations = document.get("animations", [])
        assert len(animations) == 1
        assert len(animations[0]["channels"]) == steps["parts"]


class TestAnnotationsIntegration:
    def test_annotate_manifest_and_glbf_identical(
        self, builder: SceneBuilder, tmp_path: Path
    ) -> None:
        plain_out = tmp_path / "plain"
        plain_glb = builder.build_scene("ak47", output_dir=plain_out)
        annotated_out = tmp_path / "annotated"
        annotated_glb = builder.build_scene(
            "ak47", annotate=True, output_dir=annotated_out
        )
        assert plain_glb.read_bytes() == annotated_glb.read_bytes()
        manifest = json.loads((annotated_out / "scene.json").read_text())
        annotations = manifest["annotations"]
        assert len(annotations) > 50
        assert (annotated_out / "viewer.html").exists()
        entry = annotations[0]
        assert entry["group"] in ("parts", "field_strip")
        assert len(entry["center"]) == 3
