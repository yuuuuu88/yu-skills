#!/usr/bin/env python3
"""Check that every icon manifest entry points to the expected local asset."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ICON_DIR = ROOT / "assets" / "icons"
MANIFEST = ICON_DIR / "manifest.json"


def main() -> int:
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}")
        return 1
    errors: list[str] = []
    ids: set[str] = set()
    for item in manifest.get("icons", []):
        icon_id = str(item.get("id", ""))
        if not icon_id or icon_id in ids:
            errors.append(f"duplicate or missing icon id: {icon_id}")
        ids.add(icon_id)
        rel = Path(str(item.get("file", "")))
        path = (ICON_DIR / rel).resolve()
        if not path.is_relative_to(ICON_DIR.resolve()) or not path.is_file():
            errors.append(f"missing file: {icon_id} -> {rel}")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != item.get("sha256"):
            errors.append(f"hash mismatch: {icon_id}")
        if str(item.get("rights_class", "")) == "official-restricted" and not item.get("source_url"):
            errors.append(f"official icon has no source URL: {icon_id}")
        if item.get("transform") != "preserve-artwork":
            errors.append(f"provider artwork transform is not preserve-artwork: {icon_id}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"icon manifest passed ({len(ids)} icons)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
