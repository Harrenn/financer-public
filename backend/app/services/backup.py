from __future__ import annotations

import hashlib
import shutil
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import BACKUP_DIR, BACKUP_RETENTION, DB_PATH
from ..db import engine
from ..models import BackupSnapshot


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_backup_snapshot(db: Session) -> BackupSnapshot:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.exists():
        raise FileNotFoundError("Database file does not exist yet")

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"finance_{timestamp}.db"
    shutil.copy2(DB_PATH, backup_path)

    snapshot = BackupSnapshot(
        path=str(backup_path),
        size_bytes=backup_path.stat().st_size,
        sha256=_sha256(backup_path),
    )
    db.add(snapshot)
    db.flush()

    snapshots = db.scalars(select(BackupSnapshot).order_by(BackupSnapshot.created_at.desc())).all()
    for stale in snapshots[BACKUP_RETENTION:]:
        stale_path = Path(stale.path)
        if stale_path.exists():
            stale_path.unlink()
        db.delete(stale)

    return snapshot


def restore_snapshot(db: Session, snapshot_id: int) -> BackupSnapshot:
    snapshot = db.get(BackupSnapshot, snapshot_id)
    if not snapshot:
        raise ValueError("Backup snapshot not found")

    source = Path(snapshot.path)
    if not source.exists():
        raise FileNotFoundError("Snapshot file no longer exists")

    engine.dispose()
    shutil.copy2(source, DB_PATH)
    return snapshot
