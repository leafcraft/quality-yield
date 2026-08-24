"""Global tools for the quality-yield pod.

Two day-0 tool surfaces, each reading the seeded dev store so the pod runs with
NO connector wired:

  * The Incoming-Quality Inspector's evidence reads — read_batch_genealogy /
    read_defect_record / read_prior_recalls. Each **announces itself**: when
    there is no real source and no seed, it returns `available: false` and says
    why — never a synthetic success that looks like a real read. To go live,
    wire the LIMS/vision mcp block in config.yaml (each named action becomes its
    own tool); these dev tools are the fallback contract.

  * The Batch-Inspection Sweeper's ATOMIC claim — claim_inspection. With
    `instances: N`, the copies race for the same backlog; the claim tool pops a
    DISJOINT slice under a lock so no two copies inspect the same event.
"""
import os

from leafmesh import global_tool, LeafMeshLogger

try:
    from ._shared import store
except ImportError:  # pragma: no cover — direct-run fallback
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _shared import store  # type: ignore

logger = LeafMeshLogger(__name__)


def _connector_wired() -> bool:
    """True if a real inspection connector is configured (LIMS/historian URL set)."""
    return bool(os.getenv("LIMS_MCP_URL") or os.getenv("HISTORIAN_MCP_URL"))


@global_tool(category="inspection")
def read_batch_genealogy(batch_id: str) -> dict:
    """Read a batch's genealogy record — line, shift, product class, spec
    target/tolerance, incoming lots, units shipped, and the safety-critical flag.

    Grounds defect scoring against the batch spec. When no real connector is
    wired, reads the seed store; announces `available: false` if there is
    genuinely nothing to read.

    Args:
        batch_id: The batch to read (e.g. "B-4471").
    """
    batch = store.get_batch(str(batch_id).strip())
    if not batch:
        if _connector_wired():
            return {"available": False, "reason": "connector wired but returned nothing for this batch"}
        return {"available": False, "reason": f"no seed batch {batch_id!r}; wire LIMS_MCP_URL"}
    logger.info(f"read_batch_genealogy batch={batch_id} safety_critical={batch.get('safety_critical')}")
    return {"available": True, "batch": batch, "incoming_lots": store.lots_for_batch(str(batch_id).strip())}


@global_tool(category="inspection")
def read_defect_record(source_event_id: str) -> dict:
    """Read the raw defect record for an inspection event — the dimensional
    measurement, scratch length, or complaint description that seeds the score.

    Args:
        source_event_id: The inspection event id (e.g. "evt-vision-4471").
    """
    defect = store.get_defect(str(source_event_id).strip())
    if not defect:
        return {"available": False, "reason": f"no seed defect {source_event_id!r}; wire LIMS_MCP_URL"}
    return {"available": True, "defect": defect}


@global_tool(category="inspection")
def read_prior_recalls(batch_id: str) -> dict:
    """List prior recalls touching a batch — so a batch that already carries a
    recall escalates rather than re-opening a fresh trace.

    Args:
        batch_id: The batch to check for prior recalls.
    """
    recalls = store.recalls_for_batch(str(batch_id).strip())
    return {"available": True, "batch_id": str(batch_id).strip(), "prior_recalls": recalls}


@global_tool(category="inspection")
def claim_inspection(count: int = 1) -> dict:
    """Atomically claim up to `count` pending inspection events from the backlog
    for THIS sweeper copy. The claim is disjoint across parallel `instances`
    copies (a lock-protected pop), so no two copies inspect the same event.

    Returns the claimed event ids and the remaining backlog size, or
    `available: false` when the backlog is empty.

    Args:
        count: How many pending events this copy should claim (default 1).
    """
    try:
        n = max(1, int(count))
    except (TypeError, ValueError):
        n = 1
    claimed = store.claim_pending_inspections(n)
    remaining = store.pending_inspection_count()
    if not claimed:
        return {"available": False, "reason": "pending-inspection backlog is empty",
                "claimed": [], "remaining": remaining}
    logger.info(f"claim_inspection claimed={claimed} remaining={remaining}")
    return {"available": True, "claimed": claimed, "remaining": remaining}
