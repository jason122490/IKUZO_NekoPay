"""Consistent SQLite backup (works under WAL) with rotation.

Run on the host via cron, e.g. daily:
    docker compose exec app python scripts/backup.py
Keeps the most recent BACKUP_KEEP (default 14) copies in BACKUP_DIR.
"""
from __future__ import annotations

import glob
import os
import sqlite3
from datetime import datetime

SRC = os.environ.get("BACKUP_SRC", "data/nekopay.db")
OUT_DIR = os.environ.get("BACKUP_DIR", "data/backups")
KEEP = int(os.environ.get("BACKUP_KEEP", "14"))


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(OUT_DIR, f"nekopay-{stamp}.db")

    source = sqlite3.connect(SRC)
    backup = sqlite3.connect(dst)
    with backup:
        source.backup(backup)  # online backup API: consistent even under WAL
    backup.close()
    source.close()

    files = sorted(glob.glob(os.path.join(OUT_DIR, "nekopay-*.db")), reverse=True)
    for stale in files[KEEP:]:
        os.remove(stale)
    print(f"backup -> {dst} (kept {min(len(files), KEEP)})")


if __name__ == "__main__":
    main()
