import os
from pathlib import Path

import numpy as np
from psycopg2.extensions import AsIs, register_adapter
from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import MetaData, inspect, text
from starlette.responses import RedirectResponse

from app.api.router import api_router
from app.api.v1.endpoints import download
from app.core.config import get_settings
from app.core.database import _engine
from app.models import Base
from app.web.router import web_router

register_adapter(np.float32, lambda x: AsIs(float(x)))
register_adapter(np.float64, lambda x: AsIs(float(x)))
register_adapter(np.int32, lambda x: AsIs(int(x)))
register_adapter(np.int64, lambda x: AsIs(int(x)))

settings = get_settings()

app = FastAPI(
    title="AF3-DB",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    debug=True,
)


@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    """
    Intercepts HTTPExceptions to provide user-friendly HTML error pages for browser requests,
    while maintaining standard JSON responses for API clients.
    Adds strict no-cache headers to prevent browsers from caching the 401 rejection.
    """
    # Merge existing headers (like WWW-Authenticate) with strict anti-caching headers
    headers = exc.headers.copy() if exc.headers else {}
    if exc.status_code == 401:
        headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        headers["Pragma"] = "no-cache"
        headers["Expires"] = "0"

    if exc.status_code == 401 and "text/html" in request.headers.get("accept", ""):
        html_content = """
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Unauthorized</title>
            <style>
                body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; background-color: #f8f9fa; margin: 0; }
                .container { text-align: center; background: white; padding: 2rem; border-radius: 8px; box-shadow: 0 .125rem .25rem rgba(0,0,0,.075); }
                h1 { color: #dc3545; margin-bottom: 1rem; }
                p { color: #6c757d; margin-bottom: 1.5rem; }
                .btn { text-decoration: none; background-color: #0d6efd; color: white; padding: 0.5rem 1rem; border-radius: 4px; border: none; cursor: pointer; font-size: 1rem; }
                .btn:hover { background-color: #0b5ed7; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Access Denied</h1>
                <p>Authentication failed or was cancelled.</p>
                <button class="btn" onclick="window.history.back()">Go Back</button>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(
            content=html_content,
            status_code=exc.status_code,
            headers=headers
        )

    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=headers
    )


@app.middleware("http")
async def log_upload_meta(request: Request, call_next):
    """
    Middleware to log metadata for file uploads.
    Logs headers only without reading the body to prevent blocking the stream.
    """
    if request.method == "POST" and request.url.path in (
            "/api/v1/complexes/upload", "/api/v1/complexes/"
    ):
        ctype = request.headers.get("content-type", "")
        clen = request.headers.get("content-length")
        print(f"[MW] POST {request.url.path} ctype={ctype!r} CL={clen}", flush=True)

    return await call_next(request)


# Register Routers
app.include_router(api_router, prefix="/api")
app.include_router(web_router)
app.include_router(download.router)

# Mount Static Files (CSS, JS, Images)
STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
else:
    print(f"[WARNING] Static directory missing: {STATIC_DIR}", flush=True)

# Database Initialization / Reset Check
if _engine is not None:

    print(f"[DB] Tables before check: {inspect(_engine).get_table_names(schema='public')}", flush=True)

    with _engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

    Base.metadata.create_all(bind=_engine)

    print(f"[DB] Tables after check: {inspect(_engine).get_table_names(schema='public')}", flush=True)


@app.get("/robots.txt", include_in_schema=False)
async def robots():
    """
    Redirects crawlers to the static robots.txt file.
    """
    return RedirectResponse("/static/robots.txt")