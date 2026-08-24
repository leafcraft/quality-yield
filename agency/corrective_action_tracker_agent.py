"""CAPA Register Coordinator — the dev-store CAPA register (programmatic).

auto_discover matches `corrective_action_tracker_agent`. CAPAs belong in YOUR
QMS; the config ships the mcp connector COMMENTED (an empty-url connector is
rejected at load), so this module runs the register off the seeded dev store
(agency/_shared/store.py) day-0. Swap the store bodies for QMS calls and the
mesh is unchanged.

Three modes on `input_data.mode`:
  * open   — open a CAPA for a confirmed cause (dedupes nothing here; the quality
             engineer's @chain already linked an existing open CAPA)
  * update — update a CAPA's status / effectiveness
  * report — the register state: totals, by-status, overdue (the default)
"""
from __future__ import annotations

from typing import Any

from leafmesh import LeafMeshLogger

from agency._shared import store

logger = LeafMeshLogger(__name__)


def _up(input_data: dict[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(input_data, dict) and input_data.get(key) not in (None, ""):
        return input_data[key]
    uy = (input_data or {}).get("upstream_yields") if isinstance(input_data, dict) else None
    if isinstance(uy, dict) and uy.get(key) not in (None, ""):
        return uy[key]
    return default


async def corrective_action_tracker_agent(llm_response: Any, input_data: dict, context: dict) -> dict:
    input_data = input_data if isinstance(input_data, dict) else {}
    mode = str(input_data.get("mode") or "report").lower()

    out: dict[str, Any] = {
        "mode": mode, "capa_id": "", "status": "", "due_at_utc": "",
        "total_capas": 0, "by_status": {}, "overdue_count": 0, "overdue_capas": [],
        "effectiveness": {}, "requires_qa_director_action": False, "capa_briefing": "",
    }

    if mode == "open":
        row = store.open_capa({
            "line_id": _up(input_data, "line_id", ""),
            "primary_cause": _up(input_data, "primary_cause", _up(input_data, "root_cause", "")),
            "summary": _up(input_data, "action_summary", ""),
            "owner": _up(input_data, "owner", "quality_engineer"),
            "status": "open",
        })
        out["capa_id"] = row.get("capa_id", "")
        out["status"] = row.get("status", "open")
        out["due_at_utc"] = row.get("due_at_utc", "")
        out["capa_briefing"] = f"Opened {out['capa_id']} for cause '{row.get('primary_cause','')}'."
    elif mode == "update":
        row = store.update_capa(str(input_data.get("capa_id") or ""), {
            "status": input_data.get("status"),
            "effective": input_data.get("effective"),
        })
        out["capa_id"] = row.get("capa_id", "")
        out["status"] = row.get("status", "")
        out["effectiveness"] = {"effective": row.get("effective")} if row else {}
        out["capa_briefing"] = (f"Updated {out['capa_id']} -> {out['status']}."
                                if row else "CAPA not found.")

    # Every mode reports the register state so the caller always sees the whole
    # picture (and the WORM sink records it).
    report = store.capa_report()
    out.update(total_capas=report["total_capas"], by_status=report["by_status"],
               overdue_count=report["overdue_count"], overdue_capas=report["overdue_capas"])
    out["requires_qa_director_action"] = report["overdue_count"] > 0
    if mode == "report":
        out["capa_briefing"] = (f"{report['total_capas']} CAPAs; "
                                f"{report['overdue_count']} overdue.")
    logger.info(f"[capa] mode={mode} total={report['total_capas']} overdue={report['overdue_count']}")
    return out
