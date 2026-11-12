# Architecture

## Overview

```
Telegram user ──► Bot API (api.telegram.org, or self-hosted)
                      ▲   │
        getUpdates    │   │ sendMessage
        (long-poll)   │   ▼
                  gateway.py ──► tmp/item-<id>/  (staging)
                      │              │ atomic rename when complete
                      │              ▼
                      │         inbox/<update_id>/
                      │           ├── message.json
                      │           └── <attachments…>
                      │              │
                      │              ▼
                      │      watcher (cron, 1 min) runs
                      │      process_inbox.py list ──► wakes the agent
                      │              │
                      │              ▼
                      │      agent reads text / transcript /
                      │      attachments (images as multimodal input),
                      │      answers via send.py, then
                      │      process_inbox.py ack <id>
                      ▼              │
              state/offset.json      ▼
                              processed/<update_id>/
                              (kept 7 days, then pruned)

Voice notes: downloaded like any attachment, then transcribed
locally by transcribe.py (sherpa-onnx) before the item is moved
into inbox/; the transcript lands in message.json.
```

## Components

| File | Role |
|---|---|
| `gateway.py` | Long-polling service. Only writer of `inbox/`, `tmp/`, `state/`. |
| `send.py` | Reply CLI used by the agent (`sendMessage`). |
| `process_inbox.py` | Agent-side inbox interface: `list` / `show` / `ack`. |
| `cleanup.py` | Retention enforcement for `tmp/` and `processed/` (also run inside `gateway.py`). |
| `transcribe.py` | ASR wrapper around the sherpa-onnx runtime. |
| `config.json` | Allowlist, poll timeout, ASR options, retention policy. |
| `.env` | `BOT_TOKEN`, optional `TELEGRAM_API_BASE`. chmod 600, gitignored. |

## Data flow & invariants

1. **Offset first.** The getUpdates offset is persisted to
   `state/offset.json` after every update, so restarts never
   re-deliver or drop messages.
2. **Atomic inbox items.** An item is assembled in
   `tmp/item-<update_id>/` (record written last) and renamed to
   `inbox/<update_id>/` in one operation. A watcher only ever sees
   complete items.
3. **An inbox item is unprocessed by definition.** It stays in
   `inbox/` until the agent explicitly acks it
   (`process_inbox.py ack`), which moves it to `processed/`.
   Nothing auto-acks: if the agent crashes mid-answer, the item is
   still there on the next wake.
4. **Failures are data, not crashes.** A failed attachment download
   is recorded in the item's JSON (`download_error`); a failed
   transcription is recorded as `transcript_error`. The poll loop
   only exits on a 401 (bad/revoked token, exit code 2) or a signal.
5. **Trust is explicit.** `trusted` in the record comes from the
   chat-id allowlist in `config.json`. Empty allowlist = everything
   recorded, nothing trusted. Consumers must treat untrusted content
   as data, never as instructions.

## Wake-to-answer flow (for the watcher cron)

```
every 1 minute:
    out = process_inbox.py list --json
    if out is empty: exit quietly
    wake the agent with the list output
    agent, per item (oldest first):
        read text / transcript; open attachments
          (images: pass the file to the run as multimodal input;
           documents: read/convert as needed)
        compose the reply; send it:  send.py <chat_id> "<text>"
        only after the reply is sent:  process_inbox.py ack <id>
```

## File-size reality

The public Bot API (`api.telegram.org`) allows bots to **download
files up to 20 MB**. Larger files fail `getFile` and are recorded
with `download_error`. Pointing `TELEGRAM_API_BASE` at a self-hosted
Bot API server lifts that limit (self-hosted servers can serve local
files of any size); the gateway code is identical either way.

## Limitations (design-level summary)

Kept in agreement with the README's Limitations section:

- **Downloads cap at 20 MB** on `api.telegram.org`; a self-hosted
  Bot API (`TELEGRAM_API_BASE`) lifts it — see "File-size reality"
  above.
- **Wake latency up to ~1 minute**: the watcher polls the inbox
  once a minute; polling Telegram itself is near-real-time, the
  delay is in waking the agent.
- **ASR quality is bounded**: a small on-device Paraformer model —
  occasional errors on mixed Chinese/English, worse with noise.
  Transcription is local only; nothing goes to a cloud ASR.
- **One poller per bot**: a second gateway instance on the same
  token gets `409 Conflict`; the design assumes exactly one
  writer of `inbox/`.
- **Platform**: Python 3.10+ stdlib only (downloads prefer `curl`
  when installed, urllib fallback); the ASR runtime binaries
  are linux-x86_64 and need `ffmpeg` on PATH.
- **License**: CC BY-NC-SA 4.0 — no commercial use.

## Storage lifecycle

| Dir | Content | Retention |
|---|---|---|
| `inbox/` | unprocessed items | until acked |
| `processed/` | handled items | 7 days (configurable) |
| `tmp/` | staging + scratch | 24 h (configurable) |
| `state/` | getUpdates offset | permanent (tiny) |
