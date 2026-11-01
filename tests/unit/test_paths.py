"""Unit tests for the path-safety helpers (core.paths)."""

from __future__ import annotations

from pathlib import Path

import pytest

from wog_dump.core.paths import (
    contained_path,
    is_safe_weapon_name,
    safe_object_name,
)


class TestSafeObjectName:
    @pytest.mark.parametrize(
        "name",
        ["ak47", "colt_1911", "texture.hd", "a", "muzzle_big", "shooting_01"],
    )
    def test_accepts_vendor_shapes(self, name: str) -> None:
        assert safe_object_name(name) == name

    @pytest.mark.parametrize(
        "name",
        [
            "",
            "..",
            ".",
            "../evil",
            "../../escaped",
            "/tmp/abs",
            "a/b",
            "a\\b",
            "nul\x00",
            ".hidden",
            " leading",
            "trailing ",
        ],
    )
    def test_rejects_unsafe_names(self, name: str) -> None:
        with pytest.raises(ValueError):
            safe_object_name(name)


class TestIsSafeWeaponName:
    @pytest.mark.parametrize(
        "name", ["ak47", "colt_1911", "m4a1", "desert_eagle", "bg_01"]
    )
    def test_accepts(self, name: str) -> None:
        assert is_safe_weapon_name(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "",
            "a",
            "x" * 51,
            "../pwn_ver",
            "q?x=1",
            "%2e%2e_enc",
            "spa ce",
            "a/b",
            "a\\b",
            "..",
            "-leading-dash",
        ],
    )
    def test_rejects(self, name: str) -> None:
        assert is_safe_weapon_name(name) is False


class TestContainedPath:
    def test_joins_inside_directory(self, tmp_path: Path) -> None:
        path = contained_path(tmp_path, "file.txt")
        assert path == tmp_path / "file.txt"
        assert path.parent == tmp_path

    def test_rejects_separator_escape(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            contained_path(tmp_path, "../escape.txt")

    def test_rejects_absolute_name(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            contained_path(tmp_path, "/tmp/abs.txt")

    def test_rejects_traversal_components(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            contained_path(tmp_path, "..")
