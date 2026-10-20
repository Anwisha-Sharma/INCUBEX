# RescueMesh API Contracts & Synchronization Protocol

This directory contains the contract specifications for RescueMesh client-server and peer-to-peer data models.

## Core Rules for Client Implementation (Frontend)

### 1. Canonical JSON Serialization
To ensure that an `event_id` computed on the browser matches the backend and peer devices byte-for-byte:
1. Object keys must be sorted alphabetically at every nested level (`JSON.stringify` does not guarantee this; sort keys recursively before stringifying).
2. Use minimal separators without spaces: `{"a":1,"b":"hello"}` (no whitespace after `:` or `,`).
3. Encode as UTF-8 bytes.

### 2. Content-Addressed Event ID
The `event_id` is computed as:
$$\text{event\_id} = \text{SHA-256}(\text{Canonical JSON}(\{\text{"clock"}: \dots, \text{"device\_id"}: \dots, \text{"event\_type"}: \dots, \text{"payload"}: \dots\}))$$
*Notice: `event_id` and `signature` are never included in the input hash calculation.*

### 3. Web Crypto API Signing (ECDSA P-256)
- **Algorithm**: ECDSA with curve `P-256` (prime256v1) and `SHA-256`.
- **Key Generation**:
  ```javascript
  const keyPair = await window.crypto.subtle.generateKey(
    { name: "ECDSA", namedCurve: "P-256" },
    true, // extractable
    ["sign", "verify"]
  );
  ```
- **Device ID**: SHA-256 hash (hex) of the raw uncompressed public key bytes (`crypto.subtle.exportKey("raw", keyPair.publicKey)`).
- **Signature**: Web Crypto API produces IEEE P1363 (64 bytes: 32 bytes $r$ + 32 bytes $s$). Represent as a 128-character lowercase hex string or standard Base64 string in `RescueMeshEvent.signature`.

### 4. Hybrid Logical Clock (HLC)
Every event includes an HLC clock object:
```json
{
  "wall_time_ms": 1771400000000,
  "logical_counter": 0,
  "node_id": "<device_id>"
}
```
Comparison order: `wall_time_ms` $\rightarrow$ `logical_counter` $\rightarrow$ `node_id`.

### 5. Event Types and Payloads
Supported event types:
- `report`: Fresh incident report (`incident_type`, `latitude`, `longitude`, `severity` 0-3, `people_affected`, `people_trapped`, `description`, `evidence_hashes`).
- `observation`: Follow-up observation on an existing incident (`incident_id`, `severity`, `people_affected`, `description`, `evidence_hashes`).
- `link`: Coordinator event linking multiple incidents.
- `unlink`: Coordinator event unlinking misgrouped incidents.
- `roleGrant`: Coordinator certification of responder roles.
- `assign`: Dispatching a responder to an incident.
- `status`: Responder status update (`en_route`, `on_scene`, `done`, `available`).

---

### 6. Explainable Priority Scoring Engine

$$S = 5V + 3P + 2C + 2R - 3U$$

Where each factor is scored from 0 to 3:
- **$V$ (Severity)**: $0$ (minimal) to $3$ (critical).
- **$P$ (People Affected)**:
  - $P=0$: Unknown / 0
  - $P=1$: 1–2 people
  - $P=2$: 3–9 people
  - $P=3$: 10+ people OR `people_trapped = true`
- **$C$ (Confidence)**:
  - $C=0$: No confidence / unverifiable
  - $C=1$: 1 unverified report
  - $C=2$: Corroboration from $\ge 2$ distinct signed devices OR attached photo/audio evidence hashes
  - $C=3$: Verified responder on-scene report or coordinator confirmation
- **$R$ (Time-Criticality / Recency)**:
  - Starts at $3$ for fresh incidents ($< 30$ mins)
  - Decays to $2$ ($< 2$ hours), $1$ ($< 6$ hours), $0$ ($\ge 6$ hours)
  - **Safety Rule**: $R$ cannot fall below $2$ while severity $V \ge 2$ and no responder is assigned.
- **$U$ (Uncertainty)**:
  - Scored from $0$ to $3$ based on conflicting severities, conflicting trapped observations, coordinate variations $> 100$m, or uncorroborated civilian reports.

#### Priority Thresholds
- **HIGH**: $S \ge 24$
- **MEDIUM**: $S \ge 12$
- **LOW**: $S < 12$

#### Safety Clamp Rule
If severity is Critical ($V = 3$), the priority level **CANNOT** be classified as `LOW`, even if uncertainty is high ($U=3$). It is clamped to at least `MEDIUM` ($S \ge 12$).

#### Badges
- **Needs Verification**: Rendered when $U \ge 2$ or when $V \ge 2$ with single uncorroborated civilian source ($C \le 1$).

---

### 7. Deterministic Grouping & Conflict Preservation
1. **Geospatial & Temporal Window**: Reports of the same `incident_type` occurring within **150 metres** and **2 hours** are automatically clustered into a single canonical incident.
2. **Canonical Incident ID**: Derived as `inc_{primary_report.event_id[0:16]}` where primary is the report with the earliest HLC timestamp.
3. **Conflict Preservation**: Conflicting observations are never discarded. The latest valid HLC observation provides the *current* display value, while all historical observations are kept in the incident dossier with `has_conflict = true` and human-readable conflict details.

---

### 8. Key API Endpoints
- `GET /health`: Fast process liveness probe.
- `GET /ready`: Operational readiness probe. Actively checks database and explicitly exposes `mode: "degraded_offline"` when operating with local SQLite fallback rather than clustered PostgreSQL.
- `POST /api/v1/events/validate`: In-memory event signature and canonical hash verification.
- `POST /api/v1/events/ingest`: Idempotent batch event ingestion into persistent store with concurrent savepoints.
- `GET /api/v1/events`: List raw events with filters.
- `GET /api/v1/events/{event_id}`: Retrieve single event by ID.
- `GET /api/v1/events/quarantined`: Audit list of quarantined events with forensic reasons.
- `GET /api/v1/sync/summary`: Compact event-ID summary for peer sync.
- `POST /api/v1/sync/missing`: Missing events transfer based on set difference.
- `GET /api/v1/incidents`: Reconciled incident list with priority scores, factors, and conflict flags.
- `GET /api/v1/incidents/{incident_id}`: Full incident dossier with factor explanation and timeline.
- `GET /api/v1/incidents/{incident_id}/sensitivity`: Sensitivity analysis varying weights by $\pm 1$.
- `GET /api/v1/incidents/{incident_id}/briefing`: Evidence-grounded responder briefing with Cite-or-Drop verification.
- `POST /api/v1/evidence/upload`: Privacy-first evidence image upload; strips EXIF GPS metadata and computes content-addressed SHA-256 hash.
- `GET /api/v1/evidence/{evidence_hash}`: Retrieve sanitized evidence image bytes.
- `POST /api/v1/sync/export`: Export as raw JSON or animated Base64url QR frames.
- `POST /api/v1/sync/import`: Progressive QR frame or JSON ingestion.
- `WS /ws/sync`: Local network WebSocket hub for real-time hotspot gossip with inbound canonical and signature verification.

---

### 9. Evidence-Grounded AI Briefings & Cite-or-Drop (Module D)
1. **Cite-or-Drop Rule**:
   - Every factual claim or casualty count returned by AI must reference an exact, existing `event_id`.
   - Any assertion lacking a valid citation or containing hallucinated figures not found in recorded evidence is automatically **dropped** before reaching the responder.
2. **Provider Hierarchy & Deterministic Offline Fallback**:
   - Primary: Groq API (`settings.GROQ_API_KEY`).
   - Secondary: Anthropic Claude API (`settings.ANTHROPIC_API_KEY`).
   - If Groq/Claude is unreachable, times out, or is unconfigured, the briefing generator seamlessly falls back to high-fidelity deterministic rule-based templates.
   - Core reporting, reconciliation, and priority scoring remain 100% operational offline with zero internet access.

The complete OpenAPI 3.1 specification is in [openapi.json](./openapi.json).
