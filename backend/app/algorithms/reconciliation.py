from typing import Any
from app.algorithms.geo import haversine_distance_meters
from app.algorithms.grouping import IncidentGroup
from app.models.events import RescueMeshEvent


class ReconciledIncidentState:
    """
    Derived operational state of an incident with conflict history preserved.
    Never overwrites or deletes conflicting observations.
    """
    def __init__(self, incident_id: str):
        self.incident_id = incident_id
        self.incident_type: str = "other"
        self.latitude: float = 0.0
        self.longitude: float = 0.0
        self.created_at_ms: int = 0
        self.latest_update_ms: int = 0

        # Current values (chosen by latest HLC)
        self.current_severity: int = 0
        self.current_people_affected: int = 0
        self.current_people_trapped: bool = False
        self.current_description: str = ""
        self.evidence_hashes: list[str] = []

        # Conflict preservation
        self.has_conflict: bool = False
        self.conflict_details: list[str] = []
        self.severity_observations: list[dict[str, Any]] = []
        self.location_observations: list[dict[str, Any]] = []
        self.reporting_devices: set[str] = set()
        self.verified_by_responder: bool = False
        self.assigned_responder_id: str | None = None
        self.responder_status: str | None = None

        # Evaluated factors for priority engine
        self.factor_v: int = 0  # Severity (0-3)
        self.factor_p: int = 0  # People affected (0-3)
        self.factor_c: int = 0  # Confidence (0-3)
        self.factor_u: int = 0  # Uncertainty (0-3)


def reconcile_incident_group(group: IncidentGroup) -> ReconciledIncidentState:
    """
    Reconciles an incident group's events into a comprehensive derived state.
    Preserves all historical observations and calculates exact P, C, V, and U factors.
    """
    state = ReconciledIncidentState(group.incident_id)
    primary = group.primary_report

    state.incident_type = primary.payload.get("incident_type", "other")
    state.latitude = primary.payload.get("latitude", 0.0)
    state.longitude = primary.payload.get("longitude", 0.0)
    state.created_at_ms = primary.clock.wall_time_ms
    state.latest_update_ms = primary.clock.wall_time_ms
    state.current_description = primary.payload.get("description", "")

    # Collect chronological reports and observations
    all_observations = group.reports + group.observations
    all_observations.sort(
        key=lambda e: (e.clock.wall_time_ms, e.clock.logical_counter, e.clock.node_id, e.event_id)
    )

    observed_severities = []
    observed_trapped = []
    observed_counts = []
    locations = []
    all_evidence = set()

    for ev in all_observations:
        state.reporting_devices.add(ev.device_id)
        payload = ev.payload
        wall_time = ev.clock.wall_time_ms

        if wall_time > state.latest_update_ms:
            state.latest_update_ms = wall_time

        # Check responder role
        role = payload.get("observer_role", "civilian")
        if role in ("responder", "coordinator"):
            state.verified_by_responder = True

        # Evidence hashes
        for eh in payload.get("evidence_hashes", []):
            all_evidence.add(eh)

        # Severity
        if "severity" in payload and payload["severity"] is not None:
            sev = int(payload["severity"])
            observed_severities.append({"severity": sev, "device_id": ev.device_id, "time_ms": wall_time})
            state.current_severity = sev  # latest HLC wins for current value

        # People affected
        if "people_affected" in payload and payload["people_affected"] is not None:
            count = int(payload["people_affected"])
            observed_counts.append({"count": count, "device_id": ev.device_id, "time_ms": wall_time})
            state.current_people_affected = count

        # People trapped
        if "people_trapped" in payload and payload["people_trapped"] is not None:
            trapped = bool(payload["people_trapped"])
            observed_trapped.append({"trapped": trapped, "device_id": ev.device_id, "time_ms": wall_time})
            state.current_people_trapped = trapped

        # Location
        if "latitude" in payload and "longitude" in payload:
            lat = payload["latitude"]
            lon = payload["longitude"]
            locations.append({"lat": lat, "lon": lon, "device_id": ev.device_id})
            state.latitude = lat
            state.longitude = lon

        if "description" in payload and payload["description"]:
            state.current_description = payload["description"]

    state.evidence_hashes = list(all_evidence)
    state.severity_observations = observed_severities
    state.location_observations = locations

    # Evaluate Assignments and Status Updates
    if group.assign_events:
        latest_assign = max(
            group.assign_events,
            key=lambda e: (e.clock.wall_time_ms, e.clock.logical_counter),
        )
        state.assigned_responder_id = latest_assign.payload.get("responder_id")

    if group.status_events:
        latest_status = max(
            group.status_events,
            key=lambda e: (e.clock.wall_time_ms, e.clock.logical_counter),
        )
        state.responder_status = latest_status.payload.get("status")

    # Detect Conflicts
    # 1. Severity conflicts
    distinct_severities = {item["severity"] for item in observed_severities}
    severity_conflict = len(distinct_severities) > 1
    if severity_conflict:
        state.has_conflict = True
        min_sev = min(distinct_severities)
        max_sev = max(distinct_severities)
        state.conflict_details.append(
            f"Conflicting severity reports detected across devices: values range from {min_sev} to {max_sev}."
        )

    # 2. People trapped conflicts
    distinct_trapped = {item["trapped"] for item in observed_trapped}
    trapped_conflict = len(distinct_trapped) > 1
    if trapped_conflict:
        state.has_conflict = True
        state.conflict_details.append(
            "Conflicting observations on whether people are trapped at this location."
        )

    # 3. Location distance conflicts (> 100 meters between reported locations in same cluster)
    if len(locations) > 1:
        for i in range(len(locations)):
            for j in range(i + 1, len(locations)):
                d = haversine_distance_meters(
                    locations[i]["lat"], locations[i]["lon"],
                    locations[j]["lat"], locations[j]["lon"]
                )
                if d > 100.0:
                    state.has_conflict = True
                    state.conflict_details.append(
                        f"Location coordinates vary by {round(d, 1)}m between reporting devices."
                    )
                    break

    # Calculate Factor V: Severity (0-3)
    state.factor_v = max(0, min(3, state.current_severity))

    # Calculate Factor P: People Affected (0-3)
    # P = 0 when unknown, 1 for 1-2 people, 2 for 3-9 people, 3 for 10+ people or people_trapped flag
    if state.current_people_trapped or state.current_people_affected >= 10:
        state.factor_p = 3
    elif state.current_people_affected >= 3:
        state.factor_p = 2
    elif state.current_people_affected >= 1:
        state.factor_p = 1
    else:
        state.factor_p = 0

    # Calculate Factor C: Confidence (0-3)
    # C = 0 for no confidence, 1 for 1 unverified report,
    # 2 for corroboration from at least 2 distinct signed devices or attached evidence,
    # 3 for responder verification
    if state.verified_by_responder:
        state.factor_c = 3
    elif len(state.reporting_devices) >= 2 or len(state.evidence_hashes) > 0:
        state.factor_c = 2
    elif len(state.reporting_devices) == 1:
        state.factor_c = 1
    else:
        state.factor_c = 0

    # Calculate Factor U: Uncertainty (0-3)
    # U reflects mismatches, conflicting severity or location, retraction or unverifiable origin
    u_score = 0
    if severity_conflict:
        sev_diff = max(distinct_severities) - min(distinct_severities)
        u_score += 2 if sev_diff >= 2 else 1
    if trapped_conflict:
        u_score += 1
    if len(locations) > 1 and any(
        haversine_distance_meters(locations[0]["lat"], locations[0]["lon"], loc["lat"], loc["lon"]) > 100.0
        for loc in locations[1:]
    ):
        u_score += 1
    if len(state.reporting_devices) == 1 and len(state.evidence_hashes) == 0:
        # Single uncorroborated civilian source carries slight uncertainty
        u_score = max(u_score, 1)

    state.factor_u = max(0, min(3, u_score))

    return state
