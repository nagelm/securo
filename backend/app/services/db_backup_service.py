"""Server-side database backups: pg_dump files in a folder, with retention.

The workspace backup (`backup_service`) is a user's own copy of one workspace;
this is the operator's copy of the whole database, for putting the server back
as it was — before a migration, a bulk data change, or after a disk failure.
Each backup is one `pg_dump --format=custom` file named
`securo-db-<label>-<UTC stamp>.dump`, written as `.partial`, checked with
`pg_restore --list` and only then given its final name, so a file with the
final name is always a complete, readable dump.

After every verified backup the folder is pruned: the newest
DB_BACKUP_KEEP_LATEST stay, plus, for each tier in DB_BACKUP_TIERS_DAYS, the
oldest backup younger than that many days. With the defaults (3; 14 and 60)
that is the three latest, one roughly fortnight-old copy and one roughly
two-month-old copy. "Oldest younger than N days" rather than "newest older
than N days": the latter never refills its slot when pruning runs after every
backup, because the copy that would have aged into it was already deleted.
Only files matching `securo-db-*.dump` are considered; nothing else in the
folder is touched.

The database password reaches pg_dump through PGPASSWORD, never the command
line, so it does not show up in the process list.
"""
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.engine import make_url

from app.core.config import get_settings

logger = logging.getLogger(__name__)

PREFIX = "securo-db-"
SUFFIX = ".dump"
_LABEL = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_DAY = 86400


class BackupError(RuntimeError):
    pass


@dataclass
class BackupResult:
    path: Path
    removed: list[str] = field(default_factory=list)


def backup_dir() -> Path | None:
    configured = get_settings().db_backup_dir
    return Path(configured) if configured else None


def _tiers_days() -> list[int]:
    raw = get_settings().db_backup_tiers_days
    return [int(part) for part in raw.split(",") if part.strip()]


def select_kept(
    items: list[tuple[str, float]], now: float, keep_latest: int, tiers_days: list[int]
) -> set[str]:
    """Names to keep among `items` (name, mtime) under the retention rule."""
    newest_first = sorted(items, key=lambda item: item[1], reverse=True)
    kept = {name for name, _ in newest_first[:keep_latest]}
    for days in tiers_days:
        younger = [name for name, mtime in newest_first if now - mtime < days * _DAY]
        if younger:
            kept.add(younger[-1])
    return kept


def _backups(folder: Path) -> list[Path]:
    return [p for p in folder.glob(f"{PREFIX}*{SUFFIX}") if p.is_file()]


def prune(folder: Path, now: float | None = None) -> list[str]:
    """Delete the backups the retention rule does not keep; return their names."""
    settings = get_settings()
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    items = [(p.name, p.stat().st_mtime) for p in _backups(folder)]
    kept = select_kept(items, now, settings.db_backup_keep_latest, _tiers_days())
    removed = []
    for name, _ in sorted(items):
        if name not in kept:
            (folder / name).unlink()
            removed.append(name)
    return removed


def backup_due(folder: Path, interval_hours: int, now: float | None = None) -> bool:
    """True when no backup in `folder` is younger than `interval_hours`."""
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    mtimes = [p.stat().st_mtime for p in _backups(folder)] if folder.is_dir() else []
    return not mtimes or now - max(mtimes) >= interval_hours * 3600


def _pg_env() -> dict[str, str]:
    url = make_url(get_settings().database_url)
    env = dict(os.environ)
    env.update(
        PGHOST=url.host or "localhost",
        PGPORT=str(url.port or 5432),
        PGUSER=url.username or "",
        PGDATABASE=url.database or "",
    )
    if url.password:
        env["PGPASSWORD"] = url.password
    return env


def _run(cmd: list[str], env: dict[str, str], stdout) -> None:
    try:
        done = subprocess.run(cmd, env=env, stdout=stdout, stderr=subprocess.PIPE, check=False)
    except FileNotFoundError as exc:
        raise BackupError(f"{cmd[0]} is not installed") from exc
    if done.returncode != 0:
        tail = done.stderr.decode("utf-8", "replace").strip()[-500:]
        raise BackupError(f"{cmd[0]} failed ({done.returncode}): {tail}")


def take_backup(label: str, now: datetime | None = None) -> BackupResult:
    """Dump the database to DB_BACKUP_DIR, verify it, then apply the retention."""
    if not _LABEL.match(label):
        raise BackupError("label must be lowercase letters, digits and hyphens")
    folder = backup_dir()
    if folder is None:
        raise BackupError("DB_BACKUP_DIR is not set")
    folder.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    target = folder / f"{PREFIX}{label}-{stamp}{SUFFIX}"
    partial = target.with_name(target.name + ".partial")
    if target.exists():
        raise BackupError(f"{target.name} already exists")

    env = _pg_env()
    try:
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as out:
            _run(["pg_dump", "--format=custom", "--no-password"], env, out)
        _run(["pg_restore", "--list", str(partial)], env, subprocess.DEVNULL)
        partial.replace(target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise

    removed = prune(folder)
    logger.info("database backup %s written; retention removed %d", target.name, len(removed))
    return BackupResult(path=target, removed=removed)
