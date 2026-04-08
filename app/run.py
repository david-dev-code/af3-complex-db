"""
Entry point for running the FastAPI application
"""
import os
from pathlib import Path

import uvicorn

from app.core.config import get_settings

os.umask(0o002)

settings = get_settings()
SRC_DIR = Path(__file__).resolve().parent


workers = int(os.getenv("UVICORN_WORKERS", "1"))


reload_enabled = False

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        workers=workers,
        log_level="debug",
        access_log=True,
        reload=reload_enabled,
        timeout_keep_alive=120,
        reload_dirs=[str(SRC_DIR)] if reload_enabled else None,
    )
