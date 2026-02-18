"""Vikingbot Console Server - FastAPI Web Service"""

import asyncio
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from starlette.middleware.cors import CORSMiddleware

from vikingbot import __version__
from vikingbot.config.loader import load_config, get_config_path, save_config
from vikingbot.session.manager import SessionManager
from vikingbot.utils.helpers import get_workspace_path

app = FastAPI(title="Vikingbot Console", version=__version__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_start_time = time.time()


@app.get("/health")
@app.get("/healthz")
async def health_check():
    """Health check endpoint for Kubernetes probes"""
    return {
        "status": "healthy",
        "version": __version__,
        "uptime": int(time.time() - _start_time)
    }


@app.get("/api/v1/status")
async def get_status():
    config = load_config()
    session_manager = SessionManager(config.workspace_path)
    sessions = session_manager.list_sessions()
    
    return {
        "success": True,
        "data": {
            "version": __version__,
            "uptime": int(time.time() - _start_time),
            "config_path": str(get_config_path()),
            "workspace_path": str(config.workspace_path),
            "sessions_count": len(sessions),
            "gateway_running": True
        }
    }


from vikingbot.console.api import config, sessions, workspace, partials

app.include_router(config.router, prefix="/api/v1", tags=["config"])
app.include_router(sessions.router, prefix="/api/v1", tags=["sessions"])
app.include_router(workspace.router, prefix="/api/v1", tags=["workspace"])
app.include_router(partials.router, prefix="/api/v1", tags=["partials"])


@app.get("/{path:path}")
async def serve_frontend(path: str):
    static_dir = Path(__file__).parent / "static"
    
    if path == "" or path == "/":
        return FileResponse(static_dir / "index.html")
    
    file_path = static_dir / path
    if file_path.exists() and file_path.is_file():
        return FileResponse(file_path)
    
    return FileResponse(static_dir / "index.html")


async def start_console_server(port: int = 18791, host: str = "0.0.0.0"):
    import uvicorn
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=False
    )
    server = uvicorn.Server(config)
    await server.serve()
