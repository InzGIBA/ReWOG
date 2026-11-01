"""Asset unpacking for WOG Dump with Unity asset processing.

Each supported Unity object type is handled by an ``AssetExtractor``
subclass registered in ``EXTRACTORS``; ``AssetUnpacker`` walks the
objects of a bundle and delegates to the matching extractor.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import UnityPy
from UnityPy.export import MeshExporter

from ..core.config import WOGConfig, get_config
from ..core.database import WOGDatabase
from ..core.paths import contained_path, is_safe_weapon_name, safe_object_name
from ..utils.logging import get_logger
from ..utils.normal_map import is_unity_swizzled_normal, unswizzle_normal_map


class UnpackError(Exception):
    """Base exception for unpacking operations."""

    pass


class AssetProcessingError(UnpackError):
    """Raised when asset processing fails."""

    pass


class AssetExtractor:
    """Base class for per-type Unity object extractors.

    Subclasses set ``obj_type`` to the Unity type name they handle and
    implement ``_extract_data`` returning the written file (or None when
    the object should be skipped). Read/name-check/error handling is
    shared here.
    """

    obj_type: str = ""

    def __init__(self) -> None:
        self.logger = get_logger()

    def extract(self, obj: Any, output_dir: Path) -> Path | None:
        """Read an object and write its extracted form to output_dir."""
        try:
            data = obj.read()
            if not data.m_Name:
                return None
            # m_Name comes straight from the untrusted bundle and is used
            # as a filename: names with separators, traversal or absolute
            # structure are rejected before any path is built
            safe_object_name(str(data.m_Name))
            return self._extract_data(data, output_dir)
        except ValueError as e:
            self.logger.warning(f"Skipping object with unsafe name: {e}")
            return None
        except Exception as e:
            self.logger.debug(f"{self.obj_type or 'Object'} extraction failed: {e}")
            return None

    def _extract_data(self, data: Any, output_dir: Path) -> Path | None:
        """Write the object's payload; returns the output path or None."""
        raise NotImplementedError


class TextAssetExtractor(AssetExtractor):
    """Writes TextAsset payloads with a content-based extension."""

    obj_type = "TextAsset"

    def _extract_data(self, data: Any, output_dir: Path) -> Path | None:
        if not (hasattr(data, "m_Script") and data.m_Script):
            return None

        content = data.m_Script

        # Detect content type
        if isinstance(content, bytes):
            # Try to determine if it's text or binary
            try:
                text_content = content.decode("utf-8")
                is_text = True
            except UnicodeDecodeError:
                is_text = False
        else:
            text_content = str(content)
            is_text = True

        # Choose extension and write method
        if is_text:
            if text_content.strip().startswith("{") or text_content.strip().startswith(
                "["
            ):
                extension = ".json"
            elif text_content.strip().startswith("<"):
                extension = ".xml"
            else:
                extension = ".txt"

            output_path = contained_path(output_dir, f"{data.m_Name}{extension}")
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(text_content)
        else:
            extension = ".bytes"
            output_path = contained_path(output_dir, f"{data.m_Name}{extension}")
            with open(output_path, "wb") as f:
                f.write(content)

        return output_path


class Texture2DExtractor(AssetExtractor):
    """Renders Texture2D objects to PNG.

    Unity's swizzled (DXT5nm-style) normal maps are detected and
    rewritten as standard opaque RGB maps - exported raw they look
    semi-transparent because the X channel lives in alpha.
    """

    obj_type = "Texture2D"

    def _extract_data(self, data: Any, output_dir: Path) -> Path | None:
        # Get the image data
        image = data.image
        if not image:
            return None

        if is_unity_swizzled_normal(image):
            image = unswizzle_normal_map(image)
            self.logger.debug(f"Unswizzled normal map: {data.m_Name}")

        output_path = contained_path(output_dir, f"{data.m_Name}.png")
        image.save(output_path)
        return output_path


class MeshExtractor(AssetExtractor):
    """Writes Mesh objects as OBJ files.

    Unity 6 meshes keep their vertices in packed ``m_VertexData`` (the
    legacy ``m_Vertices`` array is None), so the OBJ text comes from
    UnityPy's mesh exporter; meshes with populated legacy arrays fall
    back to a manual writer.
    """

    obj_type = "Mesh"

    def _extract_data(self, data: Any, output_dir: Path) -> Path | None:
        output_path = contained_path(output_dir, f"{data.m_Name}.obj")

        obj_text: str | None = None
        try:
            obj_text = MeshExporter.export_mesh_obj(data)
        except Exception as e:
            self.logger.debug(f"Mesh exporter failed for {data.m_Name}: {e}")

        if obj_text and any(line.startswith("v ") for line in obj_text.splitlines()):
            output_path.write_text(obj_text, encoding="utf-8")
            return output_path

        return self._write_legacy_obj(data, output_path)

    def _write_legacy_obj(self, data: Any, output_path: Path) -> Path | None:
        """Write an OBJ from legacy m_Vertices/m_UV/m_Normals/m_Triangles arrays."""
        vertices = getattr(data, "m_Vertices", None) or []
        if not vertices:
            return None

        uvs = getattr(data, "m_UV", None) or []
        normals = getattr(data, "m_Normals", None) or []
        triangles = getattr(data, "m_Triangles", None) or []

        with open(output_path, "w") as f:
            f.write(f"# Mesh: {data.m_Name}\n")
            f.write("# Exported from WOG Dump\n\n")

            for vertex in vertices:
                f.write(f"v {vertex.x} {vertex.y} {vertex.z}\n")

            for uv in uvs:
                f.write(f"vt {uv.x} {uv.y}\n")

            for normal in normals:
                f.write(f"vn {normal.x} {normal.y} {normal.z}\n")

            for i in range(0, len(triangles) - 2, 3):
                f.write(
                    f"f {triangles[i] + 1} {triangles[i + 1] + 1} "
                    f"{triangles[i + 2] + 1}\n"
                )

        return output_path


def _pair_parts(pair: Any) -> tuple[Any, Any] | None:
    """Normalize a Unity property pair into (key, value).

    UnityPy returns ``(name, value)`` tuples for typed reads and
    ``{"first": name, "second": value}`` dicts for typetree reads.
    """
    if isinstance(pair, dict) and "first" in pair:
        return pair["first"], pair["second"]
    if isinstance(pair, tuple) and len(pair) == 2:
        return pair[0], pair[1]
    return None


def _named_value(value: Any, attrs: tuple[str, ...]) -> Any:
    """Read an attribute-style or dict-style field from a Unity value."""
    for attr in attrs:
        if isinstance(value, dict):
            if attr in value:
                return value[attr]
        elif hasattr(value, attr):
            return getattr(value, attr)
    return None


def _pptr_name(pptr: Any) -> str | None:
    """Best-effort object name behind a PPtr (None when unresolvable)."""
    if pptr is None:
        return None
    # Values that already carry their name (resolved objects, dict mocks)
    name = _named_value(pptr, ("m_Name", "name"))
    if isinstance(name, str) and name:
        return name
    if isinstance(pptr, dict):
        path_id = pptr.get("m_PathID", 0)
        return f"PPtr({path_id})" if path_id else None
    if not hasattr(pptr, "deref"):
        return str(pptr) if pptr else None
    try:
        if not getattr(pptr, "m_PathID", 0):
            return None
        resolved = pptr.deref()
        name = _named_value(resolved, ("m_Name", "name"))
        if isinstance(name, str) and name:
            return name
        return f"PPtr({pptr.m_PathID})"
    except Exception:
        return None


class MaterialExtractor(AssetExtractor):
    """Dumps Material objects as JSON."""

    obj_type = "Material"

    def _extract_data(self, data: Any, output_dir: Path) -> Path | None:
        output_path = contained_path(output_dir, f"{data.m_Name}.material.json")

        material_data = {
            "name": data.m_Name,
            "shader": _pptr_name(getattr(data, "m_Shader", None)) or "Unknown",
            "properties": {},
        }

        # Extract material properties if available
        props = getattr(data, "m_SavedProperties", None)

        if props is not None:
            # Colors
            for color_pair in _named_value(props, ("m_Colors",)) or []:
                parts = _pair_parts(color_pair)
                if parts is None:
                    continue
                name, color = parts
                if isinstance(color, dict):
                    rgba = [color.get(k) for k in ("r", "g", "b", "a")]
                else:
                    rgba = [getattr(color, k, None) for k in ("r", "g", "b", "a")]
                material_data["properties"][str(name)] = {
                    "type": "color",
                    "value": rgba if all(v is not None for v in rgba) else str(color),
                }

            # Floats
            for float_pair in _named_value(props, ("m_Floats",)) or []:
                parts = _pair_parts(float_pair)
                if parts is None:
                    continue
                name, value = parts
                material_data["properties"][str(name)] = {
                    "type": "float",
                    "value": value,
                }

            # Textures
            for tex_pair in _named_value(props, ("m_TexEnvs",)) or []:
                parts = _pair_parts(tex_pair)
                if parts is None:
                    continue
                name, tex_env = parts
                texture = _named_value(tex_env, ("m_Texture",))
                material_data["properties"][str(name)] = {
                    "type": "texture",
                    "texture_name": _pptr_name(texture) or "None",
                }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(material_data, f, indent=2)

        return output_path


class AnimationClipExtractor(AssetExtractor):
    """Dumps AnimationClip metadata as JSON."""

    obj_type = "AnimationClip"

    def _extract_data(self, data: Any, output_dir: Path) -> Path | None:
        output_path = contained_path(output_dir, f"{data.m_Name}.anim.json")

        anim_data = {
            "name": data.m_Name,
            "length": getattr(data, "m_Length", 0),
            "frame_rate": getattr(data, "m_FrameRate", 0),
            "legacy": getattr(data, "m_Legacy", False),
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(anim_data, f, indent=2)

        return output_path


class AudioClipExtractor(AssetExtractor):
    """Dumps AudioClip metadata as JSON."""

    obj_type = "AudioClip"

    def _extract_data(self, data: Any, output_dir: Path) -> Path | None:
        output_path = contained_path(output_dir, f"{data.m_Name}.audio.json")

        audio_data = {
            "name": data.m_Name,
            "format": getattr(data, "m_Format", "Unknown"),
            "frequency": getattr(data, "m_Frequency", 0),
            "channels": getattr(data, "m_Channels", 0),
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(audio_data, f, indent=2)

        return output_path


# Registry: Unity object type name -> extractor instance
EXTRACTORS: dict[str, AssetExtractor] = {
    extractor.obj_type: extractor
    for extractor in (
        TextAssetExtractor(),
        Texture2DExtractor(),
        MeshExtractor(),
        MaterialExtractor(),
        AnimationClipExtractor(),
        AudioClipExtractor(),
    )
}


class WeaponListProcessor:
    """Processor for weapon list extraction and storage in the local database."""

    def __init__(self, config: WOGConfig | None = None) -> None:
        self.config = config or get_config()
        self.logger = get_logger()
        self.db = WOGDatabase(self.config)

    def extract_weapon_list(self, asset_path: Path) -> list[str]:
        """Extract weapon list from spider_gen.unity3d with enhanced error handling."""
        if not asset_path.exists():
            raise UnpackError(f"Asset file not found: {asset_path}")

        weapon_list = []

        try:
            with self.logger.time_operation("extract_weapon_list"):
                env = UnityPy.load(str(asset_path))

                for obj in env.objects:
                    if obj.type.name == "TextAsset":
                        data = obj.read()

                        if data.m_Name == "new_banners":
                            # Process the banner data to extract weapon names
                            text_content = self._extract_text_content(data)
                            weapon_list = self._parse_weapon_names(text_content)
                            break

                if not weapon_list:
                    raise UnpackError("No weapon list found in 'new_banners' TextAsset")

                # Apply filtering
                filtered_list = self._filter_weapons(weapon_list)

                self.logger.info(
                    f"Extracted {len(filtered_list)} weapons from {len(weapon_list)} total"
                )
                return filtered_list

        except Exception as e:
            raise UnpackError(f"Failed to extract weapon list: {e}") from e

    def _extract_text_content(self, text_asset: Any) -> str:
        """Extract text content from TextAsset with proper encoding handling."""
        if isinstance(text_asset.m_Script, bytes):
            try:
                return text_asset.m_Script.decode("utf-8").replace("\r", "")
            except UnicodeDecodeError:
                # latin-1 maps every byte, so decoding always succeeds
                return text_asset.m_Script.decode("latin-1").replace("\r", "")
        return str(text_asset.m_Script).replace("\r", "")

    def _parse_weapon_names(self, text_content: str) -> list[str]:
        """Parse weapon names from text content with validation.

        The banner text comes in two flavors: a plain ``<name>.png`` list
        (one per line) and a texture-atlas index (``<name>.png<tab><tab>
        new_bannersN.dds, ...``) - both carry the weapon name as the
        first whitespace-separated token of each line.

        The banner text is server-controlled content, so every name must
        match the strict weapon-name allowlist (see ``core.paths``):
        separators, traversal segments and URL metacharacters are
        rejected outright - names later become URL path segments and
        local filenames.
        """
        weapon_names = []
        for raw_line in text_content.split("\n"):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            name = line.split()[0] if line.split() else ""
            if name.endswith(".png"):
                name = name[: -len(".png")]
            if name and is_safe_weapon_name(name):
                weapon_names.append(name)

        return weapon_names

    def _filter_weapons(self, weapon_list: list[str]) -> list[str]:
        """Filter weapons using blacklist and validation rules."""
        filtered_list = []
        for weapon in weapon_list:
            # Skip blacklisted items
            if self.config.is_blacklisted(weapon):
                self.logger.debug(f"Filtered blacklisted weapon: {weapon}")
                continue

            # Additional validation
            if len(weapon) < 2:  # Too short
                self.logger.debug(f"Filtered too short weapon name: {weapon}")
                continue

            if len(weapon) > 50:  # Too long
                self.logger.debug(f"Filtered too long weapon name: {weapon}")
                continue

            filtered_list.append(weapon)

        removed_count = len(weapon_list) - len(filtered_list)
        if removed_count > 0:
            self.logger.info(f"Filtered out {removed_count} items")

        return filtered_list

    def save_weapon_list(
        self,
        weapon_list: list[str],
        include_metadata: bool = True,
        source_asset: str | None = None,
    ) -> None:
        """Save weapon list to the local database."""
        if not weapon_list:
            raise ValueError("Cannot save empty weapon list")

        try:
            filtered_weapons = self._filter_weapons(weapon_list)
            self.db.save_weapons(filtered_weapons)
            self.logger.info(f"Saved {len(filtered_weapons)} weapons to the database")
        except Exception as e:
            raise UnpackError(f"Failed to save weapon list: {e}") from e

    def load_weapon_list(
        self, validate: bool = True, try_migration: bool = True
    ) -> list[str]:
        """Load weapon list from the database, importing legacy files once."""
        try:
            weapons = self.db.get_weapons()

            if weapons:
                self.logger.info(f"Loaded {len(weapons)} weapons from the database")
                return weapons

            if try_migration:
                self.logger.info("No weapons in the database, trying legacy import")
                if self.db.import_legacy_txt(
                    weapons_file=self.config.weapons_file,
                    keys_file=self.config.keys_file,
                ):
                    weapons = self.db.get_weapons()
                    if weapons:
                        return weapons

            raise UnpackError("No weapon list found in the database or legacy files")

        except UnpackError:
            raise
        except Exception as e:
            raise UnpackError(f"Failed to load weapon list: {e}") from e

    def process_weapon_list_asset(self, asset_path: Path) -> list[str]:
        """Complete weapon list processing pipeline."""
        with self.logger.operation_context(
            "weapon_list_processing", "weapon list processing"
        ):
            weapon_list = self.extract_weapon_list(asset_path)
            self.save_weapon_list(weapon_list, source_asset=asset_path.name)
            return weapon_list


class AssetUnpacker:
    """Unity asset unpacker dispatching to per-type extractors."""

    def __init__(self, config: WOGConfig | None = None) -> None:
        self.config = config or get_config()
        self.logger = get_logger()
        self._extraction_stats = {
            "objects_processed": 0,
            "objects_extracted": 0,
            "objects_skipped": 0,
            "objects_failed": 0,
        }

    def unpack_asset(
        self,
        asset_path: Path,
        output_dir: Path | None = None,
        extract_types: list[str] | None = None,
    ) -> list[Path]:
        """Unpack a Unity asset with enhanced object extraction."""
        if not asset_path.exists():
            raise UnpackError(f"Asset file not found: {asset_path}")

        if output_dir is None:
            output_dir = asset_path.parent / "unpacked" / asset_path.stem

        if extract_types is None:
            extract_types = list(EXTRACTORS)

        output_dir.mkdir(parents=True, exist_ok=True)
        extracted_files = []

        try:
            with self.logger.time_operation(f"unpack:{asset_path.stem}"):
                env = UnityPy.load(str(asset_path))

                for obj in env.objects:
                    self._extraction_stats["objects_processed"] += 1
                    obj_type = obj.type.name

                    if obj_type not in extract_types or obj_type not in EXTRACTORS:
                        self._extraction_stats["objects_skipped"] += 1
                        continue

                    extracted_file = EXTRACTORS[obj_type].extract(obj, output_dir)

                    if extracted_file:
                        extracted_files.append(extracted_file)
                        self._extraction_stats["objects_extracted"] += 1
                    else:
                        self._extraction_stats["objects_skipped"] += 1

                self.logger.info(
                    f"Unpacked {asset_path.name}: {len(extracted_files)} files extracted"
                )
                return extracted_files

        except Exception as e:
            raise AssetProcessingError(
                f"Failed to unpack asset {asset_path}: {e}"
            ) from e

    def unpack_multiple_assets(
        self,
        asset_paths: list[Path],
        output_dir: Path | None = None,
        extract_types: list[str] | None = None,
    ) -> dict[Path, list[Path]]:
        """Unpack multiple assets with parallel processing."""
        if output_dir is None:
            output_dir = self.config.base_dir / "runtime" / "unpacked"

        results = {}

        with self.logger.create_task_progress() as progress:
            task = progress.add_task("Unpacking assets", total=len(asset_paths))

            # Use parallel processing for large numbers of assets
            if len(asset_paths) > 5:
                with ThreadPoolExecutor(
                    max_workers=self.config.max_threads
                ) as executor:
                    future_to_asset = {
                        executor.submit(
                            self._unpack_single_asset_safe,
                            asset_path,
                            output_dir,
                            extract_types,
                        ): asset_path
                        for asset_path in asset_paths
                    }

                    for future in as_completed(future_to_asset):
                        asset_path = future_to_asset[future]
                        try:
                            extracted_files = future.result()
                            results[asset_path] = extracted_files
                        except Exception as e:
                            self.logger.error(f"Failed to unpack {asset_path}: {e}")
                            results[asset_path] = []

                        progress.update(task, advance=1)
            else:
                # Sequential processing for small numbers
                for asset_path in asset_paths:
                    try:
                        extracted_files = self.unpack_asset(
                            asset_path, output_dir / asset_path.stem, extract_types
                        )
                        results[asset_path] = extracted_files
                    except Exception as e:
                        self.logger.error(f"Failed to unpack {asset_path}: {e}")
                        results[asset_path] = []

                    progress.update(task, advance=1)

        # Log summary statistics
        successful = sum(1 for files in results.values() if files)
        total_files = sum(len(files) for files in results.values())

        self.logger.info(
            f"Unpacking complete: {successful}/{len(asset_paths)} assets, {total_files} files extracted"
        )

        # Log extraction statistics
        stats = self._extraction_stats
        if stats["objects_processed"] > 0:
            self.logger.debug(f"Object extraction stats: {stats}")

        return results

    def _unpack_single_asset_safe(
        self, asset_path: Path, output_dir: Path, extract_types: list[str] | None
    ) -> list[Path]:
        """Safe wrapper for unpacking a single asset."""
        try:
            return self.unpack_asset(
                asset_path, output_dir / asset_path.stem, extract_types
            )
        except Exception as e:
            self.logger.error(f"Failed to unpack {asset_path}: {e}")
            return []

    def get_asset_info(self, asset_path: Path) -> dict[str, Any]:
        """Get detailed information about a Unity asset."""
        if not asset_path.exists():
            raise UnpackError(f"Asset file not found: {asset_path}")

        info: dict[str, Any] = {
            "path": asset_path,
            "size": asset_path.stat().st_size,
            "objects": [],
            "types": {},
            "summary": {},
        }

        try:
            env = UnityPy.load(str(asset_path))

            for obj in env.objects:
                obj_type = obj.type.name

                # Count object types
                if obj_type not in info["types"]:
                    info["types"][obj_type] = 0
                info["types"][obj_type] += 1

                # Collect object information
                obj_info = {
                    "type": obj_type,
                    "path_id": obj.path_id,
                }

                # Try to get object name
                try:
                    data = obj.read()
                    if hasattr(data, "m_Name") and data.m_Name:
                        obj_info["name"] = data.m_Name

                    # Additional type-specific info
                    if obj_type == "Texture2D" and hasattr(data, "image"):
                        obj_info["dimensions"] = f"{data.m_Width}x{data.m_Height}"
                        obj_info["format"] = str(data.m_TextureFormat)
                    elif obj_type == "Mesh" and hasattr(data, "m_VertexCount"):
                        obj_info["vertex_count"] = data.m_VertexCount

                except Exception:
                    pass  # Skip objects that can't be read

                info["objects"].append(obj_info)

            # Generate summary
            info["summary"] = {
                "total_objects": len(info["objects"]),
                "type_distribution": info["types"],
                "extractable_objects": len(
                    [obj for obj in info["objects"] if obj["type"] in EXTRACTORS]
                ),
            }

        except Exception as e:
            self.logger.error(f"Failed to analyze asset {asset_path}: {e}")
            info["error"] = str(e)

        return info

    def get_extraction_stats(self) -> dict[str, int]:
        """Get extraction statistics."""
        return self._extraction_stats.copy()
