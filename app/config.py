from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = ""
    core_album_db: str = "core_album_db"
    account_db: str = "account_db"
    poll_interval_seconds: int = 3
    log_line_limit: int = 100

    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
