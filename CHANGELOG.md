# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `docs/agent-install.md` — "Install with an AI agent": a
  ready-to-paste prompt that hands the full install/activation to
  an AI agent, a human checklist of the same steps, and failure
  notes (401 = bad token, 409 = another poller, missing ASR model).
- `docs/configuration.md` — complete configuration reference:
  every `.env` variable and `config.json` key with type, default,
  effect and example, plus the BotFather privacy-mode note.
- `README.zh-CN.md` / `README.ja.md` — full Simplified Chinese and
  Japanese translations of the README; all three READMEs now carry
  a language switcher as their first line.

### Changed
- README: "Known limits" expanded into a full Limitations section
  (20 MB cap, ~1 min wake latency, ASR quality bounds, one poller
  per bot, Python 3.10+ stdlib-only, linux-x86_64 ASR stack,
  local-only transcription, CC BY-NC-SA 4.0); configuration and
  documentation sections now link the new docs.
- `docs/architecture.md`: added a matching Limitations summary so
  both documents agree.
- License changed from MIT to CC BY-NC-SA 4.0 at the owner's request:
  no commercial use; attribution and share-alike required.
- `welcome-640x360.jpg` untracked (personal bot asset, not source).

### Fixed
- Attachment downloads could be stored silently truncated: behind
  the deployment's egress proxy, urllib streams were cut at a fixed
  byte count and `copyfileobj` swallowed the IncompleteRead (found
  via a 32,484-of-42,560-byte sticker). Downloads now verify the
  byte count against Telegram's declared size, retry up to 3 times,
  and prefer `curl` (token passed via a 0600 config file, never
  argv) with a urllib fallback.

## [0.1.0] - 2026-11-12

First working version: built, mock-tested end-to-end, not yet activated
(no production token configured in this release state).

### Added
- `gateway.py` — stdlib-only long-polling Bot API inbox service.
  Messages are stored as `inbox/<update_id>/message.json` plus their
  downloaded attachments; items are staged in `tmp/` and moved into
  `inbox/` atomically.
- Media receiving: photos (largest size), documents, video, video
  notes, audio, voice, animations and stickers are downloaded via
  `getFile` and listed in the record with kind, path, mime type and
  size. Failed downloads are recorded (`download_error`), never fatal.
- Local ASR for voice messages: sherpa-onnx v2.0.1 + Paraformer
  bilingual zh-en (int8), the same stack/invocation as the tgmcp
  deployment; transcript stored in the record. Reproduced by
  `scripts/setup_asr.sh`.
- `send.py` — reply path (`sendMessage`, auto-splits at 4096 chars).
- `process_inbox.py` — wake-to-answer helper: `list` / `show` / `ack`
  (ack moves handled items to `processed/`).
- Retention cleanup (`cleanup.py`, also run by the gateway at startup
  and hourly): `tmp/` older than 24 h and `processed/` older than
  7 days are removed; configurable in `config.json`.
- Allowlist trust model: empty allowlist records everything as
  untrusted; allowlisted chats are marked `trusted`.
- systemd user unit (shipped, not installed/enabled by default).
- Mock Bot API test suite (`tests/mock_test.py`), 14 checks, no real
  token or network needed.
- Docs: architecture, activation runbook, ASR setup, security notes.
