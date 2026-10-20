"""
Unit tests for AI Provider Adapters (Groq & Anthropic Claude) in BriefingService.
All HTTP calls are strictly mocked; no external network requests are made.
"""

import json
from unittest.mock import AsyncMock, patch
import httpx
import pytest

from app.ai.service import BriefingService
from app.algorithms.grouping import IncidentGroup
from app.algorithms.priority import PriorityEngine, PriorityResult
from app.algorithms.reconciliation import reconcile_incident_group, ReconciledIncidentState
from app.core.config import settings
from app.models.enums import EventType, PriorityLevel
from app.models.events import HybridLogicalClock, RescueMeshEvent


EVENT_ID_VALID = "1111111111111111111111111111111111111111111111111111111111110001"
EVENT_ID_FAKE = "9999999999999999999999999999999999999999999999999999999999990001"


def make_sample_incident_group():
    ev1 = RescueMeshEvent(
        event_id=EVENT_ID_VALID,
        event_type=EventType.REPORT,
        device_id="device_test_001",
        clock=HybridLogicalClock(wall_time_ms=1000, logical_counter=0, node_id="device_test_001"),
        payload={
            "incident_type": "flood",
            "latitude": 19.076,
            "longitude": 72.877,
            "severity": 3,
            "people_affected": 8,
            "people_trapped": True,
            "description": "Severe flooding on Main St",
        },
        signature="mock_sig_000000000000000000000000000000000000000000000000000000000000",
    )
    group = IncidentGroup("inc_sample_ai", ev1)
    state = reconcile_incident_group(group)
    priority = PriorityEngine().evaluate(state, current_time_ms=1000 + 5000)
    return group, state, priority


@pytest.mark.asyncio
async def test_groq_grounded_briefing_mocked():
    group, state, priority = make_sample_incident_group()

    mock_groq_response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "summary": "Severe flood with 8 people affected.",
                            "claims": [
                                {
                                    "statement": "Flooding reported on Main St with 8 people affected.",
                                    "cited_event_ids": [EVENT_ID_VALID],
                                },
                                {
                                    "statement": "Fake assertion with no valid citation.",
                                    "cited_event_ids": [EVENT_ID_FAKE],
                                },
                            ],
                            "actions": ["Dispatch rescue boat"],
                        }
                    )
                }
            }
        ]
    }

    mock_response = httpx.Response(
        status_code=200,
        json=mock_groq_response,
        request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"),
    )

    with patch.object(settings, "GROQ_API_KEY", "mock-groq-key"):
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            briefing = await BriefingService.generate_briefing(group, state, priority)

            assert briefing.briefing_type == "ai_grounded"
            assert "8 people" in briefing.executive_summary
            # Verify Cite-or-Drop retained the grounded claim
            assert len(briefing.unverified_reports) == 1
            assert briefing.unverified_reports[0].cited_event_ids == [EVENT_ID_VALID]
            # Verify Cite-or-Drop dropped the fake claim
            assert len(briefing.dropped_claims) == 1
            assert EVENT_ID_FAKE in briefing.dropped_claims[0]
            assert briefing.recommended_actions == ["Dispatch rescue boat"]


@pytest.mark.asyncio
async def test_anthropic_grounded_briefing_mocked():
    group, state, priority = make_sample_incident_group()

    mock_claude_response = {
        "content": [
            {
                "type": "text",
                "text": "```json\n"
                + json.dumps(
                    {
                        "summary": "Claude: Critical flood requiring immediate response.",
                        "claims": [
                            {
                                "statement": "Flooding on Main St confirmed by initial report.",
                                "cited_event_ids": [EVENT_ID_VALID],
                            }
                        ],
                        "actions": ["Alert local emergency medical services"],
                    }
                )
                + "\n```",
            }
        ]
    }

    mock_response = httpx.Response(
        status_code=200,
        json=mock_claude_response,
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
    )

    # Groq unconfigured, Anthropic configured
    with patch.object(settings, "GROQ_API_KEY", None):
        with patch.object(settings, "ANTHROPIC_API_KEY", "mock-anthropic-key"):
            with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
                mock_post.return_value = mock_response

                briefing = await BriefingService.generate_briefing(group, state, priority)

                assert briefing.briefing_type == "ai_grounded"
                assert "Claude" in briefing.executive_summary
                assert len(briefing.unverified_reports) == 1
                assert briefing.unverified_reports[0].cited_event_ids == [EVENT_ID_VALID]


@pytest.mark.asyncio
async def test_provider_network_error_falls_back_to_offline_template():
    group, state, priority = make_sample_incident_group()

    with patch.object(settings, "GROQ_API_KEY", "mock-groq-key"):
        with patch("httpx.AsyncClient.post", side_effect=httpx.ConnectTimeout("Network unreachable")):
            # Must not raise exception, must seamlessly fall back to deterministic template
            briefing = await BriefingService.generate_briefing(group, state, priority)

            assert briefing.briefing_type == "deterministic_template_fallback"
            assert state.incident_id == briefing.incident_id
            assert briefing.priority_score == priority.score
