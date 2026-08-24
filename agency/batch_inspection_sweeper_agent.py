"""Batch-Inspection Sweeper — the instances fan-out backlog worker.

auto_discover matches `batch_inspection_sweeper_agent`. With `instances: N` the
agent runs as N parallel copies per activation; each copy calls the atomic
`claim_inspection` tool (agency/tools.py) to pull a DISJOINT slice of the
pending-inspection backlog, so the copies never inspect the same event. The
@chain floor only NORMALISES the reported claim into the yields contract — the
actual claim happened in the tool, and `instances_handoff: last` combines the
copies deterministically (lists concat, counts sum).
"""
from __future__ import annotations

import json
from typing import Any

from leafmesh import LeafMeshLogger, chain

from agency._shared import store

logger = LeafMeshLogger(__name__)


def _as_list(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str) and v.strip():
        return [v.strip()]
    return []


async def normalise_sweep(result: Any, context: dict) -> dict:
    """Coerce the copy's reported claim into the yields contract. `claimed_count`
    is re-derived from the ids so it can never disagree with the list (and so the
    instances sum stays honest)."""
    result = result if isinstance(result, dict) else {}
    ids = _as_list(result.get("claimed_event_ids"))
    result["claimed_event_ids"] = ids
    result["claimed_count"] = len(ids)
    try:
        result["remaining_backlog"] = int(result.get("remaining_backlog"))
    except (TypeError, ValueError):
        result["remaining_backlog"] = store.pending_inspection_count()
    if not result.get("sweep_briefing"):
        result["sweep_briefing"] = (
            f"Claimed {len(ids)} pending inspection(s); {result['remaining_backlog']} remaining."
        )
    return result


@chain(normalise_sweep)
async def batch_inspection_sweeper_agent(llm_response: Any, input_data: dict, context: dict) -> dict:
    if isinstance(llm_response, str):
        try:
            llm_response = json.loads(llm_response)
        except (ValueError, TypeError):
            llm_response = {}
    result = dict(llm_response) if isinstance(llm_response, dict) else {}
    result.setdefault("claimed_event_ids", [])
    result.setdefault("claimed_count", 0)
    result.setdefault("remaining_backlog", store.pending_inspection_count())
    result.setdefault("sweep_briefing", "")
    return result
