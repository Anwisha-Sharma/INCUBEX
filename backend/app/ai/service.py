import json
from typing import Any
import httpx

from app.ai.cite_or_drop import CiteOrDropVerifier
from app.ai.models import GroundedClaim, ResponderBriefing
from app.ai.offline_templates import generate_deterministic_briefing
from app.algorithms.grouping import IncidentGroup
from app.algorithms.priority import PriorityResult
from app.algorithms.reconciliation import ReconciledIncidentState
from app.core.config import settings


class BriefingService:
    """
    Modular AI Briefing Service.
    - Uses Groq API with evidence prompting when configured and online.
    - Applies Cite-or-Drop verifier to drop uncorroborated AI statements.
    - Seamlessly falls back to deterministic rule-based templates when offline or unconfigured.
    """
    @classmethod
    async def generate_briefing(
        cls,
        group: IncidentGroup,
        state: ReconciledIncidentState,
        priority: PriorityResult,
        force_offline: bool = False,
    ) -> ResponderBriefing:
        if force_offline:
            return generate_deterministic_briefing(group, state, priority)

        # 1. Attempt AI Grounding via Groq if configured
        if settings.GROQ_API_KEY:
            try:
                return await cls._call_groq_grounded_briefing(group, state, priority)
            except Exception:
                pass

        # 2. Attempt AI Grounding via Anthropic Claude if configured
        if settings.ANTHROPIC_API_KEY:
            try:
                return await cls._call_anthropic_grounded_briefing(group, state, priority)
            except Exception:
                pass

        # 3. Deterministic offline fallback
        briefing = generate_deterministic_briefing(group, state, priority)
        briefing.briefing_type = "deterministic_template_fallback"
        return briefing

    @classmethod
    async def _call_groq_grounded_briefing(
        cls,
        group: IncidentGroup,
        state: ReconciledIncidentState,
        priority: PriorityResult,
    ) -> ResponderBriefing:
        events_summary = [
            {
                "event_id": e.event_id,
                "event_type": e.event_type.value,
                "device_id": e.device_id,
                "payload": e.payload,
            }
            for e in group.all_events
        ]

        system_prompt = (
            "You are an evidence-grounded emergency incident synthesizer for RescueMesh. "
            "You MUST ONLY state facts supported by the provided event records. "
            "Every claim MUST include cited_event_ids matching an exact event_id. "
            "Do NOT invent casualty numbers, facts, or corroboration. "
            "Output JSON with keys: 'summary' (str), 'claims' (list of {'statement': str, 'cited_event_ids': list[str]}), 'actions' (list of str)."
        )

        user_prompt = json.dumps(
            {
                "incident_id": state.incident_id,
                "priority_score": priority.score,
                "priority_level": priority.level.value,
                "events": events_summary,
            }
        )

        headers = {
            "Authorization": f"Bearer {settings.GROQ_API_KEY}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": settings.GROQ_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        }

        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            raw_content = data["choices"][0]["message"]["content"]
            parsed = json.loads(raw_content)

        raw_claims = parsed.get("claims", [])

        # Run through Cite-or-Drop verifier
        verifier = CiteOrDropVerifier(group, state)
        accepted_claims, dropped_claims = verifier.verify_and_filter(raw_claims)

        # Distinguish verified vs unverified vs conflicting
        verified_facts = []
        unverified_reports = []
        conflicting = []

        for c in accepted_claims:
            if "conflict" in c.statement.lower() or "discrepancy" in c.statement.lower():
                conflicting.append(c)
            elif len(c.cited_event_ids) >= 2 or state.verified_by_responder:
                verified_facts.append(c)
            else:
                unverified_reports.append(c)

        return ResponderBriefing(
            incident_id=state.incident_id,
            briefing_type="ai_grounded",
            executive_summary=parsed.get("summary", ""),
            verified_facts=verified_facts,
            unverified_reports=unverified_reports,
            conflicting_observations=conflicting,
            recommended_actions=parsed.get("actions", []),
            dropped_claims=dropped_claims,
            evidence_event_ids=[e.event_id for e in group.all_events],
            priority_score=priority.score,
            priority_level=priority.level.value,
            priority_explanation=priority.explanation,
        )

    @classmethod
    async def _call_anthropic_grounded_briefing(
        cls,
        group: IncidentGroup,
        state: ReconciledIncidentState,
        priority: PriorityResult,
    ) -> ResponderBriefing:
        events_summary = [
            {
                "event_id": e.event_id,
                "event_type": e.event_type.value,
                "device_id": e.device_id,
                "payload": e.payload,
            }
            for e in group.all_events
        ]

        system_prompt = (
            "You are an evidence-grounded emergency incident synthesizer for RescueMesh. "
            "You MUST ONLY state facts supported by the provided event records. "
            "Every claim MUST include cited_event_ids matching an exact event_id. "
            "Do NOT invent casualty numbers, facts, or corroboration. "
            "Output JSON with keys: 'summary' (str), 'claims' (list of {'statement': str, 'cited_event_ids': list[str]}), 'actions' (list of str)."
        )

        user_content = json.dumps(
            {
                "incident_id": state.incident_id,
                "priority_score": priority.score,
                "priority_level": priority.level.value,
                "events": events_summary,
            }
        )

        headers = {
            "x-api-key": settings.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

        payload = {
            "model": settings.ANTHROPIC_MODEL,
            "max_tokens": 1024,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.1,
        }

        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            raw_text = data["content"][0]["text"]
            # Extract JSON from markdown code block if present
            if "```json" in raw_text:
                raw_text = raw_text.split("```json")[1].split("```")[0].strip()
            elif "```" in raw_text:
                raw_text = raw_text.split("```")[1].split("```")[0].strip()
            parsed = json.loads(raw_text)

        raw_claims = parsed.get("claims", [])
        verifier = CiteOrDropVerifier(group, state)
        accepted_claims, dropped_claims = verifier.verify_and_filter(raw_claims)

        verified_facts = []
        unverified_reports = []
        conflicting = []

        for c in accepted_claims:
            if "conflict" in c.statement.lower() or "discrepancy" in c.statement.lower():
                conflicting.append(c)
            elif len(c.cited_event_ids) >= 2 or state.verified_by_responder:
                verified_facts.append(c)
            else:
                unverified_reports.append(c)

        return ResponderBriefing(
            incident_id=state.incident_id,
            briefing_type="ai_grounded",
            executive_summary=parsed.get("summary", ""),
            verified_facts=verified_facts,
            unverified_reports=unverified_reports,
            conflicting_observations=conflicting,
            recommended_actions=parsed.get("actions", []),
            dropped_claims=dropped_claims,
            evidence_event_ids=[e.event_id for e in group.all_events],
            priority_score=priority.score,
            priority_level=priority.level.value,
            priority_explanation=priority.explanation,
        )

