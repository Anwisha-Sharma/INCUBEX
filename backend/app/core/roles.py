import time
from typing import Any
from app.models.events import RescueMeshEvent


class RoleRegistry:
    """
    Coordinator-issued role authorization registry (Module E1/E5).
    Tracks certified responder and coordinator public keys and lease periods.
    """
    def __init__(self, bootstrap_coordinator_ids: set[str] | None = None):
        # Set of trusted coordinator device IDs
        self.coordinators: set[str] = set(bootstrap_coordinator_ids or [])
        # device_id -> {"role": str, "grantor_id": str, "lease_until_ms": int}
        self.role_grants: dict[str, dict[str, Any]] = {}

    def bootstrap_coordinator(self, coordinator_device_id: str) -> None:
        """Trust-on-first-use (TOFU) or QR bootstrap for field coordinator identity."""
        self.coordinators.add(coordinator_device_id)

    def process_role_grant_event(self, event: RescueMeshEvent) -> bool:
        """
        Validate and record a roleGrant event:
        Must be originated and signed by an authorized coordinator.
        """
        if event.event_type.value != "roleGrant":
            return False

        grantor_id = event.device_id
        if grantor_id not in self.coordinators:
            return False  # Grantor is not an authorized coordinator

        payload = event.payload
        target_id = payload.get("target_device_id")
        role = payload.get("role", "responder")
        lease_until = payload.get("lease_until_ms", 0)

        if not target_id:
            return False

        self.role_grants[target_id] = {
            "role": role,
            "grantor_id": grantor_id,
            "lease_until_ms": lease_until,
        }

        # If granted coordinator role, also add to coordinators
        if role == "coordinator":
            self.coordinators.add(target_id)

        return True

    def get_effective_role(self, device_id: str, current_time_ms: int | None = None) -> str:
        """Returns certified role if lease is active; defaults to 'civilian'."""
        if device_id in self.coordinators:
            return "coordinator"

        grant = self.role_grants.get(device_id)
        if not grant:
            return "civilian"

        if current_time_ms is None:
            current_time_ms = int(time.time() * 1000)

        if current_time_ms > grant["lease_until_ms"]:
            return "civilian"  # Lease expired

        return grant["role"]
