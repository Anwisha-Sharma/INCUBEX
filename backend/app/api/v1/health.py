import time
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.db.session import get_db

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
async def readiness_check(db: AsyncSession = Depends(get_db)):
    """
    Returns operational readiness status.
    Actively probes database connectivity and explicitly exposes degraded offline operation
    if running under local SQLite fallback rather than clustered PostgreSQL.
    """
    try:
        await db.execute(text("SELECT 1"))
        db_connected = True
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "not_ready",
                "app": settings.APP_NAME,
                "version": settings.APP_VERSION,
                "database": {"status": "unreachable", "error": str(exc)},
            },
        )

    is_sqlite = settings.DATABASE_URL.startswith("sqlite")

    return {
        "status": "ready",
        "mode": "degraded_offline" if is_sqlite else "operational",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "offline_mode_capable": True,
        "database_required_for_local_reports": False,
        "database": {
            "status": "connected",
            "engine": "sqlite_fallback" if is_sqlite else "postgresql",
            "degraded": is_sqlite,
            "policy": (
                "Operating in degraded local offline mode. Data is durable on this node but not clustered."
                if is_sqlite
                else "Operating in clustered production mode."
            ),
        },
        "supported_transports": ["json_export", "chunked_qr", "websocket_hub"],
        "max_event_payload_bytes": settings.MAX_EVENT_PAYLOAD_BYTES,
    }

