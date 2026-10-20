import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app


@pytest.mark.asyncio
async def test_health_check_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "uptime_seconds" in data
        assert "RescueMesh" in data["app"]


@pytest.mark.asyncio
async def test_ready_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert data["offline_mode_capable"] is True
        assert data["database_required_for_local_reports"] is False
        assert "database" in data
        assert data["database"]["status"] == "connected"
        assert data["mode"] == "degraded_offline"
        assert data["database"]["degraded"] is True
        assert "json_export" in data["supported_transports"]

