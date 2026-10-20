from typing import Any
from pydantic import BaseModel, Field


class GroundedClaim(BaseModel):
    """A factual claim tied directly to verified event citations."""
    statement: str = Field(..., description="The factual observation or claim statement")
    cited_event_ids: list[str] = Field(..., description="Event IDs corroborating this claim")
    is_grounded: bool = Field(default=True, description="True if citations were verified in accepted event store")


class ResponderBriefing(BaseModel):
    """
    Structured operational briefing for first responders and dispatch.
    Strictly distinguishes verified facts from unverified claims and conflicting observations.
    """
    incident_id: str
    briefing_type: str = Field(..., description="'ai_grounded' or 'deterministic_template'")
    executive_summary: str
    verified_facts: list[GroundedClaim] = Field(default_factory=list)
    unverified_reports: list[GroundedClaim] = Field(default_factory=list)
    conflicting_observations: list[GroundedClaim] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    dropped_claims: list[str] = Field(
        default_factory=list,
        description="Claims rejected by the Cite-or-Drop verifier due to lack of evidence citations",
    )
    evidence_event_ids: list[str] = Field(default_factory=list)
    priority_score: int
    priority_level: str
    priority_explanation: str
