import time
import uuid
import logging
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from app.database import init_db
from app.models import IngestRequest, IngestResponse
from app.ingestion import ingest_events
from app.metrics import get_metrics
from app.funnel import get_funnel
from app.heatmap import get_heatmap
from app.anomalies import get_anomalies
from app.health import get_health

# Structured JSON logger
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s'
)
logger = logging.getLogger("store_intelligence")


def log_request(trace_id: str, store_id: str | None, endpoint: str,
                latency_ms: float, status_code: int, event_count: int = 0):
    logger.info(json.dumps({
        "trace_id": trace_id,
        "store_id": store_id,
        "endpoint": endpoint,
        "latency_ms": round(latency_ms, 2),
        "event_count": event_count,
        "status_code": status_code,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info(json.dumps({"event": "startup", "message": "DB initialised"}))
    yield
    logger.info(json.dumps({"event": "shutdown"}))


import os
app = FastAPI(

    title="Purplle Store Intelligence API",
    version="1.0.0",
    lifespan=lifespan
)


@app.middleware("http")
async def add_trace_id(request: Request, call_next):
    trace_id = str(uuid.uuid4())[:8]
    request.state.trace_id = trace_id
    start = time.time()
    try:
        response = await call_next(request)
        latency = (time.time() - start) * 1000
        log_request(
            trace_id=trace_id,
            store_id=request.path_params.get("store_id"),
            endpoint=str(request.url.path),
            latency_ms=latency,
            status_code=response.status_code,
        )
        response.headers["X-Trace-Id"] = trace_id
        return response
    except Exception as e:
        latency = (time.time() - start) * 1000
        log_request(trace_id, None, str(request.url.path), latency, 500)
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error", "trace_id": trace_id}
        )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    trace_id = getattr(request.state, "trace_id", "unknown")
    logger.error(json.dumps({
        "trace_id": trace_id,
        "error": str(exc),
        "endpoint": str(request.url.path)
    }))
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "trace_id": trace_id}
    )


# ── Endpoints ──────────────────────────────────────────────────────────────

@app.post("/events/ingest", response_model=IngestResponse)
async def ingest(request: Request, payload: IngestRequest):
    start = time.time()
    try:
        result = ingest_events(payload)
        latency = (time.time() - start) * 1000
        log_request(
            trace_id=getattr(request.state, "trace_id", "?"),
            store_id=payload.events[0].store_id if payload.events else None,
            endpoint="/events/ingest",
            latency_ms=latency,
            status_code=200,
            event_count=len(payload.events)
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=503, detail={"error": str(e), "message": "Database unavailable"})


STORE_ALIASES = {"STORE_BLR_002": "ST1008", "STORE_PURPLLE_001": "ST1008", "store_1076": "ST1076"}

@app.get("/stores/{store_id}/metrics")
async def metrics(store_id: str):
    try:
        return get_metrics(STORE_ALIASES.get(store_id, store_id))
    except Exception as e:
        raise HTTPException(status_code=503, detail={"error": str(e)})


@app.get("/stores/{store_id}/funnel")
async def funnel(store_id: str):
    try:
        return get_funnel(STORE_ALIASES.get(store_id, store_id))
    except Exception as e:
        raise HTTPException(status_code=503, detail={"error": str(e)})


@app.get("/stores/{store_id}/heatmap")
async def heatmap(store_id: str):
    try:
        return get_heatmap(STORE_ALIASES.get(store_id, store_id))
    except Exception as e:
        raise HTTPException(status_code=503, detail={"error": str(e)})


@app.get("/stores/{store_id}/anomalies")
async def anomalies(store_id: str):
    try:
        return get_anomalies(STORE_ALIASES.get(store_id, store_id))
    except Exception as e:
        raise HTTPException(status_code=503, detail={"error": str(e)})


@app.get("/health")
async def health():
    return get_health()


# Serve web dashboard
_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")

@app.get("/")
async def dashboard():
    index = os.path.join(_static_dir, "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    return {"message": "Purplle Store Intelligence API", "docs": "/docs"}