# quality-yield — Quality & Yield

Inspection → defect classification → root-cause → corrective action →
batch release. A quality-inspection department where every batch
decision is made by the right role and lands in a tamper-evident audit
log. YAML-FIRST: read `configs/config.yaml` and you see the whole
operation — but the floors that protect a customer (quarantine on
severity, safety-critical hard-stop, SPC out-of-control, regulator
routing, fail-closed release) live in deterministic Python the LLM
cannot waive, and records live in YOUR systems. A seeded dev store
(`agency/_shared/store.py`) stands in for the MES/LIMS/QMS/historian so
the FULL chain runs end-to-end with no connectors set.

## Story

A vision system on Line 7 flags a candidate defect on batch `B-4471`.
The event hits the `vision_event` entry point and lands on **Priya**,
the incoming-quality inspector role (`inspection_intake_agent`). She
normalises the raw camera payload into a canonical `QAEvent`, then
classifies it: `defect_class = dimensional`, `severity = 0.72`,
`quarantine_recommended = true`. Because severity crosses the 0.6
floor, two things happen on the wire at once: the case routes to
**Dev the quality engineer** (`quality_engineer_agent`) for root-cause
work, and — because the batch must not ship on a quality engineer's
say-so — to **Maya the QA manager** (`qa_manager_human`), the only role
with batch-release authority.

Dev correlates the defect to a process cause (setpoint drift on the
press), finds it is single-source, and drafts a CAPA: immediate
corrective action, preventive action, owner, due date. The CAPA is
*drafted, never auto-applied* — it rides to Maya for review. Maya
decides `quarantine` and routes to the quality-records registrar
(`qa_audit_logger_agent`), which appends a hash-chained WORM entry
naming the batch, the decision, and the CAPA. Had a low-severity
cosmetic defect come in instead, intake would have routed it to
**Sam the QA inspector** (`qa_inspector_human`) for a first-line check
before it ever reached Maya. Separately, the SPC analyst sweeps every
line at 06:00 and a customer field complaint can open a recall trace —
both feed the same release authority and the same audit terminus.

## Why it works in an organization

This is a `qual × goods` ROI cell: pooled ~10% of revenue, ~70%
routine, internal-visibility. Scrap and rework cost material and
labor; a defect caught in-line removes that loss, and a defect caught
*at the right severity* removes the far larger cost of a field recall.
The department automates the routine 70% — normalising heterogeneous
signals (vision, SPC, complaints), classifying defect class/severity,
correlating root cause, and drafting CAPAs — while reserving the
irreversible 30% (batch release, recall scope, regulatory filing) for
human authority with default-deny gates. Deterministic floors
(quarantine on severity ≥ 0.6, regulator routing, SPC control limits)
are enforced in code/wire, not hoped for in a prompt, so the system is
audit-defensible: every batch decision is hash-chained and every
high-judgment step is signed off by a named role.

## Configuration

Three tiers, grounded in `env` + `configs/config.yaml`.

### Tier 1 — Boot (required to start)
| Var | Purpose |
|---|---|
| `LEAFMESH_LICENSE_KEY` | Required to start the SDK. |
| `LEAFMESH_ENV_TOKEN` | Per-environment telemetry token (dev/staging/prod). |
| `OPENAI_API_KEY` | Primary LLM provider — `inspection_intake_agent`, `regulatory_compliance_agent` (gpt-4o-mini) and the manager. |
| `ANTHROPIC_API_KEY` | `quality_engineer_agent` + `recall_traceability_agent` run on `claude-sonnet-4-6`. Required in practice since those two agents are core. |

### Tier 2 — Host prerequisites & adapters (optional; sensible defaults)
| Var | Default | Purpose |
|---|---|---|
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` | `localhost` / `6379` / empty | Session + auto-storage store. `docker compose up redis -d`. |
| `QUALITY_YIELD_STORE_DIR` | `./data` | Seeded dev store (batches, lots, defects, SPC series, CAPA register, recalls) — the MES/LIMS/QMS/historian stand-in. Dev stub; never your system of record. |

### Tier 3 — Connectors (wire your systems of record)
| Var | Required by | Purpose |
|---|---|---|
| `QMS_MCP_URL` | `corrective_action_tracker_agent` | MCP gateway in front of your QMS; calls the `track_corrective_actions` tool. CAPAs live in your QMS, not this template. Empty until you wire it. |
| `HISTORIAN_MCP_URL` | `spc_monitor_agent` (optional swap) | Plant historian (PI, Ignition, InfluxDB…) gateway if you swap SPC off the dev store series. Referenced only in the swap-pattern comment. |
| `PAGER_WEBHOOK` | manager escalation | PagerDuty (or any) webhook target for auto-escalation. Defaults to the PagerDuty enqueue URL. |
| `SLACK_QA_CHANNEL` | manager escalation (`channel` target, `provider: slack`) | Channel id the escalation message posts to. |
| `SLACK_BOT_TOKEN` / `SLACK_SIGNING_SECRET` | — | These are fields of a `channels: {slack: {...}}` block (`ChannelConfig`). This template has **no** `channels:` block, so they are **NOT auto-consumed**; the escalation target reads only `SLACK_QA_CHANNEL` + `message_template`. Add a `channels:` block to activate the bot creds. |

## Agents

| Agent | Role (job title) | Responsibilities | Can perform |
|---|---|---|---|
| `inspection_intake_agent` | Incoming-Quality Inspector / Defect Classifier | Normalise any inspection signal into a `QAEvent`; classify defect class + severity. Module: `@pre_compose` pulls batch genealogy + lots + prior recalls; `@chain` enforces the quarantine gate + safety-critical hard-stop; `@compose` shapes per downstream. `llm` + module, knowledge `defect_taxonomy`, `enforce_yields`. | → `quality_engineer_agent` (real defect); → `qa_manager_human` (quarantine / severity ≥ 0.6 / safety-critical); → `qa_inspector_human` (real but low-severity, first-line check) |
| `quality_engineer_agent` | Quality Engineer | Root-cause analysis then draft a CAPA — never auto-applied. Module: `@pre_compose` pulls the SPC window + lots + open CAPAs; `@chain` enforces the CAPA-required floor (multi-source / safety / out-of-spec lot ⇒ CAPA + engineer review) and dedupes against the open register. `llm` (claude-sonnet-4-6, thinking) + module, knowledge `root_cause_patterns`. | → `plant_engineer_human` (multi-source / needs engineer review); → `qa_manager_human` (single-source CAPA review) |
| `spc_monitor_agent` | SPC / Process-Control Analyst | Deterministic X-bar / UCL / LCL + Western Electric out-of-control rules. Module: `@pre_compose` pulls the measurement window from the store/historian; `@chain` SPC-out-of-control floor flags the line; daily sweep. `programmatic`, cron `0 6 * * *`, module + `_shared/spc_math.py`. | → `plant_engineer_human` (line out of control) |
| `recall_traceability_agent` | Recall & Traceability Officer | Trace complaints/audits to batch/line/shift; scope recalls. Module: `@pre_compose` pulls batch genealogy + related batches sharing a tainted lot + prior recalls; `@chain` stamps regulator routing + statutory filing windows and freezes related batches (fail-closed, idempotent actuator). `llm` + module, knowledge `batch_register`. | → `qa_manager_human` |
| `regulatory_compliance_agent` | Regulatory Compliance Officer | Per-framework audit-readiness scorecard (FDA 21CFR11, ISO 13485, EU MDR, GMP, ISO 22000); evidence inventory vs requirements. `llm`, knowledge `qms_evidence_index`. | → `quality_engineer_agent` (gaps need CAPA); → `qa_manager_human` (manager review) |
| `corrective_action_tracker_agent` | CAPA Register Coordinator | Open / update / report CAPAs with effectiveness verification in your QMS. `programmatic`, `mcp` connector → `QMS_MCP_URL`. | terminal (connector push) |
| `qa_inspector_human` | QA Inspector (first-line verification) | First-line verification of real but low-severity defects; escalate or clear. `human`, default-deny fallback → escalate to QA manager. | → `qa_manager_human` (escalate); → `qa_audit_logger_agent` (cleared) |
| `qa_manager_human` | QA Manager (batch-release authority) | The only role that releases / quarantines / reworks / halts a batch. `human`, default-deny fallback → `halt_for_review`. | → `plant_engineer_human` (rework); → `qa_audit_logger_agent` (decided) |
| `plant_engineer_human` | Plant Engineer | Process-side root-cause + corrective directive on the floor. `human`, default-deny fallback → held. | → `qa_audit_logger_agent` |
| `qa_audit_logger_agent` | Quality Records Registrar | Hash-chained WORM audit of every batch decision + CAPA. `external` / `framework: custom`, registered in `main.py`. | terminal (audit terminus) |

## Deterministic floors (the LLM cannot waive these)

Each is real Python, fired in a `@chain` (which runs after the agent on
direct calls too) or the module body — not hoped for in a prompt:

- **Defect-severity / quarantine gate + safety-critical hard-stop** —
  `agency/_shared/defect_scoring.py`, on `inspection_intake_agent`. A
  safety-critical class or batch forces severity 1.0 + quarantine; a
  dimensional reading floors severity by how far out of tolerance it is;
  severity ≥ 0.6 forces quarantine. The LLM may RAISE risk, never lower it.
- **SPC out-of-control rule** — `agency/_shared/spc_math.py`, on
  `spc_monitor_agent`. X-bar ±3σ + Western Electric rules 1/2/3, recomputed
  from the measurement window; a flattering "in control" is overridden.
- **CAPA-required floor** — on `quality_engineer_agent`. Multi-source
  cause, safety-critical defect, or out-of-spec incoming lot ⇒ CAPA +
  engineer review mandatory; `multi_source` set from the cause count.
- **Regulator routing + statutory filing windows** — `REGULATOR_BY_CLASS`
  / `FILING_HOURS` in `agency/recall_traceability_agent.py`. Which
  regulator, what deadline — compliance data, never hallucinated.
  Fail-closed: no `defect_id` ⇒ trace-only, no filing obligation.
- **Fail-closed batch-release / quarantine actuator + idempotency** —
  `agency/_shared/batch_release.py`. The only verdict that ships is an
  explicit `release` of a non-safety-critical batch; everything else
  holds. A batch_id+decision is enacted at most once.

## Self-reliant context (`@pre_compose`) + the dev store

Each key agent pulls its own context from the seeded dev store
(`agency/_shared/store.py` — MES/LIMS/QMS/historian stand-in): the batch
genealogy + incoming lots (intake, engineer, recall), the SPC measurement
window (SPC monitor, engineer), the open CAPA register (engineer), prior
recalls + related batches sharing a tainted lot (recall). The store is
SEEDED with realistic batches, an out-of-spec titanium lot, one in-line
dimensional defect on safety-critical batch `B-4471`, an SPC series that
ENDS on an out-of-control point, an open CAPA and a prior recall — so the
full chain runs out of the box. Setting `HISTORIAN_MCP_URL` / `QMS_MCP_URL`
does nothing until you wire the calls in `store.py`.

## Python kept elsewhere

- `agency/_shared/audit_logger.py` — hash-chained WORM audit sink
  (registered in `main.py` for `qa_audit_logger_agent`).
- `agency/_shared/session_stash.py` — bridges the HITL gap + bounds the
  rework back-edge.

`regulatory_compliance_agent` stays **pure-YAML** — it's judgement-only
(scorecard scoring thresholds are prompt hard rules + wire conditions),
with no deterministic floor that an LLM could defeat by being wrong.

HITL chain with default-deny (qa_inspector, qa_manager, plant_engineer);
every batch decision lands in the audit log.

## Run it

```bash
cp env .env && pip install -r requirements.txt && python main.py
```

Day-0, no connectors: the seeded store makes the full chain run. Drive a
vision event for the seeded safety-critical batch `B-4471` — intake pulls
the batch genealogy, the hard-stop forces quarantine, the engineer's
CAPA-required floor fires on the out-of-spec lot, and the QA manager gate
holds the batch:

```python
import asyncio
from main import leafmesh

async def demo():
    await leafmesh.start()
    result = await leafmesh.process(
        entry_point="vision_event",
        input_data={"source_event_id": "evt-vision-4471", "batch_id": "B-4471"},
    )
    print(result)          # safety_hard_stop=True, quarantine_recommended=True
    await leafmesh.stop()

asyncio.run(demo())
```

(`leafmesh.process` is illustrative — use your SDK build's entry-point
invocation. The module-smoke in `tests/test_agent_logic.py` calls each
floor directly and asserts these outcomes.)
