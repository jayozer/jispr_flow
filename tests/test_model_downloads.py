"""Pinned local model downloads with tiny artifacts and no real network/models."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from local_flow.errors import ConfigError
from local_flow.models import ModelDownloadManager
from local_flow.models.catalog import CATALOG, ModelFile, ModelSpec


@pytest.fixture
def artifacts():
    return {"config.json": b'{"test":true}', "weights.bin": b"model weights" * 100}


@pytest.fixture
def model(artifacts):
    return ModelSpec(
        id="test-model",
        name="Test model",
        description="Tiny headless model fixture",
        backend="mlx-whisper",
        repo_id="test/model",
        revision="a" * 40,
        languages=("auto", "en", "fr"),
        size_hint="Tiny",
        module="test_backend",
        files=tuple(
            ModelFile(name, len(content), hashlib.sha256(content).hexdigest())
            for name, content in artifacts.items()
        ),
    )


def manager(tmp_path, model, artifacts, *, handler=None, callback=None, **kwargs):
    requests = []

    def respond(request):
        requests.append(request)
        if handler is not None:
            return handler(request)
        content = artifacts[request.url.path.rsplit("/", 1)[-1]]
        return httpx.Response(
            200, headers={"content-length": str(len(content))}, stream=httpx.ByteStream(content)
        )

    result = ModelDownloadManager(
        tmp_path / "data",
        callback,
        catalog=(model,),
        cache_dir=tmp_path / "cache",
        transport=httpx.MockTransport(respond),
        module_available=lambda _name: True,
        system="Darwin",
        machine="arm64",
        **kwargs,
    )
    return result, requests


def finished(instance):
    instance._worker.join(timeout=3)
    assert not instance._worker.is_alive(), "model worker did not finish"
    return instance.snapshot()


def cached(tmp_path, model, artifacts, revision=None):
    repo = tmp_path / "cache" / ("models--" + model.repo_id.replace("/", "--"))
    snapshot = repo / "snapshots" / (revision or model.revision)
    snapshot.mkdir(parents=True)
    blobs = repo / "blobs"
    blobs.mkdir(exist_ok=True)
    for index, (name, content) in enumerate(artifacts.items()):
        blob = blobs / str(index)
        blob.write_bytes(content)
        (snapshot / name).symlink_to(blob)
    return snapshot


def test_inventory_is_offline_and_does_not_create_directories(tmp_path, model, artifacts):
    instance, requests = manager(tmp_path, model, artifacts)

    assert instance.snapshot() is None
    row = instance.inventory()[0]

    assert row["available"] and not row["installed"] and not row["verified"]
    assert row["local_path"] is None
    assert requests == []
    assert not (tmp_path / "data").exists()
    instance.close()


def test_success_publishes_only_complete_verified_model_and_exact_bytes(tmp_path, model, artifacts):
    progress = []
    instance, requests = manager(tmp_path, model, artifacts, callback=progress.append)

    initial = instance.start(model.id)
    final = finished(instance)

    assert initial["state"] == "preparing" and initial["total_bytes"] is None
    assert final["state"] == "completed"
    assert final["downloaded_bytes"] == final["total_bytes"] == sum(map(len, artifacts.values()))
    assert Path(final["local_path"]).is_dir()
    assert (Path(final["local_path"]) / ".jispr-model.json").is_file()
    assert instance.local_model(model.id) == (model.backend, final["local_path"])
    assert instance.inventory()[0]["verified"]
    assert len(requests) == len(artifacts)
    assert all(request.method == "GET" for request in requests)
    assert all(f"/resolve/{model.revision}/" in request.url.path for request in requests)
    assert all(request.headers["accept-encoding"] == "identity" for request in requests)
    assert all("authorization" not in request.headers for request in requests)
    assert all(0 <= item["downloaded_bytes"] <= (item["total_bytes"] or 0) for item in progress)
    assert list((tmp_path / "data" / "models" / ".downloads").iterdir()) == []
    instance.close()


def test_ready_managed_marker_survives_new_manager_without_network(tmp_path, model, artifacts):
    first, _ = manager(tmp_path, model, artifacts)
    first.start(model.id)
    final = finished(first)
    first.close()
    second, requests = manager(tmp_path, model, artifacts)

    assert second.inventory()[0]["verified"]
    assert second.local_model(model.id)[1] == final["local_path"]
    assert requests == []
    second.close()


@pytest.mark.parametrize("revision", [None, "b" * 40])
def test_complete_hf_cache_reused_only_after_worker_verification(
    tmp_path, model, artifacts, revision
):
    path = cached(tmp_path, model, artifacts, revision)
    before = {file: file.stat().st_mtime_ns for file in path.iterdir()}
    instance, requests = manager(tmp_path, model, artifacts)

    row = instance.inventory()[0]
    assert row["installed"] and not row["verified"]
    assert row["local_path"] == str(path)
    with pytest.raises(ConfigError, match="verify"):
        instance.local_model(model.id)
    instance.start(model.id)
    final = finished(instance)

    assert final["state"] == "completed"
    assert final["downloaded_bytes"] == final["total_bytes"] == 0
    assert instance.local_model(model.id)[1] == str(path)
    assert requests == []
    assert {file: file.stat().st_mtime_ns for file in path.iterdir()} == before
    assert not (path / ".jispr-model.json").exists()
    instance.close()


def test_partial_hf_cache_is_not_installed_or_ready(tmp_path, model, artifacts):
    path = cached(tmp_path, model, artifacts)
    (path / "weights.bin").unlink()
    (path / "weights.bin.incomplete").write_bytes(artifacts["weights.bin"])
    instance, requests = manager(tmp_path, model, artifacts)

    assert not instance.inventory()[0]["installed"]
    with pytest.raises(ConfigError):
        instance.local_model(model.id)
    assert requests == []
    instance.close()


def test_cache_symlinks_cannot_escape_repo(tmp_path, model, artifacts):
    path = cached(tmp_path, model, artifacts)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(artifacts["weights.bin"])
    (path / "weights.bin").unlink()
    (path / "weights.bin").symlink_to(outside)
    instance, _ = manager(tmp_path, model, artifacts)

    assert not instance.inventory()[0]["installed"]
    instance.close()


@pytest.mark.parametrize(
    "kind", ["truncated", "bad-hash", "too-long", "wrong-header", "http-error"]
)
def test_invalid_transfer_never_marks_model_ready(tmp_path, model, artifacts, kind):
    def respond(request):
        name = request.url.path.rsplit("/", 1)[-1]
        content = artifacts[name]
        if kind == "http-error":
            return httpx.Response(503)
        if name == "weights.bin":
            if kind == "truncated":
                content = content[:-1]
            elif kind == "bad-hash":
                content = b"x" * len(content)
            elif kind == "too-long":
                content += b"x"
            elif kind == "wrong-header":
                return httpx.Response(
                    200, headers={"content-length": "1"}, stream=httpx.ByteStream(content)
                )
        return httpx.Response(200, stream=httpx.ByteStream(content))

    instance, _ = manager(tmp_path, model, artifacts, handler=respond)
    instance.start(model.id)
    final = finished(instance)

    assert final["state"] == "error" and final["error"]
    assert final["local_path"] is None
    assert not instance.inventory()[0]["installed"]
    assert list((tmp_path / "data" / "models" / ".downloads").iterdir()) == []
    instance.close()


def test_bad_same_size_cache_can_be_repaired_without_modifying_it(tmp_path, model, artifacts):
    path = cached(tmp_path, model, artifacts)
    target = (path / "weights.bin").resolve()
    corrupted = b"x" * len(artifacts["weights.bin"])
    target.write_bytes(corrupted)
    progress = []
    instance, requests = manager(tmp_path, model, artifacts, callback=progress.append)
    instance.start(model.id)
    final = finished(instance)

    assert final["state"] == "completed"
    assert final["local_path"] != str(path)
    assert instance.local_model(model.id)[1] == final["local_path"]
    assert target.read_bytes() == corrupted
    assert len(requests) == len(artifacts)
    assert [item["downloaded_bytes"] for item in progress] == sorted(
        item["downloaded_bytes"] for item in progress
    )
    instance.close()


def test_managed_model_tampering_revokes_readiness_and_repair_preserves_old_copy(
    tmp_path, model, artifacts
):
    instance, _ = manager(tmp_path, model, artifacts)
    instance.start(model.id)
    first = finished(instance)
    old = Path(first["local_path"])
    (old / "weights.bin").write_bytes(b"x" * len(artifacts["weights.bin"]))

    assert not instance.inventory()[0]["verified"]
    with pytest.raises(ConfigError, match="verify"):
        instance.local_model(model.id)
    instance.start(model.id)
    final = finished(instance)

    assert final["state"] == "completed" and final["local_path"] != str(old)
    assert instance.local_model(model.id)[1] == final["local_path"]
    assert old.is_dir()
    instance.close()


class GateStream(httpx.SyncByteStream):
    def __init__(self, content, first_size=65536):
        self.content = content
        self.first_size = first_size
        self.blocked = threading.Event()
        self.release = threading.Event()

    def __iter__(self):
        yield self.content[: self.first_size]
        self.blocked.set()
        assert self.release.wait(2), "test stream was not released"
        yield self.content[self.first_size :]

    def close(self):
        self.release.set()


def test_cancel_is_immediate_and_retry_restarts_safely_without_partial_readiness(
    tmp_path, model, artifacts
):
    data = {**artifacts, "weights.bin": b"w" * 150000}
    model = replace(
        model,
        files=tuple(
            ModelFile(name, len(content), hashlib.sha256(content).hexdigest())
            for name, content in data.items()
        ),
    )
    stream = GateStream(data["weights.bin"])
    attempts = [0]

    def respond(request):
        name = request.url.path.rsplit("/", 1)[-1]
        if name == "weights.bin" and attempts[0] == 0:
            attempts[0] += 1
            return httpx.Response(200, stream=stream)
        return httpx.Response(200, stream=httpx.ByteStream(data[name]))

    instance, _ = manager(tmp_path, model, data, handler=respond)
    job = instance.start(model.id)
    assert stream.blocked.wait(2)
    with pytest.raises(ConfigError, match="still running"):
        instance.start(model.id)
    with pytest.raises(ConfigError, match="no longer active"):
        instance.cancel("stale-job")

    cancelled = instance.cancel(job["job_id"])
    assert cancelled["state"] == "cancelling"
    assert not instance.inventory()[0]["installed"]
    stream.release.set()
    assert finished(instance)["state"] == "cancelled"
    assert not instance.inventory()[0]["installed"]

    next_job = instance.start(model.id)
    assert next_job["job_id"] != job["job_id"]
    assert finished(instance)["state"] == "completed"
    with pytest.raises(ConfigError, match="no longer active"):
        instance.cancel(job["job_id"])
    instance.close()


def test_close_cancels_blocked_stream_and_cannot_publish_late_ready(tmp_path, model, artifacts):
    data = {**artifacts, "weights.bin": b"w" * 150000}
    model = replace(
        model,
        files=tuple(
            ModelFile(name, len(content), hashlib.sha256(content).hexdigest())
            for name, content in data.items()
        ),
    )
    stream = GateStream(data["weights.bin"])

    def respond(request):
        name = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(
            200, stream=stream if name == "weights.bin" else httpx.ByteStream(data[name])
        )

    instance, _ = manager(tmp_path, model, data, handler=respond)
    instance.start(model.id)
    assert stream.blocked.wait(2)

    instance.close()

    assert not instance._worker.is_alive()
    assert instance.snapshot()["state"] == "cancelled"
    assert not instance.inventory()[0]["installed"]
    with pytest.raises(ConfigError, match="closed"):
        instance.start(model.id)


def test_slow_transport_chunk_is_observed_before_a_full_buffer_accumulates(
    tmp_path, model, artifacts
):
    stream = GateStream(artifacts["weights.bin"], first_size=1)

    def respond(request):
        name = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(
            200, stream=stream if name == "weights.bin" else httpx.ByteStream(artifacts[name])
        )

    instance, _ = manager(tmp_path, model, artifacts, handler=respond)
    job = instance.start(model.id)
    assert stream.blocked.wait(2)
    assert instance.snapshot()["downloaded_bytes"] == len(artifacts["config.json"]) + 1

    instance.cancel(job["job_id"])
    stream.release.set()

    assert finished(instance)["state"] == "cancelled"
    instance.close()


def test_cancel_at_final_chunk_cannot_publish_ready_model(tmp_path, model, artifacts):
    instance = None

    def on_progress(progress):
        if progress["state"] == "downloading" and progress["file_name"] == "weights.bin":
            instance.cancel(progress["job_id"])

    instance, _ = manager(tmp_path, model, artifacts, callback=on_progress)
    instance.start(model.id)

    assert finished(instance)["state"] == "cancelled"
    assert not instance.inventory()[0]["installed"]
    instance.close()


def test_cancel_after_artifact_verification_but_before_directory_publish(
    tmp_path, model, artifacts, monkeypatch
):
    from local_flow.models import downloads

    original = downloads.atomic_write_text
    instance, _ = manager(tmp_path, model, artifacts)

    def cancel_after_marker(path, text):
        result = original(path, text)
        instance.cancel(instance.snapshot()["job_id"])
        return result

    monkeypatch.setattr(downloads, "atomic_write_text", cancel_after_marker)
    instance.start(model.id)

    assert finished(instance)["state"] == "cancelled"
    assert not instance.inventory()[0]["verified"]
    assert list((tmp_path / "data" / "models" / ".downloads").iterdir()) == []
    instance.close()


@pytest.mark.parametrize("phase", ["marker", "publish"])
def test_atomic_completion_failure_leaves_no_ready_directory(
    tmp_path, model, artifacts, monkeypatch, phase
):
    from local_flow.models import downloads

    original = downloads.os.replace

    def fail_replace(source, target):
        is_publish = Path(source).is_dir()
        if (phase == "publish") == is_publish:
            raise OSError("simulated completion failure")
        return original(source, target)

    monkeypatch.setattr(downloads.os, "replace", fail_replace)
    instance, _ = manager(tmp_path, model, artifacts)
    instance.start(model.id)

    assert finished(instance)["state"] == "error"
    assert not instance.inventory()[0]["installed"]
    assert list((tmp_path / "data" / "models" / ".downloads").iterdir()) == []
    instance.close()


def test_progress_callbacks_do_not_hold_manager_lock(tmp_path, model, artifacts):
    instance = None
    results = []

    def on_progress(_progress):
        acquired = threading.Event()

        def inspect_snapshot():
            instance.snapshot()
            acquired.set()

        thread = threading.Thread(target=inspect_snapshot, daemon=True)
        thread.start()
        results.append(acquired.wait(1))
        thread.join(timeout=1)

    instance, _ = manager(tmp_path, model, artifacts, callback=on_progress)
    instance.start(model.id)

    assert finished(instance)["state"] == "completed"
    assert results and all(results)
    instance.close()


def test_off_catalog_ids_paths_and_urls_are_rejected_without_network(tmp_path, model, artifacts):
    instance, requests = manager(tmp_path, model, artifacts)
    for value in ["../model", "/tmp/model", "https://evil.example/model", "other/repo", None]:
        with pytest.raises(ConfigError, match="catalog"):
            instance.start(value)
    assert requests == []
    assert not (tmp_path / "data").exists()
    instance.close()


def test_cross_host_redirect_is_refused(tmp_path, model, artifacts):
    instance, requests = manager(
        tmp_path,
        model,
        artifacts,
        handler=lambda _request: httpx.Response(
            302, headers={"location": "https://evil.example/model"}
        ),
    )
    instance.start(model.id)

    assert finished(instance)["state"] == "error"
    assert len(requests) == 1
    instance.close()


def test_git_blob_hash_artifact_verification(tmp_path, model, artifacts):
    config = artifacts["config.json"]
    digest = hashlib.sha1(
        f"blob {len(config)}\0".encode() + config, usedforsecurity=False
    ).hexdigest()
    model = replace(
        model, files=(ModelFile("config.json", len(config), digest, "git-sha1"), model.files[1])
    )
    instance, _ = manager(tmp_path, model, artifacts)
    instance.start(model.id)

    assert finished(instance)["state"] == "completed"
    instance.close()


def test_hardware_and_optional_dependencies_are_reported_without_importing_models(tmp_path):
    intel = ModelDownloadManager(
        tmp_path,
        catalog=CATALOG,
        system="Darwin",
        machine="x86_64",
        module_available=lambda _name: True,
    )
    rows = {row["id"]: row for row in intel.inventory()}
    assert not rows["parakeet-v3"]["available"]
    assert "Apple Silicon" in rows["whisper-small-mlx"]["unavailable_reason"]
    assert rows["faster-whisper-small"]["available"]
    intel.close()
    missing = ModelDownloadManager(
        tmp_path, system="Darwin", machine="arm64", module_available=lambda _name: False
    )
    assert all(not row["available"] for row in missing.inventory())
    with pytest.raises(ConfigError, match="not installed"):
        missing.start("whisper-small-mlx")
    missing.close()


def test_parakeet_requires_ffmpeg_and_english_model_disallows_auto(tmp_path):
    instance = ModelDownloadManager(
        tmp_path,
        system="Darwin",
        machine="arm64",
        module_available=lambda _name: True,
        which=lambda _name: None,
    )
    rows = {row["id"]: row for row in instance.inventory()}
    assert "FFmpeg" in rows["parakeet-v3"]["unavailable_reason"]
    assert rows["whisper-small-en-mlx"]["languages"] == ["en"]
    assert "auto" in rows["whisper-small-mlx"]["languages"]
    instance.close()


def test_manifest_rejects_traversal_and_unpinned_revisions(model):
    with pytest.raises(ValueError):
        ModelFile("../weights.bin", 1, "a" * 64)
    with pytest.raises(ValueError):
        replace(model, revision="main")
    with pytest.raises(ValueError):
        replace(model, repo_id="https://evil.example/model")


def test_bad_completion_marker_never_grants_readiness(tmp_path, model, artifacts):
    instance, _ = manager(tmp_path, model, artifacts)
    instance.start(model.id)
    final = finished(instance)
    marker = Path(final["local_path"]) / ".jispr-model.json"
    payload = json.loads(marker.read_text())
    payload["revision"] = "b" * 40
    marker.write_text(json.dumps(payload))
    instance.close()
    reopened, requests = manager(tmp_path, model, artifacts)

    assert not reopened.inventory()[0]["verified"]
    with pytest.raises(ConfigError, match="verify"):
        reopened.local_model(model.id)
    assert requests == []
    reopened.close()


def test_failed_other_model_download_preserves_a_ready_model(tmp_path, model, artifacts):
    other = replace(model, id="other-model", repo_id="test/other")

    def respond(request):
        if "/test/other/" in request.url.path:
            return httpx.Response(503)
        content = artifacts[request.url.path.rsplit("/", 1)[-1]]
        return httpx.Response(200, stream=httpx.ByteStream(content))

    instance = ModelDownloadManager(
        tmp_path / "data",
        catalog=(model, other),
        cache_dir=tmp_path / "cache",
        transport=httpx.MockTransport(respond),
        module_available=lambda _name: True,
    )
    instance.start(model.id)
    first = finished(instance)
    original = {file.name: file.read_bytes() for file in Path(first["local_path"]).iterdir()}

    instance.start(other.id)

    assert finished(instance)["state"] == "error"
    assert instance.local_model(model.id)[1] == first["local_path"]
    assert {
        file.name: file.read_bytes() for file in Path(first["local_path"]).iterdir()
    } == original
    instance.close()
