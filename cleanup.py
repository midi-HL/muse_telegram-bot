#!/usr/bin/env python3
"""
Retention cleanup for the Telegram bot gateway.

Removes:
  * tmp/ entries older than `tmp_retention_hours` (default 24) —
    scratch space: in-progress downloads and item staging dirs.
  * processed/ items older than `processed_retention_days`
    (default 7) — messages the agent has already handled.

The gateway service runs the same cleanup itself at startup and then
hourly; this script exists so it can also be run manually or from
cron. Policy values come from config.json (see config.example.json).

Usage:  python3 cleanup.py [--dry-run]
Standard library only.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gateway import TMP_DIR, PROCESSED_DIR, load_config, run_cleanup  # noqa: E402


def main() -> int:
    cfg = load_config()
    if "--dry-run" in sys.argv:
        import time
        now = time.time()
        for root, hours in ((TMP_DIR, cfg["tmp_retention_hours"] * 3600),
                            (PROCESSED_DIR, cfg["processed_retention_days"] * 86400)):
            if not root.exists():
                continue
            for entry in sorted(root.iterdir()):
                age_h = (now - entry.stat().st_mtime) / 3600
                if age_h * 3600 > hours:
                    print(f"would remove {entry} (age {age_h:.1f} h)")
        return 0
    counts = run_cleanup(cfg)
    print(f"cleanup done: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
