"""Batch Disposition & Release — the FINISHER / actuator.

auto_discover matches `batch_release_agent`. This agent sits BEHIND the
QA-manager gate. It never releases a batch on its own authority — it re-checks
that a human actually released, re-derives the safety invariant in code, renders
the release certificate, and records an append-only ledger entry. It is the
concrete "@chain fails closed":

  a) release RE-CHECK — reaching this agent does not mean released. An allow-token
     allowlist + block-token denylist; an UNKNOWN verdict fails closed.
  b) deterministic invariants a human can't waive — a batch on record, and the
     fail-closed actuator (agency/_shared/batch_release.py) that NEVER ships a
     safety-critical batch on this path and is idempotent per batch+decision.
  c) render the real certificate to ./out; on a tripped floor, DELETE the partial
     and record `status: blocked` with reasons — never a half-released batch.
  d) dev-store recovery — after the HITL gate the upstream payload is dropped, so
     the finisher recovers the case from the session stash / dev store.

Phases: phase 1 (from intake / the engineer) routes the batch to the QA manager
for a decision, and BOUNDS the rework loop — a batch that loops on rework past
its round bound routes to the Quality Director (a terminal). Phase 2 (from the
manager, released) issues the certificate.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from leafmesh import LeafMeshLogger, chain

from agency._shared import store
from agency._shared.batch_release import enact_disposition
from agency._shared.session_stash import resolve_session_id, stash_get, stash_put

logger = LeafMeshLogger(__name__)

_OUT_DIR = Path("./out")
_RELEASE_TOKENS = frozenset({"release", "released", "approve", "approved", "ship", "pass"})
_BLOCK_TOKENS = frozenset({"quarantine", "rework", "halt", "hold", "held",
                           "held_for_review", "reject", "rejected", "no", "deny", "denied"})
MAX_DISPOSITION_ROUNDS = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _signal(input_data: dict) -> str:
    """The human verdict, from wherever the SDK delivered it (shape-tolerant)."""
    sources = [input_data]
    up = input_data.get("upstream_yields")
    if isinstance(up, dict):
        sources.extend(v for v in up.values() if isinstance(v, dict))
    for src in sources:
        for key in ("human_message", "human_decision", "decision", "message"):
            val = src.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip().lower()
    return ""


def _is_released(input_data: dict) -> bool:
    """Fail closed: only an explicit release token counts; unknown => not released."""
    sig = _signal(input_data)
    if sig in _BLOCK_TOKENS:
        return False
    return sig in _RELEASE_TOKENS


def _is_blocked(input_data: dict) -> bool:
    return _signal(input_data) in _BLOCK_TOKENS


def _recover_case(input_data: dict, context: dict) -> dict:
    """Recover the case across the HITL boundary: upstream -> stash -> dev store,
    always merging the batch record so the safety flag is authoritative."""
    case: dict[str, Any] = {}
    up = input_data.get("upstream_yields") or {}
    candidates = [input_data]
    if isinstance(up, dict):
        candidates.extend(v for v in up.values() if isinstance(v, dict))
    for src in candidates:
        if isinstance(src, dict) and src.get("batch_id"):
            case = dict(src)
            break
    batch_id = str(case.get("batch_id") or input_data.get("batch_id") or "")
    if not batch_id:
        sid = resolve_session_id(input_data, context)
        stashed = stash_get(sid)
        if stashed.get("batch_id"):
            case = dict(stashed)
            batch_id = str(case.get("batch_id"))
    if not batch_id:
        return {"batch_id": ""}
    case.setdefault("batch_id", batch_id)
    # The batch record is authoritative for the safety flag — never trust the
    # upstream echo for the one invariant that can't be waived.
    batch = store.get_batch(batch_id)
    if batch:
        case["safety_critical"] = bool(batch.get("safety_critical"))
        case.setdefault("line_id", batch.get("line_id", ""))
        case.setdefault("product_class", batch.get("product_class", ""))
    return case


def _render_certificate(case: dict, act: dict) -> str:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    batch_id = str(case.get("batch_id") or "batch")
    path = _OUT_DIR / f"{batch_id}_release_certificate.md"
    lines = [
        f"# Batch release certificate — {batch_id}",
        "",
        f"- Line: {case.get('line_id', '')}",
        f"- Product class: {case.get('product_class', '')}",
        f"- Disposition: RELEASED",
        f"- Actuation id: {act.get('actuation_id', '')}",
        f"- Released at (UTC): {_now()}",
        f"- Authority: QA Manager (batch-release authority) on record",
        "",
        "_Enacted against the MES/ERP and hash-chained in the quality audit log._",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def _delete_partial(result: dict) -> None:
    p = result.get("certificate_path")
    if p:
        Path(p).unlink(missing_ok=True)
        result["certificate_path"] = ""


def _clean(result: dict) -> dict:
    result.pop("_input", None)
    result.pop("_case", None)
    return result


def enforce_release_floor(result: Any, context: dict) -> dict:
    """The finisher floor — issue ONLY on a real release that the actuator ships."""
    result = result if isinstance(result, dict) else {}
    result.setdefault("block_reasons", [])
    result.setdefault("certificate_path", "")
    result.setdefault("disposition", "")
    result.setdefault("released", False)

    if result.get("status") in ("routed", "exhausted"):
        return _clean(result)  # phase 1: routed / terminated — nothing to issue

    if not result.get("released"):
        result["status"] = "blocked"
        if _is_blocked(result.get("_input", {})):
            result["block_reasons"].append("authority quarantined / reworked / held")
        else:
            result["block_reasons"].append("no explicit release on record (fail closed)")
        _delete_partial(result)
        logger.info(f"[batch-release] floor BLOCKED: {result['block_reasons']}")
        return _clean(result)

    case = result.get("_case") or {}
    batch_id = str(case.get("batch_id") or result.get("batch_id") or "")
    if not batch_id:
        result.update(status="blocked", released=False)
        result["block_reasons"].append("no batch on record")
        _delete_partial(result)
        return _clean(result)

    # Deterministic actuation — fail-closed + idempotent + safety hard-stop. The
    # actuator never ships a safety-critical batch on this path.
    act = enact_disposition(batch_id, "release",
                            safety_critical=bool(case.get("safety_critical") or case.get("safety_hard_stop")))
    if not act.get("shipped"):
        result.update(status="blocked", released=False)
        result["block_reasons"].append(act.get("reason") or "actuator withheld release")
        _delete_partial(result)
        logger.info(f"[batch-release] actuator withheld batch={batch_id}: {act.get('reason')}")
        return _clean(result)

    # Passed the floor — render, record.
    result["certificate_path"] = _render_certificate(case, act)
    result["batch_id"] = batch_id
    store.record_release_outbox({"batch_id": batch_id, "action": "release",
                                 "actuation_id": act.get("actuation_id", "")})
    store.append_release_ledger({"batch_id": batch_id, "event": "released",
                                 "actuation_id": act.get("actuation_id", ""),
                                 "certificate_path": result["certificate_path"]})
    result.update(disposition="released", status="released")
    logger.info(f"[batch-release] released batch {batch_id} -> {result['certificate_path']}")
    return _clean(result)


@chain(enforce_release_floor)
async def batch_release_agent(llm_response: Any, input_data: dict, context: dict) -> dict:
    input_data = input_data if isinstance(input_data, dict) else {}
    case = _recover_case(input_data, context)
    sid = resolve_session_id(input_data, context)
    if case.get("batch_id"):
        stash_put(sid, case)   # keep it recoverable across the release gate

    out: dict[str, Any] = {
        "routed_for_disposition": False, "released": False, "disposition": "",
        "certificate_path": "", "status": "", "block_reasons": [],
        "batch_id": str(case.get("batch_id") or ""),
        "_input": input_data, "_case": case,
    }

    if _is_released(input_data):
        out["released"] = True                      # phase 2 — the floor issues it
    elif _is_blocked(input_data):
        out["status"] = "blocked"
        out["disposition"] = _signal(input_data)
    else:
        # phase 1 — route to the authority, bounding the rework loop.
        rounds = store.bump_disposition_round(str(case.get("batch_id") or ""))
        if rounds > MAX_DISPOSITION_ROUNDS:
            out["status"] = "exhausted"
            out["block_reasons"].append(
                f"disposition loop exceeded {MAX_DISPOSITION_ROUNDS} rounds — escalated to Quality Director"
            )
        else:
            out["status"] = "routed"
            out["routed_for_disposition"] = True
    return out
