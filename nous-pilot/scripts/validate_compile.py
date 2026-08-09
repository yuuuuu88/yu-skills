from __future__ import annotations

from pathlib import Path


errors: list[str] = []
for root_name in ("src", "tests"):
    root = Path(root_name)
    for path in sorted(root.rglob("*.py")):
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec", dont_inherit=True)
        except (OSError, SyntaxError) as exc:
            errors.append(f"{path}: {exc}")

if errors:
    print("\n".join(errors))
    raise SystemExit(1)

print("syntax-ok")
