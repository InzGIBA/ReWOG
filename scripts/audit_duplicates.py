"""Duplicate audit for runtime/unpacked: sha256 every file, group, report."""

import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path("/home/inzgiba/projects/github/inzgiba/ReWOG/runtime/unpacked")

hashes: dict[str, list[str]] = defaultdict(list)
sizes: dict[str, int] = {}
total_files = 0
total_bytes = 0
for path in ROOT.rglob("*"):
    if not path.is_file():
        continue
    total_files += 1
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    key = digest.hexdigest()
    hashes[key].append(str(path))
    size = path.stat().st_size
    sizes[key] = size
    total_bytes += size

dup_groups = {k: v for k, v in hashes.items() if len(v) > 1}
dup_files = sum(len(v) for v in dup_groups.values())
# Duplicated volume = bytes stored as copies beyond the first occurrence
dup_bytes = sum(sizes[k] * (len(v) - 1) for k, v in dup_groups.items())
report = {
    "total_files": total_files,
    "total_bytes_gb": round(total_bytes / 1e9, 2),
    "unique_blobs": len(hashes),
    "dup_groups": len(dup_groups),
    "dup_files": dup_files,
    "dup_bytes_gb": round(dup_bytes / 1e9, 2),
    "dup_percent_of_total": round(dup_bytes / max(total_bytes, 1) * 100, 1),
}
print(json.dumps(report, indent=2))
top = sorted(dup_groups.items(), key=lambda kv: -sizes[kv[0]] * (len(kv[1]) - 1))[:10]
print("\nTop duplicate groups (wasted bytes):")
for key, paths in top:
    print(f"  {sizes[key]:>12,} B x{len(paths):>4}  {paths[0].split('unpacked/')[-1]}")
