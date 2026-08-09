#!/usr/bin/env python3
"""Run lightweight, dependency-free structural checks on an OOXML PPTX."""
from __future__ import annotations

import argparse
import json
import posixpath
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main", "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
REQUIRED = {"[Content_Types].xml", "ppt/presentation.xml", "_rels/.rels"}
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def inspect(path: Path, spec_path: Path | None = None) -> tuple[dict[str, object], list[str]]:
    errors: list[str] = []
    result: dict[str, object] = {"file": str(path), "slides": 0, "shapes": 0, "connectors": 0, "line_shapes": 0, "pictures": 0, "icon_boxes": [], "text_runs": 0, "text": []}
    if not path.is_file():
        return result, [f"file not found: {path}"]
    try:
        with zipfile.ZipFile(path) as archive:
            bad_member = archive.testzip()
            if bad_member:
                errors.append(f"CRC check failed for OOXML part: {bad_member}")
            names = set(archive.namelist())
            missing = sorted(REQUIRED - names)
            if missing:
                errors.append("missing required OOXML parts: " + ", ".join(missing))
            slide_names = sorted(name for name in names if name.startswith("ppt/slides/slide") and name.endswith(".xml"))
            result["slides"] = len(slide_names)
            if not slide_names:
                errors.append("presentation contains no slide XML")
            # A broken relationship target is a common source of PowerPoint's
            # "repair this presentation" dialog.  Validate internal targets
            # without depending on lxml or python-pptx.
            for rel_name in sorted(name for name in names if name.endswith('.rels')):
                try:
                    rel_root = ET.fromstring(archive.read(rel_name))
                except ET.ParseError as exc:
                    errors.append(f"invalid XML in {rel_name}: {exc}")
                    continue
                source_dir = Path(rel_name).parent.parent if rel_name != '_rels/.rels' else Path('.')
                for rel in rel_root.findall(f"{{{REL_NS}}}Relationship"):
                    target = rel.attrib.get('Target', '')
                    if not target or rel.attrib.get('TargetMode') == 'External':
                        continue
                    base = source_dir / target
                    normal = posixpath.normpath(base.as_posix()).lstrip('./')
                    if normal not in names:
                        errors.append(f"relationship target missing: {rel_name} -> {target}")
            all_text: list[str] = []
            for slide_name in slide_names:
                try:
                    root = ET.fromstring(archive.read(slide_name))
                except ET.ParseError as exc:
                    errors.append(f"invalid XML in {slide_name}: {exc}")
                    continue
                shapes = root.findall(".//p:sp", NS)
                line_shapes = [shape for shape in shapes if (shape.find(".//a:prstGeom", NS) is not None and shape.find(".//a:prstGeom", NS).get("prst") in {"line", "lineInv"})]
                result["shapes"] = int(result["shapes"]) + len(shapes) - len(line_shapes)
                result["line_shapes"] = int(result["line_shapes"]) + len(line_shapes)
                result["connectors"] = int(result["connectors"]) + len(root.findall(".//p:cxnSp", NS)) + len(line_shapes)
                pictures = root.findall(".//p:pic", NS)
                result["pictures"] = int(result["pictures"]) + len(pictures)
                icon_boxes = result["icon_boxes"]
                assert isinstance(icon_boxes, list)
                for picture in pictures:
                    ext = picture.find(".//a:xfrm/a:ext", NS)
                    if ext is None:
                        errors.append(f"picture has no extent in {slide_name}")
                        continue
                    try:
                        cx = int(ext.attrib["cx"]) / 914400
                        cy = int(ext.attrib["cy"]) / 914400
                    except (KeyError, TypeError, ValueError):
                        errors.append(f"picture has invalid extent in {slide_name}")
                        continue
                    icon_boxes.append({"w": round(cx, 3), "h": round(cy, 3)})
                texts = [node.text or "" for node in root.findall(".//a:t", NS)]
                all_text.extend(texts)
            result["text_runs"] = len(all_text)
            result["text"] = all_text[:200]
            if int(result["slides"]) and int(result["text_runs"]) == 0:
                errors.append("slides contain no editable text runs")
            if int(result["slides"]) and int(result["shapes"]) == 0 and int(result["connectors"]) == 0 and int(result["pictures"]) == 1:
                errors.append("presentation appears to be a single flattened image; editable diagram objects are required")
            if "ppt/media" in "\n".join(sorted(names)) and int(result["pictures"]) > 30:
                errors.append("too many embedded pictures for a simple architecture diagram; review icon duplication and rasterization")
            if spec_path is not None:
                try:
                    spec = json.loads(spec_path.read_text(encoding="utf-8"))
                    node_count = len([node for node in spec.get("nodes", []) if isinstance(node, dict)])
                except (OSError, json.JSONDecodeError) as exc:
                    errors.append(f"cannot read spec for icon audit: {exc}")
                    node_count = 0
                if node_count and int(result["pictures"]) < node_count:
                    errors.append(f"icon picture count {result['pictures']} is below node count {node_count}")
                too_small = [box for box in result["icon_boxes"] if min(float(box["w"]), float(box["h"])) < 0.40]
                if too_small:
                    errors.append(f"icon picture is smaller than 0.40in: {too_small}")
    except (OSError, zipfile.BadZipFile) as exc:
        errors.append(f"cannot read PPTX zip package: {exc}")
    return result, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pptx", type=Path)
    parser.add_argument("--spec", type=Path, help="optional spec JSON for icon count and size audit")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    result, errors = inspect(args.pptx, args.spec)
    if args.as_json:
        print(json.dumps({"ok": not errors, "result": result, "errors": errors}, ensure_ascii=False, indent=2))
    else:
        print(f"slides={result['slides']} shapes={result['shapes']} connectors={result['connectors']} line_shapes={result['line_shapes']} pictures={result['pictures']} text_runs={result['text_runs']}")
        if result["text"]:
            print("text=" + " | ".join(str(item) for item in result["text"][:20]))
        for error in errors:
            print("ERROR: " + error)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
