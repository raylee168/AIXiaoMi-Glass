from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.services.moments_album import get_flow, list_flows, recent_logs

ROOT = Path(__file__).resolve().parent.parent
STATIC_ROOT = ROOT / "web"

app = FastAPI(title="AIXiaoMi Glass", version="0.1.0")
app.mount("/assets", StaticFiles(directory=STATIC_ROOT / "assets"), name="assets")


@app.get("/")
def index():
    return FileResponse(STATIC_ROOT / "index.html")


@app.get("/health")
def health():
    settings = get_settings()
    return {
        "status": "ok",
        "service": "aixiaomi-glass",
        "scenario": "moments_album",
        "poll_interval_seconds": settings.poll_interval_seconds,
    }


@app.get("/api/scenarios/moments-album/flows")
def api_flows(limit: int = Query(default=30, ge=1, le=100), user_id: str | None = None):
    return list_flows(limit=limit, user_id=user_id)


@app.get("/api/scenarios/moments-album/flows/{flow_id}")
def api_flow(flow_id: str):
    try:
        return get_flow(flow_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/scenarios/moments-album/logs")
def api_logs(upload_batch_id: str | None = None, user_id: str | None = None, limit: int = Query(default=100, ge=1, le=500)):
    return {"logs": recent_logs(upload_batch_id=upload_batch_id, user_id=user_id, limit=limit)}
