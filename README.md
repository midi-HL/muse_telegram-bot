**English** | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

# telegram-bot-gateway

**A Telegram bot as a local inbox for an AI agent.** You message the
bot — text, photos, documents, voice notes — and everything lands on
disk as structured, self-contained items. A watcher wakes the agent,
the agent reads the items (voice already transcribed locally, images
ready as files for multimodal input), replies through the bot, and
marks them done.

- **Zero dependencies** — Python 3 standard library only. No pip.
  (One exception: if `curl` is installed, file downloads prefer it —
  some proxies cut urllib streams short mid-file — with a urllib
  fallback.)
- **Local ASR** — voice notes are transcribed on the box with
  sherpa-onnx + a Paraformer zh/en bilingual model. No audio leaves
  the machine.
- **Crash-safe by design** — atomic inbox writes, a persisted
  getUpdates offset, failures recorded in-band instead of crashing.
- **Explicit trust model** — a chat-id allowlist separates "the
  owner's messages" from "stranger data". Empty allowlist = trust
  nothing.

Status: **v0.1.0 — built and fully mock-tested; activation is a
separate, deliberate step** (see [docs/activation.md](docs/activation.md)).

## How it works

```
Telegram user ──► Bot API ──► gateway.py ──► inbox/<update_id>/
                                                  ├── message.json
                                                  └── attachments…
                                                        │
                              watcher (1 min) ──► agent wakes, reads,
                                                  replies via send.py,
                                                  acks via process_inbox.py
                                                        │
                                                        ▼
                                                 processed/<id>/
```

Full diagram and invariants: [docs/architecture.md](docs/architecture.md).

## Features

- Long-polling `getUpdates` inbox service (`gateway.py`)
- Media receiving: **photos** (largest size), **documents**,
  **video**, **video notes**, **audio**, **voice**, **animations**,
  **stickers** — downloaded via `getFile`, itemised in the record
  with kind, path, mime type and size
- Voice transcription stored in the record (`transcript`)
- Wake-to-answer tooling (`process_inbox.py list|show|ack`)
- Retention cleanup: `tmp/` scratch 24 h, `processed/` 7 days
  (configurable), enforced by the service and by `cleanup.py`
- Reply CLI (`send.py`, auto-splits long texts at 4096 chars)
- systemd user unit included
- Token-free test suite against a mock Bot API (14 checks)

## Quickstart

```bash
git clone <this repo> && cd telegram-bot-gateway

# 1) Configure
cp .env.example .env            # put your BotFather token in .env
chmod 600 .env
cp config.example.json config.json   # optional; defaults work

# 2) (Optional) local voice transcription
scripts/setup_asr.sh            # ~310 MB of downloads into asr/
# needs ffmpeg on PATH at runtime

# 3) Run in the foreground to sanity-check
python3 gateway.py              # logs "gateway starting as @<bot>"

# 4) Or install as a service — see docs/activation.md
```

## Configuration

| Where | What |
|---|---|
| `.env` | `BOT_TOKEN` (required), `TELEGRAM_API_BASE` (optional; point at a self-hosted Bot API to lift the 20 MB download cap) |
| `config.json` | `allowlist_chat_ids`, `poll_timeout_seconds`, `transcribe_voice`, `asr_threads`, `tmp_retention_hours`, `processed_retention_days` |

See `.env.example` / `config.example.json`, or the full
[configuration reference](docs/configuration.md) — every variable
and key with types, defaults and examples, plus the BotFather
privacy-mode note for groups.

## File layout

```
telegram-bot-gateway/
├── gateway.py                 # long-polling inbox service
├── send.py                    # reply CLI (sendMessage)
├── process_inbox.py           # agent-side list / show / ack
├── cleanup.py                 # retention cleanup (also in-service)
├── transcribe.py              # sherpa-onnx ASR wrapper
├── config.json                # live config (see config.example.json)
├── .env                       # secrets — chmod 600, gitignored
├── inbox/  processed/  tmp/  state/     # runtime data, gitignored
├── asr/                       # runtime + models, gitignored
├── scripts/setup_asr.sh       # reproduces the ASR install
├── docs/                      # architecture, activation, asr, security
├── tests/mock_test.py         # token-free end-to-end tests
└── telegram-bot-gateway.service
```

## Tests

```bash
python3 tests/mock_test.py
# 14/14 checks passed — runs the real gateway against a local mock
# Bot API: text/photo/document/voice items, failed-download handling,
# offset persistence, process_inbox list/ack, retention cleanup,
# sendMessage payload, placeholder-token refusal.
```

ASR has its own real test with the model's bundled test wavs —
measured output and timings are in [docs/asr.md](docs/asr.md).

## Documentation

- [Install with an AI agent](docs/agent-install.md) — a
  ready-to-paste prompt that hands the whole install to an agent,
  plus a human checklist and failure notes
- [Architecture](docs/architecture.md) — data flow, invariants,
  wake-to-answer spec, storage lifecycle
- [Activation](docs/activation.md) — BotFather → token → service →
  watcher → allowlist, step by step
- [Configuration](docs/configuration.md) — every `.env` variable
  and `config.json` key, with defaults and examples
- [ASR](docs/asr.md) — the sherpa-onnx/Paraformer stack and results
- [Security](docs/security.md) — token handling, trust model,
  hostile-input rules
- **Translations** — this README is also available in
  [简体中文](README.zh-CN.md) and [日本語](README.ja.md)

## Limitations

- **20 MB download cap** on the public Bot API
  (`api.telegram.org`): files larger than that arrive as records
  with `download_error`. Point `TELEGRAM_API_BASE` at a self-hosted
  Bot API server to lift the cap.
- **Wake latency up to ~1 minute**: long-polling delivers messages
  to the inbox immediately, but the watcher runs every minute, so
  the agent may wake up to a minute after you write.
- **ASR is a small on-device model**: expect occasional errors,
  especially on mixed Chinese/English speech; background noise and
  far-away microphones make it worse. It is a convenience, not a
  court transcript.
- **Exactly one poller per bot**: only one gateway instance may
  `getUpdates` for a given token — a second one gets
  `409 Conflict` from Telegram.
- **Python 3.10+, standard library only**: no pip installs, but
  also no newer-runtime features; ASR is the only heavy piece.
  Downloads prefer `curl` when it is installed and verify byte
  counts, so a truncated transfer is retried, never stored silently.
- **ASR stack is linux-x86_64**: the prebuilt sherpa-onnx runtime
  binaries installed by `scripts/setup_asr.sh` target
  linux-x86_64, and `ffmpeg` must be on PATH. Other platforms need
  a matching sherpa-onnx build (or `transcribe_voice: false`).
- **Voice transcription is local only**: no audio is ever sent to
  a cloud ASR service. If the local model is not installed, voice
  notes are stored untranscribed.
- **License is [CC BY-NC-SA 4.0](LICENSE)**: free to use, modify
  and share with attribution under the same license; **commercial
  use is not permitted**.

## License

[CC BY-NC-SA 4.0](LICENSE) — free to use, modify and share with
attribution and under the same license; **commercial use is not
permitted**. Chosen by the project owner.
