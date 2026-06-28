"""inspection_intake_agent — context pull + the disposition floor.

The LLM (configs/config.yaml) normalises the raw signal and proposes a
defect_class / severity / quarantine recommendation + the QA briefing.
This micro-module owns the two things the LLM must NOT be trusted to do:

@pre_compose  load_inspection_context — pull the BATCH genealogy record
              (+ its incoming lots) and any PRIOR recalls touching the
              batch from the source store (MES/LIMS in prod) so the
              classifier sees whether the batch is safety_critical and
              what the dimensional spec is, rather than trusting whatever
              the upstream payload echoed. Self-reliant context pull. On
              a raw vision/complaint event it also resolves the seeded
              defect record by source_event_id so the demo runs end-to-end.

@chain        enforce_disposition_floor — the deterministic DEFECT-SEVERITY
              / QUARANTINE GATE and the SAFETY-CRITICAL HARD-STOP
              (agency/_shared/defect_scoring). A safety_critical class —
              or any defect on a batch the register flags safety_critical
              — forces severity 1.0 + quarantine, no waiver. severity >=
              0.6 forces quarantine. A dimensional reading floors severity
              by how far out of tolerance it is. The LLM may RAISE risk;
              it can never present a severe / safety-critical defect as a
              pass. The gate fires on a direct call too (it lives in the
              @chain, which the SDK runs after the body either way).

@compose      per-downstream payloads — intake fans out to three roles
              that genuinely need different things: the quality engineer
              needs the full defect + raw signal to correlate root cause;
              the QA manager (batch-release authority) needs the
              disposition + briefing to decide release/quarantine; the QA
              inspector (first-line check) needs only the batch + briefing.
              @compose only reshapes in-mesh — on a direct call the body
              shape is returned (verified empirically).
"""
from __future__ import annotations

import json
from typing import Any

from leafmesh import LeafMeshLogger, chain, compose, pre_compose

from agency._shared import store
from agency._shared.defect_scoring import enforce_disposition

logger = LeafMeshLogger(__name__)


def _resolve_batch_id(input_data: dict[str, Any], result: dict[str, Any]) -> str:
    for src in (result, input_data):
        if isinstance(src, dict) and src.get("batch_id"):
            return str(src["batch_id"])
    # Fall back to the seeded defect record (raw vision/complaint event).
    sid = str((input_data or {}).get("source_event_id") or "")
    if sid:
        defect = store.get_defect(sid)
        if defect.get("batch_id"):
            return str(defect["batch_id"])
    return ""


# ── @pre_compose — pull batch genealogy + lots + prior recalls ───────
def load_inspection_context(data: Any, ctx: Any) -> dict[str, Any]:
    """Fetch the batch record + incoming lots + prior recalls. PROD: MES/LIMS."""
    input_data = data if isinstance(data, dict) else {}
    # The raw event may name a batch directly or via a seeded defect id.
    sid = str(input_data.get("source_event_id") or "")
    defect = store.get_defect(sid) if sid else {}
    batch_id = str(input_data.get("batch_id") or defect.get("batch_id") or "")
    batch = store.get_batch(batch_id)
    return {
        "batch": batch,
        "batch_safety_critical": bool(batch.get("safety_critical")),
        "incoming_lots": store.lots_for_batch(batch_id),
        "prior_recalls": store.recalls_for_batch(batch_id),
        "seed_defect": defect,
    }


def _ctx(context: Any) -> dict[str, Any]:
    prepared = (context or {}).get("prepared_data", {}) if isinstance(context, dict) else {}
    c = prepared.get("context", {}) if isinstance(prepared, dict) else {}
    return c if isinstance(c, dict) else {}


# ── @chain — the deterministic disposition floor (LLM can't waive) ───
async def enforce_disposition_floor(result: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """Re-assert the quarantine gate + safety-critical hard-stop over
    whatever the body produced. Reads the batch safety flag from the
    @pre_compose context when present, else from the store directly so
    the floor is identical through the runtime and on a direct call."""
    if not isinstance(result, dict):
        result = {}
    cctx = _ctx(ctx)
    batch_safety = bool(cctx.get("batch_safety_critical"))
    if not batch_safety:
        # Direct-call / no-context fallback — read the batch from the store.
        batch = store.get_batch(str(result.get("batch_id") or ""))
        batch_safety = bool(batch.get("safety_critical"))
    before = (result.get("severity"), result.get("quarantine_recommended"))
    enforce_disposition(result, batch_safety_critical=batch_safety,
                        raw_signal=result.get("raw_signal"))
    if before != (result.get("severity"), result.get("quarantine_recommended")):
        logger.warning(
            f"[intake] disposition floor adjusted batch={result.get('batch_id')} "
            f"sev {before[0]}→{result['severity']} "
            f"quarantine {before[1]}→{result['quarantine_recommended']} "
            f"(safety_hard_stop={result.get('safety_hard_stop')})"
        )
    return result


# ── @compose — per-downstream payloads (each callee needs different data) ─
def _to_quality_engineer(result: dict, ctx: Any) -> dict[str, Any]:
    """Root-cause work needs the full defect + raw signal + safety flag."""
    return {
        "batch_id": result.get("batch_id", ""),
        "line_id": result.get("line_id", ""),
        "defect_class": result.get("defect_class", ""),
        "severity": result.get("severity", 0),
        "raw_signal": result.get("raw_signal", {}),
        "safety_hard_stop": result.get("safety_hard_stop", False),
        "qa_briefing": result.get("qa_briefing", ""),
    }


def _to_qa_manager(result: dict, ctx: Any) -> dict[str, Any]:
    """The batch-release authority decides on the disposition + briefing."""
    return {
        "batch_id": result.get("batch_id", ""),
        "defect_class": result.get("defect_class", ""),
        "severity": result.get("severity", 0),
        "quarantine_recommended": result.get("quarantine_recommended", False),
        "safety_hard_stop": result.get("safety_hard_stop", False),
        "qa_briefing": result.get("qa_briefing", ""),
    }


def _to_qa_inspector(result: dict, ctx: Any) -> dict[str, Any]:
    """First-line check needs only the batch identity + the briefing."""
    return {
        "batch_id": result.get("batch_id", ""),
        "defect_class": result.get("defect_class", ""),
        "severity": result.get("severity", 0),
        "qa_briefing": result.get("qa_briefing", ""),
    }


@pre_compose(context_processor=load_inspection_context)
@compose(
    quality_engineer_agent=_to_quality_engineer,
    qa_manager_human=_to_qa_manager,
    qa_inspector_human=_to_qa_inspector,
)
@chain(enforce_disposition_floor)
async def inspection_intake_agent(
    llm_response: dict[str, Any] | str | None,
    input_data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Surface the LLM's classification; the chain enforces the floor."""
    if isinstance(llm_response, str):
        try:
            llm_response = json.loads(llm_response)
        except (ValueError, TypeError):
            llm_response = {}
    result = dict(llm_response) if isinstance(llm_response, dict) else {}
    input_data = input_data if isinstance(input_data, dict) else {}
    cctx = _ctx(context)

    # Ground identity + the dimensional spec from the batch record (the
    # @pre_compose context in-mesh, the store on a direct call) so the
    # severity floor has the tolerance even if the LLM dropped it.
    batch = cctx.get("batch") or store.get_batch(_resolve_batch_id(input_data, result))
    seed = cctx.get("seed_defect") or {}
    if batch:
        result.setdefault("batch_id", batch.get("batch_id", ""))
        result.setdefault("line_id", batch.get("line_id", ""))
    if seed:
        result.setdefault("source", seed.get("source", ""))
        # Carry the raw dimensional signal so the floor can read tolerance.
        if not result.get("raw_signal"):
            result["raw_signal"] = seed.get("raw_signal", {})
    # Spec from the batch lets the floor band the reading even when the
    # event payload omitted target/tolerance.
    rs = result.get("raw_signal")
    if isinstance(rs, dict) and batch:
        rs.setdefault("spec_target", batch.get("spec_target"))
        rs.setdefault("spec_tolerance", batch.get("spec_tolerance"))

    result.setdefault("defect_class", "none")
    result.setdefault("severity", 0)
    result.setdefault("confidence", 0)
    result.setdefault("quarantine_recommended", False)
    result.setdefault("qa_briefing", "")
    return result
