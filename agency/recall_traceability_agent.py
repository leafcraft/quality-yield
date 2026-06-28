"""recall_traceability_agent — trace context + regulator-routing floor.

The LLM (configs/config.yaml) does the judgement: trace the complaint to
a batch, scope the recall, set the severity class and the escalation /
notification flags, draft the customer-facing response. This micro-module
owns the deterministic compliance pieces:

@pre_compose  load_trace_context — pull the BATCH genealogy (line/shift/
              production-date/units-shipped = recall scope), the
              incoming-material lots, the RELATED batches that shared a
              tainted lot (the freeze scope) and any PRIOR recalls on the
              batch from the source store (MES/QMS in prod). Self-reliant
              context pull — the trace is grounded in genealogy, not
              hallucinated.

@chain        enforce_regulator_routing — the deterministic REGULATOR
              ROUTING + statutory FILING-WINDOW floor. WHICH regulator
              takes a recall for a given product class, and the filing
              deadline in hours, are compliance domain data that must NOT
              be hallucinated. Fail-closed: no defect_id ⇒ trace-only, no
              filing obligation opened. When a recall is coordinated the
              actuator freezes the related batches (idempotent) and the
              recall is recorded so a later trace escalates. The floor
              fires on a direct call too (it lives in the @chain).

Recall records belong in your QMS — the dev store records them only so
the demo's idempotency + escalation are observable.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from leafmesh import LeafMeshLogger, chain, pre_compose

from agency._shared import store
from agency._shared.batch_release import enact_disposition

logger = LeafMeshLogger(__name__)

# Product class → regulator routing (compliance domain data)
REGULATOR_BY_CLASS = {
    "medical_device":  ["FDA", "EU_MDR"],
    "pharma":          ["FDA", "EMA"],
    "food_bev":        ["FDA", "EFSA"],
    "automotive":      ["NHTSA", "EU_GSR"],
    "consumer":        ["CPSC", "EU_RAPEX"],
    "electronics":     ["CPSC", "EU_RAPEX"],
    "industrial":      ["OSHA"],
}

# Statutory filing deadlines by regulator (hours from defect confirmation)
FILING_HOURS = {
    "FDA":          24,
    "NHTSA":        120,    # 5 days
    "CPSC":         24,
    "EU_MDR":       120,
    "EMA":          72,
    "EFSA":         72,
    "EU_RAPEX":     120,
    "EU_GSR":       120,
    "OSHA":         168,    # 7 days
}


def _up(input_data: dict[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(input_data, dict) and input_data.get(key) not in (None, ""):
        return input_data[key]
    uy = (input_data or {}).get("upstream_yields") if isinstance(input_data, dict) else None
    if isinstance(uy, dict) and uy.get(key) not in (None, ""):
        return uy[key]
    return default


# ── @pre_compose — pull batch genealogy + related batches + prior recalls ─
def load_trace_context(data: Any, ctx: Any) -> dict[str, Any]:
    """Fetch the trace authority from the source store. PROD: MES/QMS read."""
    input_data = data if isinstance(data, dict) else {}
    # The complaint may name a batch directly or via a seeded defect id.
    sid = str(input_data.get("source_event_id") or "")
    defect = store.get_defect(sid) if sid else {}
    batch_id = str(_up(input_data, "batch_id", "") or defect.get("batch_id") or "")
    batch = store.get_batch(batch_id)
    lots = store.lots_for_batch(batch_id)
    # Related batches = every batch that shared any tainted lot (freeze scope).
    related: list[str] = []
    for lot in lots:
        if lot.get("coa_status") == "out_of_spec":
            related += [b for b in store.batches_sharing_lot(lot.get("lot_id", ""))
                        if b != batch_id]
    return {
        "batch": batch,
        "incoming_lots": lots,
        "related_batches": sorted(set(related)),
        "prior_recalls": store.recalls_for_batch(batch_id),
        "seed_defect": defect,
    }


def _ctx(context: Any) -> dict[str, Any]:
    prepared = (context or {}).get("prepared_data", {}) if isinstance(context, dict) else {}
    c = prepared.get("context", {}) if isinstance(prepared, dict) else {}
    return c if isinstance(c, dict) else {}


# ── @chain — the deterministic regulator-routing + freeze floor ──────
async def enforce_regulator_routing(result: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """Stamp the regulator tables + statutory filing windows; on a
    coordinated recall, freeze the related batches (idempotent) and
    record the recall. Fail-closed without a defect_id."""
    if not isinstance(result, dict):
        result = {}
    out = dict(result)

    defect_id = str(out.get("defect_id") or "")
    if not defect_id:
        # Trace-only — never open a regulatory filing obligation.
        out["regulators_to_notify"] = []
        out["filing_deadlines"] = {}
        out["requires_regulatory_filing"] = False
        out.setdefault("recall_id", "")
        out.setdefault("related_batches_frozen", [])
        return out

    product_class = str(out.get("product_class") or "").lower()
    regulators = REGULATOR_BY_CLASS.get(product_class, ["OSHA"])
    now = datetime.now(timezone.utc)
    out["regulators_to_notify"] = regulators
    out["filing_deadlines"] = {
        r: (now + timedelta(hours=FILING_HOURS[r])).isoformat() for r in regulators
    }
    out["requires_regulatory_filing"] = True
    out["opened_at_utc"] = out.get("opened_at_utc") or now.isoformat()
    if not out.get("recall_id"):
        out["recall_id"] = f"recall-{defect_id}-{int(time.time())}"

    # Freeze the related batches the recall scope names (fail-closed,
    # idempotent). The actuator never ships; freeze == quarantine.
    frozen: list[str] = []
    for b in out.get("related_batches_frozen") or out.get("affected_batches") or []:
        act = enact_disposition(str(b), "quarantine")
        if act.get("enacted_action") == "quarantine":
            frozen.append(str(b))
    out["related_batches_frozen"] = sorted(set(frozen))

    # Record the recall so a later trace on the same batch escalates.
    store.record_recall({
        "recall_id": out["recall_id"], "product_class": product_class,
        "affected_batches": out.get("affected_batches") or [],
        "severity": str(out.get("severity") or ""), "opened_at_utc": out["opened_at_utc"],
    })
    logger.warning(
        f"[recall] regulator-routing floor: {defect_id} class={product_class} "
        f"regulators={regulators} frozen={out['related_batches_frozen']}"
    )
    return out


@pre_compose(context_processor=load_trace_context)
@chain(enforce_regulator_routing)
async def recall_traceability_agent(
    llm_response: dict[str, Any] | str | None,
    input_data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Surface the LLM's trace + recall scoping; the chain stamps the
    deterministic regulator routing and freezes the related batches."""
    if isinstance(llm_response, str):
        try:
            llm_response = json.loads(llm_response)
        except (ValueError, TypeError):
            llm_response = {}
    result = dict(llm_response) if isinstance(llm_response, dict) else {}
    input_data = input_data if isinstance(input_data, dict) else {}
    cctx = _ctx(context)

    # Ground the trace in the batch genealogy (the @pre_compose context
    # in-mesh, the store on a direct call).
    batch = cctx.get("batch")
    if not batch:
        batch = store.get_batch(str(result.get("batch_id") or _up(input_data, "batch_id", "") or ""))
    if batch:
        result.setdefault("batch_id", batch.get("batch_id", ""))
        result.setdefault("line_id", batch.get("line_id", ""))
        result.setdefault("shift_id", batch.get("shift_id", ""))
        result.setdefault("production_date", batch.get("production_date", ""))
        result.setdefault("product_class", batch.get("product_class", ""))
    # The lot-shared related batches are the freeze candidates.
    related = cctx.get("related_batches")
    if related is None:
        related = []
    if not result.get("related_batches_frozen") and related:
        result["related_batches_frozen"] = list(related)

    # Carry a defect_id through from the orchestrate_recall entry.
    result.setdefault("defect_id", str(_up(input_data, "defect_id", "") or ""))

    # Full-shape defaults so the OUTPUT CONTRACT is honoured on every path.
    for key, default in (
        ("shipment_id", ""), ("related_units_in_field", 0),
        ("recall_recommendation", ""), ("customer_facing_response", ""),
        ("root_cause_hypothesis", ""), ("confidence", 0), ("recall_id", ""),
        ("severity", ""), ("affected_batches", []), ("units_in_market", 0),
        ("customer_count", 0), ("regulators_to_notify", []),
        ("filing_deadlines", {}), ("related_batches_frozen", []),
        ("requires_executive_escalation", False),
        ("requires_customer_notification", False),
        ("requires_regulatory_filing", False), ("opened_at_utc", ""),
    ):
        result.setdefault(key, default)
    return result
