#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-run}"
APP_NAME="JiSpr"
BUNDLE_ID="com.acrobat.jispr"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="$ROOT_DIR/macos/JiSpr/JiSpr.xcodeproj"
DERIVED_DATA="$ROOT_DIR/build/JiSprDerivedData"
APP_BUNDLE="$DERIVED_DATA/Build/Products/Debug/$APP_NAME.app"
APP_BINARY="$APP_BUNDLE/Contents/MacOS/$APP_NAME"
ENGINE_PATH="$ROOT_DIR/.venv/bin/local-flow"

if [[ ! -x "$ENGINE_PATH" ]]; then
  echo "JiSpr engine missing at $ENGINE_PATH" >&2
  echo "Run: uv sync --all-extras" >&2
  exit 1
fi

pkill -x "$APP_NAME" >/dev/null 2>&1 || true

xcodebuild \
  -project "$PROJECT" \
  -scheme "$APP_NAME" \
  -configuration Debug \
  -derivedDataPath "$DERIVED_DATA" \
  JISPR_ENGINE_PATH="$ENGINE_PATH" \
  JISPR_WORKING_DIRECTORY="$ROOT_DIR" \
  build >/dev/null

open_app() {
  /usr/bin/open -n "$APP_BUNDLE"
}

case "$MODE" in
  run)
    open_app
    ;;
  --debug|debug)
    lldb -- "$APP_BINARY"
    ;;
  --logs|logs)
    open_app
    /usr/bin/log stream --info --style compact --predicate "process == \"$APP_NAME\""
    ;;
  --telemetry|telemetry)
    open_app
    /usr/bin/log stream --info --style compact --predicate "subsystem == \"$BUNDLE_ID\""
    ;;
  --verify|verify)
    open_app
    sleep 1
    pgrep -x "$APP_NAME" >/dev/null
    echo "$APP_NAME is running from $APP_BUNDLE"
    ;;
  *)
    echo "usage: $0 [run|--debug|--logs|--telemetry|--verify]" >&2
    exit 2
    ;;
esac
