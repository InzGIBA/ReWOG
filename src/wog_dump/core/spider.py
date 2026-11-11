"""Spider bundle: the game's system catalog asset.

``spider_gen.unity3d`` is not a weapon: it is the system asset the game
itself uses to build its menu. Besides the weapon list (``new_banners``
TextAsset, see ``core.unpack``) it carries everything the in-game store
card shows per model: full names in two languages, categories, parts
count, year, short description and links — plus one 256x256 icon and
one 1024x1024 studio card render per weapon.

This module owns everything spider-related: its system paths, the
metadata parser and the preview/metadata extraction that runs right
after the bundle is downloaded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import UnityPy

from ..utils.logging import get_logger
from .config import WOGConfig
from .database import WOGDatabase
from .paths import is_safe_weapon_name

__all__ = [
    "SPIDER_ASSET",
    "SYSTEM_ASSETS",
    "SpiderExtractResult",
    "parse_weapon_meta",
    "category_leaves",
    "spider_asset_path",
    "previews_dir",
    "extract_spider_content",
]

# The weapon-list bundle lives in a "spider" subdirectory on the data
# server (see download.SUBDIRECTORY_ASSETS) and never in weapon lists
SPIDER_ASSET = "spider_gen"

SYSTEM_ASSETS = frozenset({SPIDER_ASSET})

# Metadata records are the bare "<weapon>" TextAssets: "key = value"
# lines with quoted strings or bare numbers, separated from the
# "<weapon>_text"/"<weapon>_specs" prose assets by the '=' sign
_META_LINE_RE = re.compile(r"^([a-z_0-9]+)\s*=\s*(.*?)\s*$")

# Integer-typed keys converted on parse; everything else stays a string
_INT_KEYS = ("num_parts", "num_acts", "since", "registered_order")

# Metadata fields surfaced in the catalog and the web UI
_META_FIELDS = (
    "full_name",
    "full_name_ru",
    "short_name",
    "short_name_ru",
    "category",
    "num_parts",
    "num_acts",
    "since",
    "features",
    "features_ru",
    "brief_info",
    "brief_info_ru",
    "youtube",
    "wikipedia",
    "imfdb",
    "registered_order",
    # Server-side markers: "flags = test" marks models the game itself
    # hides (the asset bundle is not deployed yet), released is the
    # store announcement date as a raw "YYYY M D" string
    "flags",
    "released",
)

# Preview files written next to the runtime data; in the bundle the
# 1024px studio render is the "<weapon>_1" texture next to the 256px
# "<weapon>" icon
ICON_SUFFIX = ".png"
CARD_SUFFIX = "_card.png"
CARD_TEXTURE_SUFFIX = "_1"


@dataclass
class SpiderExtractResult:
    """What one extraction pass produced."""

    meta_count: int = 0
    icons: int = 0
    cards: int = 0


def parse_weapon_meta(text: str) -> dict[str, str | int | None]:
    """Parse one bare "<weapon>" TextAsset into a metadata record.

    The records are game-server content, so keys outside the known
    field set are dropped and unknown-shaped values are kept as
    strings. A record without a category line is still valid — only
    the bare "<weapon>" assets carry one, which is how prose assets
    are told apart without relying on their names.
    """
    fields: dict[str, str | int | None] = dict.fromkeys(_META_FIELDS, None)
    matched = False
    for line in text.splitlines():
        match = _META_LINE_RE.match(line.strip())
        if match is None:
            continue
        key, value = match.group(1), match.group(2)
        if key not in fields:
            continue
        matched = True
        # Quoted strings keep their inner text; bare numbers become ints
        if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        if key in _INT_KEYS:
            fields[key] = int(value) if value.isdigit() else None
        else:
            fields[key] = value
    return fields if matched else {}


def category_leaves(category: str | None) -> list[str]:
    """Split a raw category path list into display leaves.

    A record may carry several comma-separated category paths
    (``"Guns\\Assault Rifles,Guns\\Bullpup"``); the UI shows the last
    path segment of each.
    """
    if not category:
        return []
    leaves: list[str] = []
    for part in category.split(","):
        leaf = part.strip().split("\\")[-1].strip()
        if leaf and leaf not in leaves:
            leaves.append(leaf)
    return leaves


def spider_asset_path(config: WOGConfig) -> Path:
    """Where the downloader puts the system bundle (runtime/assets)."""
    return config.assets_dir / f"{SPIDER_ASSET}.unity3d"


def previews_dir(config: WOGConfig) -> Path:
    """Directory holding extracted weapon card images (runtime/previews)."""
    return config.base_dir / "runtime" / "previews"


def extract_spider_content(
    config: WOGConfig,
    asset_path: Path,
    weapon_names: list[str],
    force: bool = False,
) -> SpiderExtractResult:
    """Extract weapon metadata and card previews from the spider bundle.

    Writes one 256x256 icon and one 1024x1024 studio card per weapon to
    ``runtime/previews`` and upserts the metadata records into the
    database. Files already on disk are skipped unless ``force`` is
    set, so a re-run after a partial extraction only fills the gaps.
    """
    logger = get_logger()
    wanted = {name for name in weapon_names if is_safe_weapon_name(name)}
    if not asset_path.exists():
        logger.warning(f"Spider bundle not found: {asset_path}")
        return SpiderExtractResult()

    icons_dir = previews_dir(config)
    icons_dir.mkdir(parents=True, exist_ok=True)
    result = SpiderExtractResult()
    meta_records: dict[str, dict[str, str | int | None]] = {}

    env = UnityPy.load(str(asset_path))
    for obj in env.objects:
        if obj.type.name == "TextAsset":
            data = obj.read()
            name = data.m_Name
            if name not in wanted or name in meta_records:
                continue
            text = data.m_Script
            if isinstance(text, bytes):
                text = text.decode("utf-8", "replace")
            record = parse_weapon_meta(text)
            if record.get("category") is not None:
                meta_records[name] = record
        elif obj.type.name == "Texture2D":
            data = obj.read()
            name = data.m_Name
            if name in wanted:
                if _save_texture(data, icons_dir / f"{name}{ICON_SUFFIX}", force):
                    result.icons += 1
            elif (
                name.endswith(CARD_TEXTURE_SUFFIX)
                and name[: -len(CARD_TEXTURE_SUFFIX)] in wanted
            ):
                # Not a weapon name itself: the studio render of a weapon
                base = name[: -len(CARD_TEXTURE_SUFFIX)]
                if _save_texture(data, icons_dir / f"{base}{CARD_SUFFIX}", force):
                    result.cards += 1

    if meta_records:
        db = WOGDatabase(config)
        db.save_weapon_meta(meta_records)
    result.meta_count = len(meta_records)
    logger.info(
        f"Spider extraction: {result.meta_count} metadata records, "
        f"{result.icons} icons, {result.cards} cards"
    )
    return result


def _save_texture(texture: Any, path: Path, force: bool) -> bool:  # noqa: ANN401
    """Render one Unity texture to a PNG file, skipping existing work."""
    if path.exists() and not force:
        return False
    image = texture.image
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return True
