#!/usr/bin/env bash
set -euo pipefail
skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
if [[ -n "${PDF2SCAN_PYTHON:-}" ]]; then
  python_bin="$PDF2SCAN_PYTHON"
elif [[ -x "$skill_dir/.venv/bin/python" ]]; then
  python_bin="$skill_dir/.venv/bin/python"
else
  python_bin="python3"
fi
if ! command -v "$python_bin" >/dev/null 2>&1; then
  echo "Python interpreter not found: $python_bin" >&2
  exit 1
fi
exec "$python_bin" "$skill_dir/scripts/scan_to_pdf.py" "$@"
