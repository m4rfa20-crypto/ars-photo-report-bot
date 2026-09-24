import os

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.config import Config
from src.models import Base


os.makedirs(Config.DATA_DIR, exist_ok=True)
DATABASE_URL = f"sqlite+aiosqlite:///{os.path.join(Config.DATA_DIR, 'ars_photo_report.db')}"

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    async with engine.begin() as conn:
        # Create new tables on fresh installs and any newly introduced tables.
        await conn.run_sync(Base.metadata.create_all)

        # Lightweight migration for existing SQLite databases created before
        # Telegram Topics support was added.
        result = await conn.exec_driver_sql("PRAGMA table_info(work_logs)")
        columns = {row[1] for row in result.fetchall()}

        if "message_thread_id" not in columns:
            await conn.exec_driver_sql(
                "ALTER TABLE work_logs "
                "ADD COLUMN message_thread_id INTEGER NOT NULL DEFAULT 0"
            )

        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_work_logs_message_thread_id "
            "ON work_logs (message_thread_id)"
        )
