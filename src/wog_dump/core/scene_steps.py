"""Assembly step planning: pure split and window math (task 17).

The step export (``build-scene --assembly-steps``) places parts one
group at a time, in the game's removal order, and (optionally) animates
every part from its exploded position to home inside a per-part time
window. This module owns the numbers; scene emission lives in
``core/scene.py``.
"""

from __future__ import annotations

import math
from typing import Any

__all__ = [
    "assembled_counts",
    "assembly_windows",
    "node_paths_by_id",
    "seconds_per_step",
]


def assembled_counts(total: int, steps: int) -> list[int]:
    """Placed-part count after each step.

    Step i holds ``ceil(i / steps * total)`` parts, so the last step is
    always the fully assembled weapon and the first step carries at
    least one part (a weapon never starts from nothing).
    """
    if total < 1 or steps < 1:
        raise ValueError(f"total and steps must be >= 1, got {total}/{steps}")
    counts = []
    for step in range(1, steps + 1):
        count = math.ceil(step / steps * total)
        counts.append(max(1, min(count, total)))
    return counts


def seconds_per_step(steps: int) -> float:
    """One second per step keeps the animated build human-paced."""
    return 1.0 if steps >= 1 else 1.0


def assembly_windows(total: int, steps: int) -> list[tuple[float, float]]:
    """Animation time windows (seconds), one per part, in build order.

    Parts place sequentially: part i moves during
    ``[i * step_time, (i + 1) * step_time]``, so the whole build runs
    ``total`` seconds regardless of the step count (the steps set which
    parts sit together, the windows carry the motion).
    """
    if total < 1:
        raise ValueError(f"total must be >= 1, got {total}")
    step_time = seconds_per_step(steps)
    return [(i * step_time, (i + 1) * step_time) for i in range(total)]


def node_paths_by_id(root: Any) -> dict[int, str]:
    """Map node id -> Unity hierarchy path (the emission address space)."""
    paths: dict[int, str] = {}
    queue: list[tuple[Any, str]] = [(child, child.name) for child in root.children]
    while queue:
        node, path = queue.pop(0)
        paths[id(node)] = path
        for child in node.children:
            queue.append((child, f"{path}/{child.name}"))
    return paths


def split_plan(total: int, steps: int) -> list[list[int]]:
    """Indices of placed parts after each step (for tests and manifests)."""
    counts = assembled_counts(total, steps)
    return [list(range(count)) for count in counts]
