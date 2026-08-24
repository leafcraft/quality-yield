# quality-yield — Quality & Yield Pod

A quality-inspection **pod**: a coordinator + specialists + a finisher + human
members + a supervisor, running a defect end to end — signal → classify →
root-cause → **the batch-release authority decides** → the finisher issues the
release certificate (fail-closed) → every decision lands in a tamper-evident
audit log. Read `configs/config.yaml` and you see the whole department: every
wire is conditional, every human gate default-denies on silence, the finisher
produces the real artifact and fails closed. The floors that protect a customer
(quarantine on severity, safety-critical hard-stop, SPC out-of-control, regulator
routing, fail-closed release) live in deterministic Python the LLM cannot waive.
A seeded dev store (`agency/_shared/store.py`) stands in for the MES/LIMS/QMS/
historian so the whole chain runs **day-0 with no connectors wired**.

## Story

A vision system on Line 7 flags a candidate defect on batch `B-4471`. The signal
hits the `report_signal` entry point and lands on the **QA Case Coordinator**
(`qa_coordinator_agent`), who opens the case, records the batch, and hands it to
the **Incoming-Quality Inspector** (`inspection_intake_agent`). The inspector
pulls the batch genealogy through its tools — `B-4471` is flagged
`safety_critical` — normalises the camera payload, and classifies:
`defect_class = safety_critical`, `severity = 1.0`, `quarantine_recommended =
true`. The intake `@chain` re-asserts the safety hard-stop deterministically: the
model *cannot* present a safety-critical defect as a pass.

Two things happen on the wire at once. The case routes to the **Quality
Engineer** (`quality_engineer_agent`) for root-cause work — who finds the
out-of-spec titanium lot the batch consumed, marks it multi-source, and drafts
(never applies) a CAPA that routes to the **Plant Engineer**. And it routes to
the **Batch Disposition & Release finisher** (`batch_release_agent`), which does
*not* release anything itself: it routes the batch to the **QA Manager**
(`qa_manager_human`) — the only role with batch-release authority — and bounds the
rework loop. When the manager decides, the finisher re-checks the verdict against
an allow/deny token list (unknown fails closed), re-derives the safety invariant
in code (a safety-critical batch never ships on this path), and on a real release
renders the certificate to `./out`, deleting any partial on a tripped floor.
Every decision — release, quarantine, held-on-timeout, CAPA, recall — is
hash-chained in the **Quality Records Registrar** (`qa_audit_logger_agent`).

Had a low-severity cosmetic defect come in instead, intake would have routed it
to the **QA Inspector** for a first-line check. Separately, the SPC analyst
sweeps every line at 06:00 (plant time), a scheduled backlog sweeper fans out
across the pending-inspection queue in parallel copies, and a customer field
complaint opens a recall trace — all feeding the same release authority and the
same audit terminus. Stalls and contract violations escalate to the **Quality
Director**, the accountable owner, via the Manager supervisor.

## Why it works in an organization

This is a `qual × goods` ROI cell: pooled ~10% of revenue, ~70% routine,
internal-visibility. Scrap and rework cost material and labor; a defect caught
in-line removes that loss, and a defect caught *at the right severity* removes the
far larger cost of a field recall. The pod automates the routine 70% —
normalising heterogeneous signals (vision, SPC, complaints), classifying defect
class/severity, correlating root cause, drafting CAPAs — while reserving the
irreversible 30% (batch release, recall scope, regulatory filing) for human
authority behind default-deny gates. **One human, one agent, one system per
stage:** the agent produces, the human decides, the system records — no stage
advances on an agent's own authority. Deterministic floors (quarantine on
severity ≥ 0.6, safety hard-stop, SPC control limits, regulator routing,
fail-closed release) are enforced in code and on the wire, not hoped for in a
prompt, so the department is audit-defensible: every batch decision is
hash-chained and every high-judgment step is signed off by a named role.

## Prerequisites

Three tiers, grounded in `env` + `configs/config.yaml`.

### Tier 1 — Boot (required to start)
| Var | Purpose |
|---|---|
| `LEAFMESH_LICENSE_KEY` | Required to start the SDK. |
| `LEAFMESH_ENV_TOKEN` | Per-environment telemetry token (dev/staging/prod). |
| `OPENAI_API_KEY` | `qa_coordinator_agent`, `inspection_intake_agent`, `batch_inspection_sweeper_agent`, `regulatory_compliance_agent` (gpt-4o-mini) + the Manager. |
| `ANTHROPIC_API_KEY` | `quality_engineer_agent` + `recall_traceability_agent` run on `claude-sonnet-4-6`. |

### Tier 2 — Host prerequisites & adapters (optional; sensible defaults)
| Var | Default | Purpose |
|---|---|---|
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` | `localhost` / `6379` / empty | Session store. `docker compose up redis -d`. |
| `QUALITY_YIELD_STORE_DIR` | `./data` | Seeded dev store (batches, lots, defects, SPC series, CAPA register, recalls, pending-inspection backlog) — the MES/LIMS/QMS/historian stand-in. Dev stub; never your system of record. |
| `HITL_OUTBOUND_URL` | local stub | Where the human gates send their outbound notification. Point it at your inbox/Teams/Slack webhook relay. |

### Tier 3 — Connectors (wire your systems of record)
| Var | Required by | Purpose |
|---|---|---|
| `LIMS_MCP_URL` / `LIMS_MCP_TOKEN` | `inspection_intake_agent` (commented `mcp:` block) | LIMS / vision-system gateway. Fill the URL, then uncomment the block. An empty-url mcp block is rejected at load, so it ships commented; until then intake reads the seed store. |
| `QMS_MCP_URL` | `corrective_action_tracker_agent` (commented `integration: mcp`) | QMS gateway (`track_corrective_actions`). CAPAs live in your QMS; until wired, the tracker runs the register off the dev store. |
| `HISTORIAN_MCP_URL` | `spc_monitor_agent` (optional swap) | Plant historian (PI, Ignition, InfluxDB…) if you swap SPC off the dev-store series. |

## Agents

| Agent | Role (job title) | Responsibilities | Can perform |
|---|---|---|---|
| `qa_coordinator_agent` | QA Case Coordinator | Opens the case, orchestrates the pod, keeps live status, single point of contact. Pure orchestration — never classifies or releases. `llm` (pure-YAML). | → `inspection_intake_agent` (open case); → `quality_director_human` (escalate) |
| `inspection_intake_agent` | Incoming-Quality Inspector / Defect Classifier | Normalise any inspection signal; classify defect class + severity. `llm` + super_agent + playbook + tools; module `@pre_compose` pulls batch genealogy, `@chain` enforces the quarantine gate + safety hard-stop, `@compose` shapes per downstream. Daily intake sweep. | → `quality_engineer_agent` (real defect); → `batch_release_agent` (quarantine / severity ≥ 0.6 / safety-critical); → `qa_inspector_human` (real but low-severity, first-line) |
| `quality_engineer_agent` | Quality Engineer | Root-cause analysis then draft a CAPA — never auto-applied. `llm` (claude-sonnet-4-6, thinking) + playbook; module `@pre_compose` pulls SPC window + lots + open CAPAs, `@chain` enforces the CAPA-required floor + dedupes the register. | → `plant_engineer_human` (multi-source / review); → `batch_release_agent` (single-source disposition); → `corrective_action_tracker_agent` (CAPA required) |
| `batch_inspection_sweeper_agent` | Batch-Inspection Sweeper | Scheduled backlog fan-out — runs as `instances: 3` parallel copies, each claiming a disjoint slice of the pending-inspection queue via the atomic `claim_inspection` tool. `llm` + `@chain` normalise. Daily cron (plant timezone). | → `inspection_intake_agent` (claimed_count > 0) |
| `spc_monitor_agent` | SPC / Process-Control Analyst | Deterministic X-bar / UCL / LCL + Western Electric rules over the measurement window. `programmatic` + module (`_shared/spc_math.py`); `@chain` out-of-control floor. Daily sweep (plant timezone). | → `plant_engineer_human` (line out of control) |
| `recall_traceability_agent` | Recall & Traceability Officer | Trace complaints/audits to batch/line/shift; scope recalls; draft the customer response. `llm` + playbook; module `@chain` stamps regulator routing + statutory filing windows and freezes related batches (fail-closed, idempotent). | → `qa_manager_human` (recall / exec escalation); → `qa_audit_logger_agent` (trace-only) |
| `regulatory_compliance_agent` | Regulatory Compliance Officer | Per-framework audit-readiness scorecard (FDA 21CFR11, ISO 13485, EU MDR, GMP, ISO 22000). `llm`; module `@chain` forces regulator routing on any reportable class. | → `quality_engineer_agent` (gaps need CAPA); → `qa_manager_human` (review); → `recall_traceability_agent` (statutory filing) |
| `corrective_action_tracker_agent` | CAPA Register Coordinator | Open / update / report CAPAs with effectiveness verification. `programmatic` + dev-store module; the QMS `mcp` connector ships commented. | → `qa_audit_logger_agent` (record) |
| `batch_release_agent` | Batch Disposition & Release (the FINISHER) | The actuator behind the QA-manager gate. Re-checks the release (allow/deny tokens, unknown fails closed), re-derives the safety invariant in code, renders the certificate to `./out`, deletes on a tripped floor, records the ledger, and bounds the rework loop. `programmatic` + `@chain` release floor. | → `qa_manager_human` (route for decision); → `quality_director_human` (loop exhausted); → `qa_audit_logger_agent` (terminus) |
| `qa_manager_human` | QA Manager (batch-release authority) | The only role that releases / quarantines / reworks / halts a batch. `human`, default-deny → `held_for_review`. | → `batch_release_agent` (release); → `quality_engineer_agent` (rework); → `qa_audit_logger_agent` (held / quarantine / halt) |
| `qa_inspector_human` | QA Inspector (first-line verification) | First-line check of a real but low-severity defect; escalate or clear. `human`, default-deny → `held_for_review`. | → `batch_release_agent` (escalate); → `qa_audit_logger_agent` (pass / clear / held) |
| `plant_engineer_human` | Plant Engineer | Process-side root-cause + corrective directive on the floor. `human`, default-deny → `held_for_review`. | → `qa_audit_logger_agent` |
| `quality_director_human` | Quality Director (accountable owner) | Reviews escalations; owns the call on everything that leaves the pod; the Manager's escalation target and the terminal for exhausted rework loops. `human`, default-deny → `held_for_review`. | → `qa_audit_logger_agent` |
| `qa_audit_logger_agent` | Quality Records Registrar | Hash-chained WORM audit of every batch decision, CAPA, and recall. `external` / `framework: custom`, registered in `main.py`. | terminal (audit terminus) |

## The finisher (`batch_release_agent`)

The single most load-bearing pattern — the artifact-producing executor wired
*after* the human gate (`agency/batch_release_agent.py`):

1. **Re-checks the release** — reaching the finisher never means released. An
   allow-token allowlist (`release`, `approve`, `ship`…) + block-token denylist
   (`quarantine`, `rework`, `halt`, `held_for_review`…); an unknown verdict
   **fails closed**.
2. **Deterministic invariants a human can't waive** — a batch on record, and the
   fail-closed actuator (`_shared/batch_release.py`) that **never ships a
   safety-critical batch** on this path and is idempotent per batch+decision.
3. **Renders the real certificate** to `./out`.
4. **Fails closed on trip — deletes the partial**, marks `status: blocked` +
   `block_reasons`, records nothing downstream.
5. **Bounds the rework back-edge** — phase-1 routing increments a per-batch round
   counter; past `MAX_DISPOSITION_ROUNDS` it routes to the Quality Director (a
   terminal) instead of looping forever.
6. **Dev-store recovery** — after the HITL gap the upstream payload is dropped, so
   the finisher recovers the case from the session stash / dev store.

## Deterministic floors (the LLM cannot waive these)

Each is real Python, fired in a `@chain` (which runs after the agent on direct
calls too) or the module body — not hoped for in a prompt:

- **Defect-severity / quarantine gate + safety-critical hard-stop** —
  `agency/_shared/defect_scoring.py`, on `inspection_intake_agent`.
- **SPC out-of-control rule** — `agency/_shared/spc_math.py`, on
  `spc_monitor_agent` (X-bar ±3σ + Western Electric rules 1/2/3).
- **CAPA-required floor** — on `quality_engineer_agent` (multi-source / safety /
  out-of-spec lot ⇒ CAPA + engineer review).
- **Regulator routing + statutory filing windows** — shared tables in
  `agency/recall_traceability_agent.py`, on recall + regulatory.
- **Fail-closed batch-release actuator + idempotency** —
  `agency/_shared/batch_release.py`, behind the finisher.

## Modern capabilities

- **instances** — `batch_inspection_sweeper_agent` runs 3 parallel copies per
  activation; the atomic `claim_inspection` tool (`agency/tools.py`) splits the
  backlog so copies never duplicate work (merge is lists-concat / counts-sum).
- **timezone** — every live `wake_up` (SPC sweep 06:00, intake sweep 08:00,
  batch sweep 07:00) declares `timezone: Asia/Kolkata` (set it to your plant's
  timezone) so crons don't silently fire in UTC.
- **super_agent** — dict form on `inspection_intake_agent`
  (`{cost_ceiling, wall_clock}`).
- **playbook** — per-intent craft on intake / engineer / recall: only the
  `description` rides in the prompt; the `responsibility` loads on demand.
- **mcp** — declared but **commented** (an empty-url mcp block is rejected at
  load); dev-store tools carry day-0.

## Run it

```bash
cp env .env && pip install -r requirements.txt && python main.py
```

Day-0, no connectors: the seeded store makes the full chain run. Drive a vision
event for the seeded safety-critical batch `B-4471`:

```bash
curl -X POST http://127.0.0.1:18820/api/mesh/request \
  -H "Content-Type: application/json" \
  -d '{"entry_point":"report_signal","data":{"source_event_id":"evt-vision-4471","batch_id":"B-4471"}}'
```

The coordinator opens the case, intake's hard-stop forces quarantine + max
severity, the engineer's CAPA-required floor fires on the out-of-spec lot, and
the finisher routes the batch to the QA manager — and never ships a
safety-critical batch on the release path. The example scenarios in `examples/`
exercise the routine-inspection, defect-cluster, field-complaint, and
regulatory-audit paths; `tests/` asserts the topology and floors.
