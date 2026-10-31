"""Asset unpacking for WOG Dump with Unity asset processing."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import UnityPy

from ..core.config import WOGConfig, get_config
from ..core.database import WOGDatabase
from ..utils.logging import get_logger


class UnpackError(Exception):
    """Base exception for unpacking operations."""

    pass


class AssetProcessingError(UnpackError):
    """Raised when asset processing fails."""

    pass


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
        """Parse weapon names from text content with validation."""
        lines = [line.strip() for line in text_content.split("\n")]

        # Remove empty lines and comments
        lines = [line for line in lines if line and not line.startswith("#")]

        weapon_names = []
        for line in lines:
            # Extract weapon names (remove file extensions and clean up)
            if ".png" in line:
                weapon_name = line.split(".png")[0].strip()
                if weapon_name and (weapon_name.isalnum() or "_" in weapon_name):
                    weapon_names.append(weapon_name)
            elif not any(char in line for char in ["/", "\\", "?", "*", "<", ">", "|"]):
                # Direct weapon name without extension
                weapon_names.append(line)

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
    """Enhanced Unity asset unpacker with support for multiple object types."""

    # Supported Unity object types and their handlers
    SUPPORTED_TYPES = {
        "TextAsset": "_extract_text_asset",
        "Texture2D": "_extract_texture",
        "Mesh": "_extract_mesh",
        "Material": "_extract_material",
        "AnimationClip": "_extract_animation",
        "AudioClip": "_extract_audio",
    }

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
            extract_types = list(self.SUPPORTED_TYPES.keys())

        output_dir.mkdir(parents=True, exist_ok=True)
        extracted_files = []

        try:
            with self.logger.time_operation(f"unpack_{asset_path.stem}"):
                env = UnityPy.load(str(asset_path))

                for obj in env.objects:
                    self._extraction_stats["objects_processed"] += 1

                    if obj.type.name not in extract_types:
                        self._extraction_stats["objects_skipped"] += 1
                        continue

                    if obj.type.name not in self.SUPPORTED_TYPES:
                        self._extraction_stats["objects_skipped"] += 1
                        continue

                    try:
                        # Get the extraction method
                        extract_method = getattr(
                            self, self.SUPPORTED_TYPES[obj.type.name]
                        )
                        extracted_file = extract_method(obj, output_dir)

                        if extracted_file:
                            extracted_files.append(extracted_file)
                            self._extraction_stats["objects_extracted"] += 1
                        else:
                            self._extraction_stats["objects_skipped"] += 1

                    except Exception as e:
                        self.logger.warning(
                            f"Failed to extract object {obj.type.name}: {e}"
                        )
                        self._extraction_stats["objects_failed"] += 1

                self.logger.info(
                    f"Unpacked {asset_path.name}: {len(extracted_files)} files extracted"
                )
                return extracted_files

        except Exception as e:
            raise AssetProcessingError(
                f"Failed to unpack asset {asset_path}: {e}"
            ) from e

    def _extract_text_asset(self, obj: Any, output_dir: Path) -> Path | None:
        """Extract TextAsset objects."""
        try:
            data = obj.read()
            if not data.m_Name:
                return None

            # Determine appropriate extension based on content
            if hasattr(data, "m_Script") and data.m_Script:
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
                    if text_content.strip().startswith(
                        "{"
                    ) or text_content.strip().startswith("["):
                        extension = ".json"
                    elif text_content.strip().startswith("<"):
                        extension = ".xml"
                    else:
                        extension = ".txt"

                    output_path = output_dir / f"{data.m_Name}{extension}"
                    with open(output_path, "w", encoding="utf-8") as f:
                        f.write(text_content)
                else:
                    extension = ".bytes"
                    output_path = output_dir / f"{data.m_Name}{extension}"
                    with open(output_path, "wb") as f:
                        f.write(content)

                return output_path

        except Exception as e:
            self.logger.debug(f"TextAsset extraction failed: {e}")

        return None

    def _extract_texture(self, obj: Any, output_dir: Path) -> Path | None:
        """Extract Texture2D objects."""
        try:
            data = obj.read()
            if not data.m_Name:
                return None

            # Get the image data
            image = data.image
            if image:
                output_path = output_dir / f"{data.m_Name}.png"
                image.save(output_path)
                return output_path

        except Exception as e:
            self.logger.debug(f"Texture extraction failed: {e}")

        return None

    def _extract_mesh(self, obj: Any, output_dir: Path) -> Path | None:
        """Extract Mesh objects (basic implementation)."""
        try:
            data = obj.read()
            if not data.m_Name:
                return None

            # Basic mesh data extraction
            output_path = output_dir / f"{data.m_Name}.obj"

            with open(output_path, "w") as f:
                f.write(f"# Mesh: {data.m_Name}\n")
                f.write("# Exported from WOG Dump\n\n")

                # Write vertex data if available
                if hasattr(data, "m_Vertices") and data.m_Vertices:
                    for vertex in data.m_Vertices:
                        f.write(f"v {vertex.x} {vertex.y} {vertex.z}\n")

                # Write UV coordinates if available
                if hasattr(data, "m_UV") and data.m_UV:
                    for uv in data.m_UV:
                        f.write(f"vt {uv.x} {uv.y}\n")

                # Write normals if available
                if hasattr(data, "m_Normals") and data.m_Normals:
                    for normal in data.m_Normals:
                        f.write(f"vn {normal.x} {normal.y} {normal.z}\n")

                # Write faces if available
                if hasattr(data, "m_Triangles") and data.m_Triangles:
                    triangles = data.m_Triangles
                    for i in range(0, len(triangles), 3):
                        if i + 2 < len(triangles):
                            f.write(
                                f"f {triangles[i]+1} {triangles[i+1]+1} {triangles[i+2]+1}\n"
                            )

            return output_path

        except Exception as e:
            self.logger.debug(f"Mesh extraction failed: {e}")

        return None

    def _extract_material(self, obj: Any, output_dir: Path) -> Path | None:
        """Extract Material objects as JSON."""
        try:
            data = obj.read()
            if not data.m_Name:
                return None

            output_path = output_dir / f"{data.m_Name}.material.json"

            material_data = {
                "name": data.m_Name,
                "shader": getattr(data, "m_Shader", {}).get("m_Name", "Unknown"),
                "properties": {},
            }

            # Extract material properties if available
            if hasattr(data, "m_SavedProperties"):
                props = data.m_SavedProperties

                # Colors
                if hasattr(props, "m_Colors"):
                    for color_prop in props.m_Colors:
                        material_data["properties"][color_prop["first"]] = {
                            "type": "color",
                            "value": [
                                color_prop["second"]["r"],
                                color_prop["second"]["g"],
                                color_prop["second"]["b"],
                                color_prop["second"]["a"],
                            ],
                        }

                # Floats
                if hasattr(props, "m_Floats"):
                    for float_prop in props.m_Floats:
                        material_data["properties"][float_prop["first"]] = {
                            "type": "float",
                            "value": float_prop["second"],
                        }

                # Textures
                if hasattr(props, "m_TexEnvs"):
                    for tex_prop in props.m_TexEnvs:
                        material_data["properties"][tex_prop["first"]] = {
                            "type": "texture",
                            "texture_name": tex_prop["second"]["m_Texture"].get(
                                "m_Name", "None"
                            ),
                        }

            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(material_data, f, indent=2)

            return output_path

        except Exception as e:
            self.logger.debug(f"Material extraction failed: {e}")

        return None

    def _extract_animation(self, obj: Any, output_dir: Path) -> Path | None:
        """Extract AnimationClip objects (placeholder)."""
        try:
            data = obj.read()
            if not data.m_Name:
                return None

            output_path = output_dir / f"{data.m_Name}.anim.json"

            anim_data = {
                "name": data.m_Name,
                "length": getattr(data, "m_Length", 0),
                "frame_rate": getattr(data, "m_FrameRate", 0),
                "legacy": getattr(data, "m_Legacy", False),
            }

            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(anim_data, f, indent=2)

            return output_path

        except Exception as e:
            self.logger.debug(f"Animation extraction failed: {e}")

        return None

    def _extract_audio(self, obj: Any, output_dir: Path) -> Path | None:
        """Extract AudioClip objects (placeholder)."""
        try:
            data = obj.read()
            if not data.m_Name:
                return None

            # For now, just create a metadata file
            output_path = output_dir / f"{data.m_Name}.audio.json"

            audio_data = {
                "name": data.m_Name,
                "format": getattr(data, "m_Format", "Unknown"),
                "frequency": getattr(data, "m_Frequency", 0),
                "channels": getattr(data, "m_Channels", 0),
            }

            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(audio_data, f, indent=2)

            return output_path

        except Exception as e:
            self.logger.debug(f"Audio extraction failed: {e}")

        return None

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
                    [
                        obj
                        for obj in info["objects"]
                        if obj["type"] in self.SUPPORTED_TYPES
                    ]
                ),
            }

        except Exception as e:
            self.logger.error(f"Failed to analyze asset {asset_path}: {e}")
            info["error"] = str(e)

        return info

    def get_extraction_stats(self) -> dict[str, int]:
        """Get extraction statistics."""
        return self._extraction_stats.copy()
