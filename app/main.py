"""FastAPI 入口。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .api import router
from .config import COVERS_DIR, WEB_DIR, ensure_dirs
from .runtime import downloads, scheduler

ensure_dirs()


@asynccontextmanager
async def lifespan(app: FastAPI):
    downloads.start()
    scheduler.start()
    try:
        yield
    finally:
        scheduler.stop()
        downloads.shutdown()


app = FastAPI(title="黄果短剧采集器", version="1.0.0", lifespan=lifespan)
app.include_router(router)
app.mount("/covers", StaticFiles(directory=str(COVERS_DIR)), name="covers")
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/favicon.ico")
def favicon():
    path = WEB_DIR / "favicon.svg"
    if path.exists():
        return FileResponse(path)
    return Response(status_code=204)
