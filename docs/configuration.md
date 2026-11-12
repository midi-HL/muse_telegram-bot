# Configuration reference

Two files configure the gateway, both in the project root:

| File | Holds | Committed? |
|---|---|---|
| `.env` | Secrets and the API endpoint (`BOT_TOKEN`, `TELEGRAM_API_BASE`) | **Never** — gitignored, chmod 600 |
| `config.json` | Behaviour: allowlist, ASR, polling, retention | No — copy `config.example.json` and edit |

Changes to either file take effect on the next service start
(`systemctl --user restart telegram-bot-gateway.service`, or just
restart `gateway.py`).

## `.env`

Format: one `KEY=VALUE` per line, `#` comments. Real environment
variables with the same name override the file.

### `BOT_TOKEN` — required

| | |
|---|---|
| Type | string, format `<bot-id>:<secret>` |
| Default | none — the gateway refuses to start without it |
| Example | `BOT_TOKEN=123456789:AAE…` (yours will differ; never copy a real one into docs or chat) |

Where it comes from: [@BotFather](https://t.me/BotFather) →
`/newbot`. The gateway sanity-checks the value at startup: a
missing token, the `.env.example` placeholder, or a value that does
not look like `<id>:<secret>` is rejected with a clear error before
any polling starts. A wrong or revoked token surfaces at runtime as
a Bot API **401**, which is fatal (exit code 2) by design — see
[security.md](security.md) for handling and revocation.

### `TELEGRAM_API_BASE` — optional

| | |
|---|---|
| Type | URL, no trailing slash needed |
| Default | `https://api.telegram.org` |
| Example | `TELEGRAM_API_BASE=http://127.0.0.1:8081` |

When to change it: the public Bot API caps bot file **downloads at
20 MB**. A [self-hosted Bot API server](https://github.com/tdlib/telegram-bot-api)
removes that cap. Run one (or reuse an existing one), point this
variable at it, restart. Nothing else changes — `gateway.py` and
`send.py` both read the same variable.

## `config.json`

Copy `config.example.json` to `config.json`. The file is optional:
if it is missing, and for any key it omits, the defaults below
apply.

### `allowlist_chat_ids`

| | |
|---|---|
| Type | array of integers (Telegram chat ids) |
| Default | `[]` |
| Example | `"allowlist_chat_ids": [123456789]` |

Effect: messages from these chats are marked `"trusted": true` in
their `message.json`; everything else is recorded but untrusted.
**Empty = trust nothing** — start empty, send the bot one message,
read your chat id from the resulting inbox record
(`process_inbox.py list`), add it here, restart. See
[security.md](security.md) for why this flag matters.

### `transcribe_voice`

| | |
|---|---|
| Type | boolean |
| Default | `true` |
| Example | `"transcribe_voice": false` |

Effect: on = voice notes are transcribed locally by
`sherpa-onnx` (see [asr.md](asr.md)) and the text is stored as
`transcript` in the record. Off = voice files are still downloaded
and stored, just not transcribed. Turn it off if you skipped the
ASR install — a missing model otherwise shows up per-message as
`transcript_error` (recorded, never fatal).

### `asr_threads`

| | |
|---|---|
| Type | integer ≥ 1 |
| Default | `2` |
| Example | `"asr_threads": 4` |

Effect: `--num-threads` passed to the sherpa-onnx recogniser.
Raise it on a multi-core box for faster transcription; keep it low
(1–2) on small/shared machines.

### `poll_timeout_seconds`

| | |
|---|---|
| Type | integer (seconds) |
| Default | `50` |
| Example | `"poll_timeout_seconds": 30` |

Effect: the `getUpdates` long-poll timeout — how long each poll
waits for new messages before returning empty and polling again.
Higher = fewer idle requests, slightly slower reaction to a
stopped service; 50 is a good default. This does **not** control
how fast the agent answers — that is the watcher's interval
(1 minute, see [architecture.md](architecture.md)).

### `tmp_retention_hours`

| | |
|---|---|
| Type | integer (hours) |
| Default | `24` |
| Example | `"tmp_retention_hours": 48` |

Effect: files in `tmp/` (staging area for items being assembled)
older than this are deleted by the retention cleanup, which runs
at gateway startup, hourly while it runs, and standalone via
`cleanup.py`.

### `processed_retention_days`

| | |
|---|---|
| Type | integer (days) |
| Default | `7` |
| Example | `"processed_retention_days": 30` |

Effect: items the agent has acked live in `processed/`; entries
older than this are deleted by the same cleanup. Tighten it if the
deployment is sensitive (processed items contain real message
content and attachments), loosen it if you want a longer local
history.

### Full example

```json
{
  "allowlist_chat_ids": [123456789],
  "transcribe_voice": true,
  "asr_threads": 2,
  "poll_timeout_seconds": 50,
  "tmp_retention_hours": 24,
  "processed_retention_days": 7
}
```

## BotFather-side settings

One BotFather setting changes what the gateway can receive:

- **Privacy mode** (`/mybots` → your bot → Bot Settings → Group
  Privacy, or `/setprivacy`): **on by default**. With privacy mode
  on, a bot in a *group* only receives commands, replies to its own
  messages, and messages that mention it — regular group chatter is
  never delivered, so it never reaches the inbox. Private (1-to-1)
  chats are unaffected and always deliver everything. If you add
  the bot to a group and want the gateway to receive all group
  messages, disable privacy mode — and think first: group members'
  messages would then land in the inbox too, still governed by the
  chat-id allowlist (allowlist the *group's* chat id to trust it).
