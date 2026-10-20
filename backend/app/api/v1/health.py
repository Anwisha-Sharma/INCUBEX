import time
from fastapi import APIRouter
from app.core.config import settings

router = APIRouter(tags=["Health & Readiness"])

_server_start_time = time.time()


@router.get("/health", summary="Service Liveness Probe")
async def health_check():
    """
    Returns 200 OK indicating the API process is alive.
    Designed for fast, zero-dependency health checks in local offline or containerized environments.
    """
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "uptime_seconds": round(time.time() - _server_start_time, 2),
    }


@router.get("/ready", summary="Service Readiness Probe")
async def readiness_check():
    """
    Returns operational readiness status.
    Indicates whether event validation, cryptography, and storage interfaces are active.
    """
    return {
        "status": "ready",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "offline_mode_capable": True,
        "database_required_for_local_reports": False,
        "supported_transports": ["json_export", "chunked_qr", "websocket_hub"],
        "max_event_payload_bytes": settings.MAX_EVENT_PAYLOAD_BYTES,
    }
