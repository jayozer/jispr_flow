"""Explicit downloads of pinned speech artifacts; inventory is strictly offline."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import platform
import shutil
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from itertools import islice
from pathlib import Path
from urllib.parse import urljoin, urlparse
from uuid import uuid4

import httpx

from local_flow.atomicio import atomic_write_text
from local_flow.errors import ConfigError
from local_flow.models.catalog import CATALOG, ModelFile, ModelSpec

_CHUNK = 64 * 1024
_MARKER = ".jispr-model.json"
_ACTIVE = {"preparing", "downloading", "cancelling"}


@dataclass(frozen=True)
class DownloadProgress:
    job_id: str
    model_id: str
    state: str = "preparing"
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    file_name: str | None = None
    error: str | None = None
    local_path: str | None = None


class _Cancelled(Exception):
    pass


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _cache_root() -> Path:
    explicit = os.environ.get("HF_HUB_CACHE") or os.environ.get("HUGGINGFACE_HUB_CACHE")
    if explicit:
        return Path(explicit).expanduser()
    home = os.environ.get("HF_HOME")
    if home:
        return Path(home).expanduser() / "hub"
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "huggingface" / "hub"


def _digest(file: ModelFile):
    if file.algorithm == "git-sha1":
        result = hashlib.sha1(usedforsecurity=False)
        result.update(f"blob {file.size}\0".encode())
        return result
    return hashlib.sha256()


class ModelDownloadManager:
    """Own one cancellable worker; model IDs are resolved only through the catalog.

    ``transport``, ``catalog``, hardware and dependency probes, and ``cache_dir``
    are injectable for headless tests. Production uses the reviewed catalog and
    public HTTPS Hub artifacts. No optional ASR/model module is imported here.
    """

    def __init__(
        self,
        data_dir: Path,
        on_progress: Callable[[dict], None] | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        catalog: Sequence[ModelSpec] = CATALOG,
        cache_dir: Path | None = None,
        system: str | None = None,
        machine: str | None = None,
        module_available: Callable[[str], bool] = _module_available,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self.root = Path(data_dir) / "models"
        self.cache_dir = Path(cache_dir) if cache_dir is not None else _cache_root()
        self.catalog = tuple(catalog)
        self._models = {model.id: model for model in self.catalog}
        if len(self._models) != len(self.catalog):
            raise ValueError("Catalog model IDs must be unique.")
        self._system = system or platform.system()
        self._machine = machine or platform.machine()
        self._module_available = module_available
        self._which = which
        self._transport = transport
        self._on_progress = on_progress
        self._lock = threading.RLock()
        self._progress: DownloadProgress | None = None
        self._cancel = threading.Event()
        self._worker: threading.Thread | None = None
        self._response: httpx.Response | None = None
        self._closed = False
        self._last_notice = 0.0
        self._verified: dict[tuple[str, str], list[dict]] = {}

    def _spec(self, model_id: str) -> ModelSpec:
        if not isinstance(model_id, str) or model_id not in self._models:
            raise ConfigError("Choose a model from JiSpr's supported model catalog.")
        return self._models[model_id]

    def _unavailable(self, model: ModelSpec) -> str | None:
        if model.apple_silicon and (
            self._system != "Darwin" or self._machine.lower() not in {"arm64", "aarch64"}
        ):
            return "Requires an Apple Silicon Mac. Choose the CPU model on Intel."
        if not self._module_available(model.module):
            return f"The optional {model.backend} backend is not installed in this JiSpr build."
        if model.requires_ffmpeg and self._which("ffmpeg") is None:
            return "FFmpeg is required for Parakeet. Install it and restart JiSpr."
        return None

    @staticmethod
    def _stamps(model: ModelSpec, path: Path, allowed_root: Path) -> list[dict] | None:
        try:
            allowed = allowed_root.resolve()
            if not path.resolve().is_relative_to(allowed):
                return None
            stamps = []
            for file in model.files:
                artifact = path / file.name
                resolved = artifact.resolve(strict=True)
                if not resolved.is_relative_to(allowed) or not resolved.is_file():
                    return None
                stat = resolved.stat()
                if stat.st_size != file.size:
                    return None
                stamps.append(
                    {
                        "name": file.name,
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                        "inode": stat.st_ino,
                    }
                )
            return stamps
        except (OSError, RuntimeError):
            return None

    @staticmethod
    def _manifest(model: ModelSpec) -> dict:
        return {
            "version": 1,
            "model_id": model.id,
            "repo_id": model.repo_id,
            "revision": model.revision,
            "files": [asdict(file) for file in model.files],
        }

    def _is_verified(self, model: ModelSpec, path: Path, stamps: list[dict]) -> bool:
        with self._lock:
            if self._verified.get((model.id, str(path))) == stamps:
                return True
        marker = path / _MARKER
        try:
            if not marker.is_file() or marker.is_symlink() or marker.stat().st_size > 64 * 1024:
                return False
            content = json.loads(marker.read_text(encoding="utf-8"))
            return content == {**self._manifest(model), "stamps": stamps}
        except (OSError, ValueError, UnicodeError):
            return False

    def _local(self, model: ModelSpec) -> tuple[Path, bool] | None:
        parent = self.root / model.id
        fallback = None
        try:
            candidates = sorted(parent.glob(f"{model.revision}-*"), reverse=True)
        except OSError:
            candidates = []
        for path in candidates:
            if path.is_symlink() or not path.is_dir():
                continue
            stamps = self._stamps(model, path, self.root)
            if stamps is not None:
                if self._is_verified(model, path, stamps):
                    return path, True
                if fallback is None:
                    fallback = (path, False)
        repository = self.cache_dir / ("models--" + model.repo_id.replace("/", "--"))
        snapshots = repository / "snapshots"
        candidates = [snapshots / model.revision]
        try:
            # Hub model cards can change without changing model artifacts. A
            # bounded offline scan can reuse an older complete snapshot; only
            # the explicit worker's pinned digest check grants readiness.
            candidates.extend(islice(snapshots.iterdir(), 32))
        except OSError:
            pass
        for path in dict.fromkeys(candidates):
            stamps = self._stamps(model, path, repository)
            if stamps is not None:
                verified = self._is_verified(model, path, stamps)
                if verified:
                    return path, True
                if fallback is None:
                    fallback = (path, False)
        if fallback is not None:
            return fallback
        return None

    def inventory(self) -> list[dict]:
        rows = []
        for model in self.catalog:
            unavailable = self._unavailable(model)
            local = self._local(model)
            rows.append(
                {
                    "id": model.id,
                    "name": model.name,
                    "description": model.description,
                    "backend": model.backend,
                    "repo_id": model.repo_id,
                    "languages": list(model.languages),
                    "size_hint": model.size_hint,
                    "available": unavailable is None,
                    "unavailable_reason": unavailable,
                    "installed": local is not None,
                    "verified": bool(local and local[1]),
                    "local_path": str(local[0]) if local else None,
                }
            )
        return rows

    def local_model(self, model_id: str) -> tuple[str, str]:
        model = self._spec(model_id)
        if reason := self._unavailable(model):
            raise ConfigError(reason)
        local = self._local(model)
        if local is None or not local[1]:
            raise ConfigError("Download or verify this model before selecting it.")
        return model.backend, str(local[0])

    def snapshot(self) -> dict | None:
        with self._lock:
            return asdict(self._progress) if self._progress is not None else None

    def _notify(self, progress: dict) -> None:
        if self._on_progress is not None:
            try:
                self._on_progress(progress)
            except Exception:
                pass  # A closed UI/pipe must not leak a downloader thread.

    def _update(self, job_id: str, **changes) -> None:
        with self._lock:
            if self._progress is None or self._progress.job_id != job_id:
                return
            if self._cancel.is_set() and changes.get("state") not in {"cancelled", "error"}:
                return
            previous = self._progress
            self._progress = replace(self._progress, **changes)
            progress = asdict(self._progress)
            now = time.monotonic()
            changed = (
                previous.state,
                previous.file_name,
                previous.error,
                previous.total_bytes,
            ) != (
                self._progress.state,
                self._progress.file_name,
                self._progress.error,
                self._progress.total_bytes,
            )
            notify = not changes or changed or now - self._last_notice >= 0.1
            if notify:
                self._last_notice = now
        if notify:
            self._notify(progress)

    def start(self, model_id: str) -> dict:
        model = self._spec(model_id)
        if reason := self._unavailable(model):
            raise ConfigError(reason)
        with self._lock:
            if self._closed:
                raise ConfigError("Model downloader is closed. Reopen JiSpr to try again.")
            if self._worker is not None and self._worker.is_alive():
                raise ConfigError(
                    "A model operation is still running; cancel it before starting another."
                )
            self._cancel = threading.Event()
            self._last_notice = 0.0
            self._progress = DownloadProgress(job_id=str(uuid4()), model_id=model.id)
            progress = asdict(self._progress)
            worker = threading.Thread(
                target=self._run,
                args=(model, self._progress.job_id, self._cancel),
                name="jispr-model-download",
                daemon=True,
            )
            self._worker = worker
            # Start under the lock so close/cancel cannot race a not-yet-started worker.
            worker.start()
        return progress

    def cancel(self, job_id: str) -> dict:
        with self._lock:
            if (
                self._progress is None
                or self._progress.job_id != job_id
                or self._progress.state not in _ACTIVE
            ):
                raise ConfigError("That model download is no longer active.")
            self._cancel.set()
            self._progress = replace(self._progress, state="cancelling")
            progress = asdict(self._progress)
        self._notify(progress)
        return progress

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._cancel.set()
            response, worker = self._response, self._worker
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=1.5)

    @staticmethod
    def _check(cancel: threading.Event) -> None:
        if cancel.is_set():
            raise _Cancelled

    def _verify(self, model: ModelSpec, path: Path, job_id: str, cancel: threading.Event) -> None:
        # Verification is local disk work, not a network transfer. Keeping its
        # byte counter at zero also lets a corrupt cache fall back to a fresh
        # download without sending backwards progress under the same job ID.
        self._update(job_id, state="preparing", total_bytes=None)
        for file in model.files:
            digest = _digest(file)
            received = 0
            self._update(job_id, file_name=file.name)
            with (path / file.name).open("rb") as source:
                while chunk := source.read(_CHUNK):
                    self._check(cancel)
                    received += len(chunk)
                    if received > file.size:
                        raise ConfigError(f"Cached {file.name} exceeded the pinned size.")
                    digest.update(chunk)
            self._check(cancel)
            if digest.hexdigest() != file.digest:
                raise ConfigError(
                    f"Cached {file.name} failed integrity verification; download a fresh copy."
                )

    @staticmethod
    def _allowed_url(url: str) -> bool:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        return (
            parsed.scheme == "https"
            and parsed.port in {None, 443}
            and not parsed.username
            and not parsed.password
            and (
                host == "huggingface.co"
                or host.endswith(".huggingface.co")
                or host.endswith(".hf.co")
            )
        )

    def _download_file(
        self,
        client: httpx.Client,
        model: ModelSpec,
        file: ModelFile,
        stage: Path,
        job_id: str,
        cancel: threading.Event,
        previous: int,
    ) -> int:
        url = f"https://huggingface.co/{model.repo_id}/resolve/{model.revision}/{file.name}"
        for _redirect in range(8):
            self._check(cancel)
            if not self._allowed_url(url):
                raise ConfigError(
                    "Model download redirected outside the supported Hugging Face hosts."
                )
            with client.stream("GET", url, headers={"Accept-Encoding": "identity"}) as response:
                with self._lock:
                    self._response = response
                try:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise ConfigError("Model download returned an invalid redirect.")
                        url = urljoin(url, location)
                        continue
                    if response.status_code != 200:
                        raise ConfigError(
                            f"Model download returned HTTP {response.status_code}; retry later."
                        )
                    if response.headers.get("content-encoding", "identity") != "identity":
                        raise ConfigError(
                            "Model download returned an unsupported compressed response."
                        )
                    length = response.headers.get("content-length")
                    if length is not None and int(length) != file.size:
                        raise ConfigError(f"Unexpected download size for {file.name}.")
                    digest, received = _digest(file), 0
                    self._update(job_id, state="downloading", file_name=file.name)
                    with (stage / file.name).open("xb") as target:
                        # Do not ask httpx to aggregate 64 KiB before yielding:
                        # a slow trickle could otherwise defer cancellation
                        # indefinitely. Check every transport chunk and bound
                        # each write/hash operation ourselves.
                        for incoming in response.iter_bytes():
                            self._check(cancel)
                            for offset in range(0, len(incoming), _CHUNK):
                                self._check(cancel)
                                chunk = incoming[offset : offset + _CHUNK]
                                if received + len(chunk) > file.size:
                                    raise ConfigError(
                                        f"Download exceeded the pinned size for {file.name}."
                                    )
                                target.write(chunk)
                                digest.update(chunk)
                                received += len(chunk)
                                self._update(job_id, downloaded_bytes=previous + received)
                        self._check(cancel)
                        target.flush()
                        self._check(cancel)
                        os.fsync(target.fileno())
                    self._check(cancel)
                    if received != file.size or digest.hexdigest() != file.digest:
                        raise ConfigError(
                            f"Incomplete or invalid model artifact: {file.name}. "
                            "Retry the download."
                        )
                    return received
                finally:
                    with self._lock:
                        if self._response is response:
                            self._response = None
        raise ConfigError("Model download returned too many redirects.")

    def _run(self, model: ModelSpec, job_id: str, cancel: threading.Event) -> None:
        stage: Path | None = None
        destination: Path | None = None
        try:
            self._update(job_id)
            self._check(cancel)
            local = self._local(model)
            path = None
            if local is not None:
                try:
                    self._verify(model, local[0], job_id, cancel)
                    path = local[0]
                except (ConfigError, OSError):
                    # An explicit download/verify may repair a corrupt cached
                    # copy by downloading a new managed copy. Never edit or
                    # delete the user's shared cache or an older model.
                    self._update(job_id, downloaded_bytes=0, file_name=None)
            if path is None:
                stage = self.root / ".downloads" / f"{model.id}-{job_id}"
                if self.root.is_symlink() or stage.parent.is_symlink():
                    raise ConfigError("Model download directory must not be a symbolic link.")
                stage.mkdir(parents=True, exist_ok=False)
                self._update(job_id, total_bytes=sum(file.size for file in model.files))
                downloaded = 0
                with httpx.Client(
                    transport=self._transport,
                    follow_redirects=False,
                    timeout=httpx.Timeout(3.0, read=1.0, pool=1.0),
                ) as client:
                    for file in model.files:
                        downloaded += self._download_file(
                            client, model, file, stage, job_id, cancel, downloaded
                        )
                self._check(cancel)
                stamps = self._stamps(model, stage, self.root)
                if stamps is None:
                    raise ConfigError("Downloaded model artifacts changed before completion.")
                atomic_write_text(
                    stage / _MARKER,
                    json.dumps({**self._manifest(model), "stamps": stamps}, sort_keys=True) + "\n",
                )
                parent = self.root / model.id
                if parent.is_symlink():
                    raise ConfigError("Model destination must not be a symbolic link.")
                parent.mkdir(parents=True, exist_ok=True)
                destination = parent / f"{model.revision}-{job_id}"
                path = stage
            with self._lock:
                self._check(cancel)
                if self._progress is None or self._progress.job_id != job_id:
                    raise _Cancelled
                allowed_root = self.root if path.is_relative_to(self.root) else self.cache_dir
                stamps = self._stamps(model, path, allowed_root)
                if stamps is None:
                    raise ConfigError("Model changed before it could be marked ready.")
                # Publishing and reporting completion share the cancellation
                # lock: a cancelled job can never expose a ready directory.
                if destination is not None:
                    os.replace(path, destination)
                    stage = None
                    path = destination
                self._verified[(model.id, str(path))] = stamps
                self._progress = replace(
                    self._progress,
                    state="completed",
                    local_path=str(path),
                    file_name=None,
                    downloaded_bytes=self._progress.downloaded_bytes,
                    total_bytes=self._progress.total_bytes or 0,
                )
                progress = asdict(self._progress)
            self._notify(progress)
        except Exception as exc:
            cancelled = cancel.is_set() or isinstance(exc, _Cancelled)
            self._update(
                job_id,
                state="cancelled" if cancelled else "error",
                error=None if cancelled else str(exc),
                local_path=None,
            )
        finally:
            if stage is not None:
                shutil.rmtree(stage, ignore_errors=True)
