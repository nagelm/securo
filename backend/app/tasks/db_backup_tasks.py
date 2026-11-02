import logging

from app.core.config import get_settings
from app.worker import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.db_backup_tasks.scheduled_db_backup")
def scheduled_db_backup() -> str | None:
    """Take a "scheduled" database backup when the newest one is older than the interval.

    Runs hourly; checking the folder rather than relying on beat's own timing
    keeps the cadence right across restarts, and any backup (a named
    pre-change one included) resets the clock.
    """
    from app.services import db_backup_service

    settings = get_settings()
    folder = db_backup_service.backup_dir()
    if folder is None or settings.db_backup_interval_hours <= 0:
        return None
    if not db_backup_service.backup_due(folder, settings.db_backup_interval_hours):
        return None
    result = db_backup_service.take_backup("scheduled")
    return result.path.name
