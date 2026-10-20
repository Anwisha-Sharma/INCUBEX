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


@pytest.fixture(autouse=True)
async def clean_database():
    """Ensure tables exist and clear test data before each test for total test isolation."""
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
