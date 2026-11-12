# Activation runbook

The gateway ships **not activated**: code, tests and docs are ready,
but no production token is configured and the service is not running.
Activation is a deliberate, separate step.

## 1. Create the bot and get a token

1. In Telegram, talk to [@BotFather](https://t.me/BotFather):
   `/newbot`, pick a name and a username ending in `bot`.
2. BotFather replies with the token (`<bot-id>:<secret>`).
3. Deliver the token **through the secure credential flow — never
   paste it into chat, a ticket, or a commit**. Write it into `.env`:

   ```ini
   BOT_TOKEN=<token>
   ```

   Keep `.env` at chmod 600. It is gitignored.

## 2. Sanity-check in the foreground

```bash
cd telegram-bot-gateway
python3 gateway.py
# expect: gateway starting as @<yourbot> (id ...); allowlist=EMPTY ...
```

Send the bot a text message, a photo and a voice note from your
phone; watch `inbox/` gain one directory per message. Ctrl-C stops
the gateway.

## 3. Install the systemd service

```bash
cp telegram-bot-gateway.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now telegram-bot-gateway.service
systemctl --user status telegram-bot-gateway.service
```

## 4. Create the wake-up watcher

Create a 1-minute cron/job that runs
`python3 process_inbox.py list --json` and, when the output is
non-empty, wakes the agent with it. The exact flow is specified in
[architecture.md](architecture.md#wake-to-answer-flow-for-the-watcher-cron).

## 5. Allowlist yourself

After your first message lands in `inbox/`, read its `chat_id` from
the record (or from `process_inbox.py list`), put it into
`config.json` → `allowlist_chat_ids`, and restart the service:

```bash
systemctl --user restart telegram-bot-gateway.service
```

From then on your messages are marked `"trusted": true`; everyone
else's are recorded but untrusted.

## 6. Optional: self-hosted Bot API

To lift the 20 MB download limit of the public Bot API, run (or
reuse) a self-hosted Bot API server and set in `.env`:

```ini
TELEGRAM_API_BASE=http://<host>:<port>
```

No code changes are needed; restart the service afterwards.

## Rollback

```bash
systemctl --user disable --now telegram-bot-gateway.service
```

Inbox data and state stay on disk; nothing is lost by stopping.
