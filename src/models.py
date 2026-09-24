from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class WorkLog(Base):
    __tablename__ = "work_logs"
    __table_args__ = (
        UniqueConstraint("chat_id", "message_id", name="uq_work_logs_chat_message"),
    )

    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(String, index=True, nullable=False)
    message_thread_id = Column(Integer, index=True, nullable=False, default=0)
    message_id = Column(Integer, nullable=False)
    media_group_id = Column(String, index=True, nullable=True)

    user_id = Column(String, nullable=True)
    username = Column(String, nullable=True)

    text = Column(Text, nullable=True)
    photo_file_id = Column(Text, nullable=True)
    photo_unique_id = Column(String, nullable=True)

    timestamp = Column(DateTime, default=_utcnow, index=True, nullable=False)


class ObjectSettings(Base):
    __tablename__ = "object_settings"
    __table_args__ = (
        UniqueConstraint(
            "chat_id",
            "message_thread_id",
            name="uq_object_settings_chat_thread",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(String, index=True, nullable=False)
    message_thread_id = Column(Integer, index=True, nullable=False, default=0)
    project_name = Column(String, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)
