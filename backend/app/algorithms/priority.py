from typing import Any
from app.algorithms.reconciliation import ReconciledIncidentState
from app.models.enums import PriorityLevel


class PriorityResult:
    """Detailed explainable priority score result."""
    def __init__(
        self,
        score: int,
        raw_score: int,
        level: PriorityLevel,
        factors: dict[str, int],
        contributions: dict[str, int],
        explanation: str,
        safety_clamp_applied: bool,
        needs_verification: bool,
    ):
        self.score = score
        self.raw_score = raw_score
        self.level = level
        self.factors = factors
        self.contributions = contributions
        self.explanation = explanation
        self.safety_clamp_applied = safety_clamp_applied
        self.needs_verification = needs_verification

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "raw_score": self.raw_score,
            "level": self.level.value,
            "factors": self.factors,
            "contributions": self.contributions,
            "explanation": self.explanation,
            "safety_clamp_applied": self.safety_clamp_applied,
            "needs_verification": self.needs_verification,
        }


class PriorityEngine:
    """
    Deterministic Explainable Priority Engine.
    Formula: S = 5V + 3P + 2C + 2R - 3U

    Guarantees:
    - Strictly deterministic calculation.
    - Full transparency: every factor contribution is explainable in human terms.
    - Severe-incident safety clamp: V=3 incidents are never classified as LOW.
    - Needs-verification badge for high uncertainty (U >= 2).
    - Recency safety floor: R >= 2 when V >= 2 and unassigned.
    """
    WEIGHT_V = 5
    WEIGHT_P = 3
    WEIGHT_C = 2
    WEIGHT_R = 2
    WEIGHT_U = -3

    THRESHOLD_HIGH = 24
    THRESHOLD_MEDIUM = 12

    def __init__(
        self,
        threshold_high: int = 24,
        threshold_medium: int = 12,
    ):
        self.threshold_high = threshold_high
        self.threshold_medium = threshold_medium

    def compute_factor_r(
        self,
        created_at_ms: int,
        current_time_ms: int,
        severity: int,
        is_assigned: bool,
    ) -> int:
        """
        Calculate Time-Criticality factor R (0-3):
        - Starts at 3 for fresh incidents (< 30 minutes)
        - Decays to 2 for < 2 hours
        - Decays to 1 for < 6 hours
        - Decays to 0 for >= 6 hours
        Safety rule: R must not fall below 2 while severity >= 2 and no responder is assigned.
        """
        age_ms = max(0, current_time_ms - created_at_ms)
        age_minutes = age_ms / 60000.0

        if age_minutes < 30.0:
            r = 3
        elif age_minutes < 120.0:
            r = 2
        elif age_minutes < 360.0:
            r = 1
        else:
            r = 0

        # Safety rule: severe unassigned incidents cannot decay below R=2
        if severity >= 2 and not is_assigned:
            r = max(r, 2)

        return r

    def evaluate(
        self,
        state: ReconciledIncidentState,
        current_time_ms: int | None = None,
    ) -> PriorityResult:
        """Calculate complete explainable priority score for a reconciled incident."""
        if current_time_ms is None:
            import time
            current_time_ms = int(time.time() * 1000)

        v = state.factor_v
        p = state.factor_p
        c = state.factor_c
        r = self.compute_factor_r(
            created_at_ms=state.created_at_ms,
            current_time_ms=current_time_ms,
            severity=v,
            is_assigned=state.assigned_responder_id is not None,
        )
        u = state.factor_u

        # Formula: S = 5V + 3P + 2C + 2R - 3U
        contrib_v = self.WEIGHT_V * v
        contrib_p = self.WEIGHT_P * p
        contrib_c = self.WEIGHT_C * c
        contrib_r = self.WEIGHT_R * r
        contrib_u = self.WEIGHT_U * u

        raw_score = contrib_v + contrib_p + contrib_c + contrib_r + contrib_u
        score = raw_score

        # Severe-incident safety clamp: V=3 incidents cannot drop below MEDIUM
        safety_clamp_applied = False
        if v == 3 and score < self.THRESHOLD_MEDIUM:
            score = self.THRESHOLD_MEDIUM
            safety_clamp_applied = True

        # Determine level
        if score >= self.THRESHOLD_HIGH:
            level = PriorityLevel.HIGH
        elif score >= self.THRESHOLD_MEDIUM:
            level = PriorityLevel.MEDIUM
        else:
            level = PriorityLevel.LOW

        needs_verification = u >= 2 or (v >= 2 and c <= 1)

        # Build human-readable explanation
        explanation_parts = [
            f"Calculated Priority Score: {score} ({level.value}) via S = 5V + 3P + 2C + 2R - 3U.",
            f"Severity (V={v}): contribution +{contrib_v} pts.",
            f"People Affected (P={p}): contribution +{contrib_p} pts ({state.current_people_affected} affected, trapped={state.current_people_trapped}).",
            f"Confidence (C={c}): contribution +{contrib_c} pts ({len(state.reporting_devices)} devices, verified_responder={state.verified_by_responder}).",
            f"Time-Criticality (R={r}): contribution +{contrib_r} pts.",
            f"Uncertainty (U={u}): deduction {contrib_u} pts.",
        ]

        if safety_clamp_applied:
            explanation_parts.append(
                "SAFETY CLAMP TRIGGERED: Raw score was below MEDIUM, but severity is Critical (V=3), "
                "clamping priority level to at least MEDIUM to protect life safety."
            )

        if needs_verification:
            explanation_parts.append(
                "NEEDS VERIFICATION: High uncertainty or lack of corroboration detected; responder on-scene confirmation requested."
            )

        explanation = " ".join(explanation_parts)

        return PriorityResult(
            score=score,
            raw_score=raw_score,
            level=level,
            factors={"V": v, "P": p, "C": c, "R": r, "U": u},
            contributions={
                "V_contrib": contrib_v,
                "P_contrib": contrib_p,
                "C_contrib": contrib_c,
                "R_contrib": contrib_r,
                "U_contrib": contrib_u,
            },
            explanation=explanation,
            safety_clamp_applied=safety_clamp_applied,
            needs_verification=needs_verification,
        )

    def sensitivity_analysis(
        self,
        state: ReconciledIncidentState,
        current_time_ms: int | None = None,
    ) -> dict[str, Any]:
        """
        Evaluate stability of priority classification by varying each weight by ±1.
        Returns sensitivity bounds and level stability.
        """
        base_result = self.evaluate(state, current_time_ms)
        v = base_result.factors["V"]
        p = base_result.factors["P"]
        c = base_result.factors["C"]
        r = base_result.factors["R"]
        u = base_result.factors["U"]

        scores = []
        levels = set()

        for delta_v in (-1, 0, 1):
            w_v = self.WEIGHT_V + delta_v
            for delta_p in (-1, 0, 1):
                w_p = self.WEIGHT_P + delta_p
                for delta_c in (-1, 0, 1):
                    w_c = self.WEIGHT_C + delta_c
                    for delta_r in (-1, 0, 1):
                        w_r = self.WEIGHT_R + delta_r
                        for delta_u in (-1, 0, 1):
                            w_u = self.WEIGHT_U + delta_u
                            s = w_v * v + w_p * p + w_c * c + w_r * r + w_u * u
                            if v == 3 and s < self.THRESHOLD_MEDIUM:
                                s = self.THRESHOLD_MEDIUM

                            scores.append(s)
                            if s >= self.THRESHOLD_HIGH:
                                levels.add("HIGH")
                            elif s >= self.THRESHOLD_MEDIUM:
                                levels.add("MEDIUM")
                            else:
                                levels.add("LOW")

        return {
            "baseline_score": base_result.score,
            "baseline_level": base_result.level.value,
            "min_score": min(scores),
            "max_score": max(scores),
            "score_variance": max(scores) - min(scores),
            "possible_levels": sorted(list(levels)),
            "is_stable": len(levels) == 1,
        }
