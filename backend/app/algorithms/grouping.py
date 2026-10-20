from typing import Any
from app.algorithms.geo import haversine_distance_meters
from app.algorithms.hlc import compare_hlc
from app.models.events import RescueMeshEvent


class DisjointSet:
    """Disjoint-set (Union-Find) with path compression and union by rank."""
    def __init__(self):
        self.parent = {}
        self.rank = {}

    def find(self, item: str) -> str:
        if item not in self.parent:
            self.parent[item] = item
            self.rank[item] = 0
            return item
        if self.parent[item] != item:
            self.parent[item] = self.find(self.parent[item])
        return self.parent[item]

    def union(self, a: str, b: str) -> None:
        root_a = self.find(a)
        root_b = self.find(b)
        if root_a != root_b:
            if self.rank[root_a] < self.rank[root_b]:
                self.parent[root_a] = root_b
            elif self.rank[root_a] > self.rank[root_b]:
                self.parent[root_b] = root_a
            else:
                self.parent[root_b] = root_a
                self.rank[root_a] += 1


class IncidentGroup:
    """Represents a deterministically grouped incident with its constituent events."""
    def __init__(self, incident_id: str, primary_report: RescueMeshEvent):
        self.incident_id = incident_id
        self.primary_report = primary_report
        self.reports: list[RescueMeshEvent] = [primary_report]
        self.observations: list[RescueMeshEvent] = []
        self.status_events: list[RescueMeshEvent] = []
        self.assign_events: list[RescueMeshEvent] = []
        self.links: list[RescueMeshEvent] = []
        self.unlinks: list[RescueMeshEvent] = []

    @property
    def all_events(self) -> list[RescueMeshEvent]:
        combined = (
            self.reports
            + self.observations
            + self.status_events
            + self.assign_events
            + self.links
            + self.unlinks
        )
        # Deterministic sort by HLC then event_id
        return sorted(
            combined,
            key=lambda e: (e.clock.wall_time_ms, e.clock.logical_counter, e.clock.node_id, e.event_id),
        )


def group_incidents(
    events: list[RescueMeshEvent],
    distance_threshold_m: float = 150.0,
    time_window_ms: int = 7200000,  # 2 hours
) -> dict[str, IncidentGroup]:
    """
    Deterministically groups events into canonical incidents.
    All devices given the same event set will output the identical incident clusters
    with the exact same incident IDs regardless of event arrival order.
    """
    # 1. Separate events by type
    reports = [e for e in events if e.event_type.value == "report"]
    observations = [e for e in events if e.event_type.value == "observation"]
    links = [e for e in events if e.event_type.value == "link"]
    unlinks = [e for e in events if e.event_type.value == "unlink"]
    status_events = [e for e in events if e.event_type.value == "status"]
    assign_events = [e for e in events if e.event_type.value == "assign"]

    # Sort reports deterministically
    reports.sort(
        key=lambda e: (e.clock.wall_time_ms, e.clock.logical_counter, e.clock.node_id, e.event_id)
    )

    dset = DisjointSet()
    for r in reports:
        dset.find(r.event_id)

    # 2. Distance and time-window based grouping for matching incident types
    n = len(reports)
    for i in range(n):
        for j in range(i + 1, n):
            r1 = reports[i]
            r2 = reports[j]

            type1 = r1.payload.get("incident_type")
            type2 = r2.payload.get("incident_type")

            if type1 == type2:
                time_diff = abs(r1.clock.wall_time_ms - r2.clock.wall_time_ms)
                if time_diff <= time_window_ms:
                    dist = haversine_distance_meters(
                        r1.payload.get("latitude", 0.0),
                        r1.payload.get("longitude", 0.0),
                        r2.payload.get("latitude", 0.0),
                        r2.payload.get("longitude", 0.0),
                    )
                    if dist <= distance_threshold_m:
                        dset.union(r1.event_id, r2.event_id)

    # 3. Explicit coordinator links
    for l in links:
        inc_ids = l.payload.get("incident_ids", [])
        for k in range(len(inc_ids) - 1):
            dset.union(inc_ids[k], inc_ids[k + 1])

    # 4. Explicit coordinator unlinks
    unlinked_event_ids = set()
    for u in unlinks:
        split_ids = u.payload.get("split_event_ids", [])
        for sid in split_ids:
            unlinked_event_ids.add(sid)

    # 5. Form clusters
    clusters: dict[str, list[RescueMeshEvent]] = {}
    for r in reports:
        if r.event_id in unlinked_event_ids:
            # Isolated in its own cluster
            root = r.event_id
        else:
            root = dset.find(r.event_id)

        if root not in clusters:
            clusters[root] = []
        clusters[root].append(r)

    # 6. Build IncidentGroups with canonical ID = inc_{primary_report_id[:16]}
    result: dict[str, IncidentGroup] = {}
    event_id_to_incident_id: dict[str, str] = {}

    for root, cluster_reports in clusters.items():
        # Earliest report by HLC (ties broken by event_id) is primary
        primary = min(
            cluster_reports,
            key=lambda e: (e.clock.wall_time_ms, e.clock.logical_counter, e.clock.node_id, e.event_id),
        )
        canonical_incident_id = f"inc_{primary.event_id[:16]}"
        group = IncidentGroup(canonical_incident_id, primary)
        group.reports = cluster_reports

        for r in cluster_reports:
            event_id_to_incident_id[r.event_id] = canonical_incident_id
            # Also map the 16-char prefix in case observations link to inc_...
            event_id_to_incident_id[f"inc_{r.event_id[:16]}"] = canonical_incident_id

        result[canonical_incident_id] = group

    # 7. Associate observations, status, assigns, links, and unlinks
    for obs in observations:
        target_inc = obs.payload.get("incident_id")
        mapped_id = event_id_to_incident_id.get(target_inc) or target_inc
        if mapped_id in result:
            result[mapped_id].observations.append(obs)

    for st in status_events:
        target_inc = st.payload.get("incident_id")
        mapped_id = event_id_to_incident_id.get(target_inc) or target_inc
        if mapped_id in result:
            result[mapped_id].status_events.append(st)

    for asg in assign_events:
        target_inc = asg.payload.get("incident_id")
        mapped_id = event_id_to_incident_id.get(target_inc) or target_inc
        if mapped_id in result:
            result[mapped_id].assign_events.append(asg)

    return result
