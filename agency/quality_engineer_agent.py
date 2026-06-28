"""quality_engineer_agent — context pull + the CAPA-required floor.

The LLM (configs/config.yaml) does the judgement: correlate the defect to
its root cause(s) and draft the CAPA narrative. This micro-module owns
the deterministic pieces:

@pre_compose  load_engineering_context — pull what the engineer needs
              before reasoning: the recent SPC window for the line (was
              the process already drifting?), the incoming-material lots
              (a bad CoA points at the supplier cause) and the OPEN CAPAs
              already on the line (don't draft a duplicate corrective
              action for a cause already under remediation). Self-reliant
              context pull from the source store (MES/LIMS/QMS in prod).

@chain        enforce_capa_required_floor — the deterministic CAPA-REQUIRED
              floor. A multi-source root cause, a safety-critical defect,
              or an out-of-spec incoming lot MUST carry a CAPA and route to
              engineer review — the LLM cannot close out a systemic cause
              with no corrective action. It also forces multi_source true
              whenever 2+ causes were returned and links any matching OPEN
              CAPA so the register isn't duplicated. The floor fires on a
              direct call too (it lives in the @chain).
"""
from __future__ import annotations

import json
from typing import Any

from leafmesh import LeafMeshLogger, chain, pre_compose

from agency._shared import store

logger = LeafMeshLogger(__name__)

_VALID_CAUSES = ("process", "supplier", "batch", "environmental")


def _up(input_data: dict[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(input_data, dict) and input_data.get(key) not in (None, ""):
        return input_data[key]
    uy = (input_data or {}).get("upstream_yields") if isinstance(input_data, dict) else None
    if isinstance(uy, dict) and uy.get(key) not in (None, ""):
        return uy[key]
    return default


def _line_for(result: dict[str, Any], input_data: dict[str, Any]) -> str:
    line = str(result.get("line_id") or _up(input_data, "line_id", "") or "")
    if line:
        return line
    batch = store.get_batch(str(result.get("batch_id") or _up(input_data, "batch_id", "") or ""))
    return str(batch.get("line_id") or "")


# ── @pre_compose — pull SPC window + lots + open CAPAs (self-reliant) ─
def load_engineering_context(data: Any, ctx: Any) -> dict[str, Any]:
    """Fetch the engineer's evidence base. PROD: historian + LIMS + QMS."""
    input_data = data if isinstance(data, dict) else {}
    batch_id = str(_up(input_data, "batch_id", "") or "")
    batch = store.get_batch(batch_id)
    line_id = str(batch.get("line_id") or _up(input_data, "line_id", "") or "")
    characteristic = str(batch.get("characteristic") or "")
    lots = store.lots_for_batch(batch_id)
    return {
        "batch": batch,
        "line_id": line_id,
        "spc_window": store.spc_window(line_id, characteristic, window=30),
        "incoming_lots": lots,
        "lot_out_of_spec": any(l.get("coa_status") == "out_of_spec" for l in lots),
        "open_capas": store.open_capas_for_line(line_id),
    }


def _ctx(context: Any) -> dict[str, Any]:
    prepared = (context or {}).get("prepared_data", {}) if isinstance(context, dict) else {}
    c = prepared.get("context", {}) if isinstance(prepared, dict) else {}
    return c if isinstance(c, dict) else {}


# ── @chain — the deterministic CAPA-required floor (LLM can't waive) ──
async def enforce_capa_required_floor(result: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """A systemic cause MUST carry a CAPA + engineer review.

    Reads the supplier-CoA / safety flags from the @pre_compose context
    when present, else from the store directly so the floor is identical
    in-mesh and on a direct call.
    """
    if not isinstance(result, dict):
        result = {}

    # Keep only valid causes (off-taxonomy is discarded).
    causes = [c for c in (result.get("root_causes") or [])
              if isinstance(c, dict) and str(c.get("cause", "")).lower() in _VALID_CAUSES]
    result["root_causes"] = causes

    # multi_source is determined by the count, never by the model alone.
    multi = len(causes) >= 2
    result["multi_source"] = multi

    cctx = _ctx(ctx)
    batch_id = str(result.get("batch_id") or "")
    if "lot_out_of_spec" in cctx:
        lot_oos = bool(cctx.get("lot_out_of_spec"))
        open_capas = cctx.get("open_capas") or []
    else:
        lots = store.lots_for_batch(batch_id)
        lot_oos = any(l.get("coa_status") == "out_of_spec" for l in lots)
        batch = store.get_batch(batch_id)
        open_capas = store.open_capas_for_line(str(batch.get("line_id") or ""))

    safety = str(result.get("defect_class") or "").lower() == "safety_critical" \
        or bool(result.get("safety_hard_stop"))

    capa_required = multi or safety or lot_oos
    result["requires_engineer_review"] = bool(
        result.get("requires_engineer_review")) or multi or safety

    if capa_required:
        # A systemic cause must not close with an empty corrective action.
        if not str(result.get("immediate_corrective_action") or "").strip():
            result["immediate_corrective_action"] = (
                "Containment: quarantine the affected batch and hold the line "
                "pending root-cause confirmation."
            )
        if not str(result.get("preventive_action") or "").strip():
            result["preventive_action"] = (
                "Open a CAPA for the confirmed systemic cause with verification."
            )
        if not str(result.get("responsible_role") or "").strip():
            result["responsible_role"] = "plant_engineer" if safety else "quality_engineer"
        result["capa_required"] = True
        # Link an existing OPEN CAPA for the primary cause so we don't
        # duplicate the register.
        primary = str(result.get("primary_cause") or "").lower()
        match = next((c for c in open_capas if str(c.get("primary_cause", "")).lower() == primary), None)
        result["linked_open_capa_id"] = str(match.get("capa_id")) if match else ""
        logger.warning(
            f"[quality-engineer] CAPA-required floor fired batch={batch_id} "
            f"multi_source={multi} safety={safety} lot_oos={lot_oos} "
            f"linked={result['linked_open_capa_id'] or 'none'}"
        )
    else:
        result["capa_required"] = False
        result.setdefault("linked_open_capa_id", "")

    result["primary_cause"] = str(result.get("primary_cause") or "")
    return result


@pre_compose(context_processor=load_engineering_context)
@chain(enforce_capa_required_floor)
async def quality_engineer_agent(
    llm_response: dict[str, Any] | str | None,
    input_data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Surface the LLM's root-cause + CAPA draft; the chain enforces the
    CAPA-required floor and de-duplicates against the open register."""
    if isinstance(llm_response, str):
        try:
            llm_response = json.loads(llm_response)
        except (ValueError, TypeError):
            llm_response = {}
    result = dict(llm_response) if isinstance(llm_response, dict) else {}
    input_data = input_data if isinstance(input_data, dict) else {}

    result.setdefault("batch_id", str(_up(input_data, "batch_id", "") or ""))
    result.setdefault("line_id", _line_for(result, input_data))
    # Carry the safety flag forward so the floor (and downstream) see it.
    if "safety_hard_stop" not in result:
        result["safety_hard_stop"] = bool(_up(input_data, "safety_hard_stop", False))
    if "defect_class" not in result:
        result["defect_class"] = str(_up(input_data, "defect_class", "") or "")

    result.setdefault("root_causes", [])
    result.setdefault("primary_cause", "")
    result.setdefault("multi_source", False)
    result.setdefault("requires_engineer_review", False)
    result.setdefault("immediate_corrective_action", "")
    result.setdefault("preventive_action", "")
    result.setdefault("target_completion_utc", "")
    result.setdefault("responsible_role", "")
    return result
