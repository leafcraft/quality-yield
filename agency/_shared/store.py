"""Dev-mode source store for quality-yield — the system-of-record stand-in.

Stands in for the customer's MES / LIMS / QMS / plant historian so the
WHOLE quality chain (inspect → classify → root-cause → CAPA → release →
audit) and the recall chain (trace → scope → file) run end-to-end BEFORE
any connector is wired. Every read here is the contract a production
connector must satisfy; swap the body for the real call and the agents
are unchanged:

  * Batch / lot register   → MES / ERP batch-genealogy record
  * Inspection / defect log → LIMS / vision-system defect history
  * SPC measurement series  → plant historian (PI, Ignition, InfluxDB)
  * CAPA register           → QMS corrective-action log
  * Prior recalls           → QMS recall register / regulatory filings

Setting HISTORIAN_MCP_URL / QMS_MCP_URL alone does nothing until you
wire the calls here.

This is a SEEDED dev store, never the system of record (Rule 7): it
ships realistic batches + lots, one in-line defect, a SPC series with a
seeded OUT-OF-CONTROL point, a CAPA register and a prior recall so the
demo works out of the box. The SPC series is append-on-ingest (the SPC
monitor writes measurements back here) so the chart window accumulates
real history as the line runs.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
# Dev store location. Override QUALITY_YIELD_STORE_DIR to relocate (tests
# point it at a temp dir). Irrelevant in prod — reads hit the MES/LIMS.
_DIR = Path(os.environ.get("QUALITY_YIELD_STORE_DIR", "./data"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path(k: str) -> Path:
    _DIR.mkdir(parents=True, exist_ok=True)
    return _DIR / f"{k}.json"


def _load(k: str) -> dict[str, Any]:
    p = _path(k)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (ValueError, OSError):
        return {}


def _save(k: str, d: dict[str, Any]) -> None:
    p = _path(k)
    t = p.with_suffix(".json.tmp")
    t.write_text(json.dumps(d, indent=2, default=str))
    t.replace(p)


# ════════════════════════════════════════════════════════════════════
# SEED DATA — realistic batches / lots / defects / SPC / CAPA / recalls
# ════════════════════════════════════════════════════════════════════

# Batch register — batch_id → genealogy record. `product_class` drives
# regulator routing; `safety_critical` is a batch-level flag the
# safety-critical hard-stop reads; `incoming_lots` links to supplier
# material (the root-cause supplier path); `units_shipped` is recall
# scope. PROD: MES / ERP batch-genealogy.
_SEED_BATCHES: dict[str, Any] = {
    "B-4471": {
        "batch_id": "B-4471", "line_id": "L7", "shift_id": "S2",
        "product_class": "medical_device", "safety_critical": True,
        "production_date": "2026-06-22", "status": "in_process",
        "incoming_lots": ["LOT-TI-9920"], "units_produced": 4800,
        "units_shipped": 0, "characteristic": "bore_diameter_mm",
        "spec_target": 10.00, "spec_tolerance": 0.05,
    },
    "B-4470": {
        "batch_id": "B-4470", "line_id": "L7", "shift_id": "S1",
        "product_class": "medical_device", "safety_critical": True,
        "production_date": "2026-06-21", "status": "released",
        "incoming_lots": ["LOT-TI-9920"], "units_produced": 5000,
        "units_shipped": 5000, "characteristic": "bore_diameter_mm",
        "spec_target": 10.00, "spec_tolerance": 0.05,
    },
    "B-3300": {
        "batch_id": "B-3300", "line_id": "L3", "shift_id": "S2",
        "product_class": "consumer", "safety_critical": False,
        "production_date": "2026-06-20", "status": "released",
        "incoming_lots": ["LOT-PLA-5510"], "units_produced": 12000,
        "units_shipped": 11800, "characteristic": "surface_finish",
        "spec_target": 1.6, "spec_tolerance": 0.4,
    },
    # ── Field-complaint batch (recall trace example) — already shipped ──
    "B-2210": {
        "batch_id": "B-2210", "line_id": "L3", "shift_id": "S3",
        "product_class": "consumer", "safety_critical": False,
        "production_date": "2026-05-02", "status": "shipped",
        "incoming_lots": ["LOT-PLA-5510"], "units_produced": 14000,
        "units_shipped": 13950, "characteristic": "surface_finish",
        "spec_target": 1.6, "spec_tolerance": 0.4,
    },
}

# Incoming-material lots — lot_id → supplier record. The supplier
# root-cause path reads `coa_status` (certificate-of-analysis) and the
# shared-lot linkage (a bad lot taints every batch that consumed it).
_SEED_LOTS: dict[str, Any] = {
    "LOT-TI-9920": {
        "lot_id": "LOT-TI-9920", "material": "Ti-6Al-4V bar stock",
        "supplier_id": "SUP-TITANCORP", "coa_status": "out_of_spec",
        "received": "2026-06-18", "batches_consumed": ["B-4470", "B-4471"],
    },
    "LOT-PLA-5510": {
        "lot_id": "LOT-PLA-5510", "material": "PLA pellet",
        "supplier_id": "SUP-POLYMERX", "coa_status": "in_spec",
        "received": "2026-04-28", "batches_consumed": ["B-3300", "B-2210"],
    },
}

# Inspection / defect log — keyed by source_event_id. The demo / examples
# ingest these. The seeded defect on B-4471 is a real dimensional
# out-of-tolerance event so the FULL classify→root-cause→release chain
# runs out of the box. PROD: LIMS / vision-system defect history.
_SEED_DEFECTS: dict[str, Any] = {
    "evt-vision-4471": {
        "source_event_id": "evt-vision-4471", "batch_id": "B-4471",
        "line_id": "L7", "source": "vision",
        "raw_signal": {
            "camera": "CAM-L7-03", "measure": "bore_diameter_mm",
            "value": 10.071, "spec_target": 10.00, "spec_tolerance": 0.05,
            "image_id": "img-4471-22817",
        },
        "received_at_utc": "2026-06-22T14:02:11+00:00",
    },
    "evt-complaint-2210": {
        "source_event_id": "evt-complaint-2210", "batch_id": "B-2210",
        "line_id": "L3", "source": "complaint",
        "raw_signal": {
            "channel": "support_ticket", "ticket_id": "ZD-88120",
            "description": "surface peeling after 3 weeks of use",
            "unit_serial": "U-2210-04417",
        },
        "received_at_utc": "2026-06-23T08:40:00+00:00",
    },
}

# SPC measurement series — keyed by (line_id, characteristic). The SPC
# monitor appends ingested measurements here and reads the window for the
# chart. Line L7 / bore_diameter_mm is SEEDED with a clean process that
# ENDS on an OUT-OF-CONTROL point (10.21 mm — well beyond +3σ of a ~10.00
# process) so the SPC out-of-control floor fires on day 0. PROD: plant
# historian time-series query.
_SEED_SPC_SERIES: dict[str, list[dict[str, Any]]] = {
    "L7|bore_diameter_mm": [
        {"value": v, "sample_size": 5, "ts_utc": f"2026-06-22T{h:02d}:00:00+00:00"}
        for h, v in enumerate([
            10.00, 10.01, 9.99, 10.02, 9.98, 10.00, 10.01, 9.99,
            10.00, 10.02, 9.97, 10.01, 9.99, 10.00, 10.03, 9.98,
            10.00, 10.01, 9.99, 10.21,   # ← seeded out-of-control point
        ])
    ],
    "L3|surface_finish": [
        {"value": v, "sample_size": 5, "ts_utc": f"2026-06-20T{h:02d}:00:00+00:00"}
        for h, v in enumerate([
            1.55, 1.62, 1.58, 1.61, 1.59, 1.60, 1.57, 1.63,
            1.58, 1.60, 1.59, 1.61,
        ])
    ],
}

# CAPA register — capa_id → record. The quality engineer reads the OPEN
# CAPAs for a line so it doesn't draft a duplicate corrective action for
# a cause already under remediation. PROD: QMS corrective-action log.
_SEED_CAPAS: dict[str, Any] = {
    "CAPA-2026-0188": {
        "capa_id": "CAPA-2026-0188", "line_id": "L7",
        "primary_cause": "process",
        "summary": "Press setpoint drift on L7 — recalibrate weekly",
        "status": "open", "owner": "plant_engineer",
        "opened_at_utc": "2026-06-15T09:00:00+00:00",
        "due_at_utc": "2026-06-29T00:00:00+00:00", "effective": None,
    },
    "CAPA-2026-0150": {
        "capa_id": "CAPA-2026-0150", "line_id": "L3",
        "primary_cause": "supplier",
        "summary": "PLA pellet moisture variance — add incoming dry-check",
        "status": "verified", "owner": "quality_engineer",
        "opened_at_utc": "2026-05-10T09:00:00+00:00",
        "due_at_utc": "2026-05-31T00:00:00+00:00", "effective": True,
    },
}

# Prior recalls — recall register. The recall officer reads this so a new
# trace on a batch that already has a recall escalates rather than
# re-opening. PROD: QMS recall register.
_SEED_RECALLS: list[dict[str, Any]] = [
    {"recall_id": "recall-def-old-1717000000", "product_class": "consumer",
     "affected_batches": ["B-1100"], "severity": "recall_class3",
     "opened_at_utc": "2026-03-01T00:00:00+00:00"},
]


# ── Batch register (genealogy) ───────────────────────────────────────
def _batches() -> dict[str, Any]:
    s = _load("batches")
    if not s:
        s = dict(_SEED_BATCHES)
        _save("batches", s)
    return s


def get_batch(batch_id: str) -> dict[str, Any]:
    """Batch genealogy record. PROD: MES / ERP batch read. {} if unknown."""
    if not batch_id:
        return {}
    return _batches().get(batch_id, {})


# ── Incoming-material lots (the supplier root-cause source) ───────────
def _lots() -> dict[str, Any]:
    s = _load("lots")
    if not s:
        s = dict(_SEED_LOTS)
        _save("lots", s)
    return s


def get_lot(lot_id: str) -> dict[str, Any]:
    """Incoming-material lot record. PROD: LIMS / supplier-quality read."""
    if not lot_id:
        return {}
    return _lots().get(lot_id, {})


def lots_for_batch(batch_id: str) -> list[dict[str, Any]]:
    """The incoming lots a batch consumed — the supplier root-cause path."""
    batch = get_batch(batch_id)
    return [get_lot(l) for l in (batch.get("incoming_lots") or []) if get_lot(l)]


def batches_sharing_lot(lot_id: str) -> list[str]:
    """Every batch that consumed `lot_id` — the related-batch freeze scope
    when a supplier lot is the root cause. PROD: MES genealogy query."""
    lot = get_lot(lot_id)
    return list(lot.get("batches_consumed") or [])


# ── Inspection / defect log (the demo ingest) ────────────────────────
def _defects() -> dict[str, Any]:
    s = _load("defects")
    if not s:
        s = dict(_SEED_DEFECTS)
        _save("defects", s)
    return s


def get_defect(source_event_id: str) -> dict[str, Any]:
    return _defects().get(source_event_id, {})


def sample_defects() -> list[dict[str, Any]]:
    return list(_defects().values())


# ── SPC measurement series (the plant-historian stand-in) ────────────
def _series_key(line_id: str, characteristic: str) -> str:
    return f"{line_id}|{characteristic}"


def _spc() -> dict[str, Any]:
    s = _load("spc_series")
    if not s:
        s = {k: list(v) for k, v in _SEED_SPC_SERIES.items()}
        _save("spc_series", s)
    return s


def spc_window(line_id: str, characteristic: str, window: int = 50) -> list[dict[str, Any]]:
    """The last `window` measurements for a line/characteristic, oldest
    first — the chart window. PROD: historian time-series query."""
    s = _spc()
    if line_id and characteristic:
        rows = s.get(_series_key(line_id, characteristic), [])
    else:
        # No filter → flatten every series (the daily sweep).
        rows = [r for series in s.values() for r in series]
        rows.sort(key=lambda r: str(r.get("ts_utc") or ""))
    return list(rows[-window:])


def spc_lines() -> list[dict[str, str]]:
    """Every (line_id, characteristic) under SPC — the daily sweep set."""
    out = []
    for k in _spc():
        line_id, _, characteristic = k.partition("|")
        out.append({"line_id": line_id, "characteristic": characteristic})
    return out


def record_measurement(line_id: str, characteristic: str, value: float,
                       sample_size: int = 1) -> None:
    """Append one SPC measurement so the chart window accumulates real
    history. Called by the SPC monitor on ingest. PROD: no-op (the
    historian already holds it)."""
    if not line_id or not characteristic:
        return
    with _LOCK:
        s = _spc()
        s.setdefault(_series_key(line_id, characteristic), []).append({
            "value": float(value or 0), "sample_size": int(sample_size or 1),
            "ts_utc": _now(),
        })
        _save("spc_series", s)


# ── CAPA register (the open-CAPA dedupe source) ──────────────────────
def _capas() -> dict[str, Any]:
    s = _load("capas")
    if not s:
        s = dict(_SEED_CAPAS)
        _save("capas", s)
    return s


def open_capas_for_line(line_id: str) -> list[dict[str, Any]]:
    """OPEN CAPAs for a line — the quality engineer reads these so it
    doesn't draft a duplicate corrective action. PROD: QMS query."""
    if not line_id:
        return []
    return [c for c in _capas().values()
            if c.get("line_id") == line_id and c.get("status") == "open"]


# ── Prior recalls (the recall-officer dedupe source) ─────────────────
def _recalls() -> dict[str, Any]:
    s = _load("recalls")
    if not s:
        s = {"rows": list(_SEED_RECALLS)}
        _save("recalls", s)
    return s


def recalls_for_batch(batch_id: str) -> list[dict[str, Any]]:
    """Prior recalls touching a batch — a new trace escalates instead of
    re-opening. PROD: QMS recall register query."""
    if not batch_id:
        return []
    return [r for r in _recalls().get("rows", [])
            if batch_id in (r.get("affected_batches") or [])]


def record_recall(row: dict[str, Any]) -> None:
    """Append a coordinated recall so a later trace sees it. PROD: no-op
    (the QMS already holds it)."""
    if not row.get("recall_id"):
        return
    with _LOCK:
        s = _recalls()
        s.setdefault("rows", []).append({
            "recall_id": str(row.get("recall_id") or ""),
            "product_class": str(row.get("product_class") or ""),
            "affected_batches": list(row.get("affected_batches") or []),
            "severity": str(row.get("severity") or ""),
            "opened_at_utc": str(row.get("opened_at_utc") or _now()),
        })
        _save("recalls", s)
