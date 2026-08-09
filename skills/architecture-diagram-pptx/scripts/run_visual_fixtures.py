#!/usr/bin/env python3
"""Run user-like diagram fixtures through geometry and editable-PPTX checks.

This is a structural visual-quality gate.  If PowerPoint or LibreOffice is
available, a caller can additionally render the generated files, but the
fixture runner itself never requires a GUI application and never stores
binary outputs in the repository.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parent / "tests" / "fixtures"
sys.path.insert(0, str(HERE))
from check_diagram_spec import validate, load  # noqa: E402
from visual_qa import evaluate as visual_qa  # noqa: E402


def node_command() -> str | None:
    configured = os.environ.get("NODE_BIN")
    if configured and shutil.which(configured):
        return configured
    for candidate in ("node",):
        if shutil.which(candidate):
            return candidate
    return None


def run() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, help="optional directory for generated PPTX files")
    args = parser.parse_args()
    output_dir = args.output_dir or Path(tempfile.mkdtemp(prefix="architecture-diagram-fixtures-"))
    output_dir.mkdir(parents=True, exist_ok=True)
    node = node_command()
    if node is None:
        print("FAIL no Node.js executable found; set NODE_BIN to run the standard PptxGenJS generator")
        return 1
    failures: list[str] = []
    passed = 0
    generated = 0
    for path in sorted(FIXTURES.glob("*.json")):
        spec = load(path)
        requires_semantics = any(isinstance(edge, dict) and "relation" in edge for edge in spec.get("edges", []))
        errors, warnings = validate(spec, include_layout=True, include_semantics=requires_semantics)
        visual_report = visual_qa(spec)
        expected = str(spec.get("expect", "pass"))
        if expected == "fail":
            if not errors and not visual_report["errors"]:
                failures.append(f"{path.name}: expected {spec.get('failure_kind', 'layout/semantic')} errors but the fixture passed")
            else:
                rejected = len(errors) + len(visual_report["errors"])
                print(f"PASS {path.name}: rejected {rejected} {spec.get('failure_kind', 'layout/semantic')} errors")
                passed += 1
            continue
        if errors:
            failures.append(f"{path.name}: unexpected errors: {'; '.join(errors)}")
            continue
        if visual_report["errors"]:
            failures.append(f"{path.name}: visual QA failed: {'; '.join(visual_report['errors'])}")
            continue
        expected_warning = spec.get("expect_warning")
        if expected_warning and not any(str(expected_warning) in warning for warning in warnings):
            failures.append(f"{path.name}: expected warning containing {expected_warning!r}")
            continue
        print(f"PASS {path.name}: geometry passed ({len(warnings)} warnings)")
        output = output_dir / f"{path.stem}.pptx"
        command = [node, str(HERE / "create_drawio_pptx.cjs"), "--spec", str(path), str(output)]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            failures.append(f"{path.name}: PPTX generator failed: {result.stderr.strip()}")
            continue
        validator = subprocess.run([sys.executable, str(HERE / "validate_pptx.py"), str(output), "--spec", str(path), "--json"], capture_output=True, text=True)
        if validator.returncode != 0:
            failures.append(f"{path.name}: PPTX validation failed: {validator.stdout.strip()} {validator.stderr.strip()}")
            continue
        try:
            validation = json.loads(validator.stdout)
        except json.JSONDecodeError:
            failures.append(f"{path.name}: PPTX validator did not return JSON: {validator.stdout.strip()}")
            continue
        text_line = " ".join(str(item) for item in validation.get("result", {}).get("text", []))
        required_text = [str(spec.get("title", ""))]
        required_text.extend(str(node.get("label", "")) for node in spec.get("nodes", []) if isinstance(node, dict))
        required_text.extend(str(edge.get("label", "")) for edge in spec.get("edges", []) if isinstance(edge, dict) and edge.get("label"))
        missing = [value for value in required_text if value and value not in text_line]
        if missing:
            failures.append(f"{path.name}: generated PPTX is missing editable text: {', '.join(missing)}")
            continue
        result_summary = validation.get("result", {})
        sidecar_path = Path(f"{output}.icons.json")
        if not sidecar_path.is_file():
            failures.append(f"{path.name}: icon resolution sidecar was not generated")
            continue
        try:
            icon_metadata = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            failures.append(f"{path.name}: icon resolution sidecar is invalid JSON")
            continue
        resolutions = icon_metadata.get("resolutions", [])
        node_count = len([node for node in spec.get("nodes", []) if isinstance(node, dict)])
        if len(resolutions) != node_count:
            failures.append(f"{path.name}: icon resolution count {len(resolutions)} != node count {node_count}")
            continue
        if int(result_summary.get("pictures", 0)) < node_count:
            failures.append(f"{path.name}: pictures {result_summary.get('pictures', 0)} < icon-bearing nodes {node_count}")
            continue
        if spec.get("expect_fallback") and not any(item.get("fallback") for item in resolutions):
            failures.append(f"{path.name}: expected an explicit icon fallback")
            continue
        print(f"PASS {path.name}: editable PPTX validated (shapes={result_summary.get('shapes', 0)}, connectors={result_summary.get('connectors', 0)}, pictures={result_summary.get('pictures', 0)}) -> {output}")
        passed += 1
        generated += 1
    print(f"fixture summary: passed={passed}, generated={generated}, failures={len(failures)}, output_dir={output_dir}")
    for failure in failures:
        print(f"FAIL {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run())
