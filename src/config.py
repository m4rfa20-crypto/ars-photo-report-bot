import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str
    PROJECT_NAME: str = ""
    TIMEZONE: str = "Europe/Moscow"
    DATA_DIR: str = "data"
    ADMIN_IDS: str = ""

    @property
    def admin_ids(self) -> set[int]:
        if not self.ADMIN_IDS.strip():
            return set()
        result = set()
        for value in self.ADMIN_IDS.split(","):
            value = value.strip()
            if value:
                result.add(int(value))
        return result

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


Config = Settings()
os.makedirs(Config.DATA_DIR, exist_ok=True)
