"""配置加载（用 pydantic-settings）"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


class Settings(BaseSettings):
    # nashsu 19828 API
    nashsu_api_base: str = "http://127.0.0.1:19828/api/v1"
    nashsu_token: str = ""
    nashsu_project_id: str = "mvp-test-001"

    # nashsu wiki 根目录（用于归档写文件）
    nashsu_wiki_root: str = "/tmp/wiki-mvp"

    # JWT
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440

    # 网关
    gateway_host: str = "127.0.0.1"
    gateway_port: int = 8765

    # 用户
    allow_open_registration: bool = True
    admin_username: str = "admin"
    admin_password: str = "admin123"

    # 内部
    db_path: str = "./data/users.db"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )


settings = Settings()

# 确保 data 目录存在
Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
