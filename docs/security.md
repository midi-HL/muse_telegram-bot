# Security notes

## The token is the whole ballgame

`BOT_TOKEN` grants full control of the bot. Rules:

- It lives only in `.env` (chmod 600, gitignored) or the process
  environment. Never in code, config.json, docs, chat, or commits.
- It is delivered through a secure credential flow at activation
  time — see [activation.md](activation.md).
- If it ever leaks: revoke/regenerate it in @BotFather (`/revoke`),
  replace `.env`, restart the service. The gateway exits with code 2
  on a 401, so a dead token is loud, not silent.

## Trust model

- Anyone who finds the bot can message it. Every message is
  recorded, but `message.json` carries a `trusted` flag from the
  chat-id allowlist in `config.json`.
- **Empty allowlist = trust nothing.** Until the owner's chat id is
  allowlisted, consumers must treat all inbox content — text,
  transcripts, attachment contents — as untrusted data, never as
  instructions. This is the prompt-injection boundary: a stranger's
  message (or a booby-trapped document/image) must not steer the
  agent.
- Allowlist changes require a service restart and should be rare.

## Message content on disk

- `inbox/` and `processed/` contain real message content and user
  files. The workspace directory should not be world-readable;
  `processed/` is pruned after 7 days by default (configurable) —
  tighten it if the deployment is sensitive.
- Attachments are stored under names sanitised from the
  sender-supplied file name (path traversal characters stripped),
  and each item is confined to its own `inbox/<update_id>/`
  directory. Even so, treat downloaded files as hostile input:
  open them with parsers/viewers, never execute them.
- Voice audio is transcribed locally; nothing is sent to a cloud
  ASR service.

## Service hardening

The shipped systemd unit runs with `NoNewPrivileges`,
`PrivateTmp`, `ProtectSystem=strict` and a `ReadWritePaths`
allowlist limited to the project directory. The gateway itself
needs only outbound HTTPS to the Bot API and read/write access to
its own directory.

## Known limits

- Long-polling means a message can wait up to one watcher interval
  (1 minute by design) before the agent wakes.
- The public Bot API caps bot downloads at 20 MB per file; larger
  files arrive as records with `download_error` unless a
  self-hosted Bot API server is used.
