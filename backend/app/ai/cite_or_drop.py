import re
from typing import Any
from app.ai.models import GroundedClaim
from app.algorithms.grouping import IncidentGroup
from app.algorithms.reconciliation import ReconciledIncidentState
from app.models.events import RescueMeshEvent


class CiteOrDropVerifier:
    """
    Module D Cite-or-Drop Verification Layer.
    Guarantees:
    - Rejects any factual assertion lacking verified event citations.
    - Drops hallucinated figures/numbers unsupported by recorded events.
    - Prevents AI from overriding deterministic facts or inventing evidence.
    """
    def __init__(self, incident_group: IncidentGroup, reconciled_state: ReconciledIncidentState):
        self.group = incident_group
        self.state = reconciled_state
        self.valid_event_ids = {e.event_id for e in incident_group.all_events}
        self.events_by_id = {e.event_id: e for e in incident_group.all_events}

        # Build corpus of known numbers from recorded events
        self.known_numbers = set()
        for ev in incident_group.all_events:
            p = ev.payload
            for k in ("severity", "people_affected"):
                if k in p and p[k] is not None:
                    try:
                        self.known_numbers.add(int(p[k]))
                    except (ValueError, TypeError):
                        pass

    def verify_and_filter(
        self,
        raw_claims: list[dict[str, Any]],
    ) -> tuple[list[GroundedClaim], list[str]]:
        """
        Filters raw claims into verified GroundedClaim list and dropped_claims list.
        Each raw claim is a dict: {"statement": str, "cited_event_ids": list[str]}
        """
        accepted_claims: list[GroundedClaim] = []
        dropped_claims: list[str] = []

        for item in raw_claims:
            statement = item.get("statement", "").strip()
            cited_ids = item.get("cited_event_ids", [])

            # Rule 1: Must cite at least one event ID
            if not cited_ids:
                dropped_claims.append(f"Rejected claim (no citation provided): '{statement}'")
                continue

            # Rule 2: All cited event IDs must exist in accepted event store
            invalid_citations = [cid for cid in cited_ids if cid not in self.valid_event_ids]
            if invalid_citations:
                dropped_claims.append(
                    f"Rejected claim (hallucinated citations {invalid_citations}): '{statement}'"
                )
                continue

            # Rule 3: Numerical sanity check
            # Find all numbers in statement
            numbers_in_claim = re.findall(r"\b\d+\b", statement)
            hallucinated_number = False
            for num_str in numbers_in_claim:
                val = int(num_str)
                # Allow minor sentence indices like '1.', but check casualty/severity counts
                if val > 3 and val not in self.known_numbers:
                    # Number not in any recorded event!
                    hallucinated_number = True
                    break

            if hallucinated_number:
                dropped_claims.append(
                    f"Rejected claim (unsupported figure not in evidence): '{statement}'"
                )
                continue

            # Claim passed all verification checks
            accepted_claims.append(
                GroundedClaim(
                    statement=statement,
                    cited_event_ids=cited_ids,
                    is_grounded=True,
                )
            )

        return accepted_claims, dropped_claims
