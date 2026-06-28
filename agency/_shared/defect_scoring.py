"""Deterministic defect-severity scoring + the quarantine / hard-stop floor.

The floors the LLM cannot waive. The intake LLM proposes a defect_class,
a severity and a quarantine recommendation; this module owns the parts
that are policy, not judgement:

  * SAFETY-CRITICAL HARD-STOP — a safety_critical defect class, OR a
    defect on a batch flagged safety_critical in the register, forces
    severity = 1.0 and quarantine = True. There is no model temperature
    at which a safety-critical unit ships. This is a hard stop.

  * QUARANTINE GATE — severity >= QUARANTINE_FLOOR forces
    quarantine = True regardless of what the model recommended. A model
    that under-calls quarantine on a severe defect cannot pass the batch.

  * SEVERITY FLOOR BY MEASURE — when the raw signal carries a dimensional
    measurement, severity is floored by how far out of tolerance it is
    (in tolerance-bands), so a wildly out-of-spec reading cannot be
    presented as low severity.

These constants ARE the firm's disposition policy — quality tunes them
here, not by re-prompting a model.
"""
from __future__ import annotations

from typing import Any

QUARANTINE_FLOOR = 0.6           # severity at/above which quarantine is forced
SAFETY_CRITICAL_SEVERITY = 1.0   # safety-critical → max severity, no waiver
_VALID_CLASSES = ("dimensional", "cosmetic", "functional", "safety_critical", "none")


def _tolerance_severity(raw_signal: dict[str, Any]) -> float:
    """Severity floor from a dimensional measurement, if present.

    Distance from target in tolerance-bands → severity:
      within tolerance      → 0.0
      1–2× tolerance out     → 0.6 (quarantine floor)
      >2× tolerance out      → 0.85
    Returns 0.0 when no usable dimensional signal is present.
    """
    if not isinstance(raw_signal, dict):
        return 0.0
    try:
        value = float(raw_signal["value"])
        target = float(raw_signal["spec_target"])
        tol = float(raw_signal["spec_tolerance"])
    except (KeyError, TypeError, ValueError):
        return 0.0
    if tol <= 0:
        return 0.0
    bands = abs(value - target) / tol
    if bands <= 1.0:
        return 0.0
    if bands <= 2.0:
        return QUARANTINE_FLOOR
    return 0.85


def enforce_disposition(result: dict[str, Any], *,
                        batch_safety_critical: bool = False,
                        raw_signal: dict[str, Any] | None = None) -> dict[str, Any]:
    """Apply the deterministic disposition floors over the LLM's output.

    Mutates and returns `result`. The LLM may RAISE severity / call
    quarantine; it can never LOWER below these floors.
    """
    if not isinstance(result, dict):
        result = {}

    # Normalise the class — anything off-taxonomy fails closed to a real
    # defect that needs a human, never silently to 'none'.
    defect_class = str(result.get("defect_class") or "").strip().lower()
    if defect_class not in _VALID_CLASSES:
        defect_class = "functional"
        result["disposition_note"] = "off-taxonomy class → coerced to functional"
    result["defect_class"] = defect_class

    try:
        severity = float(result.get("severity") or 0)
    except (TypeError, ValueError):
        severity = 0.0

    # Severity floor from the dimensional measurement (LLM can't under-call).
    sig = raw_signal if isinstance(raw_signal, dict) else result.get("raw_signal")
    severity = max(severity, _tolerance_severity(sig))

    quarantine = bool(result.get("quarantine_recommended"))

    # ── SAFETY-CRITICAL HARD-STOP ─────────────────────────────────────
    safety = defect_class == "safety_critical" or bool(batch_safety_critical)
    if safety:
        severity = max(severity, SAFETY_CRITICAL_SEVERITY)
        quarantine = True
        result["safety_hard_stop"] = True
        # A safety-critical batch defect is, by disposition, a
        # safety_critical class even if the model called it dimensional.
        if defect_class != "safety_critical":
            result["defect_class"] = "safety_critical"
    else:
        result["safety_hard_stop"] = False

    # ── QUARANTINE GATE ───────────────────────────────────────────────
    if severity >= QUARANTINE_FLOOR:
        quarantine = True

    result["severity"] = round(min(severity, 1.0), 4)
    result["quarantine_recommended"] = quarantine
    return result
