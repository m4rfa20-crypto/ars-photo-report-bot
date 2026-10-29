"""Do not start Telegram polling until the old SQLite database is restored.

Render's worker can stay alive for SSH/SCP uploads while migration is pending.
After restoring the DB, create /app/data/.migration_complete to start polling.
"""
import logging
import os
import sqlite3
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

data_dir = os.environ.get("DATA_DIR", "/app/data")
db_path = os.path.join(data_dir, "ars_photo_report.db")
ready_path = os.path.join(data_dir, ".migration_complete")
os.makedirs(data_dir, exist_ok=True)


def database_is_valid() -> bool:
    if not os.path.isfile(db_path) or os.path.getsize(db_path) == 0:
        return False
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
            tables = {r[0] for r in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )}
            return {"work_logs", "object_settings"}.issubset(tables)
    except sqlite3.Error:
        return False


while True:
    if os.path.isfile(ready_path):
        if database_is_valid():
            log.info("Restored SQLite database verified. Starting Telegram polling.")
            os.execv(sys.executable, [sys.executable, "-m", "src.main"])
        log.error("Migration marker present, but the existing SQLite database is invalid.")
    else:
        log.info("Waiting for Railway SQLite restoration at %s (no Telegram polling).", db_path)
    time.sleep(30)
