"""Scene report data extraction and Markdown rendering."""

from __future__ import annotations

import math
from typing import Any

_COUNT_KEYS = ("meshes", "materials", "textures", "nodes")
_MM_PER_METER = 1000.0
_TOP_PLACEMENTS = 10


def _vector3(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        return [float(v) for v in value]
    except (TypeError, ValueError):
        return None


def _to_mm(values: list[float]) -> list[float]:
    return [round(v * _MM_PER_METER, 3) for v in values]


def _parts(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    annotations: dict[str, dict[str, Any]] = {}
    for annotation in manifest.get("annotations", []):
        if isinstance(annotation, dict) and isinstance(annotation.get("name"), str):
            annotations[annotation["name"]] = annotation
    parts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in ("parts", "field_strip"):
        for name in manifest.get(group, []):
            if not isinstance(name, str) or name in seen:
                continue
            seen.add(name)
            annotation = annotations.get(name, {})
            bbox = _vector3(annotation.get("bbox_size"))
            parts.append(
                {
                    "name": name,
                    "group": annotation.get("group", group),
                    "bbox_size_mm": _to_mm(bbox) if bbox is not None else None,
                }
            )
    return parts


def _placements(manifest: dict[str, Any]) -> dict[str, Any]:
    raw = manifest.get("placements", [])
    entries: list[dict[str, Any]] = []
    for placement in raw if isinstance(raw, list) else []:
        if not isinstance(placement, dict):
            continue
        offset = _vector3(placement.get("offset"))
        if offset is None:
            continue
        magnitude = math.sqrt(sum(v * v for v in offset))
        entries.append(
            {
                "name": str(placement.get("name", "")),
                "offset_mm": _to_mm(offset),
                "magnitude_mm": round(magnitude * _MM_PER_METER, 3),
            }
        )
    entries.sort(key=lambda entry: entry["magnitude_mm"], reverse=True)
    return {
        "count": len(raw) if isinstance(raw, list) else 0,
        "top": entries[:_TOP_PLACEMENTS],
    }


def _print_section(print_report: dict[str, Any] | None) -> dict[str, Any] | None:
    if print_report is None:
        return None
    parts: list[dict[str, Any]] = []
    for part in print_report.get("parts", []):
        if not isinstance(part, dict):
            continue
        entry: dict[str, Any] = {
            "name": str(part.get("name", "")),
            "watertight": bool(part.get("watertight", False)),
            "status": str(part.get("status", "")),
        }
        if isinstance(part.get("estimate"), dict):
            entry["estimate"] = part["estimate"]
        parts.append(entry)
    estimate = print_report.get("estimate")
    totals = estimate.get("totals") if isinstance(estimate, dict) else None
    return {
        "parts": parts,
        "estimate_totals": totals if isinstance(totals, dict) else None,
    }


def build_report(
    manifest: dict[str, Any], print_report: dict[str, Any] | None
) -> dict[str, Any]:
    """Structured report for one scene manifest (plus optional print report)."""
    viewer_states = manifest.get("viewer_states")
    report: dict[str, Any] = {
        "weapon": str(manifest.get("weapon", "")),
        "state": str(manifest.get("state", "unknown")),
        "units": str(manifest.get("units", "meters")),
        "counts": {key: int(manifest.get(key) or 0) for key in _COUNT_KEYS},
        "parts": _parts(manifest),
        "placements": _placements(manifest),
        "skipped": dict(manifest.get("skipped") or {}),
        "viewer_states": (
            sorted(viewer_states) if isinstance(viewer_states, dict) else []
        ),
        "print": _print_section(print_report),
    }
    anchor_checks = manifest.get("anchor_checks")
    if isinstance(anchor_checks, dict) and anchor_checks:
        report["anchor_checks"] = dict(anchor_checks)
    return report


def _fmt(value: float) -> str:
    return f"{value:.2f}"


def _size_cells(bbox: list[float] | None) -> str:
    if bbox is None:
        return "- | - | -"
    return " | ".join(_fmt(v) for v in bbox)


def render_markdown(report: dict[str, Any]) -> str:
    """Plain-text Markdown report; tables stay simple, no escaping needed."""
    weapon = report["weapon"]
    counts = report["counts"]
    lines: list[str] = [
        f"# Scene report: {weapon}",
        "",
        f"- Weapon: {weapon}",
        f"- State: {report['state']}",
        f"- Units: {report['units']}",
        "",
        "## Counts",
        "",
        "| Meshes | Materials | Textures | Nodes |",
        "| --- | --- | --- | --- |",
        f"| {counts['meshes']} | {counts['materials']} "
        f"| {counts['textures']} | {counts['nodes']} |",
        "",
    ]

    parts = report["parts"]
    lines += [f"## Parts ({len(parts)})", ""]
    if parts:
        lines += [
            "| Part | Group | Size X (mm) | Size Y (mm) | Size Z (mm) |",
            "| --- | --- | --- | --- | --- |",
        ]
        for part in parts:
            lines.append(
                f"| {part['name']} | {part['group']} | {_size_cells(part['bbox_size_mm'])} |"
            )
    else:
        lines.append("No parts listed.")
    lines.append("")

    placements = report["placements"]
    lines += ["## Placements", "", f"Count: {placements['count']}", ""]
    if placements["top"]:
        lines += [
            "Top by offset magnitude:",
            "",
            "| Part | Offset X (mm) | Offset Y (mm) | Offset Z (mm) | Magnitude (mm) |",
            "| --- | --- | --- | --- | --- |",
        ]
        for entry in placements["top"]:
            offset = " | ".join(_fmt(v) for v in entry["offset_mm"])
            lines.append(
                f"| {entry['name']} | {offset} | {_fmt(entry['magnitude_mm'])} |"
            )
    else:
        lines.append("No placements recorded.")
    lines.append("")

    anchor_checks = report.get("anchor_checks")
    if anchor_checks:
        lines += ["## Anchor checks", "", "| Check | Count |", "| --- | --- |"]
        lines += [f"| {key} | {value} |" for key, value in anchor_checks.items()]
        lines.append("")

    skipped = report["skipped"]
    if skipped:
        lines += ["## Skipped", "", "| Reason | Count |", "| --- | --- |"]
        lines += [f"| {key} | {value} |" for key, value in skipped.items()]
        lines.append("")

    viewer_states = report["viewer_states"]
    lines += [
        "## Viewer states",
        "",
        f"Available: {', '.join(viewer_states) if viewer_states else 'none'}",
        "",
    ]

    print_section = report["print"]
    if print_section is not None:
        lines += ["## Print diagnostics", ""]
        if print_section["parts"]:
            lines += [
                "| Part | Watertight | Status |",
                "| --- | --- | --- |",
            ]
            for part in print_section["parts"]:
                watertight = "yes" if part["watertight"] else "no"
                lines.append(f"| {part['name']} | {watertight} | {part['status']} |")
        else:
            lines.append("No print parts recorded.")
        totals = print_section["estimate_totals"]
        if totals:
            lines += ["", "Estimate totals:", ""]
            lines += [f"- {key}: {value}" for key, value in totals.items()]
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
