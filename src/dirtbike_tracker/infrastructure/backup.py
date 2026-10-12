"""
Off-box backup of the SQLite DB + state files to Google Drive via rclone.

Reuses job-agent's `jobbackup` rclone remote (drive.file scope on a dedicated
Google account: it can only see files it created). Named slots overwritten in
place, so there is no retention logic to rot. Not encrypted: the bundle holds
public listing data only; secrets (ntfy topic, healthcheck URL) live in the
crontab, which is not backed up.
"""

import json
import os
import sqlite3
import subprocess  # nosec B404 - fixed argv, no shell
import tarfile
import tempfile
from pathlib import Path

from .storage import DB_PATH

REMOTE = os.environ.get("BACKUP_REMOTE", "jobbackup:dirtbike-tracker-backups")


class BackupError(Exception):
    """Backup did not complete; the remote slot was not (fully) updated."""


def _rclone(*args: str) -> str:
    try:
        result = subprocess.run(  # nosec B603 B607 - fixed argv, rclone from PATH
            ["rclone", *args], capture_output=True, text=True, timeout=300, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackupError(f"rclone {args[0]} could not run: {exc}") from exc
    if result.returncode != 0:
        raise BackupError(f"rclone {args[0]} failed: {result.stderr.strip()[-300:]}")
    return result.stdout


def backup_offsite(
    slot: str, state_dir: Path, db_path: Path = DB_PATH, remote: str = REMOTE
) -> tuple[int, int]:
    """Snapshot the DB, bundle it with state files, upload to <remote>/<slot>.tar.gz.

    Returns (listing rows, bundle bytes). Raises BackupError on any failure,
    including an empty snapshot: a 0-row DB must never overwrite a good slot.
    """
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "listings.db"
        # sqlite backup API: consistent copy even while cron is writing
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(snap)
        try:
            src.backup(dst)
            # Row count, not quick_check: quick_check passes on an empty file
            rows = dst.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        except sqlite3.Error as exc:
            raise BackupError(f"snapshot of {db_path} failed: {exc}") from exc
        finally:
            src.close()
            dst.close()
        if rows == 0:
            raise BackupError(f"snapshot of {db_path} has 0 listings; slot '{slot}' left untouched")

        bundle = Path(tmp) / f"{slot}.tar.gz"
        with tarfile.open(bundle, "w:gz") as tar:
            tar.add(snap, arcname="listings.db")
            for f in sorted(state_dir.glob("*")):
                tar.add(f, arcname=f"state/{f.name}")
        size = bundle.stat().st_size

        target = f"{remote}/{slot}.tar.gz"
        _rclone("copyto", str(bundle), target)
        # rclone exiting 0 isn't proof the object landed whole
        remote_size = json.loads(_rclone("size", "--json", target)).get("bytes")
        if remote_size != size:
            raise BackupError(
                f"remote size mismatch for {target}: local {size}, remote {remote_size}"
            )
    return rows, size
