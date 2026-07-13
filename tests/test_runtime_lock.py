from __future__ import annotations

import pytest

from local_flow.errors import LocalFlowError
from local_flow.runtime_lock import RuntimeInstanceLock


def test_runtime_lock_rejects_a_second_owner_and_releases_cleanly(tmp_path):
    first = RuntimeInstanceLock(tmp_path, "native app")
    second = RuntimeInstanceLock(tmp_path, "legacy CLI")

    first.acquire()
    try:
        with pytest.raises(LocalFlowError, match="already active"):
            second.acquire()
    finally:
        first.release()

    with second:
        assert second.path.read_text().startswith("pid=")
