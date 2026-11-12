# ASR setup (local voice transcription)

Voice messages are transcribed **locally** — no audio leaves the
machine. The stack replicates the tgmcp deployment used on the
project owner's phone server (`internal/asr/sherpa.go` in tgmcp):

- **Runtime:** sherpa-onnx **v2.0.1**, `linux-x64-shared` build.
- **Model:** **Paraformer bilingual zh-en** (streaming variant),
  **int8** files only (`encoder.int8.onnx` + `decoder.int8.onnx` +
  `tokens.txt`, ≈228 MB) — chosen for a small CPU-only box.
- **Pipeline:** non-WAV input (Telegram voice is Opus/`.ogg`) →
  `ffmpeg` converts to 16 kHz mono PCM WAV → recogniser runs with
  `--num-threads=2 --decoding-method=greedy_search` →
  transcript parsed from the recogniser's JSON `.text` output.
- **Adaptation:** the bilingual model is a *streaming*
  encoder/decoder model, so `transcribe.py` calls the runtime's
  streaming recogniser (`sherpa-onnx` with
  `--paraformer-encoder/--paraformer-decoder`) — the usage that
  binary's own `--help` prints for this model. Single-file offline
  models are still supported via the offline invocation.

## Install

```bash
scripts/setup_asr.sh
```

This downloads the runtime and model from the k2-fsa/sherpa-onnx
GitHub releases into `asr/` (gitignored, ≈310 MB with archives;
delete `asr/*.tar.bz2` afterwards if disk is tight). Requirements:
`curl` (or `wget`), `tar` with bzip2, and `ffmpeg` on PATH at
runtime.

Manual layout produced:

```
asr/
├── sherpa/            # runtime: bin/ + lib/
└── models/
    └── sherpa-onnx-streaming-paraformer-bilingual-zh-en/
        ├── encoder.int8.onnx
        ├── decoder.int8.onnx
        ├── tokens.txt
        └── test_wavs/ # sample wavs from the model archive
```

## Test

```bash
python3 transcribe.py asr/models/sherpa-onnx-streaming-paraformer-bilingual-zh-en/test_wavs/0.wav
# {"text": "...", "backend": "sherpa", "model": "sherpa-onnx-streaming-paraformer-bilingual-zh-en"}
```

## Measured results (2026-11-12, ~1.5-core VM, 2 threads)

Bundled model test wavs (noisy English-lesson recordings — errors
below are the small model's genuine output, recorded as-is):

| file | duration | transcription |
|---|---|---|
| 0.wav | 10.1 s | 昨天是 monday teday is li 班二 the day after tomorrow 是星期三 |
| 1.wav | 5.1 s | 这是第一种第二种叫嗯与 always o s 什么意思啊 |
| 2.wav | 4.7 s | 就是平繁繁的不不认识接下来 frequently 频繁 |
| 3.wav | 8.8 s | 第一句是个什么时态加了 e s 一般般现时对对后把它十三线商品 |

Telegram round trip: 0.wav re-encoded to a 32 kbps Opus `.ogg`
(`tests/voice_sample.ogg`, committed as a fixture) and transcribed
through the full gateway path — **identical output**, ≈3.7 s wall
including model load (≈1.8 s). Good enough for voice notes; expect
small-model errors on mixed zh/en speech.

## Knobs

- `config.json`: `transcribe_voice` (on/off), `asr_threads`.
- Swap models by pointing `transcribe.py --model-dir` at another
  Paraformer directory (offline single-file models work too).
