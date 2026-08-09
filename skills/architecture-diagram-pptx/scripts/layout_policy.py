#!/usr/bin/env python3
"""Geometry checks for the editable architecture-diagram spec.

The policy is deliberately conservative: a generated slide should be easy to
scan and easy to edit.  Coordinates are inches on a 16:9 slide unless a spec
sets another canvas.  This module does not render a slide; it catches the
layout failures that tend to make a rendered slide look broken.
"""
from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from typing import Any


DEFAULT_CANVAS = {"width": 13.333, "height": 7.5}
DEFAULT_SAFE = {"x": 0.45, "y": 0.95, "w": 12.43, "h": 5.95}
EPSILON = 0.06


def _rect(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, dict):
        return None
    try:
        result = tuple(float(value[key]) for key in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    if any(item <= 0 for item in result[2:]):
        return None
    return result


def _overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float], gap: float = 0.0) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw - gap and bx < ax + aw - gap and ay < by + bh - gap and by < ay + ah - gap


def _inside(inner: tuple[float, float, float, float], outer: tuple[float, float, float, float], margin: float = 0.0) -> bool:
    x, y, w, h = inner
    ox, oy, ow, oh = outer
    return x >= ox + margin and y >= oy + margin and x + w <= ox + ow - margin and y + h <= oy + oh - margin


def _segment(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float, float, float]:
    return min(a[0], b[0]), min(a[1], b[1]), abs(a[0] - b[0]), abs(a[1] - b[1])


def _segments_cross(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float], d: tuple[float, float]) -> bool:
    # The policy only calls this for axis-aligned segments.  Shared endpoints
    # are intentional, so they do not count as crossings.
    if (a == c or a == d or b == c or b == d):
        return False
    ax, ay, aw, ah = _segment(a, b)
    cx, cy, cw, ch = _segment(c, d)
    a_horizontal = ah < EPSILON
    c_horizontal = ch < EPSILON
    if a_horizontal and c_horizontal:
        return abs(ay - cy) < EPSILON and not (ax + aw <= cx or cx + cw <= ax)
    if not a_horizontal and not c_horizontal:
        return abs(ax - cx) < EPSILON and not (ay + ah <= cy or cy + ch <= ay)
    horizontal = (a, b) if a_horizontal else (c, d)
    vertical = (c, d) if a_horizontal else (a, b)
    hx = min(horizontal[0][0], horizontal[1][0])
    hx2 = max(horizontal[0][0], horizontal[1][0])
    hy = horizontal[0][1]
    vx = vertical[0][0]
    vy = min(vertical[0][1], vertical[1][1])
    vy2 = max(vertical[0][1], vertical[1][1])
    return hx + EPSILON < vx < hx2 - EPSILON and vy + EPSILON < hy < vy2 - EPSILON


def _anchor(box: tuple[float, float, float, float], anchor: str | None, *, source: bool) -> tuple[float, float]:
    x, y, w, h = box
    name = anchor or ("right" if source else "left")
    if name == "left":
        return x, y + h / 2
    if name == "top":
        return x + w / 2, y
    if name == "bottom":
        return x + w / 2, y + h
    return x + w, y + h / 2


def _edge_points(edge: dict[str, Any], boxes: dict[str, tuple[float, float, float, float]]) -> list[tuple[float, float]] | None:
    source = boxes.get(str(edge.get("from")))
    target = boxes.get(str(edge.get("to")))
    if not source or not target:
        return None
    sx, sy, sw, sh = source
    tx, ty, tw, th = target
    start = _anchor(source, edge.get("from_anchor"), source=True)
    end = _anchor(target, edge.get("to_anchor"), source=False)
    route = edge.get("route")
    if isinstance(route, list) and route:
        points: list[tuple[float, float]] = [start]
        for point in route:
            if not isinstance(point, dict):
                return None
            try:
                points.append((float(point["x"]), float(point["y"])))
            except (KeyError, TypeError, ValueError):
                return None
        points.append(end)
        return points
    return [start, end]


def validate_layout(spec: dict[str, Any], *, strict: bool = True) -> tuple[list[str], list[str]]:
    """Return ``(errors, warnings)`` for the optional geometry contract."""
    errors: list[str] = []
    warnings: list[str] = []
    layout = spec.get("layout")
    if not isinstance(layout, dict):
        return ["layout is required for strict visual validation"], []

    canvas = spec.get("canvas") if isinstance(spec.get("canvas"), dict) else {}
    try:
        canvas_w = float(canvas.get("width", DEFAULT_CANVAS["width"]))
        canvas_h = float(canvas.get("height", DEFAULT_CANVAS["height"]))
    except (TypeError, ValueError):
        errors.append("canvas.width and canvas.height must be numbers")
        canvas_w, canvas_h = DEFAULT_CANVAS["width"], DEFAULT_CANVAS["height"]
    if abs(canvas_w / canvas_h - 16 / 9) > 0.03:
        warnings.append("canvas is not approximately 16:9; confirm this is intentional")
    safe = _rect(layout.get("safe_area")) or DEFAULT_SAFE
    if not _inside(safe, (0, 0, canvas_w, canvas_h)):
        errors.append("layout.safe_area must be inside the canvas")
    direction = str(layout.get("direction", "left-to-right"))
    allow_staggered = bool(layout.get("allow_staggered", False))
    grid = layout.get("grid", 0.25)
    try:
        grid = float(grid)
    except (TypeError, ValueError):
        errors.append("layout.grid must be a positive number")
        grid = 0.25
    if grid <= 0:
        errors.append("layout.grid must be a positive number")

    boxes: dict[str, tuple[float, float, float, float]] = {}
    kinds: defaultdict[str, list[tuple[float, float, float, float, str]]] = defaultdict(list)
    for index, node in enumerate(spec.get("nodes", [])):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id", index))
        box = _rect(node.get("position"))
        if box is None:
            errors.append(f"nodes[{index}] {node_id}: position must contain positive x/y/w/h")
            continue
        boxes[node_id] = box
        x, y, w, h = box
        if not _inside(box, safe, 0.02):
            errors.append(f"nodes[{index}] {node_id}: box is outside the safe area")
        if w < 0.72:
            warnings.append(f"nodes[{index}] {node_id}: width {w:.2f}in is narrow for an editable label")
        ratio = h / w
        if ratio > 1.45:
            errors.append(f"nodes[{index}] {node_id}: vertically stretched box ({w:.2f} x {h:.2f})")
        elif ratio > 1.20:
            warnings.append(f"nodes[{index}] {node_id}: tall box ({w:.2f} x {h:.2f}); shorten or split the label")
        kind = str(node.get("kind", "default"))
        kinds[kind].append((*box, node_id))

    for left, right in combinations(boxes.items(), 2):
        if _overlap(left[1], right[1]):
            errors.append(f"node boxes overlap: {left[0]} and {right[0]}")
        elif abs(left[1][1] - right[1][1]) < EPSILON and abs((left[1][0] + left[1][2]) - right[1][0]) < 0.16:
            warnings.append(f"nodes {left[0]} and {right[0]} have a tight horizontal gap")

    for kind, entries in kinds.items():
        if len(entries) < 2:
            continue
        widths = [item[2] for item in entries]
        heights = [item[3] for item in entries]
        if max(widths) - min(widths) > 0.16 or max(heights) - min(heights) > 0.16:
            warnings.append(f"same-kind nodes are not consistently sized: {kind}")

    main_nodes = [box for box in boxes.values()]
    if not allow_staggered and len(main_nodes) > 2:
        centers_y = [y + h / 2 for _, y, _, h in main_nodes]
        centers_x = [x + w / 2 for x, _, w, _ in main_nodes]
        spread_y = max(centers_y) - min(centers_y)
        spread_x = max(centers_x) - min(centers_x)
        if direction == "left-to-right" and spread_y > 0.18:
            warnings.append(f"left-to-right layout has a {spread_y:.2f}in vertical baseline spread; align rows or declare layers")
        if direction == "top-to-bottom" and spread_x > 0.18:
            warnings.append(f"top-to-bottom layout has a {spread_x:.2f}in horizontal baseline spread; align columns or declare layers")

    for node in spec.get("nodes", []):
        if not isinstance(node, dict) or not isinstance(node.get("position"), dict):
            continue
        position = node["position"]
        for key in ("x", "y", "w", "h"):
            try:
                value = float(position[key])
            except (KeyError, TypeError, ValueError):
                continue
            if grid and abs(value / grid - round(value / grid)) > 0.12:
                warnings.append(f"node {node.get('id')}: {key} is not aligned to the {grid:.2f}in grid")
                break

    edge_segments: list[tuple[str, tuple[float, float], tuple[float, float]]] = []
    for index, edge in enumerate(spec.get("edges", [])):
        if not isinstance(edge, dict):
            continue
        points = _edge_points(edge, boxes)
        if not points:
            continue
        route = edge.get("route")
        if (
            not isinstance(route, list)
            and abs(points[0][0] - points[-1][0]) > EPSILON
            and abs(points[0][1] - points[-1][1]) > EPSILON
        ):
            errors.append(f"edges[{index}] {edge.get('id', index)} is diagonal; provide an orthogonal route")
        for point_index, (start, end) in enumerate(zip(points, points[1:])):
            if abs(start[0] - end[0]) > EPSILON and abs(start[1] - end[1]) > EPSILON:
                errors.append(f"edges[{index}] {edge.get('id', index)} segment {point_index} is diagonal")
            edge_segments.append((str(edge.get("id", index)), start, end))
        if edge.get("label_box") is not None:
            label_box = _rect(edge.get("label_box"))
            if label_box is None:
                errors.append(f"edges[{index}] label_box must contain positive x/y/w/h")
            else:
                for node_id, node_box in boxes.items():
                    if _overlap(label_box, node_box, gap=0.01):
                        errors.append(f"edge label {edge.get('id', index)} overlaps node {node_id}")

    for first, second in combinations(edge_segments, 2):
        if first[0] == second[0]:
            continue
        if _segments_cross(first[1], first[2], second[1], second[2]):
            errors.append(f"connector paths cross: {first[0]} and {second[0]}")

    node_count = len(spec.get("nodes", []))
    allow_high_density = bool((spec.get("layout") or {}).get("allow_high_density"))
    if node_count > 18:
        errors.append(f"{node_count} nodes is too dense for one slide; split the diagram")
    elif node_count > 12 and not allow_high_density:
        warnings.append(f"{node_count} nodes is above the recommended 6-12 node range")
    if len(spec.get("edges", [])) > 20:
        warnings.append("many connectors may turn the slide into a maze; split views or use a legend")

    for annotation in spec.get("annotations", []):
        if not isinstance(annotation, dict):
            continue
        box = _rect(annotation.get("position"))
        if box and not _inside(box, (0, 0, canvas_w, canvas_h)):
            errors.append(f"annotation {annotation.get('id', '')} is outside the canvas")

    if strict and warnings:
        # The caller may choose to treat warnings as warnings.  Keeping them
        # separate lets fixture tests demonstrate both modes.
        pass
    return errors, warnings
