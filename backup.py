"""Consistent SQLite backups (NTF-029).

Uses SQLite's own online backup API, which takes a correct copy even while
the bot is running and writing. Backups go to NTF_BACKUP_DIR (default: a
"backups" folder next to the database) and the newest NTF_BACKUP_KEEP
(default 30) are kept per label.

    python3 backup.py            # daily backup, run by the systemd timer in deploy/
                                 # (by hand, set PRO_CLUBS_DB_PATH first - see deploy/ORACLE.md)
    python3 backup.py --verify   # also opens the newest backup and checks it

The bot also calls make_backup("pre-season-reset") before every season reset.
Backups hold player data, so the folder is git-ignored and never committed."""

import datetime
import logging
import os
import sqlite3
import sys

import config

log = logging.getLogger("ntf")


def backup_dir() -> str:
    default = os.path.join(os.path.dirname(os.path.abspath(config.DB_PATH)), "backups")
    return os.environ.get("NTF_BACKUP_DIR", default)


def keep_count() -> int:
    try:
        return max(1, int(os.environ.get("NTF_BACKUP_KEEP", "30")))
    except ValueError:
        return 30


def verify(path: str) -> bool:
    """True if the backup opens and passes SQLite's integrity check."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def make_backup(label: str = "daily") -> str:
    """Copies the live database to a timestamped file, checks the copy and
    removes the oldest copies with the same label past the keep count.
    Returns the new file's path. Raises if the copy fails or doesn't verify."""
    if not os.path.exists(config.DB_PATH):
        # never "back up" by creating an empty database somewhere else
        raise FileNotFoundError(
            f"no database at {config.DB_PATH} - set PRO_CLUBS_DB_PATH (on the server it's in /etc/ntf-bot.env)"
        )
    folder = backup_dir()
    os.makedirs(folder, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(folder, f"ntf-{label}-{stamp}.db")
    src = sqlite3.connect(config.DB_PATH)
    try:
        out = sqlite3.connect(dest)
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()
    if not verify(dest):
        raise RuntimeError(f"backup {dest} failed its integrity check")
    mine = sorted(f for f in os.listdir(folder) if f.startswith(f"ntf-{label}-") and f.endswith(".db"))
    for old in mine[:-keep_count()]:
        try:
            os.remove(os.path.join(folder, old))
        except OSError:
            log.warning("could not remove old backup %s", old)
    log.info("database backup written: %s", dest)
    return dest


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    path = make_backup("daily")
    if "--verify" in sys.argv:
        print("verified" if verify(path) else "FAILED")
    print(path)
