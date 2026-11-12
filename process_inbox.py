#!/usr/bin/env python3
"""
Wake-to-answer helper for the Telegram bot gateway.

This is the interface between the inbox on disk and the agent (Muse)
that answers the messages. The future 1-minute watcher cron uses it
like this:

    1. `python3 process_inbox.py list`     -> what's waiting, in order
    2. the agent reads each item (text / transcript / attachments,
       opening image attachments as multimodal input)
    3. the agent answers via send.py
    4. `python3 process_inbox.py ack <update_id>`  -> ONLY after the
       item has been fully handled; moves inbox/<id>/ to processed/<id>/

Commands
--------
    list [--json]     list unprocessed items, oldest first, with a
                      summary per item (text, transcript, attachments)
    show <update_id>  print the full message.json of one inbox item
    ack <update_id>   mark one item processed (move to processed/)
    ack --all         mark every inbox item processed (use with care)

An item is "unprocessed" exactly while its directory lives in inbox/.
Nothing here deletes message content: ack is a move, and processed/
is pruned by the retention policy (see cleanup.py / gateway config).

Standard library only. Exit codes: 0 ok, 1 item not found, 2 usage.
"""

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
INBOX_DIR = BASE_DIR / "inbox"
PROCESSED_DIR = BASE_DIR / "processed"
RECORD_NAME = "message.json"


def iter_items() -> list[tuple[int, Path, dict]]:
    """All inbox items as (update_id, dir, record), oldest first."""
    items = []
    if not INBOX_DIR.exists():
        return items
    for entry in INBOX_DIR.iterdir():
        record_file = entry / RECORD_NAME
        if not entry.is_dir() or not record_file.exists():
            continue
        try:
            record = json.loads(record_file.read_text(encoding="utf-8"))
            update_id = int(record.get("update_id") or entry.name)
        except Exception:
            continue
        items.append((update_id, entry, record))
    items.sort(key=lambda t: t[0])
    return items


def summarise(update_id: int, item_dir: Path, record: dict) -> dict:
    attachments = []
    for att in record.get("attachments", []):
        attachments.append({
            "kind": att.get("kind"),
            "file_name": att.get("file_name"),
            "path": str(item_dir / att["file_name"]) if att.get("file_name") else None,
            "mime_type": att.get("mime_type"),
            "bytes": att.get("bytes"),
            "download_error": att.get("download_error"),
        })
    return {
        "update_id": update_id,
        "received_at": record.get("received_at"),
        "trusted": record.get("trusted"),
        "chat_id": record.get("chat_id"),
        "text": record.get("text"),
        "transcript": record.get("transcript"),
        "transcript_error": record.get("transcript_error"),
        "attachments": attachments,
        "dir": str(item_dir),
    }


def cmd_list(as_json: bool) -> int:
    summaries = [summarise(uid, d, r) for uid, d, r in iter_items()]
    if as_json:
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
        return 0
    if not summaries:
        print("inbox empty")
        return 0
    for s in summaries:
        trust = "trusted" if s["trusted"] else "UNTRUSTED"
        print(f"== update {s['update_id']}  chat {s['chat_id']}  [{trust}]  {s['received_at']}")
        if s["text"]:
            print(f"   text: {s['text']}")
        if s["transcript"]:
            print(f"   voice transcript: {s['transcript']}")
        if s["transcript_error"]:
            print(f"   voice transcript FAILED: {s['transcript_error']}")
        for att in s["attachments"]:
            if att["download_error"]:
                print(f"   attachment [{att['kind']}] DOWNLOAD FAILED: {att['download_error']}")
            else:
                print(f"   attachment [{att['kind']}] {att['path']} "
                      f"({att['mime_type']}, {att['bytes']} bytes)")
    return 0


def cmd_show(update_id: int) -> int:
    record_file = INBOX_DIR / str(update_id) / RECORD_NAME
    if not record_file.exists():
        print(f"ERROR: no inbox item {update_id}", file=sys.stderr)
        return 1
    print(record_file.read_text(encoding="utf-8"))
    return 0


def cmd_ack(update_id: int | None, ack_all: bool) -> int:
    items = iter_items()
    if ack_all:
        targets = items
    else:
        targets = [t for t in items if t[0] == update_id]
        if not targets:
            print(f"ERROR: no inbox item {update_id}", file=sys.stderr)
            return 1
    PROCESSED_DIR.mkdir(exist_ok=True)
    for uid, item_dir, _record in targets:
        dest = PROCESSED_DIR / str(uid)
        if dest.exists():
            print(f"update {uid}: already in processed/, removing inbox copy")
            import shutil
            shutil.rmtree(item_dir)
        else:
            item_dir.replace(dest)
            print(f"update {uid}: moved to processed/")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    cmd, rest = args[0], args[1:]
    if cmd == "list":
        return cmd_list(as_json="--json" in rest)
    if cmd == "show" and len(rest) == 1:
        try:
            return cmd_show(int(rest[0]))
        except ValueError:
            pass
    if cmd == "ack":
        if rest == ["--all"]:
            return cmd_ack(None, True)
        if len(rest) == 1:
            try:
                return cmd_ack(int(rest[0]), False)
            except ValueError:
                pass
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
