from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.core.config import settings
from app.db.session import init_db
from app.api.v1.health import router as health_router
from app.api.v1.events import router as events_router
from app.api.v1.sync import router as sync_router
from app.api.v1.incidents import router as incidents_router
from app.sync.hub import hub_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure database schema is initialized on startup
    await init_db()
    yield


limiter = Limiter(key_func=get_remote_address, default_limits=[settings.RATE_LIMIT_DEFAULT])

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="RescueMesh Offline-First Backend & Event Verification Core",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS middleware for local offline PWA and network sync
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global safe error handling
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    if settings.DEBUG:
        return JSONResponse(
            status_code=500,
            content={"error": "InternalServerError", "detail": str(exc)},
        )
    return JSONResponse(
        status_code=500,
        content={"error": "InternalServerError", "message": "An unexpected server error occurred."},
    )

# Include API v1 routes
app.include_router(health_router, prefix=settings.API_V1_PREFIX)
app.include_router(events_router, prefix=settings.API_V1_PREFIX)
app.include_router(sync_router, prefix=settings.API_V1_PREFIX)
app.include_router(incidents_router, prefix=settings.API_V1_PREFIX)
app.include_router(hub_router)
app.include_router(health_router)  # Also expose /health and /ready at root for top-level probes
