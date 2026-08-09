#!/usr/bin/env python3
"""Relationship semantics shared by the spec checker and PPTX renderer.

The important rule is that an arrow is not just decoration.  ``from`` and
``to`` are read as a sentence, and the relation chooses the line treatment.
The renderer maps this small vocabulary to PowerPoint-safe line properties.
"""
from __future__ import annotations

from typing import Any


RELATIONS = {
    "data_flow": {
        "label": "データフロー",
        "line": "solid",
        "color": "2563EB",
        "arrow": "triangle",
        "meaning": "fromからtoへデータを渡す",
        "examples": ("送信", "受信", "保存", "読み出し", "申請", "結果"),
    },
    "dependency": {
        "label": "依存関係",
        "line": "dashed",
        "color": "64748B",
        "arrow": "triangle",
        "meaning": "fromがtoを利用・必要とする",
        "examples": ("依存", "利用", "参照", "認証を利用", "APIを呼び出し"),
    },
    "control": {
        "label": "制御",
        "line": "solid",
        "color": "7C3AED",
        "arrow": "triangle",
        "meaning": "fromがtoへ命令・操作を送る",
        "examples": ("制御", "起動", "停止", "設定", "デプロイ", "承認"),
    },
    "monitoring": {
        "label": "監視・テレメトリ",
        "line": "dotted",
        "color": "059669",
        "arrow": "triangle",
        "meaning": "fromがtoへログ・メトリクス・トレースを送る",
        "examples": ("ログ", "メトリクス", "トレース", "監視", "送信"),
    },
    "message": {
        "label": "メッセージ／イベント",
        "line": "solid",
        "color": "0F766E",
        "arrow": "triangle",
        "meaning": "fromがtoへメッセージまたはイベントを発行する",
        "examples": ("イベント", "メッセージ", "発行", "publish", "enqueue", "キュー"),
    },
    "response": {
        "label": "応答",
        "line": "dashed",
        "color": "2563EB",
        "arrow": "triangle",
        "meaning": "fromがtoへ応答データを返す",
        "examples": ("応答", "結果", "返却", "response"),
    },
    "association": {
        "label": "関連",
        "line": "solid",
        "color": "64748B",
        "arrow": "none",
        "meaning": "fromとtoに関連がある。流れや依存を意味しない",
        "examples": ("関連", "対応", "所属"),
    },
}


def relation_style(relation: str) -> dict[str, str] | None:
    value = RELATIONS.get(relation)
    return dict(value) if value else None


def validate_semantics(spec: dict[str, Any], *, required: bool = False) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    for index, edge in enumerate(spec.get("edges", [])):
        if not isinstance(edge, dict):
            continue
        relation = edge.get("relation")
        if relation is None:
            if required:
                errors.append(f"edges[{index}] relation is required; choose a semantic relation")
            else:
                warnings.append(f"edges[{index}] has no relation; arrow meaning cannot be inferred safely")
            continue
        style = relation_style(str(relation))
        if style is None:
            errors.append(f"edges[{index}] relation must be one of: {', '.join(sorted(RELATIONS))}")
            continue
        label = str(edge.get("label", "")).strip()
        if not label:
            errors.append(f"edges[{index}] {relation} requires a directional label")
        declared_line = edge.get("line_style", edge.get("style"))
        if declared_line and declared_line not in {style["line"], "bold" if style["line"] == "solid" else style["line"]}:
            errors.append(f"edges[{index}] {relation} must use {style['line']} line, not {declared_line}")
        arrow = edge.get("arrow")
        if isinstance(arrow, dict):
            end = str(arrow.get("end", "triangle"))
            start = str(arrow.get("start", "none"))
            if end != style["arrow"]:
                errors.append(f"edges[{index}] {relation} end arrow must be {style['arrow']}, not {end}")
            if start != "none":
                errors.append(f"edges[{index}] {relation} must be single-ended; start arrow is {start}")
        if relation == "association" and edge.get("direction") not in {None, "undirected"}:
            warnings.append(f"edges[{index}] association should declare direction=undirected or use a directional relation")
        if relation == "dependency" and any(token in label.lower() for token in ("送信", "データフロー", "保存")):
            warnings.append(f"edges[{index}] dependency label sounds like data flow; use data_flow if data movement is intended")
    return errors, warnings


def legend_items(relations: list[str] | None = None) -> list[dict[str, str]]:
    selected = relations or list(RELATIONS)
    return [
        {"relation": relation, "label": RELATIONS[relation]["label"], "meaning": RELATIONS[relation]["meaning"], "line": RELATIONS[relation]["line"], "color": RELATIONS[relation]["color"], "arrow": RELATIONS[relation]["arrow"]}
        for relation in selected
        if relation in RELATIONS
    ]
