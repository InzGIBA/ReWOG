"""Asset unpacking module for WOG Dump with Unity asset processing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import UnityPy
from UnityPy.classes import TextAsset

from ..core.config import WOGConfig, get_config
from ..utils.logging import get_logger


class UnpackError(Exception):
    """Raised when unpacking operations fail."""
    pass


class WeaponListProcessor:
    """Processes weapon list from Unity assets."""
    
    def __init__(self, config: WOGConfig | None = None) -> None:
        self.config = config or get_config()
        self.logger = get_logger()
    
    def extract_weapon_list(self, asset_path: Path) -> list[str]:
        """Extract weapon list from spider_gen.unity3d asset."""
        weapon_list = []
        
        try:
            env = UnityPy.load(str(asset_path))
            
            for obj in env.objects:
                if obj.type.name == "TextAsset":
                    data = obj.read()
                    if data.name == "new_banners":
                        text = data.text.replace("\r", "")
                        lines = text.split("\n")
                        
                        # Process lines
                        lines = [line for line in lines if line.strip()]  # Remove empty lines
                        lines = [line for line in lines if not line.startswith("#")]  # Remove comments
                        
                        # Extract weapon names (remove .png extension)
                        for line in lines:
                            if ".png" in line:
                                weapon_name = line.split(".png")[0]
                                weapon_list.append(weapon_name)
                        
                        break
            
            if not weapon_list:
                raise UnpackError("No weapon list found in asset")
            
            # Remove blacklisted items
            weapon_list = self._filter_blacklisted(weapon_list)
            
            self.logger.info(f"Extracted {len(weapon_list)} weapons from asset")
            return weapon_list
            
        except Exception as e:
            raise UnpackError(f"Failed to extract weapon list: {e}") from e
    
    def _filter_blacklisted(self, weapon_list: list[str]) -> list[str]:
        """Remove blacklisted weapons from the list."""
        blacklist = self.config.get_combined_blacklist()
        filtered_list = [weapon for weapon in weapon_list if weapon not in blacklist]
        
        removed_count = len(weapon_list) - len(filtered_list)
        if removed_count > 0:
            self.logger.info(f"Filtered out {removed_count} blacklisted items")
        
        return filtered_list
    
    def save_weapon_list(self, weapon_list: list[str]) -> None:
        """Save weapon list to file."""
        try:
            with open(self.config.weapons_file, "w", encoding="utf-8") as f:
                f.write("\n".join(weapon_list))
            
            self.logger.info(f"Saved {len(weapon_list)} weapons to {self.config.weapons_file}")
            
        except IOError as e:
            raise UnpackError(f"Failed to save weapon list: {e}") from e
    
    def load_weapon_list(self) -> list[str]:
        """Load weapon list from file."""
        try:
            if not self.config.weapons_file.exists():
                raise UnpackError(f"Weapon list file {self.config.weapons_file} not found")
            
            with open(self.config.weapons_file, "r", encoding="utf-8") as f:
                weapons = [line.strip() for line in f if line.strip()]
            
            self.logger.info(f"Loaded {len(weapons)} weapons from {self.config.weapons_file}")
            return weapons
            
        except IOError as e:
            raise UnpackError(f"Failed to load weapon list: {e}") from e
    
    def process_weapon_list_asset(self, asset_path: Path) -> list[str]:
        """Process weapon list asset and return filtered weapon list."""
        weapon_list = self.extract_weapon_list(asset_path)
        self.save_weapon_list(weapon_list)
        return weapon_list


class AssetUnpacker:
    """Handles unpacking of Unity assets."""
    
    def __init__(self, config: WOGConfig | None = None) -> None:
        self.config = config or get_config()
        self.logger = get_logger()
    
    def unpack_asset(self, asset_path: Path, output_dir: Path | None = None) -> list[Path]:
        """Unpack a Unity asset file and extract its contents."""
        if output_dir is None:
            output_dir = asset_path.parent / "unpacked" / asset_path.stem
        
        output_dir.mkdir(parents=True, exist_ok=True)
        extracted_files = []
        
        try:
            env = UnityPy.load(str(asset_path))
            
            for obj in env.objects:
                if obj.type.name in ["TextAsset", "Texture2D", "Mesh", "Material"]:
                    data = obj.read()
                    
                    # Determine output filename and extension
                    filename = getattr(data, 'name', f"object_{obj.path_id}")
                    if not filename:
                        filename = f"object_{obj.path_id}"
                    
                    # Set appropriate extension based on type
                    if obj.type.name == "TextAsset":
                        if hasattr(data, 'script') and len(data.script) > 0:
                            # Check if it's binary data or text
                            try:
                                data.text  # Try to access text property
                                extension = ".txt"
                            except:
                                extension = ".bytes"
                        else:
                            extension = ".txt"
                    elif obj.type.name == "Texture2D":
                        extension = ".png"
                    elif obj.type.name == "Mesh":
                        extension = ".obj"
                    elif obj.type.name == "Material":
                        extension = ".mat"
                    else:
                        extension = ".bin"
                    
                    output_path = output_dir / f"{filename}{extension}"
                    
                    # Extract and save the data
                    try:
                        if obj.type.name == "TextAsset":
                            if hasattr(data, 'script'):
                                with open(output_path, "wb") as f:
                                    f.write(bytes(data.script))
                            else:
                                with open(output_path, "w", encoding="utf-8") as f:
                                    f.write(data.text)
                        elif obj.type.name == "Texture2D":
                            # Convert texture to image
                            image = data.image
                            if image:
                                image.save(output_path)
                        else:
                            # For other types, save raw data if available
                            if hasattr(data, 'save'):
                                data.save(output_path)
                        
                        extracted_files.append(output_path)
                        
                    except Exception as e:
                        self.logger.warning(f"Failed to extract {filename}: {e}")
            
            self.logger.info(f"Extracted {len(extracted_files)} files from {asset_path.name}")
            return extracted_files
            
        except Exception as e:
            raise UnpackError(f"Failed to unpack asset {asset_path}: {e}") from e
    
    def unpack_multiple_assets(self, asset_paths: list[Path]) -> dict[Path, list[Path]]:
        """Unpack multiple assets and return mapping of asset to extracted files."""
        results = {}
        
        with self.logger.create_task_progress() as progress:
            task = progress.add_task("Unpacking assets", total=len(asset_paths))
            
            for asset_path in asset_paths:
                try:
                    extracted_files = self.unpack_asset(asset_path)
                    results[asset_path] = extracted_files
                except Exception as e:
                    self.logger.error(f"Failed to unpack {asset_path}: {e}")
                    results[asset_path] = []
                
                progress.update(task, advance=1)
        
        successful = sum(1 for files in results.values() if files)
        self.logger.info(f"Successfully unpacked {successful}/{len(asset_paths)} assets")
        
        return results
    
    def get_asset_info(self, asset_path: Path) -> dict[str, Any]:
        """Get information about a Unity asset."""
        info = {
            "path": asset_path,
            "size": asset_path.stat().st_size,
            "objects": [],
            "types": set(),
        }
        
        try:
            env = UnityPy.load(str(asset_path))
            
            for obj in env.objects:
                obj_info = {
                    "type": obj.type.name,
                    "path_id": obj.path_id,
                }
                
                # Try to get additional info
                try:
                    data = obj.read()
                    if hasattr(data, 'name'):
                        obj_info["name"] = data.name
                except:
                    pass
                
                info["objects"].append(obj_info)
                info["types"].add(obj.type.name)
            
            info["types"] = list(info["types"])
            info["object_count"] = len(info["objects"])
            
        except Exception as e:
            self.logger.error(f"Failed to get info for {asset_path}: {e}")
        
        return info