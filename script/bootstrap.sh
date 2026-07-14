#!/usr/bin/env bash
# One-command setup for the native macOS JiSpr app.
#
# Chains the whole bring-up: prerequisite checks -> uv sync --all-extras ->
# xcodegen generate -> build + launch (via build_and_run.sh --verify). This is the
# command to run on a fresh machine so you end up with the *app*, not just the
# engine. Safe to re-run; note the xcodegen step below regenerates the git-tracked
# macos/JiSpr/JiSpr.xcodeproj, which can then show up in `git status`.
#
# What it can't do (GUI-only, so still manual): LM Studio auth + model + server,
# macOS privacy grants, and picking the two models in Settings. It prints those
# follow-up steps at the end.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SPEC="$ROOT_DIR/macos/JiSpr/project.yml"

# --- Preflight: verify prerequisites before mutating anything ---

# Full Xcode (not just the Command Line Tools). `xcodebuild -version` succeeds only
# under a real Xcode toolchain with the license accepted, so it alone is a reliable
# gate — don't also match on the bundle name, since Xcode-beta.app / versioned
# installs (Xcode_16.app, etc.) are valid but wouldn't contain "/Xcode.app/".
if ! xcodebuild -version >/dev/null 2>&1; then
  echo "Full Xcode is required to build JiSpr (the Command Line Tools alone won't work)." >&2
  echo "Install Xcode from the App Store, then point the toolchain at it and accept the license:" >&2
  echo "  sudo xcode-select -s /Applications/Xcode.app/Contents/Developer" >&2
  echo "  sudo xcodebuild -license accept" >&2
  echo "  sudo xcodebuild -runFirstLaunch" >&2
  exit 1
fi

# uv (installs the Python dictation engine).
if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required but not installed." >&2
  echo "Install it from https://docs.astral.sh/uv/ then re-run this script." >&2
  exit 1
fi

# xcodegen (regenerates the Xcode project from project.yml).
if ! command -v xcodegen >/dev/null 2>&1; then
  echo "xcodegen is required but not installed." >&2
  echo "Install it with: brew install xcodegen" >&2
  exit 1
fi

# --- Build the app ---

cd "$ROOT_DIR"

echo "==> Installing the dictation engine (uv sync --all-extras)"
uv sync --all-extras

echo "==> Generating the Xcode project (xcodegen)"
xcodegen generate --spec "$SPEC"

echo "==> Building and launching JiSpr.app"
set +e
"$ROOT_DIR/script/build_and_run.sh" --verify
verify_status=$?
set -e

if [[ $verify_status -eq 0 ]]; then
  echo "JiSpr.app is built and running."
else
  echo "warning: build_and_run.sh exited $verify_status — the app may have failed to" >&2
  echo "build, or simply not come up within the verification window. Check the output" >&2
  echo "above. The manual steps below still apply once JiSpr is running." >&2
fi

# --- Manual follow-up (GUI, can't be scripted). Printed regardless of the verify ---
# --- result: it's most needed exactly when a first launch didn't stick.          ---

cat <<'EOF'

============================================================
A few steps need the GUI, so do these once:

  1. LM Studio (writing polish): authenticate with Hugging Face,
     download a chat model, then Developer -> Start Server.
  2. Grant JiSpr Microphone, Accessibility, and Input Monitoring
     (System Settings -> Privacy & Security), then RESTART JiSpr.
     The Fn hotkey tap is created at launch and won't pick up a
     new grant until you quit and reopen the app.
  3. In JiSpr Settings -> Models, explicitly pick your Speech
     Recognition and Writing Polish models.

Full details: README.md -> "Native macOS app (JiSpr)".
============================================================
EOF
