#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: $0 APP_BUNDLE [VENV]" >&2
  exit 2
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_BUNDLE="$1"
VENV="${2:-$ROOT_DIR/.venv}"
PYTHON="$VENV/bin/python3"

if [[ ! -d "$APP_BUNDLE/Contents" ]]; then
  echo "App bundle not found: $APP_BUNDLE" >&2
  exit 1
fi
if [[ ! -x "$PYTHON" ]]; then
  echo "Python environment not found: $PYTHON" >&2
  exit 1
fi

BASE_PREFIX="$($PYTHON -c 'import sys; print(sys.base_prefix)')"
PURELIB="$($PYTHON -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
PYTHON_VERSION="$($PYTHON -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
ENGINE_DIR="$APP_BUNDLE/Contents/Resources/engine"
RUNTIME_DIR="$ENGINE_DIR/python"
RUNTIME_SITE="$RUNTIME_DIR/lib/python$PYTHON_VERSION/site-packages"

rm -rf "$ENGINE_DIR"
mkdir -p "$ENGINE_DIR"
/usr/bin/ditto "$BASE_PREFIX" "$RUNTIME_DIR"
mkdir -p "$RUNTIME_SITE"
/usr/bin/ditto "$PURELIB" "$RUNTIME_SITE"

# Editable installs point back to the repository. Replace that pointer with a
# real package copy so the engine is completely self-contained.
rm -f "$RUNTIME_SITE/_editable_impl_local_flow.pth"
rm -rf "$RUNTIME_SITE/local_flow"
/usr/bin/ditto "$ROOT_DIR/local_flow" "$RUNTIME_SITE/local_flow"

# Development-only material is large and never imported by the app host.
rm -rf "$RUNTIME_SITE/_pytest" "$RUNTIME_SITE/PyObjCTest"
find "$RUNTIME_SITE" -maxdepth 1 -type d -name 'pytest-*.dist-info' -exec rm -rf {} +
find "$RUNTIME_DIR" -type f -name '*.pyc' -delete
find "$RUNTIME_DIR" -type d -name '__pycache__' -prune -exec rm -rf {} +

/usr/bin/install -m 755 "$ROOT_DIR/script/resources/local-flow" "$ENGINE_DIR/local-flow"

echo "Bundled JiSpr engine at $ENGINE_DIR"
