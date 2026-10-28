"""Pinned public Hugging Face artifacts supported by JiSpr's ASR adapters.

Revisions, byte sizes, and Git/LFS digests were read from the official Hub API
(``/api/models/<repo>?blobs=true``). Updating a model requires reviewing this
manifest; user input cannot choose an alternate repository, revision, or URL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelFile:
    name: str
    size: int
    digest: str
    algorithm: str = "sha256"

    def __post_init__(self) -> None:
        if (
            not re.fullmatch(r"[A-Za-z0-9_.-]+", self.name)
            or self.name in {".", ".."}
            or not 0 < self.size <= 8 * 1024**3
        ):
            raise ValueError("Model artifacts must be bounded files without directory components.")
        length = {"sha256": 64, "git-sha1": 40}.get(self.algorithm)
        if length is None or not re.fullmatch(rf"[a-f0-9]{{{length}}}", self.digest):
            raise ValueError("Model artifact requires a valid pinned Git or LFS digest.")


@dataclass(frozen=True)
class ModelSpec:
    id: str
    name: str
    description: str
    backend: str
    repo_id: str
    revision: str
    languages: tuple[str, ...]
    size_hint: str
    files: tuple[ModelFile, ...]
    module: str
    apple_silicon: bool = False
    requires_ffmpeg: bool = False

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", self.id):
            raise ValueError("Model ID must be a catalog identifier, never a path or URL.")
        if (
            not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repo_id)
            or any(part in {".", ".."} for part in self.repo_id.split("/"))
            or not re.fullmatch(r"[a-f0-9]{40}", self.revision)
        ):
            raise ValueError("Model repository and immutable revision must be pinned.")
        if not self.files or len({file.name for file in self.files}) != len(self.files):
            raise ValueError("Model requires a unique, nonempty artifact manifest.")


WHISPER_LANGUAGES = tuple(
    (
        "auto en zh de es ru ko fr ja pt tr pl ca nl ar sv it id hi fi vi he uk el ms cs ro "
        "da hu ta no th ur hr bg lt la mi ml cy sk te fa lv bn sr az sl kn et mk br eu is "
        "hy ne mn bs kk sq sw gl mr pa si km sn yo so af oc ka be tg sd gu am yi lo uz fo "
        "ht ps tk nn mt sa lb my bo tl mg as tt haw ln ha ba jw su"
    ).split()
)
PARAKEET_LANGUAGES = tuple(
    ("auto en es fr de bg hr cs da nl et fi el hu it lv lt mt pl pt ro sk sl sv ru uk").split()
)

CATALOG = (
    ModelSpec(
        id="parakeet-v3",
        name="Parakeet v3",
        description="Fast multilingual dictation on Apple Silicon; requires FFmpeg.",
        backend="mlx-parakeet",
        repo_id="mlx-community/parakeet-tdt-0.6b-v3",
        revision="ed2b7e8c15f9aaa0b5772e2efb986255eaef7e15",
        languages=PARAKEET_LANGUAGES,
        size_hint="2.5 GB",
        module="parakeet_mlx",
        apple_silicon=True,
        requires_ffmpeg=True,
        files=(
            ModelFile(
                "config.json", 244093, "4f469c2e92c981861f7ce6bcd940e608401d931e", "git-sha1"
            ),
            ModelFile(
                "model.safetensors",
                2508288736,
                "05e01c7f396c298cf7d23f61da7b504adeab698f0aaeafd9c82d198625464592",
            ),
        ),
    ),
    ModelSpec(
        id="whisper-small-mlx",
        name="Whisper Small · Multilingual",
        description="A smaller Apple Silicon model with automatic language detection.",
        backend="mlx-whisper",
        repo_id="mlx-community/whisper-small-mlx",
        revision="45f3915923c7a79a5a5b5a7d909d39aeb0e5630e",
        languages=WHISPER_LANGUAGES,
        size_hint="481 MB",
        module="mlx_whisper",
        apple_silicon=True,
        files=(
            ModelFile("config.json", 266, "df4c65c442a30e50034ee63422b31bde60c3d407", "git-sha1"),
            ModelFile(
                "weights.npz",
                481307592,
                "55b6674c9b339702d486e2b1573839a66f8ec8f821ed2886993ef717a86b09f5",
            ),
        ),
    ),
    ModelSpec(
        id="whisper-small-en-mlx",
        name="Whisper Small · English",
        description="A smaller Apple Silicon model for English-only dictation.",
        backend="mlx-whisper",
        repo_id="mlx-community/whisper-small.en-mlx",
        revision="52a88bf6e98b114a210c21bb83e22d6e1505cb73",
        languages=("en",),
        size_hint="481 MB",
        module="mlx_whisper",
        apple_silicon=True,
        files=(
            ModelFile("config.json", 266, "0ab1faf5d15595c7aa7759fdc4ef021a8443fa23", "git-sha1"),
            ModelFile(
                "weights.npz",
                481306200,
                "1bb29b030aca711a035f7a084a0eefac6251ecc2bdd356fa748858fbad082f5a",
            ),
        ),
    ),
    ModelSpec(
        id="whisper-turbo-mlx",
        name="Whisper Large v3 Turbo",
        description="Higher-accuracy multilingual dictation on Apple Silicon.",
        backend="mlx-whisper",
        repo_id="mlx-community/whisper-large-v3-turbo",
        revision="a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb",
        languages=(*WHISPER_LANGUAGES, "yue"),
        size_hint="1.6 GB",
        module="mlx_whisper",
        apple_silicon=True,
        files=(
            ModelFile("config.json", 268, "6ac9a52a28f70a2e5681c250a470eca6e9c8cc3e", "git-sha1"),
            ModelFile(
                "weights.safetensors",
                1613977612,
                "951ed3fc1203e6a62467abb2144a96ce7eafca8fa77e3704fdb8635ff3e7f8a6",
            ),
        ),
    ),
    ModelSpec(
        id="faster-whisper-small",
        name="Whisper Small · CPU",
        description="Multilingual CPU-compatible dictation, including Intel Macs.",
        backend="faster-whisper",
        repo_id="Systran/faster-whisper-small",
        revision="536b0662742c02347bc0e980a01041f333bce120",
        languages=WHISPER_LANGUAGES,
        size_hint="486 MB",
        module="faster_whisper",
        files=(
            ModelFile("config.json", 2370, "e5047537059bd8f182d9ca64c470201585015187", "git-sha1"),
            ModelFile(
                "model.bin",
                483546902,
                "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671",
            ),
            ModelFile(
                "tokenizer.json", 2203239, "7818adb6de9fa3064d3ff81226fdd675be1f6344", "git-sha1"
            ),
            ModelFile(
                "vocabulary.txt", 459861, "c9074644d9d1205686f16d411564729461324b75", "git-sha1"
            ),
        ),
    ),
)
