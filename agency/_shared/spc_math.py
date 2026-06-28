"""Deterministic SPC control-chart math + Western Electric rules.

The SPC out-of-control floor the LLM cannot waive. X-bar control limits
(±3σ UCL/LCL) and the Western Electric run rules are deterministic domain
MATH that must not be hallucinated:

    Rule 1 — any point beyond 3σ
    Rule 2 — 2 of 3 consecutive points beyond 2σ (same side)
    Rule 3 — 7 consecutive points on the same side of the mean

These constants ARE the firm's process-control policy — a process
engineer tunes the rules here, not by re-prompting a model. Production
reads the measurement window from the plant historian (store.spc_window
in dev); the arithmetic below is identical in both.
"""
from __future__ import annotations

import statistics
from typing import Any

MIN_SAMPLES = 5          # below this there is no chart to speak of
SIGMA_LIMIT = 3          # control-limit multiplier (±3σ)
TWO_SIGMA = 2            # Western Electric rule-2 zone
RULE3_RUN = 7            # Western Electric rule-3 run length


def control_chart(values: list[float]) -> dict[str, Any]:
    """Compute X-bar / σ / UCL / LCL + Western Electric out-of-control
    signals over an ordered measurement window. Returns the full chart
    shape; `out_of_control_signals` is empty when the process is in
    control. Returns `enough_samples: False` below MIN_SAMPLES.
    """
    values = [float(v) for v in (values or [])]
    if len(values) < MIN_SAMPLES:
        return {
            "enough_samples": False, "samples": len(values),
            "x_bar": 0.0, "sigma": 0.0, "ucl": 0.0, "lcl": 0.0,
            "out_of_control_signals": [],
        }

    x_bar = statistics.mean(values)
    sigma = statistics.stdev(values) if len(values) > 1 else 0.0
    ucl = x_bar + SIGMA_LIMIT * sigma
    lcl = x_bar - SIGMA_LIMIT * sigma
    two_upper = x_bar + TWO_SIGMA * sigma
    two_lower = x_bar - TWO_SIGMA * sigma

    signals: list[dict[str, Any]] = []
    # Rule 1 — any point beyond 3σ
    for i, v in enumerate(values):
        if v > ucl or v < lcl:
            signals.append({"rule": "beyond_3_sigma", "index": i, "value": v})
    # Rule 2 — 2 of 3 consecutive beyond 2σ (same side)
    for i in range(2, len(values)):
        window3 = values[i - 2:i + 1]
        if sum(v > two_upper for v in window3) >= 2 \
                or sum(v < two_lower for v in window3) >= 2:
            signals.append({"rule": "two_of_three_2_sigma", "index": i,
                            "window": window3})
    # Rule 3 — RULE3_RUN consecutive on the same side of the mean
    if len(values) >= RULE3_RUN:
        for i in range(RULE3_RUN - 1, len(values)):
            run = values[i - (RULE3_RUN - 1):i + 1]
            if all(v > x_bar for v in run) or all(v < x_bar for v in run):
                signals.append({"rule": "seven_consecutive_one_side", "index": i,
                                "side": "above" if run[0] > x_bar else "below"})

    return {
        "enough_samples": True, "samples": len(values),
        "x_bar": round(x_bar, 4), "sigma": round(sigma, 4),
        "ucl": round(ucl, 4), "lcl": round(lcl, 4),
        "out_of_control_signals": signals,
    }
