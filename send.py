#!/usr/bin/env python3
"""
Send a Telegram message through the gateway bot (Muse's reply path).

Usage:
    python3 send.py <chat_id> <text>
    python3 send.py <chat_id> --file <path-to-text-file>

Reads BOT_TOKEN (and optional TELEGRAM_API_BASE) from the same .env as
gateway.py. Prints the Bot API JSON response to stdout; exits non-zero
on error. Standard library only.

Note: Telegram bots can only message chats that have already talked to
the bot (or group chats the bot is in). Long texts are split into
<=4096-character chunks automatically.
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gateway import check_token, load_env  # noqa: E402

MAX_LEN = 4096


def send_message(token: str, api_base: str, chat_id: int, text: str) -> dict:
    url = f"{api_base.rstrip('/')}/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if not body.get("ok"):
        raise RuntimeError(f"sendMessage failed: {body}")
    return body["result"]


def main() -> int:
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    try:
        chat_id = int(args[0])
    except ValueError:
        print(f"ERROR: chat_id must be an integer, got {args[0]!r}", file=sys.stderr)
        return 2
    if args[1] == "--file":
        if len(args) < 3:
            print("ERROR: --file needs a path", file=sys.stderr)
            return 2
        text = Path(args[2]).read_text(encoding="utf-8")
    else:
        text = args[1]

    env = load_env()
    token = env.get("BOT_TOKEN", "")
    err = check_token(token)
    if err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 2
    api_base = env.get("TELEGRAM_API_BASE", "https://api.telegram.org")

    chunks = [text[i:i + MAX_LEN] for i in range(0, len(text), MAX_LEN)] or [""]
    try:
        for chunk in chunks:
            result = send_message(token, api_base, chat_id, chunk)
            print(json.dumps(result, ensure_ascii=False))
    except urllib.error.HTTPError as exc:
        print(f"ERROR: HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
