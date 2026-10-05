#!/usr/bin/env bash
set -euo pipefail

skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
supported_python() {
  command -v "$1" >/dev/null 2>&1 &&
    "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

if [[ -n "${PDF2SCAN_SETUP_PYTHON:-}" ]]; then
  python_bin="$PDF2SCAN_SETUP_PYTHON"
else
  python_bin=""
  for candidate in python3 python3.13 python3.12 python3.11 python3.10; do
    if supported_python "$candidate"; then
      python_bin="$candidate"
      break
    fi
  done
  if [[ -z "$python_bin" ]] && command -v uv >/dev/null 2>&1; then
    candidate="$(uv python find '>=3.10' 2>/dev/null || true)"
    if [[ -n "$candidate" ]] && supported_python "$candidate"; then
      python_bin="$candidate"
    fi
  fi
fi
if [[ -z "$python_bin" ]] || ! supported_python "$python_bin"; then
  echo "pdf2scan requires Python 3.10 or newer. Install it or set PDF2SCAN_SETUP_PYTHON." >&2
  exit 1
fi

"$python_bin" -m venv "$skill_dir/.venv"
"$skill_dir/.venv/bin/python" -m pip install -r "$skill_dir/requirements.txt"
"$skill_dir/.venv/bin/python" -c 'import cv2, numpy, PIL, pymupdf'
echo "pdf2scan is ready: $skill_dir"
