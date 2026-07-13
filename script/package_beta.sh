#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="$ROOT_DIR/macos/JiSpr/JiSpr.xcodeproj"
BUILD_ROOT="$ROOT_DIR/build/JiSprRelease"
DERIVED_DATA="$BUILD_ROOT/DerivedData"
APP="$DERIVED_DATA/Build/Products/Release/JiSpr.app"
STAGING="$BUILD_ROOT/dmg-root"
DIST="$ROOT_DIR/dist"
VERSION="${JISPR_VERSION:-0.1.2}"
BUILD_NUMBER="${JISPR_BUILD_NUMBER:-4}"
DMG="$DIST/JiSpr-$VERSION-arm64.dmg"
NOTARY_PROFILE="${JISPR_NOTARY_PROFILE:-}"
PYTHON_ENTITLEMENTS="$ROOT_DIR/script/resources/python-runtime.entitlements"
APP_ENTITLEMENTS="$ROOT_DIR/script/resources/jispr-app.entitlements"

IDENTITY="${JISPR_SIGNING_IDENTITY:-}"
if [[ -z "$IDENTITY" ]]; then
  IDENTITY="$(security find-identity -v -p codesigning 2>/dev/null \
    | sed -n 's/.*"\(Developer ID Application:[^"]*\)".*/\1/p' \
    | head -n 1)"
fi
if [[ -z "$IDENTITY" ]]; then
  IDENTITY="-"
  echo "warning: no Developer ID Application identity found; creating an ad-hoc beta artifact" >&2
  echo "warning: this DMG is for local validation only and must not be distributed" >&2
fi

rm -rf "$BUILD_ROOT" "$DMG"
mkdir -p "$BUILD_ROOT" "$DIST"

xcodegen generate --spec "$ROOT_DIR/macos/JiSpr/project.yml"
xcodebuild \
  -project "$PROJECT" \
  -scheme JiSpr \
  -configuration Release \
  -derivedDataPath "$DERIVED_DATA" \
  -arch arm64 \
  CODE_SIGNING_ALLOWED=NO \
  MARKETING_VERSION="$VERSION" \
  CURRENT_PROJECT_VERSION="$BUILD_NUMBER" \
  JISPR_ENGINE_PATH= \
  JISPR_WORKING_DIRECTORY= \
  build

"$ROOT_DIR/script/stage_engine.sh" "$APP"

sign_nested() {
  local target="$1"
  if [[ "$target" == "$APP/Contents/Resources/engine/python/bin/python"* ]]; then
    if [[ "$IDENTITY" == "-" ]]; then
      codesign --force --sign - --entitlements "$PYTHON_ENTITLEMENTS" "$target"
    else
      codesign --force --sign "$IDENTITY" --options runtime --timestamp \
        --entitlements "$PYTHON_ENTITLEMENTS" "$target"
    fi
  else
    if [[ "$IDENTITY" == "-" ]]; then
      # Ad-hoc signatures have no Team ID, so Hardened Runtime library
      # validation cannot establish that Python and libpython belong together.
      # Real Developer ID builds use Hardened Runtime on every nested binary.
      codesign --force --sign - "$target"
    else
      codesign --force --sign "$IDENTITY" --options runtime --timestamp "$target"
    fi
  fi
}

sign_app() {
  if [[ "$IDENTITY" == "-" ]]; then
    codesign --force --sign - --options runtime \
      --entitlements "$APP_ENTITLEMENTS" "$APP"
  else
    codesign --force --sign "$IDENTITY" --options runtime --timestamp \
      --entitlements "$APP_ENTITLEMENTS" "$APP"
  fi
}

# Sign every embedded Mach-O before signing the outer application bundle.
while IFS= read -r -d '' candidate; do
  if file -b "$candidate" | grep -q 'Mach-O'; then
    sign_nested "$candidate"
  fi
done < <(find "$APP/Contents/Resources/engine" -type f -print0)
sign_app
codesign --verify --deep --strict --verbose=2 "$APP"
"$APP/Contents/Resources/engine/local-flow" --version
PYTHONHOME="$APP/Contents/Resources/engine/python" \
PYTHONNOUSERSITE=1 \
PYTHONDONTWRITEBYTECODE=1 \
  "$APP/Contents/Resources/engine/python/bin/python3" -c \
  'from numba import njit; assert njit(lambda value: value + 1)(1) == 2'
codesign --verify --deep --strict --verbose=2 "$APP"

mkdir -p "$STAGING"
/usr/bin/ditto "$APP" "$STAGING/JiSpr.app"
ln -s /Applications "$STAGING/Applications"
hdiutil create \
  -volname JiSpr \
  -srcfolder "$STAGING" \
  -ov \
  -format UDZO \
  "$DMG"
if [[ "$IDENTITY" == "-" ]]; then
  codesign --force --sign - "$DMG"
else
  codesign --force --sign "$IDENTITY" --timestamp "$DMG"
fi

if [[ "$IDENTITY" != "-" && -n "$NOTARY_PROFILE" ]]; then
  xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG"
  xcrun stapler validate "$DMG"
elif [[ "$IDENTITY" != "-" ]]; then
  echo "Signed DMG created but not notarized." >&2
  echo "Set JISPR_NOTARY_PROFILE to a notarytool Keychain profile and rerun." >&2
fi

echo "JiSpr beta artifact: $DMG"
du -h "$DMG"
