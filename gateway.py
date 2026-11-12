#!/usr/bin/env python3
"""
Telegram bot gateway for Muse — long-polling inbox service.

What it does
------------
- Long-polls the Telegram Bot API (getUpdates) using ONLY the Python
  standard library (urllib). No third-party packages.
- Every incoming message becomes one directory in inbox/:

      inbox/<update_id>/message.json     the message record
      inbox/<update_id>/<attachment>     downloaded files (photo,
                                         document, video, audio,
                                         voice, sticker, ...)

  The record lists every attachment with its kind, stored path, mime
  type and size, so the consumer (Muse, woken by a watcher) can open
  and read them — images are meant to be passed to the agent run as
  multimodal input.
- Voice messages are additionally transcribed locally with sherpa-onnx
  via transcribe.py; the transcript is stored in the record.
- Items are built inside tmp/ and renamed into inbox/ atomically, so a
  watcher never sees a half-written item.
- Trust model: config.json holds an allowlist of chat ids. Messages
  from allowlisted chats are marked "trusted": true. If the allowlist
  is EMPTY, every message is recorded but marked "trusted": false —
  the consumer must treat the content as untrusted data, never as
  instructions from the user.
- Retention: tmp/ scratch older than `tmp_retention_hours` (default
  24) and processed/ items older than `processed_retention_days`
  (default 7) are removed by run_cleanup() — at startup and hourly.
  The same cleanup can be run manually via cleanup.py.

Configuration
-------------
- .env (next to this file, chmod 600):
      BOT_TOKEN=<token from BotFather>        (required)
      TELEGRAM_API_BASE=https://api.telegram.org   (optional override;
          point it at a self-hosted Bot API server if desired)
- config.json (next to this file); see config.example.json:
      allowlist_chat_ids, poll_timeout_seconds, transcribe_voice,
      asr_threads, tmp_retention_hours, processed_retention_days

File size limits: the public Bot API (api.telegram.org) lets bots
download files up to 20 MB. A self-hosted Bot API server (via
TELEGRAM_API_BASE) lifts that limit. Oversized/failed downloads are
recorded in the record's attachment entry ("download_error") and
never crash the poll loop.

State: state/offset.json persists the getUpdates offset across
restarts so no update is processed twice after a crash.

Exit codes: 0 clean shutdown (SIGINT/SIGTERM), 2 configuration/token
problem (systemd should NOT restart-loop silently — check the log).
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
CONFIG_FILE = BASE_DIR / "config.json"
INBOX_DIR = BASE_DIR / "inbox"
PROCESSED_DIR = BASE_DIR / "processed"
TMP_DIR = BASE_DIR / "tmp"
STATE_DIR = BASE_DIR / "state"
OFFSET_FILE = STATE_DIR / "offset.json"
TRANSCRIBE_SCRIPT = BASE_DIR / "transcribe.py"
RECORD_NAME = "message.json"

PLACEHOLDER_HINTS = ("PASTE", "HERE", "YOUR_TOKEN", "XXX")

DEFAULT_CONFIG = {
    "allowlist_chat_ids": [],
    "poll_timeout_seconds": 50,
    "transcribe_voice": True,
    "asr_threads": 2,
    "tmp_retention_hours": 24,
    "processed_retention_days": 7,
}


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {msg}", flush=True)


def load_env() -> dict:
    """Minimal .env parser (KEY=VALUE per line, # comments)."""
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"').strip("'")
    # Real environment variables win over the file.
    for key in ("BOT_TOKEN", "TELEGRAM_API_BASE"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    cfg["allowlist_chat_ids"] = [int(x) for x in cfg["allowlist_chat_ids"]]
    return cfg


def check_token(token: str) -> str | None:
    """Return an error message if the token looks unconfigured/invalid."""
    if not token:
        return f"BOT_TOKEN is not set. Put the BotFather token into {ENV_FILE} (see README)."
    if any(h in token for h in PLACEHOLDER_HINTS):
        return (
            "BOT_TOKEN is still the placeholder. Replace it in "
            f"{ENV_FILE} with the real BotFather token (see README)."
        )
    if ":" not in token or len(token) < 20:
        return "BOT_TOKEN does not look like a Telegram bot token (expected '<id>:<secret>')."
    return None


class BotAPI:
    """Minimal Bot API client (stdlib urllib, POST+JSON for all calls)."""

    def __init__(self, token: str, api_base: str):
        self.token = token
        self.api_base = api_base.rstrip("/")

    def _url(self, method: str) -> str:
        return f"{self.api_base}/bot{self.token}/{method}"

    def call(self, method: str, params: dict | None = None, timeout: float = 30) -> dict:
        # Always POST with a JSON body (the Bot API accepts POST for every
        # method; this also keeps behaviour identical against self-hosted
        # Bot API servers and test doubles).
        data = json.dumps(params or {}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        req = urllib.request.Request(self._url(method), data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if not body.get("ok"):
            raise RuntimeError(f"Bot API {method} failed: {body}")
        return body["result"]

    def get_updates(self, offset: int | None, poll_timeout: int) -> list:
        params: dict = {"timeout": poll_timeout, "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        return self.call("getUpdates", params, timeout=poll_timeout + 20)

    def download_file(self, file_id: str, dest: Path,
                      expected_size: int | None = None) -> str:
        """Download a file via getFile; returns the server-side file_path.

        Up to 3 attempts. When the expected byte count is known (the size
        Telegram declared for the attachment, or the size from getFile),
        a short download raises instead of being stored silently — a
        flaky proxy closing the stream early otherwise leaves corrupt
        attachments behind with no trace of the failure.
        """
        info = self.call("getFile", {"file_id": file_id})
        file_path = info["file_path"]
        if expected_size is None:
            expected_size = info.get("file_size")
        url = f"{self.api_base}/file/bot{self.token}/{urllib.parse.quote(file_path)}"
        last_exc: Exception | None = None
        for _attempt in range(3):
            try:
                self._download_once(url, dest)
                if expected_size is not None and dest.stat().st_size != int(expected_size):
                    raise OSError(
                        f"short download: got {dest.stat().st_size} "
                        f"of {int(expected_size)} bytes")
                return file_path
            except Exception as exc:  # retried below; final attempt re-raises
                last_exc = exc
        raise last_exc if last_exc is not None else RuntimeError("download failed")

    def _download_once(self, url: str, dest: Path) -> None:
        """One download attempt, writing the response body to dest.

        Prefers curl when it is installed: behind some egress proxies a
        Python-urllib stream is cut short mid-body at a fixed byte count
        (http.client.IncompleteRead, silently swallowed by copyfileobj)
        while curl receives the full file. The URL embeds the bot
        token, so it is handed to curl through a 0600 config file,
        never on its command line. Falls back to urllib when curl is
        absent.
        """
        curl = shutil.which("curl")
        if curl:
            cfg = dest.with_suffix(".curlcfg")
            try:
                cfg.write_text(f'url = "{url}"\n', encoding="utf-8")
                cfg.chmod(0o600)
                proc = subprocess.run(
                    [curl, "-f", "-sS", "--max-time", "300", "-K", str(cfg),
                     "-o", str(dest)],
                    capture_output=True, text=True, timeout=320)
                if proc.returncode != 0:
                    raise OSError(
                        f"curl download failed: {proc.stderr.strip()[-200:]}")
                return
            finally:
                cfg.unlink(missing_ok=True)
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=300) as resp, open(dest, "wb") as out:
            shutil.copyfileobj(resp, out, length=65536)


def load_offset() -> int | None:
    try:
        return int(json.loads(OFFSET_FILE.read_text(encoding="utf-8"))["offset"])
    except Exception:
        return None


def save_offset(offset: int) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    tmp = OFFSET_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"offset": offset}), encoding="utf-8")
    tmp.replace(OFFSET_FILE)


def transcribe_voice(audio_path: Path, threads: int) -> tuple[str | None, str | None]:
    """Run transcribe.py; return (text, error)."""
    try:
        proc = subprocess.run(
            [sys.executable, str(TRANSCRIBE_SCRIPT), str(audio_path), "--threads", str(threads)],
            capture_output=True, text=True, timeout=600,
        )
        if proc.returncode != 0:
            return None, (proc.stderr or proc.stdout).strip()[-500:]
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        return result.get("text"), None
    except Exception as exc:  # ASR must never kill the gateway
        return None, str(exc)


# --------------------------------------------------------------------------
# Attachments
# --------------------------------------------------------------------------

def _safe_name(name: str) -> str:
    """Make a server/user supplied file name safe to store on disk."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name or "file"


def attachment_specs(message: dict) -> list[dict]:
    """Extract downloadable attachment specs from a message.

    Each spec: {"kind", "file_id", "file_unique_id", "file_name"
    (declared, may be None), "mime_type", "file_size", "extra"}.
    Photos: Telegram sends several sizes — only the largest is kept.
    """
    specs: list[dict] = []

    def add(kind: str, obj: dict | None, extra: dict | None = None) -> None:
        if not obj or not obj.get("file_id"):
            return
        specs.append({
            "kind": kind,
            "file_id": obj["file_id"],
            "file_unique_id": obj.get("file_unique_id"),
            "file_name": obj.get("file_name"),
            "mime_type": obj.get("mime_type"),
            "file_size": obj.get("file_size"),
            "extra": extra or {},
        })

    photos = message.get("photo")
    if photos:
        def photo_rank(p: dict) -> tuple:
            return (int(p.get("file_size") or 0),
                    int(p.get("width") or 0) * int(p.get("height") or 0))
        biggest = max(photos, key=photo_rank)
        add("photo", biggest, {
            "width": biggest.get("width"),
            "height": biggest.get("height"),
            "sizes_available": len(photos),
        })

    add("document", message.get("document"))
    add("video", message.get("video"),
        {"width": message.get("video", {}).get("width"),
         "height": message.get("video", {}).get("height"),
         "duration": message.get("video", {}).get("duration")})
    add("video_note", message.get("video_note"),
        {"duration": message.get("video_note", {}).get("duration")})
    add("audio", message.get("audio"),
        {"duration": message.get("audio", {}).get("duration"),
         "title": message.get("audio", {}).get("title"),
         "performer": message.get("audio", {}).get("performer")})
    add("voice", message.get("voice"),
        {"duration": message.get("voice", {}).get("duration")})
    add("animation", message.get("animation"))
    sticker = message.get("sticker")
    if sticker:
        # Static stickers arrive as .webp images; animated/video
        # stickers as .tgs/.webm — downloaded the same way either way.
        add("sticker", sticker,
            {"is_animated": sticker.get("is_animated"),
             "is_video": sticker.get("is_video"),
             "emoji": sticker.get("emoji")})
    return specs


# --------------------------------------------------------------------------
# Retention cleanup
# --------------------------------------------------------------------------

def _remove_aged(root: Path, max_age_seconds: float, now: float) -> int:
    """Remove files/dirs directly under root older than max_age_seconds."""
    removed = 0
    if not root.exists():
        return 0
    for entry in root.iterdir():
        try:
            if now - entry.stat().st_mtime <= max_age_seconds:
                continue
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
            removed += 1
        except OSError:
            continue
    return removed


def run_cleanup(cfg: dict) -> dict:
    """Enforce the tmp/ and processed/ retention policy. Returns counts."""
    now = time.time()
    tmp_removed = _remove_aged(
        TMP_DIR, float(cfg["tmp_retention_hours"]) * 3600, now)
    processed_removed = _remove_aged(
        PROCESSED_DIR, float(cfg["processed_retention_days"]) * 86400, now)
    return {"tmp_removed": tmp_removed, "processed_removed": processed_removed}


# --------------------------------------------------------------------------
# Gateway
# --------------------------------------------------------------------------

class Gateway:
    def __init__(self, api: BotAPI, cfg: dict):
        self.api = api
        self.cfg = cfg
        self.stop = False

    def _fetch_attachment(self, spec: dict, staging: Path) -> dict:
        """Download one attachment into the staging dir; return its record."""
        entry = {
            "kind": spec["kind"],
            "file_id": spec["file_id"],
            "file_unique_id": spec["file_unique_id"],
            "mime_type": spec["mime_type"],
            "file_size": spec["file_size"],
            **{k: v for k, v in spec["extra"].items() if v is not None},
        }
        raw_path = staging / f".dl-{spec['kind']}"
        try:
            server_path = self.api.download_file(
                spec["file_id"], raw_path, expected_size=spec.get("file_size"))
            suffix = Path(server_path).suffix
            if spec["file_name"]:
                name = _safe_name(spec["file_name"])
                if suffix and not name.lower().endswith(suffix.lower()):
                    name += suffix
            else:
                name = _safe_name(Path(server_path).name) or f"{spec['kind']}{suffix}"
            dest = staging / name
            raw_path.replace(dest)
            entry["file_name"] = dest.name
            entry["path"] = dest.name  # relative to the item dir; completed later
            entry["bytes"] = dest.stat().st_size
        except Exception as exc:  # a failed download must never kill the loop
            raw_path.unlink(missing_ok=True)
            entry["download_error"] = str(exc)
        return entry

    def handle_update(self, update: dict) -> None:
        update_id = update["update_id"]
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        allowlist = self.cfg["allowlist_chat_ids"]
        # Empty allowlist => record everything, trust nothing.
        trusted = bool(allowlist) and chat_id in allowlist

        for d in (INBOX_DIR, TMP_DIR):
            d.mkdir(exist_ok=True)
        item_dir = INBOX_DIR / str(update_id)
        if item_dir.exists():
            log(f"update {update_id} already in inbox, skipping")
            return
        staging = TMP_DIR / f"item-{update_id}"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)

        record = {
            "update_id": update_id,
            "received_at": datetime.now(timezone.utc).isoformat(),
            "trusted": trusted,
            "chat_id": chat_id,
            "text": message.get("text") or message.get("caption"),
            "attachments": [],
            "message": message,
        }

        voice_entry = None
        for spec in attachment_specs(message):
            entry = self._fetch_attachment(spec, staging)
            if "path" in entry:
                entry["path"] = f"inbox/{update_id}/{entry['file_name']}"
            record["attachments"].append(entry)
            if spec["kind"] == "voice":
                voice_entry = entry

        if voice_entry is not None:
            record["voice_duration"] = voice_entry.get("duration")
            if "path" in voice_entry:
                record["voice_file"] = voice_entry["file_name"]
                if self.cfg["transcribe_voice"]:
                    text, err = transcribe_voice(
                        staging / voice_entry["file_name"],
                        int(self.cfg["asr_threads"]),
                    )
                    if text is not None:
                        record["transcript"] = text
                    else:
                        record["transcript_error"] = err
            elif "download_error" in voice_entry:
                record["voice_download_error"] = voice_entry["download_error"]

        # Write the record last, then atomically move the whole item
        # into the inbox so a watcher never reads a half-written item.
        (staging / RECORD_NAME).write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        staging.replace(item_dir)
        kinds = [a["kind"] for a in record["attachments"]] or ["text"]
        log(f"saved update {update_id} (chat {chat_id}, trusted={trusted}, {','.join(kinds)})")

    def run(self) -> None:
        offset = load_offset()
        backoff = 2
        last_cleanup = 0.0
        while not self.stop:
            try:
                updates = self.api.get_updates(offset, int(self.cfg["poll_timeout_seconds"]))
                backoff = 2
                for update in updates:
                    self.handle_update(update)
                    offset = int(update["update_id"]) + 1
                    save_offset(offset)
                if time.time() - last_cleanup > 3600:
                    counts = run_cleanup(self.cfg)
                    last_cleanup = time.time()
                    if counts["tmp_removed"] or counts["processed_removed"]:
                        log(f"cleanup: {counts}")
            except urllib.error.HTTPError as exc:
                if exc.code == 401:
                    log("FATAL: Bot API returned 401 Unauthorized — token is wrong or revoked.")
                    sys.exit(2)
                log(f"HTTP error {exc.code}; retrying in {backoff}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
            except Exception as exc:
                log(f"poll error: {exc}; retrying in {backoff}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)


def main() -> int:
    env = load_env()
    token = env.get("BOT_TOKEN", "")
    err = check_token(token)
    if err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 2
    cfg = load_config()
    api = BotAPI(token, env.get("TELEGRAM_API_BASE", "https://api.telegram.org"))

    for d in (INBOX_DIR, PROCESSED_DIR, TMP_DIR, STATE_DIR):
        d.mkdir(exist_ok=True)
    counts = run_cleanup(cfg)
    if counts["tmp_removed"] or counts["processed_removed"]:
        log(f"startup cleanup: {counts}")

    me = api.call("getMe")
    log(f"gateway starting as @{me.get('username')} (id {me.get('id')}); "
        f"allowlist={cfg['allowlist_chat_ids'] or 'EMPTY (all untrusted)'}")

    gw = Gateway(api, cfg)

    def _stop(signum, frame):  # noqa: ARG001
        log(f"signal {signum} received, shutting down")
        gw.stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    gw.run()
    log("gateway stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
