"""Turntable preview rendering through headless Blender.

``RenderManager`` locates Blender (``WOG_BLENDER_PATH`` or PATH),
generates a turntable script from ``resources/render_turntable.py``
and runs ``blender --background --python ...`` against a built scene
GLB. Output: N transparent PNGs around the weapon plus a GIF assembled
with Pillow.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from ..utils.logging import get_logger
from .export import ExportError, find_blender
from .paths import is_safe_weapon_name

__all__ = [
    "RenderError",
    "RenderManager",
    "RenderOptions",
    "VideoOptions",
]


class RenderError(Exception):
    """Raised when a preview render cannot be produced."""


@dataclass
class RenderOptions:
    """Turntable render parameters."""

    angles: int = 8
    size: int = 1280  # square, pixels
    elevation: float = 15.0  # degrees above the horizon
    image_format: str = "png"  # png, jpg, webp
    gif: bool = True  # assemble preview.gif from the frames

    def __post_init__(self) -> None:
        if self.angles < 1:
            raise RenderError(f"Angles must be >= 1, got {self.angles}")
        if self.size < 64 or self.size > 8192:
            raise RenderError(f"Size must be 64..8192, got {self.size}")
        if not -89 <= self.elevation <= 89:
            raise RenderError(
                f"Elevation must be -89..89 degrees, got {self.elevation}"
            )
        if self.image_format not in ("png", "jpg", "webp"):
            raise RenderError(f"Unsupported image format: {self.image_format!r}")


@dataclass
class VideoOptions:
    """Video render parameters.

    ``clip`` names an animation from the scene manifest; empty means
    the orbit turntable over ``duration`` seconds.
    """

    duration: float = 10.0  # seconds, orbit mode only
    fps: int = 30
    size: int = 1920
    square: bool = False
    clip: str | None = None

    def __post_init__(self) -> None:
        if self.duration <= 0:
            raise RenderError(f"Duration must be positive, got {self.duration}")
        if self.fps < 1 or self.fps > 120:
            raise RenderError(f"FPS must be 1..120, got {self.fps}")
        if self.size < 64 or self.size > 4096:
            raise RenderError(f"Size must be 64..4096, got {self.size}")

    def frame_count(self, clip_duration: float | None) -> int:
        seconds = clip_duration if clip_duration is not None else self.duration
        return max(1, int(round(seconds * self.fps)))

    def resolution(self) -> tuple[int, int]:
        return (
            (self.size, self.size) if self.square else (self.size, self.size * 9 // 16)
        )


class RenderManager:
    """Renders turntable previews and videos for built scenes."""

    def __init__(self, config: Any = None) -> None:
        from .config import WOGConfig, get_config

        self.config: WOGConfig = config or get_config()
        self.logger = get_logger()

    def scenes_dir(self, weapon: str) -> Path:
        return self.config.base_dir / "runtime" / "scenes" / weapon

    def render(self, weapon: str, options: RenderOptions | None = None) -> list[Path]:
        """Render one weapon's turntable into its scene directory."""
        if not is_safe_weapon_name(weapon):
            raise RenderError(f"Invalid weapon name: {weapon!r}")
        options = options or RenderOptions()

        glb_path = self.scenes_dir(weapon) / f"{weapon}.glb"
        if not glb_path.exists():
            raise RenderError(
                f"Scene not found for {weapon}: {glb_path} (run build-scene first)"
            )

        blender = find_blender()
        if blender is None:
            raise RenderError(
                "Blender not found: set WOG_BLENDER_PATH or install Blender "
                "(blender.org); previews render through headless Blender"
            )

        output_dir = self.scenes_dir(weapon) / "previews"
        script = _turntable_script(glb_path, output_dir, options)
        self.logger.info(f"Rendering {weapon}: {options.angles} views with {blender}")

        with tempfile.TemporaryDirectory(prefix="wog-render-") as tmp:
            script_path = Path(tmp) / "render_turntable_user.py"
            script_path.write_text(script, encoding="utf-8")
            result = subprocess.run(
                [str(blender), "--background", "--python", str(script_path)],
                capture_output=True,
                text=True,
                timeout=1800,
            )

        frames = self._collect_frames(output_dir, options)
        if result.returncode != 0 or not frames:
            tail = (result.stderr or result.stdout or "").strip().splitlines()[-5:]
            for frame in frames:
                frame.unlink(missing_ok=True)
            raise RenderError(f"Blender render failed for {weapon}: {' | '.join(tail)}")

        if options.gif:
            gif_path = output_dir / "preview.gif"
            _assemble_gif(frames, gif_path)

        self.logger.info(f"Previews: {len(frames)} frames -> {output_dir}")
        return frames

    def render_video(self, weapon: str, options: VideoOptions | None = None) -> Path:
        """Render an orbit MP4 or one animation clip for a built scene."""
        if not is_safe_weapon_name(weapon):
            raise RenderError(f"Invalid weapon name: {weapon!r}")
        options = options or VideoOptions()

        scenes_dir = self.scenes_dir(weapon)
        glb_path = scenes_dir / f"{weapon}.glb"
        if not glb_path.exists():
            raise RenderError(
                f"Scene not found for {weapon}: {glb_path} (run build-scene first)"
            )
        if options.clip is not None and options.clip not in self.available_clips(
            weapon
        ):
            available = self.available_clips(weapon)
            raise RenderError(
                f"Clip {options.clip!r} not found. Available: "
                f"{available or 'none (rebuild the scene with --animations)'}"
            )

        blender = find_blender()
        if blender is None:
            raise RenderError(
                "Blender not found: set WOG_BLENDER_PATH or install Blender "
                "(blender.org); videos render through headless Blender"
            )

        clip_duration = None
        if options.clip is not None:
            manifest = self._scene_manifest(weapon)
            for entry in manifest.get("animations", []):
                if entry.get("name") == options.clip:
                    clip_duration = float(entry.get("duration", 0.0))
            if not clip_duration:
                raise RenderError(
                    f"Clip {options.clip!r} has no duration in the manifest"
                )
        frames = options.frame_count(clip_duration)
        res_x, res_y = options.resolution()

        output_dir = scenes_dir / "renders"
        name = f"{options.clip}.mp4" if options.clip else "turntable.mp4"
        output_path = output_dir / name
        script = _video_script(glb_path, output_path, options, frames, res_x, res_y)

        self.logger.info(
            f"Rendering video {weapon}: {frames} frames @ {options.fps} fps"
        )
        with tempfile.TemporaryDirectory(prefix="wog-video-") as tmp:
            script_path = Path(tmp) / "render_video_user.py"
            script_path.write_text(script, encoding="utf-8")
            process = subprocess.Popen(
                [str(blender), "--background", "--python", str(script_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            tail: list[str] = []
            assert process.stdout is not None
            for line in process.stdout:
                line = line.strip()
                if line.startswith("WOG FRAME"):
                    self.logger.info(line)
                elif line:
                    tail.append(line)
            code = process.wait()

        if code != 0 or not output_path.exists():
            detail = " | ".join(tail[-5:])
            raise RenderError(f"Blender video render failed for {weapon}: {detail}")
        self.logger.info(f"Video: {output_path}")
        return output_path

    def _scene_manifest(self, weapon: str) -> dict[str, Any]:
        manifest_path = self.scenes_dir(weapon) / "scene.json"
        try:
            document: dict[str, Any] = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
            return document
        except (OSError, json.JSONDecodeError):
            return {}

    def available_clips(self, weapon: str) -> list[str]:
        return [
            str(entry.get("name"))
            for entry in self._scene_manifest(weapon).get("animations", [])
        ]

    def _collect_frames(self, output_dir: Path, options: RenderOptions) -> list[Path]:
        frames = []
        for index in range(options.angles):
            frame = output_dir / f"view_{index + 1:02d}.{options.image_format}"
            if frame.exists():
                frames.append(frame)
        return frames


def _video_script(
    glb_path: Path,
    output_path: Path,
    options: VideoOptions,
    frames: int,
    res_x: int,
    res_y: int,
) -> str:
    """Fill the Blender video template with this run's parameters."""
    template = (
        resources.files("wog_dump") / "resources" / "render_video.py"
    ).read_text(encoding="utf-8")
    replacements = {
        "GLB_PATH": str(glb_path),
        "OUTPUT_PATH": str(output_path),
        "FRAME_COUNT": str(frames),
        "FPS": str(options.fps),
        "RES_X": str(res_x),
        "RES_Y": str(res_y),
        "CLIP_NAME": options.clip or "",
        "ORBIT": "True" if options.clip is None else "False",
    }
    script = template
    for key, value in replacements.items():
        script = script.replace(f"@@{key}@@", value)
    if "@@" in script:
        raise RenderError("video template placeholders missing")
    return script


def _turntable_script(glb_path: Path, output_dir: Path, options: RenderOptions) -> str:
    """Fill the Blender turntable template with this run's parameters."""
    template = (
        resources.files("wog_dump") / "resources" / "render_turntable.py"
    ).read_text(encoding="utf-8")
    replacements = {
        "GLB_PATH": str(glb_path),
        "OUTPUT_DIR": str(output_dir),
        "ANGLE_COUNT": str(options.angles),
        "RENDER_SIZE": str(options.size),
        "ELEVATION_DEG": str(options.elevation),
        "IMAGE_FORMAT": options.image_format,
    }
    script = template
    for key, value in replacements.items():
        script = script.replace(f"@@{key}@@", value)
    missing = [key for key in replacements if f"@@{key}@@" in script]
    if missing:
        raise RenderError(f"Render template missing placeholders: {missing}")
    return script


def _assemble_gif(frames: list[Path], gif_path: Path) -> None:
    """Combine the turntable frames into one looping GIF."""
    from PIL import Image

    images = [Image.open(frame).convert("RGB") for frame in frames]
    if not images:
        return
    first, rest = images[0], images[1:]
    gif_path.parent.mkdir(parents=True, exist_ok=True)
    first.save(
        gif_path,
        save_all=True,
        append_images=rest,
        duration=500,
        loop=0,
    )
