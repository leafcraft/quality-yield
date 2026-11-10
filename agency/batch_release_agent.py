"""Batch Disposition & Release — the FINISHER / actuator.

auto_discover matches `batch_release_agent`. This agent sits BEHIND the
QA-manager gate. It never releases a batch on its own authority — it re-checks
that a human actually released, re-derives the safety invariant in code, renders
the release certificate, and records an append-only ledger entry. It is the
concrete "@chain fails closed":

  a) release RE-CHECK — reaching this agent does not mean released. An allow-token
     allowlist + block-token denylist; an UNKNOWN verdict fails closed.
  b) deterministic invariants a human can't waive — a batch on record, and the
     fail-closed actuator (agency/_shared/batch_release.py), idempotent per
     batch+decision.
  b2) TWO SIGNATURES on a safety-critical batch — the QA Manager's release is
     the first signature; the batch then waits for the Quality Director's
     (status `awaiting_second_signature`, second_signer names the missing
     seat). It ships only when both seats have released it, and — when the
     Inbox records who answered — two different people. The same person
     signing twice blocks the release.
  c) render the real certificate to ./out; on a tripped floor, DELETE the partial
     and record `status: blocked` with reasons — never a half-released batch.
  d) dev-store recovery — after the HITL gate the upstream payload is dropped, so
     the finisher recovers the case from the session stash / dev store.

Phases: phase 1 (from intake / the engineer) routes the batch to the QA manager
for a decision, and BOUNDS the rework loop — a batch that loops on rework past
its round bound routes to the Quality Director (a terminal). Phase 2 (from the
manager, released) issues the certificate — after the second signature when
the batch is safety-critical.
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
# The two seats whose `release` counts as a signature on a safety-critical batch.
RELEASE_SEATS = ("qa_manager_human", "quality_director_human")


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


def _signer(input_data: dict) -> tuple[str, str]:
    """(seat, operator_id) of the person whose reply reached the finisher —
    the SDK stamps every human reply with source_agent + operator_id."""
    sources = [input_data]
    up = input_data.get("upstream_yields")
    if isinstance(up, dict):
        sources.append(up)
        sources.extend(v for v in up.values() if isinstance(v, dict))
    for src in sources:
        seat = src.get("source_agent")
        if isinstance(seat, str) and seat:
            return seat, str(src.get("operator_id") or "")
    return "", ""


# Values that name nobody in particular (the Inbox's fallback when a reply
# carries no login).
_UNKNOWN_SIGNERS = frozenset({"", "operator", "system", "unknown", "anonymous"})


def signature_status(signatures: list[dict]) -> dict[str, Any]:
    """The two-signature rule for a safety-critical batch. Pure; testable.

    complete     — both RELEASE_SEATS signed, by two identified, different people.
    conflict     — the same person signed for both seats (four-eyes broken).
    unidentified — seats whose signer is not recorded (no login on the reply,
                   or the Inbox's generic name): two different people cannot be
                   proven, so the batch does not ship.
    missing      — the seat still to sign ("" when both have signed).
    """
    by_seat: dict[str, set[str]] = {}
    for s in signatures or []:
        seat = str(s.get("seat") or "")
        if seat in RELEASE_SEATS:
            by_seat.setdefault(seat, set()).add(str(s.get("operator_id") or "").strip().lower())
    missing = next((seat for seat in RELEASE_SEATS if seat not in by_seat), "")
    if missing:
        return {"complete": False, "conflict": False, "unidentified": [], "missing": missing}
    people = {seat: by_seat[seat] - _UNKNOWN_SIGNERS for seat in RELEASE_SEATS}
    unidentified = [seat for seat in RELEASE_SEATS if not people[seat]]
    if unidentified:
        return {"complete": False, "conflict": False, "unidentified": unidentified, "missing": ""}
    if people["qa_manager_human"] & people["quality_director_human"]:
        return {"complete": False, "conflict": True, "unidentified": [], "missing": ""}
    return {"complete": True, "conflict": False, "unidentified": [], "missing": ""}


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
    result.pop("_signoff", None)
    return result


def enforce_release_floor(result: Any, context: dict) -> dict:
    """The finisher floor — issue ONLY on a real release that the actuator ships."""
    result = result if isinstance(result, dict) else {}
    result.setdefault("block_reasons", [])
    result.setdefault("certificate_path", "")
    result.setdefault("disposition", "")
    result.setdefault("released", False)

    if result.get("status") in ("routed", "exhausted", "awaiting_second_signature"):
        return _clean(result)  # routed / terminated / waiting for a signature — nothing to issue

    if not result.get("released"):
        if result.get("status") == "blocked" and result["block_reasons"]:
            _delete_partial(result)              # the body already said why
            return _clean(result)
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
    # actuator ships a safety-critical batch only with the two-signature sign-off.
    act = enact_disposition(batch_id, "release",
                            safety_critical=bool(case.get("safety_critical") or case.get("safety_hard_stop")),
                            safety_signoff=bool(result.get("_signoff")))
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

    batch_id = str(case.get("batch_id") or "")
    out: dict[str, Any] = {
        "routed_for_disposition": False, "released": False, "disposition": "",
        "certificate_path": "", "status": "", "block_reasons": [],
        "second_signer": "", "batch_id": batch_id,
        "_input": input_data, "_case": case,
    }

    if _is_released(input_data) and (case.get("safety_critical") or case.get("safety_hard_stop")):
        # Safety-critical: the release is a SIGNATURE; it ships on the second.
        seat, operator_id = _signer(input_data)
        if seat in RELEASE_SEATS and batch_id:
            signatures = store.record_release_signature(batch_id, seat, operator_id)
        else:
            signatures = store.release_signatures(batch_id)
        st = signature_status(signatures)
        if st["complete"]:
            out["released"] = True                  # the floor issues it
            out["_signoff"] = True
        elif st["unidentified"]:
            out["status"] = "blocked"
            out["disposition"] = "release"
            out["block_reasons"].append(
                f"who signed as {', '.join(st['unidentified'])} is not recorded — a safety-critical "
                f"release needs both signers identified (give each seat its operator_ids)")
        elif st["conflict"]:
            out["status"] = "blocked"
            out["disposition"] = "release"
            out["block_reasons"].append("the same person signed as QA Manager and Quality Director "
                                        "— a safety-critical release needs two different people")
        else:
            out["status"] = "awaiting_second_signature"
            out["second_signer"] = st["missing"]
            logger.info(f"[batch-release] {batch_id} safety-critical: signature recorded, "
                        f"waiting for {st['missing']}")
    elif _is_released(input_data):
        out["released"] = True                      # phase 2 — the floor issues it
    elif _is_blocked(input_data):
        out["status"] = "blocked"
        out["disposition"] = _signal(input_data)
        store.clear_release_signatures(batch_id)    # earlier signatures are void
    else:
        store.clear_release_signatures(batch_id)    # a new disposition round starts unsigned
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
