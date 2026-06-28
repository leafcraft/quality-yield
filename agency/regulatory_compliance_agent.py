"""regulatory_compliance_agent — audit-readiness + the regulator-routing
+ statutory filing-window floor.

The LLM (configs/config.yaml) does the judgement: build the per-framework
audit-readiness scorecard (evidence inventory vs requirements → CAPA
recommendations). This micro-module owns the deterministic compliance
pieces:

@pre_compose  load_compliance_context — pull the BATCH genealogy (product
              class = the regulator routing key) and any PRIOR recalls on
              the batch from the source store (MES/QMS in prod). A new
              reportable event on a batch that already carries a recall is
              an escalation, not a fresh filing — the context makes that
              visible. Self-reliant context pull, grounded in genealogy.

@chain        enforce_reportability_floor — the deterministic REGULATOR
              ROUTING + statutory FILING-WINDOW floor. WHEN the scorecard
              (or an inbound defect) carries a reportable defect / recall
              class, WHICH regulator takes it for the product class and
              the filing deadline in hours are compliance domain data that
              must NOT be hallucinated or omitted. A reportable class
              FORCES the regulator list + the statutory windows + a manager
              review — the LLM cannot mark a reportable batch audit-ready
              and walk away with no filing obligation opened. Fail-closed:
              no reportable class ⇒ no filing obligation stamped (a routine
              audit scorecard is untouched). The floor fires on a direct
              call too (it lives in the @chain).

The regulator routing + statutory windows are shared with the recall
officer (agency/recall_traceability_agent.py) — the same compliance
tables, so a recall and an audit-readiness review can never disagree on
WHO must be notified by WHEN.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from leafmesh import LeafMeshLogger, chain, pre_compose

from agency._shared import store
# Single source of truth for the regulator routing + statutory windows —
# shared with the recall officer so the two roles can never disagree.
from agency.recall_traceability_agent import FILING_HOURS, REGULATOR_BY_CLASS

logger = LeafMeshLogger(__name__)

# Defect / recall classes that are REPORTABLE — any of these forces a
# regulatory filing obligation. A defect_class of "safety_critical" or any
# recall severity above an informational/voluntary advisory is reportable.
_REPORTABLE_DEFECT_CLASSES = frozenset({"safety_critical"})
_REPORTABLE_RECALL_SEVERITIES = frozenset({
    "recall_class1", "recall_class2", "recall_class3",
})

_DEFAULTS: dict[str, Any] = {
    "scorecard_id": "",
    "framework": "",
    "evidence_present": [],
    "evidence_missing": [],
    "evidence_stale": [],
    "readiness_score": 0,
    "audit_ready": False,
    "requires_capa": False,
    "requires_quality_manager_review": False,
    "assessed_at_utc": "",
    # ── stamped by the @chain reportability floor ──
    "reportable": False,
    "product_class": "",
    "regulators_to_notify": [],
    "filing_deadlines": {},
    "requires_regulatory_filing": False,
}


def _up(input_data: dict[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(input_data, dict) and input_data.get(key) not in (None, ""):
        return input_data[key]
    uy = (input_data or {}).get("upstream_yields") if isinstance(input_data, dict) else None
    if isinstance(uy, dict) and uy.get(key) not in (None, ""):
        return uy[key]
    return default


def _ctx(context: Any) -> dict[str, Any]:
    prepared = (context or {}).get("prepared_data", {}) if isinstance(context, dict) else {}
    c = prepared.get("context", {}) if isinstance(prepared, dict) else {}
    return c if isinstance(c, dict) else {}


def _parse(text: Any) -> dict[str, Any]:
    if isinstance(text, dict):
        return text
    if not isinstance(text, str):
        return {}
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return {}


# ── @pre_compose — pull batch product class + prior recalls (self-reliant) ─
def load_compliance_context(data: Any, ctx: Any) -> dict[str, Any]:
    """Fetch the reportability authority from the source store. PROD:
    MES/QMS read. The product class is the regulator routing key; prior
    recalls turn a new reportable event into an escalation."""
    input_data = data if isinstance(data, dict) else {}
    batch_id = str(_up(input_data, "batch_id", "") or "")
    batch = store.get_batch(batch_id)
    product_class = str(batch.get("product_class") or _up(input_data, "product_class", "") or "")
    return {
        "batch": batch,
        "batch_id": batch_id,
        "product_class": product_class,
        "prior_recalls": store.recalls_for_batch(batch_id),
    }


def _is_reportable(defect_class: str, recall_severity: str) -> bool:
    return (
        str(defect_class or "").lower() in _REPORTABLE_DEFECT_CLASSES
        or str(recall_severity or "").lower() in _REPORTABLE_RECALL_SEVERITIES
    )


# ── @chain — the deterministic regulator-routing + filing-window floor ──
async def enforce_reportability_floor(result: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """When a reportable defect / recall class is in play, FORCE the
    regulator routing + statutory filing windows + manager review. The LLM
    cannot omit a regulator or mark a reportable batch audit-ready with no
    filing obligation. Fail-closed: a routine scorecard (no reportable
    class) is left untouched."""
    if not isinstance(result, dict):
        result = {}
    out = dict(result)

    cctx = _ctx(ctx)
    # Product class drives regulator routing — context (in-mesh) then the
    # store (direct call) then whatever the LLM/inbound carried.
    product_class = str(
        cctx.get("product_class")
        or store.get_batch(str(out.get("batch_id") or "")).get("product_class")
        or out.get("product_class")
        or ""
    ).lower()

    defect_class = str(out.get("defect_class") or "")
    recall_severity = str(out.get("recall_severity") or out.get("severity") or "")
    reportable = _is_reportable(defect_class, recall_severity)

    if not reportable:
        # Routine audit-readiness scorecard — no filing obligation opened.
        out["reportable"] = False
        out["product_class"] = product_class
        out["regulators_to_notify"] = []
        out["filing_deadlines"] = {}
        out["requires_regulatory_filing"] = False
        return out

    # A reportable class forces the regulator routing + statutory windows.
    regulators = REGULATOR_BY_CLASS.get(product_class, ["OSHA"])
    now = datetime.now(timezone.utc)
    out["reportable"] = True
    out["product_class"] = product_class
    out["regulators_to_notify"] = regulators
    out["filing_deadlines"] = {
        r: (now + timedelta(hours=FILING_HOURS[r])).isoformat() for r in regulators
    }
    out["requires_regulatory_filing"] = True
    # A reportable event always needs a CAPA + quality-manager review — the
    # LLM cannot mark it audit-ready and close the case.
    out["requires_capa"] = True
    out["requires_quality_manager_review"] = True
    out["audit_ready"] = False

    prior = cctx.get("prior_recalls") or store.recalls_for_batch(str(out.get("batch_id") or ""))
    logger.warning(
        f"[regulatory] reportability floor FIRED defect_class={defect_class or '-'} "
        f"recall_severity={recall_severity or '-'} class={product_class} "
        f"regulators={regulators} prior_recalls={len(prior)}"
    )
    return out


@pre_compose(context_processor=load_compliance_context)
@chain(enforce_reportability_floor)
async def regulatory_compliance_agent(
    llm_response: dict[str, Any] | str | None,
    input_data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Surface the LLM's audit-readiness scorecard; the chain stamps the
    deterministic regulator routing + statutory filing windows whenever a
    reportable defect / recall class is in play."""
    parsed = _parse(llm_response)
    result = dict(_DEFAULTS)
    if isinstance(parsed, dict):
        for k in result:
            if k in parsed:
                result[k] = parsed[k]
    input_data = input_data if isinstance(input_data, dict) else {}
    cctx = _ctx(context)

    # Carry the reportability signals through so the floor sees them on
    # every path (the @pre_compose context in-mesh, the input on a direct
    # call). These are inputs to the floor, not declared yields.
    result["batch_id"] = str(cctx.get("batch_id") or _up(input_data, "batch_id", "") or "")
    result["defect_class"] = str(parsed.get("defect_class") or _up(input_data, "defect_class", "") or "")
    result["recall_severity"] = str(
        parsed.get("recall_severity") or parsed.get("severity")
        or _up(input_data, "recall_severity", "") or _up(input_data, "severity", "") or ""
    )
    if not result["product_class"]:
        result["product_class"] = str(cctx.get("product_class") or "")
    return result
