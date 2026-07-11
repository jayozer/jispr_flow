# Private model benchmark

JiSpr's real-voice benchmark is deliberately local. Put audio, manifests,
frozen transcripts, blind reviews, and raw reports under `benchmarks/private/`;
Git ignores that directory.

## Corpus

Create 24 English recordings: six general dictations, six proper-name/technical
samples, six filler/punctuation/self-correction samples, and six samples rich
in exact numbers, URLs, paths, identifiers, or code. Add eight multilingual
recordings (licensed public Spanish speech is the fallback).

Copy `corpus.example.jsonl` to `benchmarks/private/corpus.jsonl`. Each line is:

```json
{"id":"unique-id","audio":"audio/file.wav","language":"en","verbatim":"exact spoken words including fillers","intended":"desired final text","category":"general|names|fillers|protected","proper_names":["JiSpr Flow"],"protected_tokens":["3.14"],"fillers":["um"]}
```

## Stage 1: freeze Parakeet v3 and compare polishers

Load the three Q4 GGUFs in LM Studio and use their exact `/v1/models` IDs:

```bash
uv run local-flow benchmark-models benchmarks/private/corpus.jsonl \
  --output benchmarks/private/parakeet-v3 \
  --polisher gemma-4-26B-A4B-it-UD-Q4_K_M.gguf \
  --polisher Qwen3.5-35B-A3B-Q4_K_M.gguf \
  --polisher Qwen3.5-9B-Q4_K_M.gguf --runs 3
```

The command writes `frozen-asr.jsonl`, `model-benchmark.json`, and a blinded
`blind-review.jsonl`. Inspect every blind output and set
`material_meaning_change` and `hallucination` to `false` or `true`; leaving
either null makes that model ineligible. Apply the completed review without
retranscribing:

```bash
uv run local-flow benchmark-models benchmarks/private/corpus.jsonl \
  --output benchmarks/private/parakeet-reviewed \
  --frozen benchmarks/private/parakeet-v3/frozen-asr.jsonl \
  --polisher gemma-4-26B-A4B-it-UD-Q4_K_M.gguf \
  --polisher Qwen3.5-35B-A3B-Q4_K_M.gguf \
  --polisher Qwen3.5-9B-Q4_K_M.gguf \
  --reviews benchmarks/private/parakeet-v3/blind-review.jsonl
```

## Stage 2: ASR comparison

Run the same corpus with Whisper Turbo and only the winning LM Studio model:

```bash
uv run local-flow benchmark-models benchmarks/private/corpus.jsonl \
  --output benchmarks/private/whisper-turbo \
  --asr-backend mlx-whisper \
  --asr-model mlx-community/whisper-large-v3-turbo \
  --language auto --polisher WINNING_LM_STUDIO_MODEL_ID
```

Publish only aggregate, redacted results. Never commit voice recordings,
verbatim private speech, dictionaries, or blind-review text.
