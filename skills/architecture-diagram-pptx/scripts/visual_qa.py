#!/usr/bin/env python3
"""Geometry-based visual QA for architecture diagram specs.

This complements the OOXML validator.  It does not pretend to replace a
human, but it catches the failures visible in a rendered slide: connector
paths through nodes, labels over nodes or other labels, labels that float away
from their own connector, sibling-boundary collisions, connectors that run
along a boundary edge, zone-heading collisions, and legend overflow.
``render_preview.cjs`` produces the image that a reviewer can inspect
alongside this report.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


CANVAS = {"width": 13.333, "height": 7.5}
EPSILON = 0.02
MAX_EDGE_LABEL_DISTANCE = 0.32
MIN_SIBLING_BOUNDARY_GAP = 0.12
MIN_BOUNDARY_EDGE_OVERLAP = 0.15
MAX_NODE_TEXT_OVERLAP = 0.03


def rect(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, dict):
        return None
    try:
        x, y, w, h = (float(value[key]) for key in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return x, y, w, h


def overlaps(a: tuple[float, float, float, float], b: tuple[float, float, float, float], gap: float = 0.0) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw - gap and bx < ax + aw - gap and ay < by + bh - gap and by < ay + ah - gap


def inside(value: tuple[float, float, float, float], canvas: tuple[float, float, float, float]) -> bool:
    x, y, w, h = value
    cx, cy, cw, ch = canvas
    return x >= cx and y >= cy and x + w <= cx + cw and y + h <= cy + ch


def contains(outer: tuple[float, float, float, float], inner: tuple[float, float, float, float], padding: float = 0.0) -> bool:
    ox, oy, ow, oh = outer
    ix, iy, iw, ih = inner
    return (
        ix >= ox + padding
        and iy >= oy + padding
        and ix + iw <= ox + ow - padding
        and iy + ih <= oy + oh - padding
    )


def interval_overlap(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def rect_segment_distance(box: tuple[float, float, float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    """Return the shortest distance from an axis-aligned box to a route segment."""
    x, y, w, h = box
    x2, y2 = x + w, y + h
    if segment_intersects_rect(a, b, box):
        return 0.0
    if abs(a[1] - b[1]) <= EPSILON:
        left, right = sorted((a[0], b[0]))
        dx = max(x - right, left - x2, 0.0)
        dy = max(y - a[1], a[1] - y2, 0.0)
        return math.hypot(dx, dy)
    if abs(a[0] - b[0]) <= EPSILON:
        top, bottom = sorted((a[1], b[1]))
        dx = max(x - a[0], a[0] - x2, 0.0)
        dy = max(y - bottom, top - y2, 0.0)
        return math.hypot(dx, dy)
    return min(point_rect_distance(point, box) for point in (a, b))


def point_rect_distance(point: tuple[float, float], box: tuple[float, float, float, float]) -> float:
    px, py = point
    x, y, w, h = box
    dx = max(x - px, 0.0, px - (x + w))
    dy = max(y - py, 0.0, py - (y + h))
    return math.hypot(dx, dy)


def segment_runs_on_boundary_edge(a: tuple[float, float], b: tuple[float, float], boundary: tuple[float, float, float, float]) -> bool:
    """Detect the hard-to-read case where a connector follows a zone outline."""
    x, y, w, h = boundary
    if abs(a[1] - b[1]) <= EPSILON:
        overlap = interval_overlap(min(a[0], b[0]), max(a[0], b[0]), x, x + w)
        return overlap >= MIN_BOUNDARY_EDGE_OVERLAP and min(abs(a[1] - y), abs(a[1] - (y + h))) <= EPSILON
    if abs(a[0] - b[0]) <= EPSILON:
        overlap = interval_overlap(min(a[1], b[1]), max(a[1], b[1]), y, y + h)
        return overlap >= MIN_BOUNDARY_EDGE_OVERLAP and min(abs(a[0] - x), abs(a[0] - (x + w))) <= EPSILON
    return False


def anchor(box: tuple[float, float, float, float], name: str | None, source: bool) -> tuple[float, float]:
    x, y, w, h = box
    side = name or ("right" if source else "left")
    if side == "left":
        return x, y + h / 2
    if side == "top":
        return x + w / 2, y
    if side == "bottom":
        return x + w / 2, y + h
    return x + w, y + h / 2


def edge_points(edge: dict[str, Any], nodes: dict[str, dict[str, Any]]) -> list[tuple[float, float]]:
    source = rect(nodes[str(edge.get("from"))].get("position")) if str(edge.get("from")) in nodes else None
    target = rect(nodes[str(edge.get("to"))].get("position")) if str(edge.get("to")) in nodes else None
    if not source or not target:
        return []
    points = [anchor(source, edge.get("from_anchor"), True)]
    for point in edge.get("route", []) if isinstance(edge.get("route"), list) else []:
        if isinstance(point, dict):
            points.append((float(point["x"]), float(point["y"])))
    points.append(anchor(target, edge.get("to_anchor"), False))
    return points


def segment_box(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float, float, float]:
    return min(a[0], b[0]), min(a[1], b[1]), abs(a[0] - b[0]), abs(a[1] - b[1])


def segment_intersects_rect(a: tuple[float, float], b: tuple[float, float], box: tuple[float, float, float, float]) -> bool:
    x, y, w, h = box
    # All supported routes are orthogonal.  Expand the test very slightly so
    # a line that touches a node edge is treated as attached, not crossing.
    if abs(a[1] - b[1]) <= EPSILON:
        return y - EPSILON < a[1] < y + h + EPSILON and max(min(a[0], b[0]), x) < min(max(a[0], b[0]), x + w) - EPSILON
    if abs(a[0] - b[0]) <= EPSILON:
        return x - EPSILON < a[0] < x + w + EPSILON and max(min(a[1], b[1]), y) < min(max(a[1], b[1]), y + h) - EPSILON
    return True


def segments_cross(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float], d: tuple[float, float]) -> bool:
    if a in {c, d} or b in {c, d}:
        return False
    a_horizontal = abs(a[1] - b[1]) <= EPSILON
    c_horizontal = abs(c[1] - d[1]) <= EPSILON
    if a_horizontal and c_horizontal:
        return abs(a[1] - c[1]) <= EPSILON and max(min(a[0], b[0]), min(c[0], d[0])) < min(max(a[0], b[0]), max(c[0], d[0])) - EPSILON
    if not a_horizontal and not c_horizontal:
        return abs(a[0] - c[0]) <= EPSILON and max(min(a[1], b[1]), min(c[1], d[1])) < min(max(a[1], b[1]), max(c[1], d[1])) - EPSILON
    horizontal, vertical = ((a, b), (c, d)) if a_horizontal else ((c, d), (a, b))
    hx1, hx2 = sorted((horizontal[0][0], horizontal[1][0]))
    hy = horizontal[0][1]
    vx = vertical[0][0]
    vy1, vy2 = sorted((vertical[0][1], vertical[1][1]))
    return hx1 + EPSILON < vx < hx2 - EPSILON and vy1 + EPSILON < hy < vy2 - EPSILON


def boundary_label_box(boundary: dict[str, Any], position: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    custom = rect(boundary.get("label_position"))
    if custom:
        return custom
    label = str(boundary.get("label") or boundary.get("id") or "ゾーン")
    width = max(0.8, min(position[2] - 0.25, len(label) * 0.13 + 0.35))
    return position[0] + 0.14, position[1] + 0.08, width, 0.25


def edge_label_box(edge: dict[str, Any], points: list[tuple[float, float]], nodes: dict[str, dict[str, Any]]) -> tuple[float, float, float, float] | None:
    custom = rect(edge.get("label_position") or edge.get("label_box"))
    if custom:
        return custom
    if not edge.get("label") or len(points) < 2:
        return None
    longest = max(
        zip(points, points[1:]),
        key=lambda segment: math.hypot(segment[1][0] - segment[0][0], segment[1][1] - segment[0][1]),
    )
    start, end = longest
    center_x = (start[0] + end[0]) / 2
    center_y = (start[1] + end[1]) / 2
    height = 0.28
    horizontal = abs(end[1] - start[1]) < 0.001
    source = nodes.get(str(edge.get("from")), {})
    target = nodes.get(str(edge.get("to")), {})
    source_position = rect(source.get("position"))
    target_position = rect(target.get("position"))
    if not source_position or not target_position:
        return None
    width = max(0.55, min(1.8, len(str(edge["label"])) * 0.13 + 0.2))
    if not custom and horizontal and len(points) == 2:
        gap = target_position[0] - (source_position[0] + source_position[2])
        if gap > 0:
            width = min(width, max(0.4, gap - 0.04))
    y = center_y - height / 2
    x = center_x - width / 2 if horizontal else center_x + 0.12
    return x, y, width, height


def node_text_boxes(node: dict[str, Any], position: tuple[float, float, float, float]) -> tuple[tuple[float, float, float, float], tuple[float, float, float, float] | None]:
    """Approximate the editable label/role text boxes used by the generator.

    This is intentionally a geometry check, not OCR.  It catches the common
    compact-card failure where the secondary role line is placed on top of the
    primary product label before a reviewer even opens PowerPoint.
    """
    x, y, w, h = position
    icon_size = max(0.42, min(0.68, w * 0.58, h * 0.58))
    label_y = y + max(0.58, icon_size + 0.07)
    label_height = min(0.30, max(0.18, h - (label_y - y) - (0.16 if node.get("role") else 0.04)))
    label_box = (x + 0.03, label_y, max(0.0, w - 0.06), label_height)
    role_box = None
    if node.get("role"):
        role_box = (x + 0.04, y + h - 0.17, max(0.0, w - 0.08), 0.14)
    return label_box, role_box


def legend_boxes(spec: dict[str, Any]) -> list[tuple[str, tuple[float, float, float, float]]]:
    relations = spec.get("legend_relations") or list(dict.fromkeys(edge.get("relation") for edge in spec.get("edges", []) if edge.get("relation")))
    widths = {"monitoring": 1.95, "message": 1.75, "dependency": 1.65}
    result = [("legend-title", (0.55, 6.38, 0.55, 0.22))]
    x = 1.1
    for relation in relations:
        width = widths.get(str(relation), 1.55)
        result.append((f"legend:{relation}", (x + 0.48, 6.37, width - 0.48, 0.28)))
        x += width + 0.12
        if x > 12.8:
            break
    return result


def evaluate(spec: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []
    canvas_data = spec.get("canvas") if isinstance(spec.get("canvas"), dict) else {}
    canvas = (0.0, 0.0, float(canvas_data.get("width", CANVAS["width"])), float(canvas_data.get("height", CANVAS["height"])))
    nodes = {str(node.get("id")): node for node in spec.get("nodes", []) if isinstance(node, dict) and node.get("id")}
    node_boxes = {node_id: rect(node.get("position")) for node_id, node in nodes.items()}
    node_boxes = {node_id: box for node_id, box in node_boxes.items() if box}

    boundaries: list[tuple[str, tuple[float, float, float, float], tuple[float, float, float, float]]] = []
    boundary_members: dict[str, list[str]] = {}
    for boundary in spec.get("boundaries", []) if isinstance(spec.get("boundaries"), list) else []:
        if not isinstance(boundary, dict):
            continue
        position = rect(boundary.get("position"))
        if not position:
            members = [node_boxes[node_id] for node_id in boundary.get("node_ids", []) if node_id in node_boxes]
            if members:
                position = (min(item[0] for item in members) - 0.22, min(item[1] for item in members) - 0.45, max(item[0] + item[2] for item in members) - min(item[0] for item in members) + 0.44, max(item[1] + item[3] for item in members) - min(item[1] for item in members) + 0.8)
        if not position:
            continue
        boundary_id = str(boundary.get("id") or boundary.get("label") or "boundary")
        boundaries.append((boundary_id, position, boundary_label_box(boundary, position)))
        boundary_members[boundary_id] = [str(node_id) for node_id in boundary.get("node_ids", [])]

    edge_data: list[tuple[str, dict[str, Any], list[tuple[float, float]], tuple[float, float, float, float] | None]] = []
    for index, edge in enumerate(spec.get("edges", []) if isinstance(spec.get("edges"), list) else []):
        if not isinstance(edge, dict):
            continue
        points = edge_points(edge, nodes)
        label = edge_label_box(edge, points, nodes) if edge.get("label") else None
        edge_data.append((str(edge.get("id") or index), edge, points, label))

    node_text_collisions = 0
    for node_id, node_box in node_boxes.items():
        node = nodes[node_id]
        label_box, role_box = node_text_boxes(node, node_box)
        if not role_box:
            continue
        overlap_height = interval_overlap(label_box[1], label_box[1] + label_box[3], role_box[1], role_box[1] + role_box[3])
        overlap_width = interval_overlap(label_box[0], label_box[0] + label_box[2], role_box[0], role_box[0] + role_box[2])
        overlap_area = overlap_height * overlap_width
        if overlap_height > MAX_NODE_TEXT_OVERLAP and overlap_width > EPSILON:
            node_text_collisions += 1
            errors.append(f"node text boxes overlap: {node_id} label/role ({overlap_height:.2f}in vertically)")

    # Node placement and text-space checks.
    for left_id, left_box in node_boxes.items():
        if not inside(left_box, canvas):
            errors.append(f"node {left_id} is outside the canvas")
        for right_id, right_box in node_boxes.items():
            if left_id < right_id and overlaps(left_box, right_box):
                errors.append(f"node boxes overlap: {left_id} and {right_id}")

    # Headings are separate editable text in the PPTX and must not sit on
    # another heading or a node.  Nested boundaries need explicit label lanes.
    for index, (boundary_id, position, heading) in enumerate(boundaries):
        if not inside(heading, canvas):
            errors.append(f"boundary heading {boundary_id} is outside the canvas")
        if not inside(heading, position):
            errors.append(f"boundary heading {boundary_id} is outside its boundary")
        for node_id, node_box in node_boxes.items():
            if overlaps(heading, node_box):
                errors.append(f"boundary heading {boundary_id} overlaps node {node_id}")
        for other_id, _other_position, other_heading in boundaries[index + 1 :]:
            if overlaps(heading, other_heading):
                errors.append(f"boundary headings overlap: {boundary_id} and {other_id}")
        for node_id in boundary_members.get(boundary_id, []):
            node_box = node_boxes.get(node_id)
            if node_box and not contains(position, node_box):
                errors.append(f"boundary {boundary_id} does not contain member node {node_id}")

    # Boundary outlines are containers, not a second route system.  Nested
    # boundaries (account -> zone -> VPC) may overlap, but sibling boundaries
    # must have a visible gap.  This prevents the common screenshot failure
    # where two pale rectangles merge into one ambiguous region.
    for index, (first_id, first_position, _first_heading) in enumerate(boundaries):
        for second_id, second_position, _second_heading in boundaries[index + 1 :]:
            if contains(first_position, second_position) or contains(second_position, first_position):
                continue
            if overlaps(first_position, second_position):
                errors.append(f"boundaries overlap without nesting: {first_id} and {second_id}")
                continue
            fx, fy, fw, fh = first_position
            sx, sy, sw, sh = second_position
            x_projection = interval_overlap(fx, fx + fw, sx, sx + sw)
            y_projection = interval_overlap(fy, fy + fh, sy, sy + sh)
            horizontal_gap = max(sx - (fx + fw), fx - (sx + sw), 0.0)
            vertical_gap = max(sy - (fy + fh), fy - (sy + sh), 0.0)
            if x_projection > EPSILON and vertical_gap < MIN_SIBLING_BOUNDARY_GAP:
                errors.append(f"boundaries are too close vertically: {first_id} and {second_id}")
            if y_projection > EPSILON and horizontal_gap < MIN_SIBLING_BOUNDARY_GAP:
                errors.append(f"boundaries are too close horizontally: {first_id} and {second_id}")

    # Edge labels are the most common source of screenshots that look broken.
    for edge_id, _edge, _points, label_box in edge_data:
        if not label_box:
            if _edge.get("label"):
                warnings.append(f"edge {edge_id} has a label but no usable label placement")
            continue
        if not inside(label_box, canvas):
            errors.append(f"edge label {edge_id} is outside the canvas")
        if _points:
            distance = min(
                (rect_segment_distance(label_box, start, end) for start, end in zip(_points, _points[1:])),
                default=math.inf,
            )
            if distance > MAX_EDGE_LABEL_DISTANCE:
                errors.append(
                    f"edge label {edge_id} is too far from its connector ({distance:.2f} > {MAX_EDGE_LABEL_DISTANCE:.2f})"
                )
            if len(_points) == 2 and abs(_points[0][1] - _points[1][1]) <= EPSILON:
                source_position = rect(nodes.get(str(_edge.get("from")), {}).get("position"))
                target_position = rect(nodes.get(str(_edge.get("to")), {}).get("position"))
                if source_position and target_position:
                    gap_left = source_position[0] + source_position[2]
                    gap_right = target_position[0]
                    if gap_right > gap_left and (
                        label_box[0] < gap_left - EPSILON or label_box[0] + label_box[2] > gap_right + EPSILON
                    ):
                        errors.append(
                            f"edge label {edge_id} does not fit between endpoint nodes "
                            f"(label width {label_box[2]:.2f}, gap {gap_right - gap_left:.2f})"
                        )
        for node_id, node_box in node_boxes.items():
            if overlaps(label_box, node_box, gap=0.01):
                errors.append(f"edge label {edge_id} overlaps node {node_id}")
        for boundary_id, _position, heading in boundaries:
            if overlaps(label_box, heading, gap=0.01):
                errors.append(f"edge label {edge_id} overlaps boundary heading {boundary_id}")
        for other_id, _other_edge, _other_points, other_label in edge_data:
            if other_id != edge_id and other_label and overlaps(label_box, other_label, gap=0.01):
                errors.append(f"edge labels overlap: {edge_id} and {other_id}")

    # Connector paths must not go through unrelated nodes, headings or other
    # labels.  The source/target node edges are intentionally attached.
    segment_data: list[tuple[str, tuple[float, float], tuple[float, float]]] = []
    for edge_id, edge, points, label_box in edge_data:
        if len(points) < 2:
            continue
        endpoint_ids = {str(edge.get("from")), str(edge.get("to"))}
        for start, end in zip(points, points[1:]):
            segment_data.append((edge_id, start, end))
            for node_id, node_box in node_boxes.items():
                if node_id not in endpoint_ids and segment_intersects_rect(start, end, node_box):
                    errors.append(f"connector {edge_id} passes through node {node_id}")
            for boundary_id, _position, heading in boundaries:
                if segment_intersects_rect(start, end, heading):
                    errors.append(f"connector {edge_id} passes through boundary heading {boundary_id}")
                if segment_runs_on_boundary_edge(start, end, _position):
                    errors.append(f"connector {edge_id} runs along boundary edge {boundary_id}")
                for other_id, _other_edge, _other_points, other_label in edge_data:
                    if other_id != edge_id and other_label and segment_intersects_rect(start, end, other_label):
                        errors.append(f"connector {edge_id} passes through label {other_id}")
        if label_box and any(segment_intersects_rect(start, end, label_box) for start, end in zip(points, points[1:])):
            notes.append(f"edge label {edge_id} sits on its own connector; white label background is required")

    for first_index, first in enumerate(segment_data):
        for second in segment_data[first_index + 1 :]:
            if first[0] != second[0] and segments_cross(first[1], first[2], second[1], second[2]):
                errors.append(f"connector paths cross: {first[0]} and {second[0]}")

    for first_index, (first_id, _position, first_heading) in enumerate(boundaries):
        for second_id, _second_position, second_heading in boundaries[first_index + 1 :]:
            if overlaps(first_heading, second_heading):
                errors.append(f"boundary headings overlap: {first_id} and {second_id}")

    # Legend boxes should not overlap each other and should stay above the
    # footer/annotation lane.
    legend = legend_boxes(spec)
    for index, (first_id, first_box) in enumerate(legend):
        for second_id, second_box in legend[index + 1 :]:
            if overlaps(first_box, second_box, gap=0.01):
                errors.append(f"legend text boxes overlap: {first_id} and {second_id}")

    annotation_boxes: list[tuple[str, tuple[float, float, float, float]]] = []
    for annotation in spec.get("annotations", []) if isinstance(spec.get("annotations"), list) else []:
        if isinstance(annotation, dict) and annotation.get("text"):
            box = rect(annotation.get("position"))
            if box:
                annotation_boxes.append((str(annotation.get("id") or "annotation"), box))
                if not inside(box, canvas):
                    errors.append(f"annotation {annotation.get('id', '')} is outside the canvas")

    # Duplicate messages are noisy when a route has several segments.
    errors = list(dict.fromkeys(errors))
    warnings = list(dict.fromkeys(warnings))
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "notes": list(dict.fromkeys(notes)),
        "metrics": {
            "nodes": len(node_boxes),
            "boundaries": len(boundaries),
            "edge_labels": sum(1 for _edge_id, edge, _points, box in edge_data if edge.get("label") and box),
            "connectors": len(segment_data),
            "legend_items": max(0, len(legend) - 1),
            "node_text_collisions": node_text_collisions,
        },
        "rules": {
            "max_edge_label_distance": MAX_EDGE_LABEL_DISTANCE,
            "min_sibling_boundary_gap": MIN_SIBLING_BOUNDARY_GAP,
            "min_boundary_edge_overlap": MIN_BOUNDARY_EDGE_OVERLAP,
            "max_node_text_overlap": MAX_NODE_TEXT_OVERLAP,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    args = parser.parse_args()
    try:
        spec = json.loads(args.spec.read_text(encoding="utf-8"))
        report = evaluate(spec)
    except (OSError, json.JSONDecodeError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}")
        return 2
    if args.json_out:
        args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for item in report["errors"]:
        print(f"ERROR: {item}")
    for item in report["warnings"]:
        print(f"WARNING: {item}")
    for item in report["notes"]:
        print(f"NOTE: {item}")
    failed = report["errors"] or (args.strict and report["warnings"])
    print(f"visual QA {'failed' if failed else 'passed'}: {report['metrics']}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
