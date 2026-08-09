#!/usr/bin/env python3
"""Validate the small JSON contract used before generating an architecture PPTX."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from layout_policy import validate_layout
    from semantic_policy import validate_semantics
except ImportError:  # pragma: no cover - package/importlib callers
    from .layout_policy import validate_layout
    from .semantic_policy import validate_semantics

DIAGRAM_TYPES = {"context", "container", "component", "deployment", "network", "data-flow", "dependency", "sequence", "flowchart"}
EDGE_STYLES = {"solid", "dashed", "dotted", "bold"}
ICON_MANIFEST = Path(__file__).resolve().parents[1] / "assets" / "icons" / "manifest.json"
KIND_ICON = {
    "actor": "user", "person": "user", "external": "internet", "application": "app",
    "service": "app", "gateway": "api", "security": "firewall", "data": "database",
    "data-store": "database", "queue": "queue", "operations": "server", "identity": "network",
    "default": "server",
}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("spec root must be an object")
    return value


def icon_ids() -> tuple[set[str], dict[str, str]]:
    try:
        manifest = json.loads(ICON_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set(), {}
    return {str(item.get("id")) for item in manifest.get("icons", []) if item.get("id")}, {str(k): str(v) for k, v in manifest.get("aliases", {}).items()}


def validate_icons(nodes: list[Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    known, aliases = icon_ids()
    if not known:
        return ["icon manifest is missing or empty"], []
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        requested = str(node.get("icon") or KIND_ICON.get(str(node.get("kind", "default")), "server")).removesuffix(".svg")
        provider = str(node.get("provider") or node.get("vendor") or "").strip().lower()
        candidates = []
        if "." in requested:
            candidates.append(requested)
        if provider:
            candidates.append(f"{provider}.{requested}")
        candidates.extend([f"generic.{requested}", requested])
        resolved = next((candidate for candidate in candidates if candidate in known or candidate in aliases), None)
        if resolved is None:
            fallback = "generic.server" if "generic.server" in known else None
            message = f"nodes[{index}] icon is not in manifest: {node.get('id')}={requested}; generator will fallback to {fallback or 'none'}"
            if node.get("strict_icon"):
                errors.append(message)
            else:
                warnings.append(message)
    return errors, warnings


def validate(spec: dict[str, Any], *, include_layout: bool = False, include_semantics: bool = False) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    for key in ("title", "purpose", "diagram_type", "audience", "nodes", "edges"):
        if key not in spec:
            errors.append(f"missing required field: {key}")
    if not isinstance(spec.get("title"), str) or not spec.get("title", "").strip():
        errors.append("title must be a non-empty string")
    if not isinstance(spec.get("purpose"), str) or not spec.get("purpose", "").strip():
        errors.append("purpose must be a non-empty string")
    if spec.get("diagram_type") not in DIAGRAM_TYPES:
        errors.append(f"diagram_type must be one of: {', '.join(sorted(DIAGRAM_TYPES))}")
    if not isinstance(spec.get("audience"), str) or not spec.get("audience", "").strip():
        errors.append("audience must be a non-empty string")

    nodes = spec.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        errors.append("nodes must be a non-empty array")
        nodes = []
    edges = spec.get("edges")
    if not isinstance(edges, list):
        errors.append("edges must be an array")
        edges = []

    ids: set[str] = set()
    connected: set[str] = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            errors.append(f"nodes[{index}] must be an object")
            continue
        node_id = node.get("id")
        label = node.get("label")
        if not isinstance(node_id, str) or not node_id.strip():
            errors.append(f"nodes[{index}].id must be a non-empty string")
            continue
        if node_id in ids:
            errors.append(f"duplicate node id: {node_id}")
        ids.add(node_id)
        if not isinstance(label, str) or not label.strip():
            errors.append(f"nodes[{index}].label must be a non-empty string")
        elif len(label) > 28:
            warnings.append(f"nodes[{index}] label is long; consider a shorter label or a detail slide: {node_id}")
        if isinstance(node.get("role"), str) and len(node["role"]) > 22:
            warnings.append(f"nodes[{index}] role is long; keep the icon and short label primary: {node_id}")
        if node.get("kind") is not None and not isinstance(node.get("kind"), str):
            errors.append(f"nodes[{index}].kind must be a string when present")

    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            errors.append(f"edges[{index}] must be an object")
            continue
        source, target = edge.get("from"), edge.get("to")
        if source not in ids:
            errors.append(f"edges[{index}].from references unknown node: {source}")
        if target not in ids:
            errors.append(f"edges[{index}].to references unknown node: {target}")
        if source == target and source in ids:
            errors.append(f"edges[{index}] is a self-loop: {source}")
        if source in ids:
            connected.add(source)
        if target in ids:
            connected.add(target)
        if edge.get("style") is not None and edge.get("style") not in EDGE_STYLES:
            errors.append(f"edges[{index}].style must be one of: {', '.join(sorted(EDGE_STYLES))}")
        if edge.get("label") is None or not str(edge.get("label", "")).strip():
            warnings.append(f"edges[{index}] has no label; add one when the relationship is not obvious")

    allow_high_density = bool((spec.get("layout") or {}).get("allow_high_density"))
    if len(nodes) > 18:
        warnings.append(f"{len(nodes)} nodes may be too dense for one slide; split the diagram")
    elif len(nodes) > 12 and not allow_high_density:
        warnings.append(f"{len(nodes)} nodes is above the recommended 6-12 node range")
    if nodes and edges and len(connected) < len(ids):
        warnings.append("one or more nodes are disconnected; confirm that isolation is intentional")

    icon_errors, icon_warnings = validate_icons(nodes)
    errors.extend(icon_errors)
    warnings.extend(icon_warnings)

    boundaries = spec.get("boundaries", [])
    if boundaries is not None and not isinstance(boundaries, list):
        errors.append("boundaries must be an array when present")
        boundaries = []
    for index, boundary in enumerate(boundaries or []):
        if not isinstance(boundary, dict):
            errors.append(f"boundaries[{index}] must be an object")
            continue
        members = boundary.get("node_ids", [])
        if not isinstance(members, list):
            errors.append(f"boundaries[{index}].node_ids must be an array")
            continue
        unknown = [str(item) for item in members if item not in ids]
        if unknown:
            errors.append(f"boundaries[{index}] references unknown nodes: {', '.join(unknown)}")
        if not boundary.get("label"):
            warnings.append(f"boundaries[{index}] has no label")

    legend = spec.get("legend", [])
    legend_relations = spec.get("legend_relations", [])
    if legend is not None and not isinstance(legend, list):
        errors.append("legend must be an array when present")
    if legend_relations is not None and not isinstance(legend_relations, list):
        errors.append("legend_relations must be an array when present")
    elif edges and len(legend or []) == 0 and len(legend_relations or []) == 0:
        warnings.append("no legend is provided; add one if colors or line styles have semantic meaning")

    metadata = spec.get("metadata", {})
    if metadata is not None and not isinstance(metadata, dict):
        errors.append("metadata must be an object when present")
    if not isinstance(metadata, dict) or not metadata.get("version"):
        warnings.append("metadata.version is missing")
    if not isinstance(metadata, dict) or not metadata.get("updated_at"):
        warnings.append("metadata.updated_at is missing")

    if include_layout or "layout" in spec:
        layout_errors, layout_warnings = validate_layout(spec, strict=False)
        errors.extend(layout_errors)
        warnings.extend(layout_warnings)
    if include_semantics or any(isinstance(edge, dict) and "relation" in edge for edge in edges):
        semantic_errors, semantic_warnings = validate_semantics(spec, required=include_semantics)
        errors.extend(semantic_errors)
        warnings.extend(semantic_warnings)

    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    parser.add_argument("--layout", action="store_true", help="require and validate the geometry contract")
    parser.add_argument("--semantics", action="store_true", help="require and validate explicit relationship semantics")
    args = parser.parse_args()
    try:
        spec = load(args.spec)
        errors, warnings = validate(spec, include_layout=args.layout, include_semantics=args.semantics)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 2
    for item in errors:
        print(f"ERROR: {item}")
    for item in warnings:
        print(f"WARNING: {item}")
    if errors or (args.strict and warnings):
        print(f"diagram spec failed ({len(errors)} errors, {len(warnings)} warnings)")
        return 1
    print(f"diagram spec passed ({len(warnings)} warnings)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
