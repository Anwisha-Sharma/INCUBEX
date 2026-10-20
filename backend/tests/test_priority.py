from app.algorithms.grouping import IncidentGroup
from app.algorithms.priority import PriorityEngine
from app.algorithms.reconciliation import ReconciledIncidentState, reconcile_incident_group
from app.models.enums import PriorityLevel


def test_priority_formula_exact_score():
    engine = PriorityEngine()
    state = ReconciledIncidentState("inc_test1")

    # Manually configure factors: V=2, P=2, C=2, R=2, U=0
    # Expected S = 5(2) + 3(2) + 2(2) + 2(2) - 3(0) = 10 + 6 + 4 + 4 - 0 = 24 -> HIGH
    state.factor_v = 2
    state.factor_p = 2
    state.factor_c = 2
    state.factor_u = 0
    state.created_at_ms = 1000000

    result = engine.evaluate(state, current_time_ms=1000000 + 10000)  # fresh -> R=3, but let's test R calculation
    # fresh (< 30 min) -> R=3 -> S = 10 + 6 + 4 + 6 - 0 = 26 -> HIGH
    assert result.factors["V"] == 2
    assert result.factors["P"] == 2
    assert result.factors["C"] == 2
    assert result.factors["R"] == 3
    assert result.factors["U"] == 0
    assert result.score == 26
    assert result.level == PriorityLevel.HIGH


def test_safety_clamp_prevents_critical_incident_from_dropping_to_low():
    engine = PriorityEngine()
    state = ReconciledIncidentState("inc_test_clamp")

    # Critical severity V=3, but P=0, C=0, R=0, high uncertainty U=3
    # Raw Score = 5(3) + 3(0) + 2(0) + 2(0) - 3(3) = 15 - 9 = 6 (normally LOW)
    # But because V=3, safety clamp MUST elevate score to at least 12 (MEDIUM)
    state.factor_v = 3
    state.factor_p = 0
    state.factor_c = 0
    state.factor_u = 3
    state.created_at_ms = 1000000
    state.assigned_responder_id = "assigned"  # allow R to drop to 0

    result = engine.evaluate(state, current_time_ms=1000000 + 3600000 * 10)  # 10 hours old -> R=0
    assert result.raw_score == 6
    assert result.score == 12  # Clamped to MEDIUM
    assert result.level == PriorityLevel.MEDIUM
    assert result.safety_clamp_applied is True
    assert "SAFETY CLAMP TRIGGERED" in result.explanation


def test_recency_safety_floor_for_unassigned_severe_incidents():
    engine = PriorityEngine()
    state = ReconciledIncidentState("inc_test_recency")

    state.factor_v = 2
    state.factor_p = 1
    state.factor_c = 1
    state.factor_u = 0
    state.created_at_ms = 1000000
    state.assigned_responder_id = None  # UNASSIGNED!

    # 12 hours old -> ordinarily R would decay to 0, but since V=2 and unassigned, R cannot fall below 2
    result = engine.evaluate(state, current_time_ms=1000000 + 3600000 * 12)
    assert result.factors["R"] == 2


def test_sensitivity_analysis():
    engine = PriorityEngine()
    state = ReconciledIncidentState("inc_sens")
    state.factor_v = 2
    state.factor_p = 2
    state.factor_c = 2
    state.factor_u = 1
    state.created_at_ms = 1000000

    analysis = engine.sensitivity_analysis(state, current_time_ms=1000000 + 1000)
    assert "baseline_score" in analysis
    assert "min_score" in analysis
    assert "max_score" in analysis
    assert analysis["min_score"] <= analysis["baseline_score"] <= analysis["max_score"]
