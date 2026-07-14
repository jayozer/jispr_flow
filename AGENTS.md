# Repository Guidelines

## Project Structure & Module Organization

`local_flow/` contains the Python package. `app.py` wires the application; `pipeline.py` coordinates dictation; adapter-focused subpackages such as `audio/`, `asr/`, `llm/`, `hotkeys/`, and `insertion/` isolate platform or service integrations. Personalization, transforms, history, scratchpad, and tray behavior live in their matching subpackages. Tests are under `tests/` and mirror the production areas (`tests/test_config.py`, `tests/test_hotkeys.py`, etc.). Architecture notes and implementation plans are in `docs/`; product usage belongs in `README.md`, while `TODO.md` and `ROADMAP.md` track remaining work. The native macOS app lives in `macos/JiSpr/` (SwiftUI menu-bar app; XcodeGen spec in `macos/JiSpr/project.yml`), and build/packaging scripts are in `script/` (`bootstrap.sh`, `build_and_run.sh`, `package_beta.sh`). The macOS app is the primary deliverable; the Python package is the dictation engine it drives.

## Build, Test, and Development Commands

- `uv sync --all-extras`: install development and desktop/audio/ASR/tray dependencies.
- `uv run pytest`: run the complete headless test suite.
- `uv run pytest tests/test_config.py` or `uv run pytest -k "test_name"`: run focused tests.
- `uv run ruff check .`: enforce imports, style, and common correctness rules.
- `uv run local-flow demo`: exercise the full mocked pipeline without hardware or permissions.
- `uv run local-flow check`: inspect the local LM Studio, ASR, microphone, and clipboard setup.
- `./script/bootstrap.sh`: **build and launch the native macOS app end to end** (checks full Xcode + `xcodegen`, runs `uv sync --all-extras`, generates the Xcode project, then builds and launches JiSpr.app). Use this on a fresh machine — `uv sync` alone gives only the engine. See the README's "Native macOS app (JiSpr)" section for LM Studio, permissions, and model selection.

## Coding Style & Naming Conventions

Use Python 3.11+, four-space indentation, and a 100-character line limit. Ruff enables `E`, `F`, `W`, `I`, `UP`, and `B`; fix warnings before submitting. Use `snake_case` for modules, functions, and variables; `PascalCase` for classes; and descriptive adapter names such as `ClipboardPasteSink`. Keep OS-specific imports inside lazily loaded leaf modules. New configuration fields must also be documented in `.env.example` and `local-flow.example.toml`.

## Testing Guidelines

Pytest discovers `tests/test_*.py`; name cases `test_<behavior>`. CI-style tests must not require a microphone, GPU, display, LM Studio server, or clipboard. Add a mock for every new adapter and test failure/degradation paths as first-class behavior. Run the full suite, Ruff, and the mocked demo before opening a PR. Report README manual-checklist items separately when real hardware validation is required.

## Commit & Pull Request Guidelines

Follow the repository’s concise imperative convention: `fix: prevent duplicate Fn dictation`, `docs: update roadmap with shipped features`. Keep commits behaviorally focused. PRs should explain the user-visible problem, summarize the implementation, list exact validation commands, and call out platform-specific manual testing. Link relevant issues or roadmap items; include screenshots only for tray or scratchpad UI changes.

## Security & Architecture Invariants

Preserve local-first behavior: only the configured local LM Studio endpoint may receive text. Never commit `.env`, user dictionaries, history, or generated audio. Maintain graceful degradation: unavailable polish falls back to rules, and failed insertion falls through to safer sinks with actionable errors.
