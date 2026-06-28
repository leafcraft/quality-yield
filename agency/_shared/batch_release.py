"""Fail-closed batch-release / quarantine actuator + idempotency.

The actuator that enacts a QA-manager batch decision against the
MES/ERP. Two properties that MUST be code, not prompt:

  * FAIL-CLOSED — the only decision that ships a batch is an explicit
    `release` from the batch-release authority (qa_manager). Anything
    else — quarantine, rework, halt, an unrecognised verdict, a missing
    decision, a batch flagged safety_critical without an explicit
    release — holds the batch. The default is NOT to ship.

  * IDEMPOTENT — a batch_id+decision is enacted at most once. A duplicate
    resume after a HITL gap (or a retried chain) returns the prior
    actuation id instead of double-shipping. The dedupe key is the
    batch_id+decision pair.

In prod `_dispatch` is the MES/ERP release API; in dev it records the
actuation in-process so the demo is observable and idempotent.
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from leafmesh import LeafMeshLogger

logger = LeafMeshLogger(__name__)

_LOCK = threading.Lock()
# batch_id|decision → actuation record (idempotency ledger). In prod this
# is the MES actuation log; in dev it lives in-process.
_ACTUATED: dict[str, dict[str, Any]] = {}

# The ONLY verdict that ships. Everything else holds (fail-closed).
_RELEASE_VERDICT = "release"
_KNOWN_VERDICTS = ("release", "quarantine", "rework", "halt")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dispatch(batch_id: str, action: str) -> str:
    """Enact the disposition against the MES/ERP. PROD: real release API.
    Returns the actuation id. Dev: record + return a synthetic id."""
    logger.info(f"[batch-release] ENACT {action} on batch {batch_id}")
    return f"act-{action}-{uuid.uuid4().hex[:10]}"


def enact_disposition(batch_id: str, decision: str, *,
                      safety_critical: bool = False) -> dict[str, Any]:
    """Enact a batch disposition, fail-closed + idempotent.

    Returns {batch_id, enacted_action, shipped, actuation_id, idempotent,
    reason}. `shipped` is True ONLY for an explicit release of a batch
    that is not safety-critical-without-release.
    """
    batch_id = str(batch_id or "")
    decision = str(decision or "").strip().lower()

    # Fail-closed normalisation: an unknown / empty verdict holds.
    if decision not in _KNOWN_VERDICTS:
        reason = f"unrecognised verdict {decision!r} → hold (fail-closed)"
        action = "halt"
    elif decision == _RELEASE_VERDICT and safety_critical:
        # A safety-critical batch cannot be released by this actuator's
        # default path — it requires the explicit safety sign-off the
        # caller must have already cleared; absent that, hold.
        reason = "safety_critical batch — release withheld pending safety sign-off"
        action = "halt"
    else:
        reason = f"enacted {decision}"
        action = decision

    shipped = action == _RELEASE_VERDICT

    key = f"{batch_id}|{action}"
    with _LOCK:
        if key in _ACTUATED:
            prior = _ACTUATED[key]
            logger.info(f"[batch-release] idempotent replay {key} → {prior['actuation_id']}")
            return {**prior, "idempotent": True}
        actuation_id = _dispatch(batch_id, action) if batch_id else ""
        record = {
            "batch_id": batch_id,
            "enacted_action": action,
            "shipped": shipped,
            "actuation_id": actuation_id,
            "idempotent": False,
            "reason": reason,
            "enacted_at_utc": _now(),
        }
        if batch_id:
            _ACTUATED[key] = record
    return record
