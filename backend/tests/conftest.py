import pytest
from sqlalchemy import delete
from app.db.session import AsyncSessionLocal, init_db
from app.db.models import (
    DeviceModel,
    EventModel,
    IncidentEventLinkModel,
    IncidentModel,
    QuarantinedEventModel,
    SyncRecordModel,
)


DB_TEST_MODULES = {"test_persistence", "test_api", "test_e2e", "test_websocket"}


@pytest.fixture(autouse=True)
async def clean_database(request):
    """Ensure tables exist and clear test data before DB tests for total test isolation."""
    mod_name = request.module.__name__
    if not any(target in mod_name for target in DB_TEST_MODULES):
        yield
        return

    await init_db()
    async with AsyncSessionLocal() as session:
        await session.execute(delete(IncidentEventLinkModel))
        await session.execute(delete(IncidentModel))
        await session.execute(delete(QuarantinedEventModel))
        await session.execute(delete(SyncRecordModel))
        await session.execute(delete(EventModel))
        await session.execute(delete(DeviceModel))
        await session.commit()
    yield
    from app.db.session import engine
    await engine.dispose()

