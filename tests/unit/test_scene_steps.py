"""Unit tests for the assembly step planner (task 17)."""

from __future__ import annotations

from pathlib import Path

import pytest

from wog_dump.core.scene_steps import (
    assembled_counts,
    assembly_windows,
    node_paths_by_id,
    seconds_per_step,
    split_plan,
)


class TestAssembledCounts:
    def test_even_split(self) -> None:
        assert assembled_counts(6, 3) == [2, 4, 6]

    def test_ceil_split_covers_every_part(self) -> None:
        counts = assembled_counts(23, 3)
        assert counts == [8, 16, 23]
        assert counts[-1] == 23

    def test_first_step_never_empty(self) -> None:
        assert assembled_counts(2, 5) == [1, 1, 2, 2, 2]

    def test_single_step_is_full(self) -> None:
        assert assembled_counts(9, 1) == [9]

    def test_rejects_invalid(self) -> None:
        with pytest.raises(ValueError):
            assembled_counts(0, 3)
        with pytest.raises(ValueError):
            assembled_counts(5, 0)


class TestWindows:
    def test_sequential_one_second_windows(self) -> None:
        assert assembly_windows(3, 3) == [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)]

    def test_step_count_does_not_change_total_duration(self) -> None:
        # The build always takes one second per part; steps set which
        # parts sit together, not the pace
        assert assembly_windows(4, 1) == assembly_windows(4, 4)

    def test_windows_monotonic_and_contiguous(self) -> None:
        windows = assembly_windows(10, 3)
        times = [t for window in windows for t in window]
        assert times == sorted(times)
        for (_, end), (start, _) in zip(windows, windows[1:]):
            assert start == end

    def test_seconds_per_step(self) -> None:
        assert seconds_per_step(5) == 1.0


class TestSplitPlan:
    def test_plan_matches_counts(self) -> None:
        plan = split_plan(5, 5)
        assert plan[0] == [0]
        assert plan[-1] == [0, 1, 2, 3, 4]


class TestNodePaths:
    def test_paths_match_emission_convention(self) -> None:
        from wog_dump.core.scene import _GameObjectInfo

        leaf = _GameObjectInfo(name="bolt")
        middle = _GameObjectInfo(name="receiver", children=[leaf])
        root = _GameObjectInfo(name="root", children=[middle])

        paths = node_paths_by_id(root)
        assert paths[id(middle)] == "receiver"
        assert paths[id(leaf)] == "receiver/bolt"


class TestStepsExportValidation:
    def test_steps_with_state_rejected(self, tmp_path: Path) -> None:
        from wog_dump.core.config import set_config
        from wog_dump.core.scene import SceneBuilder, SceneExportError

        set_config(base_dir=tmp_path)
        builder = SceneBuilder()
        with pytest.raises(SceneExportError, match="assembly-steps"):
            builder.build_scene("ak47", state="exploded", assembly_steps=3)

    def test_bad_steps_source_rejected(self, tmp_path: Path) -> None:
        from wog_dump.core.config import set_config
        from wog_dump.core.scene import SceneBuilder, SceneExportError

        set_config(base_dir=tmp_path)
        builder = SceneBuilder()
        with pytest.raises(SceneExportError, match="steps source"):
            builder.build_scene("ak47", assembly_steps=3, steps_from="bogus")
