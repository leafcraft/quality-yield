# quality-yield — Quality & Yield Pod — PRD

## Story

A vision system on Line 7 flags a candidate defect on batch `B-4471`. The
`report_signal` entry point lands it on the **QA Case Coordinator**
(`qa_coordinator_agent`), which opens the case and hands it to the
**Incoming-Quality Inspector** (`inspection_intake_agent`). Intake pulls the
batch genealogy (`B-4471` is `safety_critical`), normalises the payload, and
classifies it; the `@chain` re-asserts the safety hard-stop deterministically —
`severity 1.0`, `quarantine_recommended true`, the model cannot present it as a
pass. The case routes simultaneously to the **Quality Engineer** (root cause +
CAPA draft) and to the **Batch Disposition & Release finisher**, which routes the
batch to the **QA Manager** — the only batch-release authority. The manager
decides; the finisher re-checks the verdict (fail-closed), re-derives the safety
invariant in code (a safety-critical batch never ships on this path), and records
the outcome. A low-severity cosmetic defect would instead route to the **QA
Inspector** for a first-line check. A daily SPC sweep, a scheduled backlog fan-out
(`instances`), and customer field complaints feed the same authority and the same
hash-chained audit terminus. Stalls escalate to the **Quality Director**.

## Pod shape

Coordinator + specialists + finisher + human members + supervisor:

- **Coordinator** — `qa_coordinator_agent` (owns the case, orchestrates, keeps
  status; never classifies or releases).
- **Specialists** — `inspection_intake_agent`, `quality_engineer_agent`,
  `spc_monitor_agent`, `recall_traceability_agent`, `regulatory_compliance_agent`,
  `batch_inspection_sweeper_agent`, `corrective_action_tracker_agent`.
- **Finisher** — `batch_release_agent` (the actuator behind the QA-manager gate).
- **Human members** — `qa_manager_human` (batch-release authority),
  `qa_inspector_human`, `plant_engineer_human`, `quality_director_human`
  (accountable owner).
- **Supervisor** — the LeafMesh Manager, escalating to the Quality Director.

## Flow

```
 report_signal / case_status ─▶ qa_coordinator_agent
                                  │ open_case ─▶ inspection_intake_agent
                                  └ escalate ─▶ quality_director_human
 vision_event / spc_out_of_control / direct_call_inspection_intake ─▶ inspection_intake_agent
     │ defect_class != none ─────────────▶ quality_engineer_agent
     │                                        │ multi/review ─▶ plant_engineer_human ─▶ audit
     │                                        │ single-source ─▶ batch_release_agent
     │                                        └ capa_required ─▶ corrective_action_tracker_agent ─▶ audit
     │ quarantine/sev≥0.6/safety ─────────▶ batch_release_agent (FINISHER)
     └ real & low-severity ───────────────▶ qa_inspector_human
                                               │ escalate ─▶ batch_release_agent
                                               └ pass/clear/held ─▶ audit
 batch_release_agent ── status routed ─────▶ qa_manager_human
                        status exhausted ──▶ quality_director_human   (bounded back-edge terminal)
                        always ────────────▶ qa_audit_logger_agent
 qa_manager_human ── release ──────────────▶ batch_release_agent (issue certificate)
                     rework ───────────────▶ quality_engineer_agent  (bounded back-edge)
                     held/quarantine/halt ─▶ qa_audit_logger_agent
 scheduled_batch_inspection ─▶ batch_inspection_sweeper_agent (instances:3, claim_inspection) ─▶ inspection_intake_agent
 spc_* ─▶ spc_monitor_agent ── out-of-control ─▶ plant_engineer_human
 capa_* ─▶ corrective_action_tracker_agent ─▶ qa_audit_logger_agent
 field_complaint / customer_complaint / regulatory_batch_audit / orchestrate_recall ─▶ recall_traceability_agent
                     recall/exec ─▶ qa_manager_human   ;   trace-only ─▶ qa_audit_logger_agent
 regulatory_audit_readiness ─▶ regulatory_compliance_agent
                     requires_capa ─▶ quality_engineer_agent
                     manager review ─▶ qa_manager_human
                     statutory filing ─▶ recall_traceability_agent
 inspector_reply / manager_reply ─▶ (HITL resume into the human gates)
 qa_audit_logger_agent  [WORM, terminal]
```

## Actor inventory (job titles)

| Agent | Role | Type |
|---|---|---|
| `qa_coordinator_agent` | QA Case Coordinator | llm (pure-YAML) |
| `inspection_intake_agent` | Incoming-Quality Inspector / Defect Classifier | llm + super_agent + playbook + tools + module |
| `quality_engineer_agent` | Quality Engineer | llm (thinking) + playbook + module |
| `batch_inspection_sweeper_agent` | Batch-Inspection Sweeper | llm (instances:3) + tools + module |
| `spc_monitor_agent` | SPC / Process-Control Analyst | programmatic (cron) + module |
| `recall_traceability_agent` | Recall & Traceability Officer | llm + playbook + module |
| `regulatory_compliance_agent` | Regulatory Compliance Officer | llm + module |
| `corrective_action_tracker_agent` | CAPA Register Coordinator | programmatic + dev-store module (mcp commented) |
| `batch_release_agent` | Batch Disposition & Release (FINISHER) | programmatic + @chain floor |
| `qa_manager_human` | QA Manager (batch-release authority) | human |
| `qa_inspector_human` | QA Inspector (first-line verification) | human |
| `plant_engineer_human` | Plant Engineer | human |
| `quality_director_human` | Quality Director (accountable owner) | human |
| `qa_audit_logger_agent` | Quality Records Registrar (WORM audit) | external/custom |

## Data artifacts

- **Defect classification** — `defect_class`, `severity`, `confidence`,
  `quarantine_recommended`, `safety_hard_stop`, `qa_briefing` (intake yields).
- **Root cause + CAPA draft** — `root_causes[]`, `primary_cause`, `multi_source`,
  corrective/preventive actions, owner, due date, `capa_required`,
  `linked_open_capa_id`.
- **SPC chart** — `x_bar`, `sigma`, `ucl`, `lcl`, `out_of_control_signals[]`.
- **Recall record** — trace fields + `regulators_to_notify`, `filing_deadlines`,
  `recall_id`, severity class (regulator routing stamped by the module).
- **Audit-readiness scorecard** — evidence present/stale/missing,
  `readiness_score`, `audit_ready`, reportability + filing (stamped).
- **Release certificate** — `certificate_path` (`./out`), `disposition`, `status`
  (`routed | released | blocked | exhausted`), `block_reasons` (finisher yields).
- **WORM audit entry** — `audit_event_id`, `hash_chain_prev`,
  `hash_chain_current` (hash-chained JSONL, dev sink).

## Entry points (20)

Coordinator (`report_signal`, `case_status`); direct specialist entries
(`vision_event`, `spc_out_of_control`, `direct_call_inspection_intake`,
`direct_call_quality_engineer`); recall & audit (`customer_complaint`,
`field_complaint`, `regulatory_batch_audit`, `orchestrate_recall`,
`regulatory_audit_readiness`); SPC (`spc_measurement_ingest`, `spc_check`,
`scheduled_spc_sweep`); scheduled fan-out (`scheduled_batch_inspection`); CAPA
register (`capa_open`, `capa_update`, `capa_report`); HITL resume
(`inspector_reply`, `manager_reply`).

## Controls / trust

- **One human, one agent, one system per stage** — the agent produces, the human
  decides, the system records; no stage advances on an agent's own authority.
- **Every wire conditional** — a bare unconditional edge is a smell; the only
  `always` edges are the audit termini.
- **Default-deny human gates** — all four humans set `fallback_on_timeout: true`
  with `human_message: held_for_review`; silence never releases a batch. `dual`
  lives on the human boundary only; no human calls another human (dual→dual
  ping-pong) — human→human escalation is the Manager's job.
- **The finisher** — re-checks the release (allow/deny tokens, unknown fails
  closed), re-derives the safety invariant in code, renders the certificate to
  `./out`, deletes on a tripped floor, records the ledger. Bounds the rework loop
  with a per-batch round counter → a terminal escalation to the Quality Director.
- **Deterministic floors (LLM cannot waive)** — quarantine gate + safety hard-stop
  (`_shared/defect_scoring`); SPC out-of-control (`_shared/spc_math`);
  CAPA-required floor (engineer `@chain`); regulator routing + filing windows
  (recall + regulatory `@chain`, shared tables); fail-closed release actuator +
  idempotency (`_shared/batch_release`).
- **WORM audit terminus** — every terminal path lands in `qa_audit_logger_agent`
  (hash-chained, insertion/deletion-detectable), including gates held on timeout.
- **Mesh boundary** — customers are never members; complaints enter via entry
  points; CAPAs/filings leave via connectors (all `${VAR:}` empty-default, inert
  until wired).

## Modern capabilities

- **instances** — `batch_inspection_sweeper_agent: instances: 3` +
  `instances_handoff: last`; the atomic `claim_inspection` tool splits the
  pending-inspection backlog so copies never duplicate work. Yields are
  lists+counts only, so the deterministic merge (concat / sum) stays honest.
- **timezone** — every live `wake_up` declares `timezone` (plant time), so crons
  don't silently fire in UTC.
- **super_agent** — dict form on intake (`cost_ceiling` / `wall_clock`).
- **playbook** — per-intent craft (intake / engineer / recall): `description` in
  the prompt, `responsibility` loaded on demand (never duplicated into the prompt).
- **mcp** — declared but commented on intake (LIMS) and the CAPA tracker (QMS);
  an empty-url mcp block is rejected at load, so dev-store tools carry day-0.

## Config pointer

`configs/config.yaml` is the department. `agency/*_agent.py` bind by name
(auto_discover): the coordinator is pure-YAML; intake / engineer / SPC / recall /
regulatory / sweeper / finisher / CAPA-tracker carry modules; `_shared/` holds
the deterministic helpers (`store`, `spc_math`, `defect_scoring`, `batch_release`,
`session_stash`, `audit_logger`). `agency/tools.py` registers the day-0 tools
(evidence reads + the atomic claim). `main.py` imports `agency.tools` before
`from_yaml`, then registers the WORM sink. `validate_config.py` checks targets,
conditions, the yields↔prompt contract, cron shape, and module reconciliation.

## Reflection / decisions

- **Coordinator + finisher added** — the prior build wired specialists straight
  to human gates; the pod now owns the case in a coordinator and puts the
  artifact-producing executor *behind* the batch-release gate, so a release is a
  re-checked, fail-closed actuation, not a hand-off.
- **No dual→dual** — humans route only to chain/programmatic/external agents;
  human→human escalation goes through the Manager supervisor (0 dual warnings).
- **Bounded back-edge** — the rework loop (manager → engineer → finisher →
  manager) is bounded by a per-batch round counter that terminates at the Quality
  Director.
- **instances on a dedicated sweeper** — fan-out lives on the backlog sweeper (its
  yields are lists+counts that merge cleanly), never on the per-case classifier
  (whose severity would wrongly sum across copies).
- **State out of the template** — CAPAs → QMS (mcp, commented), measurements →
  historian; `store.py` is a seeded dev stand-in (Rule 7), the JSONL audit file a
  dev sink — never systems of record.
