"""spc_monitor_agent — control-chart math + the out-of-control floor.

Three patterns, each serving a real domain need:

@pre_compose  load_spc_window — pull the recent MEASUREMENT WINDOW for the
              line/characteristic (or every series, on the daily sweep)
              from the source store. In dev that's the seeded series in
              agency/_shared/store.py; in prod the plant historian (PI,
              Ignition, InfluxDB). Self-reliant context pull.

@chain        enforce_spc_floor — the deterministic SPC OUT-OF-CONTROL
              rule (agency/_shared/spc_math: X-bar ±3σ + Western Electric
              rules 1/2/3). The chart is recomputed from the window over
              whatever the body produced; a line with any out-of-control
              signal is flagged requires_qa_action and listed in
              out_of_control_lines — the plant-engineer route fires on it.
              The math is never hallucinated and the floor fires on a
              direct call too (it lives in the @chain).

INGEST: `mode: ingest` appends one measurement to the store window. In
production measurements live in your historian — feed `spc_measurement_
ingest` from it, or swap this agent to an mcp connector following the
${HISTORIAN_MCP_URL:} pattern.
"""
from __future__ import annotations

from typing import Any

from leafmesh import LeafMeshLogger, chain, pre_compose

from agency._shared import store
from agency._shared.spc_math import MIN_SAMPLES, control_chart

logger = LeafMeshLogger(__name__)


# ── @pre_compose — pull the measurement window (self-reliant) ─────────
def load_spc_window(data: Any, ctx: Any) -> dict[str, Any]:
    """Fetch the chart window from the source store. PROD: historian read."""
    input_data = data if isinstance(data, dict) else {}
    line_id = str(input_data.get("line_id") or "")
    characteristic = str(input_data.get("characteristic") or "")
    window = int(input_data.get("window") or 50)
    return {
        "window_rows": store.spc_window(line_id, characteristic, window),
        "all_lines": store.spc_lines(),
    }


def _ctx(context: Any) -> dict[str, Any]:
    prepared = (context or {}).get("prepared_data", {}) if isinstance(context, dict) else {}
    c = prepared.get("context", {}) if isinstance(prepared, dict) else {}
    return c if isinstance(c, dict) else {}


def _chart_for(line_id: str, characteristic: str, window: int) -> dict[str, Any]:
    rows = store.spc_window(line_id, characteristic, window)
    return control_chart([r.get("value") for r in rows])


# ── @chain — the deterministic SPC out-of-control floor ──────────────
async def enforce_spc_floor(result: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """Recompute the chart from the store window and assert the
    out-of-control verdict. A direct call that hands in a flattering
    'in control' is overridden by the recomputed Western Electric rules.
    """
    if not isinstance(result, dict):
        result = {}
    line_id = str(result.get("line_id") or "")
    characteristic = str(result.get("characteristic") or "")
    window = int(result.get("_window") or 50)
    if line_id and characteristic:
        chart = _chart_for(line_id, characteristic, window)
        result["x_bar"] = chart["x_bar"]
        result["sigma"] = chart["sigma"]
        result["ucl"] = chart["ucl"]
        result["lcl"] = chart["lcl"]
        result["samples"] = chart["samples"]
        result["out_of_control_signals"] = chart["out_of_control_signals"]
        ooc = bool(chart["out_of_control_signals"]) and chart["enough_samples"]
        result["requires_qa_action"] = ooc
        result["out_of_control_lines"] = [line_id] if ooc else []
    result.pop("_window", None)
    return result


@pre_compose(context_processor=load_spc_window)
@chain(enforce_spc_floor)
async def spc_monitor_agent(
    llm_response: dict[str, Any] | str | None,   # programmatic — unused
    input_data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    input_data = input_data if isinstance(input_data, dict) else {}
    mode = str(input_data.get("mode") or "report").lower()

    if mode == "ingest":
        store.record_measurement(
            str(input_data.get("line_id") or "unknown"),
            str(input_data.get("characteristic") or "dimension_1"),
            float(input_data.get("value") or 0),
            int(input_data.get("sample_size") or 1),
        )

    line_id = str(input_data.get("line_id") or "")
    characteristic = str(input_data.get("characteristic") or "")
    window = int(input_data.get("window") or 50)

    # Daily sweep — every series under SPC; flag any out-of-control line.
    cctx = _ctx(context)
    all_lines = cctx.get("all_lines") or store.spc_lines()
    out_of_control_lines: list[str] = []
    for ser in all_lines:
        chart = _chart_for(ser["line_id"], ser["characteristic"], window)
        if chart["enough_samples"] and chart["out_of_control_signals"]:
            out_of_control_lines.append(ser["line_id"])
    lines_swept = len({s["line_id"] for s in all_lines})

    # Focused chart for the requested line (the @chain re-asserts it).
    chart = _chart_for(line_id, characteristic, window) if (line_id and characteristic) \
        else {"enough_samples": False, "samples": 0, "x_bar": 0.0, "sigma": 0.0,
              "ucl": 0.0, "lcl": 0.0, "out_of_control_signals": []}

    if line_id and characteristic and not chart["enough_samples"]:
        briefing = (f"Not enough samples ({chart['samples']}) on {line_id} — "
                    f"need at least {MIN_SAMPLES} for SPC.")
    else:
        briefing = (
            f"SPC on {line_id or 'all lines'} / {characteristic or 'all chars'}: "
            f"X-bar {chart['x_bar']:.3f}, σ {chart['sigma']:.3f}, "
            f"UCL {chart['ucl']:.3f}, LCL {chart['lcl']:.3f}. "
            f"{len(chart['out_of_control_signals'])} out-of-control signals across "
            f"{chart['samples']} samples. "
            f"{'QA action required.' if chart['out_of_control_signals'] else 'Process in control.'}"
        )
    logger.info(f"[spc] {briefing}")

    return {
        "lines_swept": lines_swept,
        "out_of_control_lines": out_of_control_lines,
        "mode": mode,
        "line_id": line_id,
        "characteristic": characteristic,
        "samples": chart["samples"],
        "x_bar": chart["x_bar"],
        "sigma": chart["sigma"],
        "ucl": chart["ucl"],
        "lcl": chart["lcl"],
        "out_of_control_signals": chart["out_of_control_signals"],
        "requires_qa_action": bool(chart["out_of_control_signals"]) and chart["enough_samples"],
        "spc_briefing": briefing,
        "_window": window,   # consumed + stripped by enforce_spc_floor
    }
