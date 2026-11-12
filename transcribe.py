#!/usr/bin/env python3
"""
Local ASR for the Telegram bot gateway — same stack as the user's phone
server (the tgmcp deployment):

  * sherpa-onnx runtime v2.0.1 (shared build), invoked as a subprocess
  * Paraformer model, int8 variant preferred
  * ffmpeg converts non-WAV input (e.g. Telegram .ogg/Opus) to
    16 kHz mono PCM WAV first, because sherpa-onnx only reads WAV
  * LD_LIBRARY_PATH is pointed at the runtime's lib/ (and bin/) dirs
  * recognised text is parsed from the recogniser's JSON output lines

This mirrors tgmcp's internal/asr/sherpa.go (same ffmpeg flags, same
offline invocation, same thread default of 2). One adaptation: the
bilingual zh-en Paraformer that tgmcp offers is the *streaming* variant
(encoder/decoder files instead of a single model file), so when the
model directory contains encoder*.onnx + decoder*.onnx this script uses
the streaming recogniser (`sherpa-onnx` binary, --paraformer-encoder /
--paraformer-decoder) — the exact usage printed by that binary's own
--help for this model. Offline single-file models keep tgmcp's exact
`sherpa-onnx-offline --paraformer=...` invocation.

Usage:
    python3 transcribe.py <audio-file> [--threads N] [--model-dir DIR]

Output (stdout, single JSON line):
    {"text": "...", "backend": "sherpa", "model": "<dir name>"}
Exit code 0 on success (text may be empty for silence), 1 on error
(error message on stderr).
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SHERPA_BIN_DIR = BASE_DIR / "asr" / "sherpa" / "bin"
SHERPA_LIB_DIR = BASE_DIR / "asr" / "sherpa" / "lib"
DEFAULT_MODEL_DIR = (
    BASE_DIR / "asr" / "models" / "sherpa-onnx-streaming-paraformer-bilingual-zh-en"
)


def is_wav(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(12)
        return head[0:4] == b"RIFF" and head[8:12] == b"WAVE"
    except OSError:
        return False


def ensure_wav(src: Path) -> tuple[Path, bool]:
    """Return (wav_path, is_temp). Converts with ffmpeg like tgmcp does."""
    if is_wav(src):
        return src, False
    fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="tbgw_asr_")
    os.close(fd)
    tmp_path = Path(tmp)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src),
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        str(tmp_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg conversion failed: {proc.stderr.strip()[-300:]}")
    return tmp_path, True


def sherpa_env() -> dict:
    env = dict(os.environ)
    lib = str(SHERPA_LIB_DIR)
    existing = env.get("LD_LIBRARY_PATH", "")
    env["LD_LIBRARY_PATH"] = lib + (os.pathsep + existing if existing else "")
    return env


def resolve_layout(model_dir: Path) -> dict:
    """Detect offline single-file vs streaming encoder/decoder layout."""
    tokens = model_dir / "tokens.txt"
    if not tokens.exists():
        raise RuntimeError(f"tokens.txt not found in model dir {model_dir}")
    for name in ("model.int8.onnx", "model.onnx"):
        if (model_dir / name).exists():
            return {"kind": "offline", "tokens": tokens, "model": model_dir / name}
    enc = dec = None
    for name in ("encoder.int8.onnx", "encoder.onnx"):
        if (model_dir / name).exists():
            enc = model_dir / name
            break
    for name in ("decoder.int8.onnx", "decoder.onnx"):
        if (model_dir / name).exists():
            dec = model_dir / name
            break
    if enc and dec:
        return {"kind": "streaming", "tokens": tokens, "encoder": enc, "decoder": dec}
    raise RuntimeError(f"no Paraformer model files found in {model_dir}")


def parse_text(stdout: str) -> str:
    """Extract recognised text: prefer JSON lines with a .text field
    (tgmcp-compatible); otherwise fall back to the streaming CLI's
    result lines."""
    texts = []
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("text"):
                texts.append(obj["text"])
    if texts:
        return " ".join(texts).strip()
    # Fallback for the streaming CLI's plain output: lines after the
    # per-file header that are not log noise. Kept deliberately simple;
    # verified against the bundled test wavs (see README).
    candidates = [
        ln.strip() for ln in stdout.splitlines()
        if ln.strip() and not ln.startswith(("[", "/", "Elapsed", "Wave", "Started", "Done"))
        and "sherpa-onnx" not in ln
    ]
    return candidates[-1] if candidates else ""


def transcribe(audio: Path, model_dir: Path, threads: int) -> str:
    layout = resolve_layout(model_dir)
    wav_path, is_temp = ensure_wav(audio)
    try:
        if layout["kind"] == "offline":
            cli = SHERPA_BIN_DIR / "sherpa-onnx-offline"
            args = [
                str(cli),
                f"--tokens={layout['tokens']}",
                f"--paraformer={layout['model']}",
                f"--num-threads={threads}",
                "--decoding-method=greedy_search",
                str(wav_path),
            ]
        else:
            cli = SHERPA_BIN_DIR / "sherpa-onnx"
            args = [
                str(cli),
                f"--tokens={layout['tokens']}",
                f"--paraformer-encoder={layout['encoder']}",
                f"--paraformer-decoder={layout['decoder']}",
                f"--num-threads={threads}",
                "--decoding-method=greedy_search",
                str(wav_path),
            ]
        if not cli.exists():
            raise RuntimeError(f"sherpa-onnx binary not found: {cli}")
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=900, env=sherpa_env()
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"sherpa-onnx exited {proc.returncode}: {proc.stderr.strip()[-400:]}"
            )
        # The offline recogniser prints its JSON result on stdout; the
        # streaming recogniser prints it (plain line + JSON) on stderr.
        # Scan both, exactly as tgmcp parses the .text field.
        return parse_text(proc.stdout + "\n" + proc.stderr)
    finally:
        if is_temp:
            wav_path.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Transcribe audio with sherpa-onnx Paraformer")
    ap.add_argument("audio", help="audio file (ogg/wav/mp3/...)")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--model-dir", default=str(DEFAULT_MODEL_DIR))
    args = ap.parse_args()

    audio = Path(args.audio)
    if not audio.exists():
        print(f"ERROR: audio file not found: {audio}", file=sys.stderr)
        return 1
    model_dir = Path(args.model_dir)
    try:
        text = transcribe(audio, model_dir, args.threads)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(
        {"text": text, "backend": "sherpa", "model": model_dir.name},
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
