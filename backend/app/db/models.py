from datetime import datetime
from typing import Any
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    JSON,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class EventModel(Base):
    """
    Append-only persistent store for valid, content-addressed events.
    Key properties:
    - Immutable: never updated in place.
    - Idempotent: event_id is the primary key. Duplicate insertion attempts are ignored.
    - Historical: preserves observation changes over time.
    """
    __tablename__ = "events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    device_id: Mapped[str] = mapped_column(String(130), index=True, nullable=False)
    wall_time_ms: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    logical_counter: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    node_id: Mapped[str] = mapped_column(String(130), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    signature: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    canonical_bytes_len: Mapped[int] = mapped_column(Integer, nullable=False)


class QuarantinedEventModel(Base):
    """
    Isolated storage for malformed, tampered, or unverified events.
    Never influences incident derived state or priority scores.
    Preserved for security audit, coordinator review, and forensic triage.
    """
    __tablename__ = "quarantined_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    device_id: Mapped[str | None] = mapped_column(String(130), nullable=True, index=True)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    quarantine_reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    quarantined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    remote_addr: Mapped[str | None] = mapped_column(String(64), nullable=True)


class DeviceModel(Base):
    """
    Registered or observed devices and public key identities.
    """
    __tablename__ = "devices"

    device_id: Mapped[str] = mapped_column(String(130), primary_key=True)
    public_key_hex: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="civilian", nullable=False)
    role_grantor_id: Mapped[str | None] = mapped_column(String(130), nullable=True)
    lease_until_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class IncidentModel(Base):
    """
    Materialized incident view derived deterministically from the agreed event history.
    """
    __tablename__ = "incidents"

    incident_id: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)
    incident_type: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    primary_event_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    created_at_ms: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    current_severity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_people_affected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_people_trapped: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at_ms: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)


class IncidentEventLinkModel(Base):
    """
    Links between incidents and their constituent events (grouping, observations, linking).
    """
    __tablename__ = "incident_event_links"

    incident_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)


class SyncRecordModel(Base):
    """
    Audit ledger tracking synchronization exchanges (QR, JSON, WebSocket).
    """
    __tablename__ = "sync_records"

    sync_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(130), index=True, nullable=False)
    transport: Mapped[str] = mapped_column(String(32), nullable=False)
    events_received: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    events_persisted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    events_quarantined: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
