#!/usr/bin/env python3
"""
Token-free end-to-end test of the gateway against a local mock Bot API.

Spins up a fake Telegram Bot API on 127.0.0.1 (stdlib http.server),
copies the gateway scripts into a temp workspace with a FAKE token,
then verifies:

  1.  text update saved as inbox/<id>/message.json, allowlist trust
  2.  voice .ogg downloaded byte-for-byte, recorded as an attachment
  3.  photo: only the largest size is downloaded; caption becomes text
  4.  document downloaded with its declared file name
  5.  a failing download is recorded (download_error) and the poll
      loop survives — the next update is still saved
  6.  state/offset.json advances past all processed updates
  7.  process_inbox.py list shows items in order with attachment paths
  8.  process_inbox.py ack moves an item to processed/
  9.  cleanup.py enforces tmp/ and processed/ retention
  10. send.py posts a correct sendMessage payload
  11. a placeholder token makes gateway.py refuse to start (exit 2)

No real token, no network access to Telegram. ASR is disabled in the
test config (transcribe_voice=false); ASR has its own real test with
the bundled model test wavs (see docs/asr.md).

Run:  python3 tests/mock_test.py     (exit 0 = all checks passed)
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent
FAKE_TOKEN = "123456789:AAFakeTokenForLocalMockTestOnly00"
ALLOWED_CHAT = 424242
OTHER_CHAT = 777

FAKE_OGG = b"OggS" + bytes(range(256)) * 4
JPG_SMALL = b"\xff\xd8small-jpg" * 10
JPG_BIG = b"\xff\xd8big-jpg" * 100
PDF_BYTES = b"%PDF-1.4 fake pdf body" * 20

FILE_PATHS = {
    "voicefileid": "voice/file_1.oga",
    "photo_small_id": "photos/file_small.jpg",
    "photo_big_id": "photos/file_big.jpg",
    "docfileid": "documents/report.pdf",
}
FILE_BYTES = {
    "voice/file_1.oga": FAKE_OGG,
    "photos/file_small.jpg": JPG_SMALL,
    "photos/file_big.jpg": JPG_BIG,
    "documents/report.pdf": PDF_BYTES,
}

sent_messages: list[dict] = []
updates_served = {"done": False}


def msg(message_id, chat_id, **extra):
    m = {
        "message_id": message_id,
        "from": {"id": chat_id, "first_name": "Tester"},
        "chat": {"id": chat_id, "type": "private"},
        "date": 1700000000 + message_id,
    }
    m.update(extra)
    return m


UPDATES = [
    {"update_id": 900001, "message": msg(1, ALLOWED_CHAT, text="hello gateway")},
    {"update_id": 900002, "message": msg(2, OTHER_CHAT, voice={
        "file_id": "voicefileid", "file_unique_id": "uv",
        "duration": 3, "mime_type": "audio/ogg", "file_size": len(FAKE_OGG)})},
    {"update_id": 900003, "message": msg(3, ALLOWED_CHAT, caption="look at this", photo=[
        {"file_id": "photo_small_id", "file_unique_id": "up1", "width": 90, "height": 90,
         "file_size": len(JPG_SMALL)},
        {"file_id": "photo_big_id", "file_unique_id": "up2", "width": 800, "height": 800,
         "file_size": len(JPG_BIG)}])},
    {"update_id": 900004, "message": msg(4, OTHER_CHAT, document={
        "file_id": "docfileid", "file_unique_id": "ud", "file_name": "report.pdf",
        "mime_type": "application/pdf", "file_size": len(PDF_BYTES)})},
    {"update_id": 900005, "message": msg(5, ALLOWED_CHAT, document={
        "file_id": "gonefileid", "file_unique_id": "ug", "file_name": "gone.bin",
        "mime_type": "application/octet-stream", "file_size": 10})},
    {"update_id": 900006, "message": msg(6, OTHER_CHAT, text="still alive?")},
]
LAST_OFFSET = 900007


class MockHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path.endswith("/getMe"):
            self._json({"ok": True, "result": {"id": 999, "is_bot": True, "username": "mockbot"}})
        elif self.path.endswith("/getUpdates"):
            if not updates_served["done"]:
                updates_served["done"] = True
                self._json({"ok": True, "result": UPDATES})
            else:
                time.sleep(0.3)
                self._json({"ok": True, "result": []})
        elif self.path.endswith("/getFile"):
            fid = payload.get("file_id")
            if fid in FILE_PATHS:
                self._json({"ok": True, "result": {
                    "file_id": fid, "file_unique_id": "u", "file_path": FILE_PATHS[fid]}})
            else:
                self._json({"ok": False, "description": "Bad Request: file not found"},
                           status=400)
        elif self.path.endswith("/sendMessage"):
            sent_messages.append(payload)
            self._json({"ok": True, "result": {"message_id": 555,
                                                "chat": {"id": payload["chat_id"]}}})
        else:
            self._json({"ok": False, "description": "unknown method"}, status=404)

    def do_GET(self):
        for path, data in FILE_BYTES.items():
            if "/file/bot" in self.path and self.path.endswith(path):
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
        self._json({"ok": False}, status=404)


def check(name: str, cond: bool, detail: str = "") -> bool:
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not cond else ""))
    return bool(cond)


def load_record(tmp: Path, update_id: int) -> dict | None:
    f = tmp / "inbox" / str(update_id) / "message.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def main() -> int:
    results = []
    server = HTTPServer(("127.0.0.1", 0), MockHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    api_base = f"http://127.0.0.1:{port}"

    tmp = Path(tempfile.mkdtemp(prefix="tbgw_test_"))
    try:
        for name in ("gateway.py", "send.py", "transcribe.py",
                     "process_inbox.py", "cleanup.py"):
            shutil.copy(SRC_DIR / name, tmp / name)
        for d in ("inbox", "processed", "tmp", "state"):
            (tmp / d).mkdir()
        (tmp / ".env").write_text(
            f"BOT_TOKEN={FAKE_TOKEN}\nTELEGRAM_API_BASE={api_base}\n", encoding="utf-8")
        (tmp / "config.json").write_text(json.dumps({
            "allowlist_chat_ids": [ALLOWED_CHAT],
            "poll_timeout_seconds": 5,
            "transcribe_voice": False,
            "asr_threads": 2,
            "tmp_retention_hours": 24,
            "processed_retention_days": 7,
        }), encoding="utf-8")

        # --- run the gateway until all updates are saved ---
        proc = subprocess.Popen(
            [sys.executable, str(tmp / "gateway.py")],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        deadline = time.time() + 40

        def all_saved():
            return all((tmp / "inbox" / str(u["update_id"]) / "message.json").exists()
                       for u in UPDATES)

        while time.time() < deadline and not all_saved():
            if proc.poll() is not None:
                break
            time.sleep(0.25)
        proc.send_signal(signal.SIGTERM)
        try:
            out, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate()

        # 1 — text item
        rec = load_record(tmp, 900001)
        results.append(check("text item saved as inbox/<id>/message.json", rec is not None,
                             out[-400:]))
        if rec:
            results.append(check("text trusted + text field set",
                                 rec.get("trusted") is True
                                 and rec.get("text") == "hello gateway"))

        # 2 — voice item
        rec = load_record(tmp, 900002)
        results.append(check("voice item saved", rec is not None))
        if rec:
            atts = rec.get("attachments", [])
            ogg_ok = False
            if atts:
                p = tmp / "inbox" / "900002" / atts[0].get("file_name", "")
                ogg_ok = p.exists() and p.read_bytes() == FAKE_OGG
            results.append(check("voice untrusted + .ogg byte-for-byte + attachment path",
                                 rec.get("trusted") is False and ogg_ok
                                 and atts and atts[0].get("path") == "inbox/900002/" + atts[0]["file_name"]))

        # 3 — photo: largest only
        rec = load_record(tmp, 900003)
        photo_ok = False
        if rec:
            atts = [a for a in rec.get("attachments", []) if a.get("kind") == "photo"]
            if len(atts) == 1:
                p = tmp / "inbox" / "900003" / atts[0]["file_name"]
                photo_ok = (p.read_bytes() == JPG_BIG
                            and atts[0].get("sizes_available") == 2)
            files = [f.name for f in (tmp / "inbox" / "900003").iterdir()]
            photo_ok = photo_ok and len(files) == 2  # message.json + one photo
        results.append(check("photo: largest size only, caption as text",
                             photo_ok and rec and rec.get("text") == "look at this"))

        # 4 — document
        rec = load_record(tmp, 900004)
        doc_ok = False
        if rec:
            atts = rec.get("attachments", [])
            if atts and atts[0].get("file_name") == "report.pdf":
                doc_ok = (tmp / "inbox" / "900004" / "report.pdf").read_bytes() == PDF_BYTES
        results.append(check("document downloaded with declared name", doc_ok))

        # 5 — failed download recorded, loop survived
        rec = load_record(tmp, 900005)
        results.append(check("failed download recorded as download_error",
                             bool(rec) and bool(rec.get("attachments"))
                             and "download_error" in rec["attachments"][0]))
        results.append(check("poll loop survived failure (next update saved)",
                             load_record(tmp, 900006) is not None))

        # 6 — offset
        offset_file = tmp / "state" / "offset.json"
        results.append(check(
            f"offset advanced to {LAST_OFFSET}",
            offset_file.exists()
            and json.loads(offset_file.read_text())["offset"] == LAST_OFFSET))

        # 7 — process_inbox list
        proc = subprocess.run(
            [sys.executable, str(tmp / "process_inbox.py"), "list", "--json"],
            capture_output=True, text=True, timeout=30)
        listing = []
        try:
            listing = json.loads(proc.stdout)
        except Exception:
            pass
        ids = [s["update_id"] for s in listing]
        photo_path_ok = False
        for s in listing:
            if s["update_id"] == 900003 and s["attachments"]:
                photo_path_ok = Path(s["attachments"][0]["path"]).exists()
        results.append(check("process_inbox list: 6 items in order + real attachment paths",
                             ids == [u["update_id"] for u in UPDATES] and photo_path_ok,
                             proc.stderr[-200:]))

        # 8 — ack moves to processed
        proc = subprocess.run(
            [sys.executable, str(tmp / "process_inbox.py"), "ack", "900001"],
            capture_output=True, text=True, timeout=30)
        results.append(check("process_inbox ack moves item to processed/",
                             proc.returncode == 0
                             and (tmp / "processed" / "900001" / "message.json").exists()
                             and not (tmp / "inbox" / "900001").exists()))

        # 9 — cleanup retention
        old = time.time() - 40 * 86400
        ancient_tmp = tmp / "tmp" / "ancient.part"
        ancient_tmp.write_bytes(b"x")
        os.utime(ancient_tmp, (old, old))
        fresh_tmp = tmp / "tmp" / "fresh.part"
        fresh_tmp.write_bytes(b"x")
        ancient_proc = tmp / "processed" / "800000"
        ancient_proc.mkdir()
        (ancient_proc / "message.json").write_text("{}", encoding="utf-8")
        os.utime(ancient_proc, (old, old))
        proc = subprocess.run(
            [sys.executable, str(tmp / "cleanup.py")],
            capture_output=True, text=True, timeout=30)
        results.append(check("cleanup removes aged tmp + processed, keeps fresh",
                             proc.returncode == 0
                             and not ancient_tmp.exists()
                             and fresh_tmp.exists()
                             and not ancient_proc.exists()
                             and (tmp / "processed" / "900001").exists(),
                             proc.stderr[-200:]))

        # 10 — send.py
        proc = subprocess.run(
            [sys.executable, str(tmp / "send.py"), str(ALLOWED_CHAT), "reply from test"],
            capture_output=True, text=True, timeout=30)
        ok_send = (proc.returncode == 0 and sent_messages
                   and sent_messages[-1].get("chat_id") == ALLOWED_CHAT
                   and sent_messages[-1].get("text") == "reply from test")
        results.append(check("send.py posts sendMessage correctly", ok_send,
                             proc.stderr[-300:]))

        # 11 — placeholder refusal
        (tmp / ".env").write_text(
            "BOT_TOKEN=__PASTE_BOT_TOKEN_HERE__\nTELEGRAM_API_BASE=https://api.telegram.org\n",
            encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(tmp / "gateway.py")],
            capture_output=True, text=True, timeout=30)
        results.append(check(
            "placeholder token refused (exit 2)",
            proc.returncode == 2 and "placeholder" in proc.stderr,
            f"rc={proc.returncode} stderr={proc.stderr[-200:]}"))
    finally:
        server.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
