# MLX Whisper Evaluation

## Decision

Ship `mlx-whisper` as an **opt-in Apple Silicon backend**. Keep
`faster-whisper` as the default for portability. On the controlled corpus,
MLX reduced aggregate median transcription latency by **89.3%** with no
aggregate WER regression, clearing the roadmap gate of at least 20% lower
latency and no more than 2 absolute WER points of regression.

The adapter uses the public `mlx_whisper.transcribe` API, imports MLX only when
selected, and accepts the same live dictionary prompt used by faster-whisper.
Its model is an MLX-converted Whisper repository rather than a CTranslate2
model directory.

## Environment

Measured on 2026-07-09 on a MacBook Pro with Apple M5 Max (18 CPU cores,
128 GB RAM), macOS 26.5.1, and Python 3.13.12.

| Package | Version |
|---|---:|
| `faster-whisper` | 1.2.1 |
| `mlx-whisper` | 0.4.3 |
| `mlx` | 0.32.0 |
| `numpy` | 2.4.6 |

The implementation follows the [official MLX Whisper example and API](https://github.com/ml-explore/mlx-examples/tree/main/whisper),
uses the [published `mlx-whisper` package](https://pypi.org/project/mlx-whisper/),
and evaluates a model from the [MLX Community Whisper collection](https://huggingface.co/collections/mlx-community/whisper-663256f9964fbb1177db93dc).

## Reproduction

Create three deterministic, local synthetic samples; no evaluation audio is
stored in the repository:

```bash
mkdir -p /tmp/jispr-asr-eval/data
say -v Samantha -r 170 --file-format=WAVE --data-format=LEI16@16000 \
  -o /tmp/jispr-asr-eval/01-short.wav \
  'Jispr Flow tests local speech recognition on Apple silicon.'
say -v Samantha -r 170 --file-format=WAVE --data-format=LEI16@16000 \
  -o /tmp/jispr-asr-eval/02-jargon.wav \
  'Deploy PostgreSQL and Kubernetes with CTranslate2, MLX Whisper, and Qwen.'
say -v Samantha -r 170 --file-format=WAVE --data-format=LEI16@16000 \
  -o /tmp/jispr-asr-eval/03-long.wav \
  'When evaluating a dictation system, measure model loading time, transcription latency, real time factor, and word error rate. Run each sample several times after a warmup, preserve the raw transcript, and compare accuracy before choosing a new speech recognition backend.'
```

For a controlled vocabulary test, put this in
`/tmp/jispr-asr-eval/data/dictionary.json`:

```json
{
  "terms": [
    "JiSpr Flow",
    "PostgreSQL",
    "Kubernetes",
    "CTranslate2",
    "MLX Whisper",
    "Qwen",
    "Apple Silicon"
  ]
}
```

Then run the same files, references, three measured runs, and one warmup for
each backend. The abbreviated command below shows the shared arguments; repeat
`--reference` once for each sample using the exact text above.

```bash
FILES=(
  /tmp/jispr-asr-eval/01-short.wav
  /tmp/jispr-asr-eval/02-jargon.wav
  /tmp/jispr-asr-eval/03-long.wav
)
LOCAL_FLOW_DATA_DIR=/tmp/jispr-asr-eval/data \
  uv run --extra asr local-flow benchmark-asr "${FILES[@]}" \
  --backend faster-whisper --model small.en --language en \
  --device auto --compute-type int8 --runs 3 --warmup 1 \
  --reference 'Jispr Flow tests local speech recognition on Apple silicon.' \
  --reference 'Deploy PostgreSQL and Kubernetes with CTranslate2, MLX Whisper, and Qwen.' \
  --reference 'When evaluating a dictation system, measure model loading time, transcription latency, real time factor, and word error rate. Run each sample several times after a warmup, preserve the raw transcript, and compare accuracy before choosing a new speech recognition backend.' \
  --json /tmp/jispr-asr-eval/faster-final.json

LOCAL_FLOW_DATA_DIR=/tmp/jispr-asr-eval/data \
  uv run --extra mlx-asr local-flow benchmark-asr "${FILES[@]}" \
  --backend mlx-whisper --model mlx-community/whisper-small.en-mlx \
  --language en --device auto --compute-type fp16 --runs 3 --warmup 1 \
  --reference 'Jispr Flow tests local speech recognition on Apple silicon.' \
  --reference 'Deploy PostgreSQL and Kubernetes with CTranslate2, MLX Whisper, and Qwen.' \
  --reference 'When evaluating a dictation system, measure model loading time, transcription latency, real time factor, and word error rate. Run each sample several times after a warmup, preserve the raw transcript, and compare accuracy before choosing a new speech recognition backend.' \
  --json /tmp/jispr-asr-eval/mlx-final.json
```

## Results

| Backend | Model load | Short median / RTF / WER | Jargon median / RTF / WER | Long median / RTF / WER | Aggregate median / WER |
|---|---:|---:|---:|---:|---:|
| faster-whisper `small.en` | 0.877 s | 0.793 s / 0.216 / 0.000 | 0.862 s / 0.155 / 0.000 | 1.073 s / 0.064 / 0.050 | 0.862 s / 0.034 |
| MLX `whisper-small.en-mlx` | 0.805 s | 0.068 s / 0.019 / 0.000 | 0.093 s / 0.017 / 0.000 | 0.158 s / 0.009 / 0.050 | 0.093 s / 0.034 |

## Large-v3-turbo follow-up (2026-07-10)

A second controlled run compared the new `fast` and `accuracy` profiles on
the same 11.339-second synthetic technical-dictation sample. Both used three
measured runs, one warmup, the same vocabulary store, and the intended written
reference (including `V3`).

| Profile / model | Cached load | Median latency | RTF | WER |
|---|---:|---:|---:|---:|
| `fast` / `whisper-small.en-mlx` | 0.785 s | 0.129 s | 0.011 | 0.190 |
| `accuracy` / `whisper-large-v3-turbo` | 2.385 s | 0.153 s | 0.014 | 0.048 |

Turbo reduced WER by 0.142 absolute (75% relative) while adding 0.024 seconds
to median transcription latency (18.6%). Its first run took 19.353 seconds
including the one-time model download; subsequent fresh-process loads took
2.4-3.1 seconds. On this hardware, Turbo is therefore a strong accuracy-mode
default, but this one synthetic voice is not enough to replace `fast` as the
portable repository default. A real-user microphone corpus remains the final
decision gate.

Reproduce either side with the same file and reference:

```bash
uv run --extra mlx-asr local-flow benchmark-asr sample.wav \
  --profile fast --reference "expected transcript" --runs 3 --warmup 1
uv run --extra mlx-asr local-flow benchmark-asr sample.wav \
  --profile accuracy --reference "expected transcript" --runs 3 --warmup 1
```

An exploratory run with an empty dictionary produced aggregate WER 0.085 for
faster-whisper and 0.119 for MLX: a 3.4-point MLX regression that would fail
the accuracy gate. The controlled run above then enabled the same product
vocabulary for both backends and reduced both to 0.034. The ship decision is
therefore limited to an opt-in adapter using Jispr Flow's full vocabulary-aware
path; it is not evidence that raw MLX decoding is universally as accurate.

Model load reflects cached weights. The first MLX run observed during setup
took 10.410 seconds while weights were downloaded and initialized, so it is
not comparable to the warm-cache row. This small corpus uses one synthetic
voice and does not measure memory, power, multilingual accuracy, noisy rooms,
or diverse speakers. A manual microphone smoke test remains advisable before
making MLX the default; this evaluation only justifies the opt-in adapter.
