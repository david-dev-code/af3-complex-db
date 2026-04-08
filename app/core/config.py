from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Server Configuration
    host: str = "0.0.0.0"
    port: int = 9090
    reload: bool = True

    # Security
    admin_username: str = "admin"
    admin_password: str = "admin123"

    # Hoster
    hoster_name: str = "Local Administrator"
    hoster_email: str = ""
    hoster_description: str = ""

    # Database
    database_url: str | None = None

    # Filesystem Storage
    storage_root: Path = Path("./storage_root")

    # Biophysical Thresholds
    threshold_h_bond: float = 3.5
    threshold_salt_bridge: float = 4.0
    threshold_interface: float = 4.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

settings = Settings()
settings.storage_root.mkdir(parents=True, exist_ok=True)

def get_settings() -> Settings:
    return settings