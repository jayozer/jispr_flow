"""Repository-wide pytest isolation from developer-specific local profiles."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_local_asr_profile(monkeypatch):
    """Keep a developer's `.env` accuracy profile from loading models in CI.

    Individual tests can still override this environment value or pass an
    explicit mapping to ``load_config`` when exercising named profiles.
    """
    monkeypatch.setenv("LOCAL_FLOW_ASR_PROFILE", "custom")
