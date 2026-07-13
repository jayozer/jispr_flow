"""Hardened Runtime entitlements for the packaged JiSpr.app.

Regression coverage for the live "no speech detected" failure of 2026-07-12:
`package_beta.sh` signs both the Swift app and the embedded Python with
``--options runtime`` (Hardened Runtime). A hardened process without the
``com.apple.security.device.audio-input`` entitlement is not refused the
microphone -- Core Audio silently delivers all-zero sample buffers, so every
recording legitimately measures peak amplitude 0 and dictation reports
"no speech detected" while the stream itself looks perfectly healthy.

These tests pin the packaging inputs: both entitlements files must grant
audio input, and the packaging script must actually apply the app-level
entitlements file when signing the outer bundle.
"""

import plistlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RESOURCES = REPO_ROOT / "script" / "resources"
AUDIO_INPUT = "com.apple.security.device.audio-input"


def _entitlements(name: str) -> dict:
    return plistlib.loads((RESOURCES / name).read_bytes())


class TestAudioInputEntitlement:
    def test_python_runtime_entitlements_grant_audio_input(self):
        # The embedded Python process is what actually opens the microphone
        # (sounddevice/PortAudio), so its Hardened Runtime signature is the
        # one Core Audio consults before handing over real samples.
        assert _entitlements("python-runtime.entitlements").get(AUDIO_INPUT) is True

    def test_python_runtime_keeps_numba_jit_exceptions(self):
        # Adding audio-input must not displace the JIT exceptions that MLX
        # Parakeet's Numba/LLVM runtime needs (see the file's own comment).
        entitlements = _entitlements("python-runtime.entitlements")
        assert entitlements.get("com.apple.security.cs.allow-jit") is True
        assert entitlements.get("com.apple.security.cs.allow-unsigned-executable-memory") is True

    def test_app_entitlements_grant_audio_input(self):
        # The Swift app is the TCC "responsible process" for its spawned
        # engine; it is hardened too, so it declares the same capability.
        assert _entitlements("jispr-app.entitlements").get(AUDIO_INPUT) is True

    def test_package_script_signs_app_with_entitlements(self):
        # The entitlements file only matters if the outer-bundle codesign
        # invocations actually pass it; pin the wiring, not just the file.
        script = (REPO_ROOT / "script" / "package_beta.sh").read_text()
        assert script.count('--entitlements "$APP_ENTITLEMENTS"') >= 2
        assert 'APP_ENTITLEMENTS="$ROOT_DIR/script/resources/jispr-app.entitlements"' in script
