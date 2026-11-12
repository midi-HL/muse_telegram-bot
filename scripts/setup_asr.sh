#!/usr/bin/env bash
# setup_asr.sh — install the local ASR stack (sherpa-onnx runtime +
# Paraformer bilingual zh-en int8 model) into ../asr/.
#
# This reproduces the exact deployment the gateway expects, the same
# stack tgmcp installs on the phone server:
#   * runtime: sherpa-onnx v2.0.1, linux-x64 shared build
#   * model:   sherpa-onnx-streaming-paraformer-bilingual-zh-en,
#              int8 encoder/decoder only (like the phone deployment)
#
# Downloads come from the k2-fsa/sherpa-onnx GitHub releases.
# Idempotent: existing installs are left untouched unless --force.
# The asr/ tree is gitignored (~310 MB with the kept tarballs).
#
# Usage:  scripts/setup_asr.sh [--force]
set -euo pipefail

cd "$(dirname "$0")/.."
ASR_DIR="$PWD/asr"
RUNTIME_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/v2.0.1/sherpa-onnx-v2.0.1-linux-x64-shared.tar.bz2"
MODEL_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-streaming-paraformer-bilingual-zh-en.tar.bz2"
RUNTIME_TOP="sherpa-onnx-v2.0.1-linux-x64-shared"
MODEL_TOP="sherpa-onnx-streaming-paraformer-bilingual-zh-en"
FORCE="${1:-}"

fetch() {  # fetch <url> <dest>
    if command -v curl >/dev/null 2>&1; then
        curl -fL --retry 3 -o "$2" "$1"
    else
        wget -O "$2" "$1"
    fi
}

mkdir -p "$ASR_DIR/models"

# --- runtime -------------------------------------------------------------
if [[ -x "$ASR_DIR/sherpa/bin/sherpa-onnx" && "$FORCE" != "--force" ]]; then
    echo "runtime already installed: $ASR_DIR/sherpa (use --force to redo)"
else
    echo "downloading sherpa-onnx runtime v2.0.1 ..."
    fetch "$RUNTIME_URL" "$ASR_DIR/sherpa-runtime.tar.bz2"
    rm -rf "$ASR_DIR/$RUNTIME_TOP" "$ASR_DIR/sherpa"
    tar -xjf "$ASR_DIR/sherpa-runtime.tar.bz2" -C "$ASR_DIR"
    mv "$ASR_DIR/$RUNTIME_TOP" "$ASR_DIR/sherpa"
    echo "runtime installed: $ASR_DIR/sherpa"
fi

# --- model (int8 only) -----------------------------------------------------
MODEL_DIR="$ASR_DIR/models/$MODEL_TOP"
if [[ -f "$MODEL_DIR/encoder.int8.onnx" && "$FORCE" != "--force" ]]; then
    echo "model already installed: $MODEL_DIR (use --force to redo)"
else
    echo "downloading Paraformer bilingual zh-en model ..."
    fetch "$MODEL_URL" "$ASR_DIR/model-bilingual.tar.bz2"
    rm -rf "$MODEL_DIR"
    mkdir -p "$MODEL_DIR"
    # Extract only what the gateway uses: int8 encoder/decoder,
    # tokens, and the bundled test wavs (handy for a smoke test).
    tar -xjf "$ASR_DIR/model-bilingual.tar.bz2" -C "$ASR_DIR/models" \
        "$MODEL_TOP/tokens.txt" \
        "$MODEL_TOP/encoder.int8.onnx" \
        "$MODEL_TOP/decoder.int8.onnx" \
        "$MODEL_TOP/test_wavs"
    echo "model installed: $MODEL_DIR"
fi

echo
echo "ASR stack ready. Smoke test:"
echo "  python3 transcribe.py \"$MODEL_DIR/test_wavs/0.wav\""
