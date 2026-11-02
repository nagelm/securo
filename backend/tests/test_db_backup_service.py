import os
import subprocess
import time
from datetime import datetime, timezone

import pytest

from app.core.config import get_settings
from app.services import db_backup_service
from app.services.db_backup_service import BackupError, backup_due, prune, select_kept, take_backup
from app.tasks import db_backup_tasks

DAY = 86400
NOW = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc).timestamp()


def _items(*ages_days):
    return [(f"securo-db-x-{i}.dump", NOW - age * DAY) for i, age in enumerate(ages_days)]


def test_select_kept_three_latest_plus_oldest_under_each_tier():
    items = _items(0, 1, 2, 5, 10, 13, 20, 50, 59, 61, 90)
    kept = select_kept(items, NOW, 3, [14, 60])
    # 0, 1, 2 latest; 13 days is the oldest under 14; 59 days the oldest under 60.
    assert kept == {"securo-db-x-0.dump", "securo-db-x-1.dump", "securo-db-x-2.dump",
                    "securo-db-x-5.dump", "securo-db-x-8.dump"}


def test_select_kept_tier_slots_refill_when_pruned_after_every_backup():
    # One backup a day for 90 days, pruned after each: the tier copies keep
    # existing and age forward instead of disappearing.
    mtimes: list[float] = []
    for day in range(90):
        now = NOW + day * DAY
        mtimes.append(now)
        items = [(str(m), m) for m in mtimes]
        kept = select_kept(items, now, 3, [14, 60])
        mtimes = [m for m in mtimes if str(m) in kept]
    ages = sorted((NOW + 89 * DAY - m) / DAY for m in mtimes)
    assert len(mtimes) == 5
    assert ages[:3] == [0, 1, 2]
    assert 7 <= ages[3] < 14
    assert 30 <= ages[4] < 60


def test_select_kept_with_few_items_keeps_them_all():
    items = _items(0, 1)
    assert select_kept(items, NOW, 3, [14, 60]) == {name for name, _ in items}


def _touch(folder, name, age_days, now=NOW):
    path = folder / name
    path.write_bytes(b"x")
    os.utime(path, (now - age_days * DAY, now - age_days * DAY))
    return path


def test_prune_deletes_only_unkept_backups(tmp_path):
    for i, age in enumerate([0, 1, 2, 3, 4, 20]):
        _touch(tmp_path, f"securo-db-x-{i}.dump", age)
    _touch(tmp_path, "notes.txt", 100)
    _touch(tmp_path, "securo-db-x-9.dump.partial", 100)

    removed = prune(tmp_path, now=NOW)

    # 4 days is the oldest under 14, 20 days the oldest under 60.
    assert removed == ["securo-db-x-3.dump"]
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "notes.txt", "securo-db-x-0.dump", "securo-db-x-1.dump", "securo-db-x-2.dump",
        "securo-db-x-4.dump", "securo-db-x-5.dump", "securo-db-x-9.dump.partial",
    ]


def test_backup_due(tmp_path):
    assert backup_due(tmp_path / "missing", 24, now=NOW)
    assert backup_due(tmp_path, 24, now=NOW)
    _touch(tmp_path, "securo-db-x-0.dump", 0.5)
    assert not backup_due(tmp_path, 24, now=NOW)
    assert backup_due(tmp_path, 6, now=NOW)


@pytest.fixture
def backup_settings(monkeypatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(settings, "db_backup_dir", str(tmp_path))
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://app:s3cret@db:5433/money")
    return tmp_path


def _fake_run(calls, fail=None):
    def run(cmd, env, stdout, stderr, check):
        calls.append((cmd, env))
        if cmd[0] == fail:
            return subprocess.CompletedProcess(cmd, 1, stderr=b"boom")
        if cmd[0] == "pg_dump":
            stdout.write(b"PGDMP fake archive")
        return subprocess.CompletedProcess(cmd, 0, stderr=b"")
    return run


def test_take_backup_writes_verified_dump(monkeypatch, backup_settings):
    calls = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls))

    result = take_backup("pre-change", now=datetime(2026, 6, 30, 1, 2, 3, tzinfo=timezone.utc))

    assert result.path == backup_settings / "securo-db-pre-change-20260630T010203Z.dump"
    assert result.path.read_bytes() == b"PGDMP fake archive"
    assert result.path.stat().st_mode & 0o777 == 0o600 or os.name == "nt"
    assert [cmd[0] for cmd, _ in calls] == ["pg_dump", "pg_restore"]
    dump_cmd, env = calls[0]
    assert "s3cret" not in " ".join(dump_cmd)
    assert (env["PGHOST"], env["PGPORT"], env["PGUSER"], env["PGDATABASE"], env["PGPASSWORD"]) == (
        "db", "5433", "app", "money", "s3cret")
    assert result.removed == []


def test_take_backup_applies_retention(monkeypatch, backup_settings):
    monkeypatch.setattr(subprocess, "run", _fake_run([]))
    now = time.time()
    for i, age in enumerate([1, 2, 3, 30, 31]):
        _touch(backup_settings, f"securo-db-old-{i}.dump", age, now)

    result = take_backup("scheduled")

    # New + 1 and 2 days latest; 3 days is the oldest under 14, 31 the oldest under 60.
    assert result.removed == ["securo-db-old-3.dump"]


def test_take_backup_failure_leaves_no_file(monkeypatch, backup_settings):
    monkeypatch.setattr(subprocess, "run", _fake_run([], fail="pg_restore"))
    _touch(backup_settings, "securo-db-old-0.dump", 1)
    _touch(backup_settings, "securo-db-old-1.dump", 2)
    _touch(backup_settings, "securo-db-old-2.dump", 3)
    _touch(backup_settings, "securo-db-old-3.dump", 4)

    with pytest.raises(BackupError, match="pg_restore failed"):
        take_backup("scheduled")

    # Nothing written and nothing pruned.
    assert len(list(backup_settings.iterdir())) == 4


def test_take_backup_rejects_bad_label_and_missing_dir(monkeypatch, backup_settings):
    with pytest.raises(BackupError, match="label"):
        take_backup("../escape")
    monkeypatch.setattr(get_settings(), "db_backup_dir", "")
    with pytest.raises(BackupError, match="DB_BACKUP_DIR"):
        take_backup("scheduled")


def test_scheduled_task_backs_up_only_when_due(monkeypatch, backup_settings):
    taken = []

    def fake_take(label):
        taken.append(label)
        return db_backup_service.BackupResult(path=backup_settings / "securo-db-scheduled-x.dump")

    monkeypatch.setattr(db_backup_service, "take_backup", fake_take)
    monkeypatch.setattr(get_settings(), "db_backup_interval_hours", 24)

    assert db_backup_tasks.scheduled_db_backup() == "securo-db-scheduled-x.dump"
    _touch(backup_settings, "securo-db-recent.dump", 0, time.time())
    assert db_backup_tasks.scheduled_db_backup() is None
    monkeypatch.setattr(get_settings(), "db_backup_interval_hours", 0)
    assert db_backup_tasks.scheduled_db_backup() is None
    assert taken == ["scheduled"]


def test_scheduled_task_off_without_backup_dir(monkeypatch):
    monkeypatch.setattr(get_settings(), "db_backup_dir", "")
    assert db_backup_tasks.scheduled_db_backup() is None
