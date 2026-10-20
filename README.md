# RescueMesh — Offline-First Disaster Incident Reporting, Reconciliation & Prioritization

RescueMesh is an offline-first emergency coordination platform allowing civilians and first responders to record incident reports without internet connectivity, retain reports locally, exchange data opportunistically (via animated chunked QR or local WebSocket hotspot), merge observations without losing conflicts, and deterministically prioritize incidents using an explainable scoring formula.

---

## Architecture Overview

```
                          ┌──────────────────────────┐
                          │   Frontend / Client      │
                          │   (PWA / Offline React)  │
                          └─────────────┬────────────┘
                                        │ (Web Crypto ECDSA P-256)
                                        │
             ┌──────────────────────────┴──────────────────────────┐
             │                                                     │
   [Tier 0: Optical Sync]                                 [Tier 1: Hotspot Gossip]
Animated Chunked QR Frames                             Local WebSocket Hub (/ws/sync)
(Deflate + Base64url + SHA-256)                          (FastAPI / Standalone Node.js)
             │                                                     │
             └──────────────────────────┬──────────────────────────┘
                                        │
                          ┌─────────────▼────────────┐
                          │  RescueMesh Core Backend │
                          │       (FastAPI / uv)     │
                          └─────────────┬────────────┘
                                        │
       ┌─────────────────┬──────────────┴──────────────┬──────────────────┐
       │                 │                             │                  │
┌──────▼──────┐   ┌──────▼──────┐               ┌──────▼──────┐    ┌──────▼──────┐
│  Module B   │   │  Module C   │               │  Module D   │    │  Module E   │
│ Persistence │   │ Algorithms  │               │ Evidence AI │    │  Security   │
│  & Ingestion│   │ & Priority  │               │ & Grounding │    │   & DevOps  │
└─────────────┘   └─────────────┘               └─────────────┘    └─────────────┘
```

---

## Modules Implemented

### Module B: Backend and Data Layer
- **Framework**: FastAPI, Uvicorn, Pydantic v2, Pydantic Settings, slowapi rate limiting.
- **Persistence**: PostgreSQL 17 engine with async SQLite fallback (`aiosqlite`) for zero-configuration standalone offline development.
- **Append-Only Store**: `EventModel` (immutable, primary key = SHA-256 `event_id`), `QuarantinedEventModel` (isolated quarantine store), `DeviceModel`, `IncidentModel`, and `SyncRecordModel`.
- **Alembic Migrations**: Fully configured async migrations with initial revision.

### Module C: Detection, Algorithms and Incident Prioritization
- **Hybrid Logical Clocks (HLC)**: Monotonic physical + logical clock ensuring strict causality across disconnected devices.
- **Deterministic Grouping**: Clusters candidate reports of matching incident type within **150 metres** and **2 hours** into canonical incidents (`inc_{primary_report.event_id[:16]}`). Supports coordinator `link` and `unlink` events.
- **Conflict Preservation**: Retains all historical observations across distinct observers. Conflicting severities, locations ($> 100$m), or trapped counts are flagged with human-readable conflict details.
- **Explainable Priority Engine**:
  $$S = 5V + 3P + 2C + 2R - 3U$$
  - Thresholds: **HIGH** ($\ge 24$), **MEDIUM** ($\ge 12$), **LOW** ($< 12$).
  - **Severe Incident Safety Clamp**: Incidents with Critical severity ($V=3$) can never drop below `MEDIUM` ($S \ge 12$), regardless of uncertainty.
  - **Recency Floor**: Time criticality $R$ cannot fall below $2$ while severity $V \ge 2$ and no responder is assigned.
  - **Needs Verification Badge**: Triggered when $U \ge 2$ or for uncorroborated civilian reports.
  - **Sensitivity Analysis**: Computes stability of priority level under weight variations $\pm 1$.
- **Multi-Device Convergence Simulator**: Mathematically verified convergence under network chaos (packet drops, duplicate delivery, clock skew, out-of-order arrival).

### Module D: AI and Evidence Grounding
- **Cite-or-Drop Verifier**:
  - Requires every factual claim and figure to cite verified `event_id`s.
  - Rejects unsupported claims, hallucinated citations, or fabricated numbers.
- **Deterministic Offline Fallback Templates**:
  - High-fidelity rule-based briefing generator producing structured responder briefs with zero external network or model dependency.
- **Modular AI Grounding**:
  - Pluggable Groq API (`openai/gpt-oss-120b`) or Anthropic Claude adapter passing outputs through the Cite-or-Drop verifier.

### Module E: Security, Synchronization and DevOps
- **Cryptographic Event Signatures**: Web Crypto API compatible ECDSA SEC P-256 + SHA-256 verification (supports raw IEEE P1363 64-byte format and DER format).
- **Quarantine Store**: Unsigned, tampered, or invalid events are quarantined with forensic reasons and client IP without polluting derived incident state.
- **Privacy & EXIF Stripping**: Strips GPS and camera EXIF metadata from photo uploads.
- **Tier 0 Sync**: Deflate compression + Base64url chunked animated QR frame sequence (`RM1:<id>:<idx>:<total>:<hash>:<chunk>`) with progressive reassembly.
- **Tier 1 Sync**: Local network WebSocket Hub (`/ws/sync` and standalone `hub/server.js`) for hotspot gossip exchange with zero internet requirement.
- **Containers & CI**: Multi-stage non-root Dockerfile (`UID 10001`), Docker Compose (FastAPI + PostgreSQL 17 + Node Hub), and GitHub Actions CI workflow.

---

## Getting Started

### Local Development (Python 3.12 + Astral uv)

```bash
# Navigate to backend
cd backend

# Install dependencies into uv virtual environment
uv sync

# Run all 40 unit and integration tests
uv run pytest -v

# Start development server
uv run uvicorn app.main:app --reload --port 8000
```

Access Swagger UI documentation at: `http://localhost:8000/docs`

### Running with Docker Compose

```bash
docker compose up --build
```
- FastAPI Backend: `http://localhost:8000`
- PostgreSQL 17: `localhost:5432`
- Node.js Hotspot Hub: `ws://localhost:8080/ws/sync`

---

## Integration with Frontend Teammate

Contract definitions, canonical JSON formatting specifications, and OpenAPI schemas are located in:
- [`contracts/openapi.json`](file:///c:/Users/rajpu/OneDrive/Pictures/Documents/incubex/contracts/openapi.json)
- [`contracts/README.md`](file:///c:/Users/rajpu/OneDrive/Pictures/Documents/incubex/contracts/README.md)
