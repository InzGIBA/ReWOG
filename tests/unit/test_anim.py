"""Unit tests for animation extraction and glTF animation writing."""

from __future__ import annotations

import json
import struct
from types import SimpleNamespace
from typing import Any

import pytest

from wog_dump.core.anim import ClipTracks, extract_tracks
from wog_dump.core.glb import AnimationChannel, GLBWriter, SceneError


def make_key(time: float, value: Any) -> SimpleNamespace:
    return SimpleNamespace(time=time, value=value)


def make_clip(
    name: str = "Take 001",
    positions: list[tuple[str, list[tuple[float, Any]]]] | None = None,
    rotations: list[tuple[str, list[tuple[float, Any]]]] | None = None,
) -> SimpleNamespace:
    """A UnityPy-shaped AnimationClip stand-in."""

    def curves(specs: list[tuple[str, list[tuple[float, Any]]]] | None) -> list:
        result = []
        for path, keys in specs or []:
            result.append(
                SimpleNamespace(
                    path=path,
                    curve=SimpleNamespace(m_Curve=[make_key(t, v) for t, v in keys]),
                )
            )
        return result

    return SimpleNamespace(
        m_Name=name,
        m_PositionCurves=curves(positions),
        m_RotationCurves=curves(rotations),
    )


class TestExtractTracks:
    def test_translation_and_rotation_tracks(self) -> None:
        clip = make_clip(
            positions=[
                ("receiver/bolt", [(0.0, (1, 2, 3)), (1.5, (4, 5, 6))]),
            ],
            rotations=[
                ("receiver/bolt", [(0.0, (0, 0, 0, 1)), (1.5, (0, 0.7, 0, 0.7))]),
            ],
        )

        result = extract_tracks(clip)

        assert result is not None
        assert isinstance(result, ClipTracks)
        assert result.name == "Take 001"
        assert [t.property for t in result.tracks] == ["translation", "rotation"]
        assert result.tracks[0].times == [0.0, 1.5]
        assert result.tracks[0].values == [(1.0, 2.0, 3.0), (4.0, 5.0, 6.0)]
        assert result.tracks[1].values[1] == (0.0, 0.7, 0.0, 0.7)
        assert result.duration == 1.5

    def test_empty_clip_returns_none(self) -> None:
        assert extract_tracks(make_clip()) is None

    def test_scale_curves_ignored(self) -> None:
        clip = make_clip()
        clip.m_ScaleCurves = [
            SimpleNamespace(
                path="receiver",
                curve=SimpleNamespace(m_Curve=[make_key(0.0, (1, 1, 1))]),
            )
        ]

        assert extract_tracks(clip) is None

    def test_curve_without_keys_skipped(self) -> None:
        clip = make_clip()
        clip.m_PositionCurves = [SimpleNamespace(path="receiver", curve=None)]

        assert extract_tracks(clip) is None


class TestGLBAnimationWriting:
    def _writer_with_node(self) -> tuple[GLBWriter, int]:
        writer = GLBWriter()
        node = writer.add_node("bolt", translation=(0.0, 0.0, 0.0))
        return writer, node

    def test_animation_document_structure(self, tmp_path: Any) -> None:
        writer, node = self._writer_with_node()
        writer.add_animation(
            "Take 001",
            [
                AnimationChannel(
                    node_index=node,
                    property="translation",
                    times=[0.0, 0.5, 1.0],
                    values=[(0, 0, 0), (1, 0, 0), (2, 0, 0)],
                ),
                AnimationChannel(
                    node_index=node,
                    property="rotation",
                    times=[0.0, 1.0],
                    values=[(0, 0, 0, 1), (0, 0.7, 0, 0.7)],
                ),
            ],
        )

        path = tmp_path / "anim.glb"
        writer.write(path)
        data = path.read_bytes()
        (json_len,) = struct.unpack_from("<I", data, 12)
        document = json.loads(data[20 : 20 + json_len])

        animations = document["animations"]
        assert len(animations) == 1
        animation = animations[0]
        assert animation["name"] == "Take 001"
        assert len(animation["channels"]) == 2
        assert len(animation["samplers"]) == 2

        translation_channel = animation["channels"][0]
        assert translation_channel["target"] == {"node": node, "path": "translation"}
        sampler = animation["samplers"][0]
        input_accessor = document["accessors"][sampler["input"]]
        assert input_accessor["type"] == "SCALAR"
        assert input_accessor["min"] == [0.0]
        assert input_accessor["max"] == [1.0]
        output_accessor = document["accessors"][sampler["output"]]
        assert output_accessor["type"] == "VEC3"
        assert output_accessor["count"] == 3

        rotation_channel = animation["channels"][1]
        rotation_output = document["accessors"][animation["samplers"][1]["output"]]
        assert rotation_output["type"] == "VEC4"
        assert rotation_channel["target"]["path"] == "rotation"

    def test_no_animations_key_when_empty(self, tmp_path: Any) -> None:
        writer, _ = self._writer_with_node()
        path = tmp_path / "plain.glb"
        writer.write(path)
        data = path.read_bytes()
        (json_len,) = struct.unpack_from("<I", data, 12)
        document = json.loads(data[20 : 20 + json_len])
        assert "animations" not in document

    def test_empty_channels_rejected(self) -> None:
        writer, _ = self._writer_with_node()
        with pytest.raises(SceneError, match="no channels"):
            writer.add_animation("empty", [])

    def test_unknown_property_rejected(self) -> None:
        writer, node = self._writer_with_node()
        with pytest.raises(SceneError, match="Unsupported animation property"):
            writer.add_animation(
                "bad",
                [
                    AnimationChannel(
                        node_index=node,
                        property="scale",
                        times=[0.0],
                        values=[(1, 1, 1)],
                    )
                ],
            )
