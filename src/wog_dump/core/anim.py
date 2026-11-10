"""Animation track extraction from Unity AnimationClips.

Every weapon bundle carries exactly one legacy clip (``Take 001``), the
operate animation (fire, reload, safety switch). This module reads its
translation and rotation curves into tracks keyed by Unity hierarchy
path (``receiver/bolt_carrier``). Scale curves, morph targets, material
and float curves stay out of scope: the weapons animate transforms
only.

Tracks leave this module in Unity space; the scene builder converts
values to glTF space the same way it converts static node transforms
(``scene.convert_position`` / ``convert_rotation``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["AnimationTrack", "ClipTracks", "extract_tracks"]


@dataclass
class AnimationTrack:
    """One curve bound to a hierarchy path."""

    path: str  # Unity path, e.g. receiver/bolt_carrier
    property: str  # "translation" or "rotation"
    times: list[float]  # seconds
    values: list[tuple[float, ...]]  # (x, y, z) or (x, y, z, w)


@dataclass
class ClipTracks:
    """TRS content of one AnimationClip."""

    name: str
    duration: float = 0.0  # seconds, last keyframe time across tracks
    tracks: list[AnimationTrack] = field(default_factory=list)


def _curve_keys(curve: Any) -> list[Any]:
    """Read an AnimationCurve's keyframes (UnityPy stores m_Curve)."""
    animation_curve = getattr(curve, "curve", None)
    if animation_curve is None:
        return []
    return getattr(animation_curve, "m_Curve", []) or []


def _vec3(v: Any) -> tuple[float, float, float]:
    """Keyframe value to a triple (UnityPy objects or plain sequences)."""
    if hasattr(v, "x"):
        return (float(v.x), float(v.y), float(v.z))
    return (float(v[0]), float(v[1]), float(v[2]))


def _quat(v: Any) -> tuple[float, float, float, float]:
    """Keyframe value to a quaternion (UnityPy objects or sequences)."""
    if hasattr(v, "x"):
        return (float(v.x), float(v.y), float(v.z), float(v.w))
    return (float(v[0]), float(v[1]), float(v[2]), float(v[3]))


def extract_tracks(clip: Any) -> ClipTracks | None:
    """Extract translation and rotation tracks from an AnimationClip.

    Returns None when the clip carries no usable TRS curves.
    """
    result = ClipTracks(name=str(getattr(clip, "m_Name", "")))
    for curve in getattr(clip, "m_PositionCurves", []) or []:
        keys = _curve_keys(curve)
        if not keys:
            continue
        result.tracks.append(
            AnimationTrack(
                path=str(curve.path),
                property="translation",
                times=[float(k.time) for k in keys],
                values=[_vec3(k.value) for k in keys],
            )
        )
    for curve in getattr(clip, "m_RotationCurves", []) or []:
        keys = _curve_keys(curve)
        if not keys:
            continue
        result.tracks.append(
            AnimationTrack(
                path=str(curve.path),
                property="rotation",
                times=[float(k.time) for k in keys],
                values=[_quat(k.value) for k in keys],
            )
        )
    if not result.tracks:
        return None
    result.duration = max(
        (track.times[-1] for track in result.tracks if track.times), default=0.0
    )
    return result
