# RescueMesh — Comprehensive Backend Audit & Baseline Gap Analysis

**Date**: 2026-10-20  
**Repository Branch**: `feature/backend-core`  
**Baseline Git Commit**: `cb8dd5a` ("feat: implement RescueMesh core modules B, C, D, and E")  
**Environment**: Windows 11 (win32), Python 3.12.13, uv 0.6.14, Node.js v24.19.0, PostgreSQL 17 client libraries (psycopg 3)

---

## 1. Executive Summary

RescueMesh is an offline-first disaster incident reporting, coordination, and synchronization system. This audit evaluates the backend implementation against the full project specifications across:
- **Module B**: Backend, database, persistence, and event store
- **Module C**: Event algorithms, reconciliation, incident grouping, and prioritization
- **Module D**: AI briefing generation and evidence-grounding verification
- **Module E**: Security, synchronization, QR transfer, and DevOps

### Baseline Test Run
- **Test Framework**: `pytest 9.1.1` with `pytest-asyncio 1.4.0`
- **Execution Command**: `uv run pytest -v` (inside `backend/`)
- **Baseline Result**: **40 tests passed, 0 failures, 1 deprecation warning** in 15.52s.
- **Initial Defect Discovered and Resolved**: On Windows, an `autouse=True` fixture in `conftest.py` instantiated an async session and `aiosqlite` threads on non-database algorithm tests. When per-test event loops closed, abandoned worker threads caused a fatal Windows exception (`0xc000070a`). Scoping the database teardown fixture to DB-backed test modules and configuring `NullPool` for SQLite in `app/db/session.py` eliminated the thread leak and brought the baseline test suite to 100% stability.

---

## 2. Requirement-by-Requirement Audit Matrix

| Module / Requirement | Relevant Source Files | Evidence of Implementation | Existing Test Coverage | Status | Required Action |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **B1: Liveness Endpoint (`GET /health`)** | `app/api/v1/health.py`, `app/main.py` | Returns 200 OK, uptime, process liveness, zero external dependencies. | `tests/test_api_health.py::test_health_check_endpoint` | **PASS** | None. |
| **B2: Readiness Endpoint (`GET /ready`)** | `app/api/v1/health.py`, `app/db/session.py` | Endpoint exists; reports static configuration. However, it does not execute a DB probe (`SELECT 1`), does not report if SQLite fallback is running in degraded offline mode, and does not return 503 when the DB is unavailable. | `tests/test_api_health.py::test_ready_endpoint` | **PARTIAL** | Add active DB connectivity check, explicit degraded offline mode status reporting (`sqlite_offline_fallback` vs `postgresql`), and HTTP 503 error handling. |
| **B3: Event Validation Endpoint (`POST /api/v1/events/validate`)** | `app/api/v1/events.py`, `app/core/canonical.py`, `app/core/security.py` | In-memory verification of canonical hash, clock skew, and ECDSA signature. Returns quarantine reasons. | `tests/test_api_events.py` (3 tests) | **PASS** | None. |
| **B4: Event Ingestion Endpoint (`POST /api/v1/events/ingest`)** | `app/api/v1/events.py`, `app/db/models.py` | Idempotent event ingestion; duplicate detection; quarantine routing; device auto-registration; sync session ledgering. | `tests/test_persistence.py::test_idempotent_event_ingestion_and_retrieval` | **PASS** | Add concurrent submission test (`asyncio.gather`). |
| **B5: Event Retrieval Endpoints (`GET /events/{id}`, `GET /events`)** | `app/api/v1/events.py` | Single event retrieval by ID; query by `event_type`, `device_id`, `since_wall_time_ms`, with `limit` and `offset` pagination. | `tests/test_e2e_offline_flow.py`, `tests/test_persistence.py` | **PASS** | None. |
| **B6: Peer Sync Endpoints (`GET /sync/summary`, `POST /sync/missing`)** | `app/api/v1/sync.py` | Fast set-difference reconciliation: summary of known IDs, batch fetch of missing events. | `tests/test_persistence.py::test_sync_summary_and_missing_events` | **PASS** | None. |
| **B7: SQLAlchemy 2 & PostgreSQL 17 Support** | `app/db/session.py`, `backend/pyproject.toml`, `docker-compose.yml` | `AsyncSession` and `create_async_engine`; `psycopg[binary]>=3.2.0` in pyproject; Postgres 17 service in Docker Compose. | `tests/test_persistence.py` | **PASS** | Verify runtime degraded-mode documentation. |
| **B8: SQLite Degraded Offline Fallback** | `app/core/config.py`, `app/db/session.py` | SQLite fallback enabled by default connection string for single-device offline operation. | `tests/test_persistence.py` | **PARTIAL** | Add explicit documentation that SQLite is a degraded local offline mode, not clustered HA. Expose degraded flag in `/ready`. |
| **B9: Persistence Across Process Restart** | `app/db/session.py`, `app/db/models.py` | Tables and events written to SQLite file survive process exit; however, no automated test explicitly verified engine disposal and re-initialization against the same persistent store. | Missing explicit restart test | **PARTIAL** | Add `test_persistence_across_restart` verifying event survivability across engine resets. |
| **B10: Uniqueness & Append-Only Invariants** | `app/db/models.py`, `app/api/v1/events.py` | Event ID enforced as primary key; zero update or delete endpoints exist for events. | `tests/test_persistence.py` | **PASS** | None. |
| **B11: Quarantined Event Model & Audit Isolation** | `app/db/models.py`, `app/api/v1/events.py`, `app/algorithms/reconciliation.py` | `QuarantinedEventModel` stores tampered/skewed events separately. Reconciliation queries explicitly filter for accepted `EventModel` only. | `tests/test_persistence.py::test_invalid_event_is_quarantined_in_db` | **PASS** | None. |
| **B12: Alembic Migrations** | `backend/alembic/`, `backend/alembic.ini` | Async migration `7f11e043b774` defines all tables, columns, foreign keys, and indexes. `uv run alembic current` verifies schema head. | Verified via CLI inspection | **PASS** | None. |
| **C1: Canonical JSON & SHA-256 Event ID** | `app/core/canonical.py` | Deterministic key ordering (`sort_keys=True`), compact `(",", ":")` separators, UTF-8 bytes. Event ID excludes `event_id` and `signature`. | `tests/test_canonical.py` (3 tests) | **PARTIAL** | Add cross-language test vectors comparing Python output directly with Web Crypto/TypeScript reference representations. |
| **C2: Hybrid Logical Clock (HLC)** | `app/algorithms/hlc.py` | HLC implementation with wall time, logical counter, and node ID. Causal update, skew checks, total ordering. | `tests/test_hlc.py` (3 tests) | **PASS** | None. |
| **C3: Deterministic Incident Grouping** | `app/algorithms/grouping.py` | Union-Find spatial (150m Haversine) and temporal (2h) clustering. Order-invariant clustering; deterministic incident IDs `inc_{primary[:16]}`. | `tests/test_grouping.py` (4 tests) | **PASS** | None. |
| **C4: Conflict-Preserving Reconciliation** | `app/algorithms/reconciliation.py` | Latest valid HLC provides display values; full observation timeline preserved; flags for severity, trapped, and drift (>100m) conflicts. | `tests/test_grouping.py`, `tests/test_api_incidents.py` | **PASS** | None. |
| **C5: Priority Scoring Engine** | `app/algorithms/priority.py` | $S = 5V + 3P + 2C + 2R - 3U$. Input boundaries, safety clamp for $V=3$, recency floor ($R \ge 2$), Needs Verification badge ($U \ge 2$). | `tests/test_priority.py` (4 tests) | **PASS** | None. |
| **C6: Multi-Device Simulator** | `app/algorithms/simulator.py` | Network chaos simulation with message drops, delays, duplicates, and clock skew. Proves event-set convergence. | `tests/test_simulator.py` | **PASS** | None. |
| **D1: Cite-or-Drop AI Verifier** | `app/ai/cite_or_drop.py` | Validates every assertion against accepted event IDs; rejects ungrounded claims; detects hallucinated casualty numbers (>3 not in evidence). | `tests/test_ai_cite_or_drop.py` (3 tests) | **PASS** | None. |
| **D2: Offline Briefing Fallback** | `app/ai/offline_templates.py` | High-fidelity deterministic rule-based briefings when offline or credentials absent. Separates verified, unverified, and conflicting items. | `tests/test_ai_offline_templates.py` | **PASS** | None. |
| **D3: AI Provider Adapters (Groq & Anthropic)** | `app/ai/service.py`, `app/core/config.py` | Groq adapter implemented. Anthropic Claude adapter is currently missing from `service.py`. No tests existed to test provider call flows without external network access. | Missing Anthropic implementation & provider mock tests | **PARTIAL** | Implement Anthropic Claude adapter in `service.py`; add unit tests mocking Groq and Anthropic responses with Cite-or-Drop verification. |
| **E1: ECDSA P-256 Digital Signatures** | `app/core/security.py` | ECDSA P-256 + SHA-256. Supports raw IEEE P1363 (64 bytes hex) and DER. Deterministic device ID from raw public key bytes. | `tests/test_security.py` (4 tests) | **PASS** | None. |
| **E2: Image Privacy & EXIF Stripping** | `app/core/privacy.py` | Strips JPEG APP1 (0xFFE1) EXIF GPS metadata. However, no HTTP endpoint exists in `app/api/v1` to upload evidence images. | `tests/test_privacy_and_roles.py` | **PARTIAL** | Add `POST /api/v1/evidence/upload` endpoint connecting multipart upload to `strip_image_exif`, returning content-addressed SHA-256 evidence hash. |
| **E3: Tier 0 Chunked QR Transfer** | `app/sync/qr.py` | `RM1:<transfer_id>:<frame_idx>:<total_frames>:<hash>:<chunk>`. Deflate + Base64url encoding and progressive reassembly. | `tests/test_sync_qr.py` (2 tests) | **PARTIAL** | Add decompression bomb guard (max decompressed bytes), frame index validation (`frame_idx >= total_frames`), and tests for out-of-order, duplicate, corrupted, and invalid frame counts. |
| **E4: Tier 1 WebSocket Gossip Sync** | `app/sync/hub.py`, `hub/server.js` | Endpoint `/ws/sync` mounted. Inbound `PUBLISH_EVENTS` currently persists events to DB without cryptographic signature or canonical hash verification. | `tests/test_websocket_hub.py` | **PARTIAL** | Validate canonical hash, clock skew, and signatures before persisting inbound events over WebSocket; quarantine or reject invalid payloads. |
| **E5: Containerization & DevOps** | `backend/Dockerfile`, `docker-compose.yml`, `.github/workflows/ci.yml` | Multi-stage non-root Dockerfile (`rescuemesh` uid 10001). Docker Compose with Postgres 17 and local hub. In `ci.yml`, OpenAPI contract drift check is a mock print rather than a real comparison. | CI workflow inspection | **PARTIAL** | Harden CI contract drift check to perform true structural comparison between `app.openapi()` and `contracts/openapi.json`. |

---

## 3. Prioritized Implementation Plan for Verified Gaps

1. **Gap 1 (Module B — Readiness & Degraded Mode Policy)**:
   - Enhance `GET /ready` to query database with `SELECT 1`.
   - Report `mode: "degraded_offline"` with clear documentation when using SQLite fallback.
   - Return HTTP 503 if database connectivity fails.
   - Add persistence survival across restart test (`test_persistence_across_restart`).
   - Add concurrent ingestion test (`test_concurrent_event_ingestion`).

2. **Gap 2 (Module C — Cross-Language Canonical JSON Vectors)**:
   - Create shared test vectors matching TypeScript / Web Crypto JSON formatting and SHA-256 hashing (`test_canonical_cross_language.py`).

3. **Gap 3 (Module D — Anthropic Adapter & Mocked Provider Tests)**:
   - Implement Anthropic Claude adapter in `app/ai/service.py`.
   - Add unit tests mocking both Groq and Anthropic responses with `respx`/`unittest.mock` to verify Cite-or-Drop integration without network access.

4. **Gap 4 (Module E — Image Privacy API Route)**:
   - Implement `POST /api/v1/evidence/upload` endpoint accepting multipart images.
   - Strip EXIF GPS metadata using `strip_image_exif`.
   - Return content-addressed SHA-256 evidence hash.
   - Test with synthetic GPS-tagged JPEG image.

5. **Gap 5 (Module E — QR Transfer Hardening)**:
   - Add decompression size bounds and frame index bounds in `app/sync/qr.py`.
   - Add tests for out-of-order frames, duplicates, invalid frame counts, and decompression bombs.

6. **Gap 6 (Module E — WebSocket Security Hardening)**:
   - Enforce canonical hash validation and signature verification in `app/sync/hub.py` before persisting inbound `PUBLISH_EVENTS`.
   - Quarantine invalid events instead of allowing malicious injection into persistent store.

7. **Gap 7 (DevOps & Contracts)**:
   - Update `contracts/openapi.json` and `contracts/README.md` to reflect new `/evidence/upload` endpoint and degraded readiness schema.
   - Verify contract parity with automated drift validation.
