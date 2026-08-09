#!/usr/bin/env python3
"""Run the complete diagram review loop for one spec.

The loop is intentionally explicit: contract/layout/semantic checks, geometry
visual QA, a reviewable SVG/PNG preview, PPTX generation, and OOXML validation.
It is the single command used before accepting a diagram example.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from check_diagram_spec import load, validate  # noqa: E402
from visual_qa import evaluate as visual_qa  # noqa: E402


def node_command() -> str | None:
    configured = os.environ.get("NODE_BIN")
    if configured and shutil.which(configured):
        return configured
    for candidate in ("node",):
        if shutil.which(candidate):
            return candidate
    return None


def run_command(command: list[str]) -> tuple[int, str]:
    result = subprocess.run(command, capture_output=True, text=True)
    return result.returncode, (result.stdout + result.stderr).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--pptx", type=Path, help="output editable PPTX; defaults beside the spec")
    parser.add_argument("--preview-dir", type=Path, help="directory for SVG/PNG/report artifacts")
    parser.add_argument("--json-out", type=Path, help="write the combined review report here")
    parser.add_argument("--no-png", action="store_true", help="write SVG only; useful when PNG renderer dependencies are unavailable")
    parser.add_argument("--skip-pptx", action="store_true")
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    args = parser.parse_args()
    spec_path = args.spec.resolve()
    spec = load(spec_path)
    stem = spec_path.stem
    preview_dir = (args.preview_dir or spec_path.parent.parent / "previews").resolve()
    preview_dir.mkdir(parents=True, exist_ok=True)
    pptx_path = (args.pptx or spec_path.parent.parent / "pptx" / f"{stem}.pptx").resolve()
    pptx_path.parent.mkdir(parents=True, exist_ok=True)
    svg_path = preview_dir / f"{stem}.preview.svg"
    png_path = preview_dir / f"{stem}.preview.png"
    report_path = (args.json_out or preview_dir / f"{stem}.visual-qa.json").resolve()

    requires_semantics = any(isinstance(edge, dict) and "relation" in edge for edge in spec.get("edges", []))
    spec_errors, spec_warnings = validate(spec, include_layout=True, include_semantics=requires_semantics)
    visual_report = visual_qa(spec)
    report: dict[str, object] = {
        "spec": str(spec_path),
        "pptx": str(pptx_path) if not args.skip_pptx else None,
        "preview": {"svg": str(svg_path), "png": None if args.no_png else str(png_path)},
        "contract": {"errors": spec_errors, "warnings": spec_warnings},
        "visual_qa": visual_report,
        "commands": {},
    }
    failures: list[str] = []
    if spec_errors:
        failures.extend(f"spec: {item}" for item in spec_errors)
    if visual_report["errors"]:
        failures.extend(f"visual QA: {item}" for item in visual_report["errors"])
    if args.strict and spec_warnings:
        failures.extend(f"spec warning: {item}" for item in spec_warnings)
    if args.strict and visual_report["warnings"]:
        failures.extend(f"visual QA warning: {item}" for item in visual_report["warnings"])

    node = node_command()
    if node is None:
        failures.append("Node.js is required for preview/PPTX generation")
    else:
        render_command = [node, str(HERE / "render_preview.cjs"), "--spec", str(spec_path), "--svg", str(svg_path)]
        if not args.no_png:
            render_command.extend(["--png", str(png_path)])
        code, output = run_command(render_command)
        report["commands"]["render_preview"] = {"exit_code": code, "output": output}
        if code:
            failures.append("preview render failed")
        if not args.skip_pptx:
            generate_command = [node, str(HERE / "create_drawio_pptx.cjs"), "--spec", str(spec_path), str(pptx_path)]
            code, output = run_command(generate_command)
            report["commands"]["generate_pptx"] = {"exit_code": code, "output": output}
            if code:
                failures.append("PPTX generation failed")
            else:
                validator = [sys.executable, str(HERE / "validate_pptx.py"), str(pptx_path), "--spec", str(spec_path), "--json"]
                code, output = run_command(validator)
                validator_report: object = output
                try:
                    validator_report = json.loads(output)
                except json.JSONDecodeError:
                    pass
                report["commands"]["validate_pptx"] = {"exit_code": code, "report": validator_report}
                if code:
                    failures.append("PPTX structural validation failed")

    report["ok"] = not failures
    report["failures"] = failures
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"review {'passed' if not failures else 'failed'}: {spec_path.name}")
    print(f"preview: {svg_path}{'' if args.no_png else f' / {png_path}'}")
    if not args.skip_pptx:
        print(f"pptx: {pptx_path}")
    print(f"report: {report_path}")
    for failure in failures:
        print(f"ERROR: {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
