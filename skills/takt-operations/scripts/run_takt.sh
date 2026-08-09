#!/usr/bin/env bash
set -euo pipefail

repo_root="${TAKT_REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"

if [[ -x "$repo_root/.venv/bin/python" ]]; then
  export PATH="$repo_root/.venv/bin:$PATH"
fi
export PATH="$HOME/.local/node-24.15.0/bin:$HOME/.local/bin:$PATH"

if ! command -v takt >/dev/null 2>&1; then
  printf '%s\n' 'takt が見つかりません。インストール先とPATHを確認してください。' >&2
  exit 1
fi

exec takt "$@"
