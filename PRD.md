# quality-yield — Quality & Yield — PRD

## Story

A vision system on Line 7 flags a candidate defect on batch `B-4471`.
The `vision_event` entry point lands it on the incoming-quality
inspector (`inspection_intake_agent`), which normalises the raw camera
payload into a canonical `QAEvent` and classifies it: `dimensional`,
`severity 0.72`, `quarantine_recommended true`. Because severity
crosses the 0.6 floor, the case routes simultaneously to the quality
engineer (root cause + CAPA draft) and to the QA manager (the only
batch-release authority). The engineer finds a single-source process
cause and drafts — never applies — a CAPA. The manager decides
`quarantine` and the decision plus CAPA are appended to a hash-chained
WORM audit. A low-severity cosmetic defect would instead route to the
QA inspector for a first-line check. A daily SPC sweep and customer
field complaints feed the same release authority and the same audit
terminus.

## Flow

```
 vision_event ─┐
 spc_out_of_control ─┤
 customer_complaint ─┼─▶ inspection_intake_agent (normalise + classify)
 manual_action ─┘            │
                             ├─ defect_class != none ──▶ quality_engineer_agent
                             │                              │ multi_source/review ─▶ plant_engineer_human ─┐
                             │                              │ single-source CAPA ──▶ qa_manager_human       │
                             ├─ quarantine/sev≥0.6/safety ─▶ qa_manager_human ──────────────────────────┐  │
                             └─ real & low-severity ───────▶ qa_inspector_human ─┐                       │  │
                                                                escalate ─▶ qa_manager_human ◀───────────┘  │
                                                                cleared  ─▶ qa_audit_logger_agent           │
 scheduled_batch_inspection ─┐                                                                              │
 spc_measurement_ingest ─────┼─▶ spc_monitor_agent (X-bar + Western Electric) ─ out-of-control ─▶ plant_engineer_human
 spc_check ──────────────────┘                                                                             │
 capa_open/update/report ────▶ corrective_action_tracker_agent (mcp → QMS)  [terminal]                     │
 field_complaint ─┐                                                                                         │
 regulatory_batch_audit ─┼─▶ recall_traceability_agent (trace + recall + regulator stamp) ─▶ qa_manager_human
 internal_qa_review ─┘                                                                                      │
 orchestrate_recall ─────────▶ recall_traceability_agent                                                   │
 regulatory_audit_readiness ─▶ regulatory_compliance_agent ─ requires_capa ─▶ quality_engineer_agent       │
                                                            └ manager review ─▶ qa_manager_human            │
 qa_manager_human ─ rework ─▶ plant_engineer_human ─────────────────────────────────────────────────────┐ │
 qa_manager_human / plant_engineer_human / decided ──────────────────────▶ qa_audit_logger_agent [WORM, terminal]
```

## Actor inventory (job titles)

| Agent | Role | Type |
|---|---|---|
| `inspection_intake_agent` | Incoming-Quality Inspector / Defect Classifier | llm + module (@pre_compose / @chain / @compose) |
| `quality_engineer_agent` | Quality Engineer | llm (thinking) + module (@pre_compose / @chain) |
| `spc_monitor_agent` | SPC / Process-Control Analyst | programmatic (cron) + module (@pre_compose / @chain) |
| `recall_traceability_agent` | Recall & Traceability Officer | llm + module (@pre_compose / @chain) |
| `regulatory_compliance_agent` | Regulatory Compliance Officer | llm (pure-YAML) |
| `corrective_action_tracker_agent` | CAPA Register Coordinator | programmatic (mcp) |
| `qa_inspector_human` | QA Inspector (first-line verification) | human |
| `qa_manager_human` | QA Manager (batch-release authority) | human |
| `plant_engineer_human` | Plant Engineer | human |
| `qa_audit_logger_agent` | Quality Records Registrar (WORM audit) | external/custom |

## Data artifacts

- **`QAEvent`** (`data_structures`) — `batch_id`, `line_id`, `source`
  (`vision | spc | complaint | scheduled`), `received_at_utc`.
- **Defect classification** — `defect_class`, `severity`, `confidence`,
  `quarantine_recommended`, `qa_briefing` (intake yields).
- **Root cause + CAPA draft** — `root_causes[]`, `primary_cause`,
  `multi_source`, corrective/preventive actions, owner, due date.
- **SPC chart** — `x_bar`, `sigma`, `ucl`, `lcl`,
  `out_of_control_signals[]` (X-bar + Western Electric rules 1/2/3).
- **Recall record** — trace fields + `regulators_to_notify`,
  `filing_deadlines`, `recall_id`, severity class (regulator routing +
  filing windows stamped deterministically by the micro module).
- **Audit-readiness scorecard** — evidence present/stale/missing,
  `readiness_score`, `audit_ready`.
- **WORM audit entry** — `audit_event_id`, `hash_chain_prev`,
  `hash_chain_current` (hash-chained JSONL, dev sink).

## Per-agent spec

See `configs/config.yaml` — each agent carries its prompt (with an
`OUTPUT CONTRACT`), `yields`, `inputs`, `knowledge` group, and
`can_call` edges. Highlights:

- **inspection_intake** — two-stage prompt (normalise → classify);
  module `@pre_compose` pulls batch genealogy + lots + prior recalls,
  `@chain` enforces the quarantine gate + safety-critical hard-stop
  (`_shared/defect_scoring`), `@compose` shapes per downstream; knowledge
  `defect_taxonomy`.
- **quality_engineer** — root cause + CAPA in one pass; module
  `@pre_compose` pulls the SPC window + lots + open CAPAs, `@chain`
  enforces the CAPA-required floor (multi-source / safety / out-of-spec
  lot) and dedupes the register; knowledge `root_cause_patterns`.
- **spc_monitor** — module `@pre_compose` pulls the measurement window
  from the store/historian; deterministic statistics in `_shared/spc_math`;
  `@chain` SPC-out-of-control floor; cron `0 6 * * *`; the dev store SPC
  series (seeded with an out-of-control point) is the chart-window stub.
- **recall_traceability** — LLM trace + recall scoping; module
  `@pre_compose` pulls genealogy + related batches sharing a tainted lot
  + prior recalls, `@chain` stamps regulator routing / filing deadlines
  and freezes related batches via the fail-closed actuator
  (`_shared/batch_release`); knowledge `batch_register`.
- **regulatory_compliance** — five-framework scorecard; thresholds
  (0.90 ready / 0.80 review / 3+ missing) as prompt rules; knowledge
  `qms_evidence_index`.
- **corrective_action_tracker** — mcp connector to your QMS
  (`track_corrective_actions`); no template storage.
- **qa_audit_logger** — `external` / `framework: custom`; registered as
  an intelligence function in `main.py` (`audit_logger.register`).

## Entry points

17 entry points: channel/event ingress (`vision_event`,
`spc_out_of_control`, `customer_complaint`, `manual_action`), SPC
(`spc_measurement_ingest`, `spc_check`, `scheduled_batch_inspection`),
CAPA register (`capa_open` / `capa_update` / `capa_report`), recall &
audit (`field_complaint`, `regulatory_batch_audit`, `internal_qa_review`,
`orchestrate_recall`, `regulatory_audit_readiness`), and direct-call
debug entries for intake + quality engineer.

## Controls / trust

- **Default-deny human gates** — `qa_inspector_human`,
  `qa_manager_human`, `plant_engineer_human` all have
  `fallback_on_timeout: true` with halt/escalate fallbacks; an
  unanswered approval holds the batch, never silently ships it.
- **Deterministic floors (LLM cannot waive)** — quarantine gate +
  safety-critical hard-stop (`_shared/defect_scoring`, `@chain` on
  intake); SPC out-of-control rule (`_shared/spc_math`, `@chain` on the
  SPC monitor); CAPA-required floor (`@chain` on the engineer); regulator
  routing + statutory filing windows (`@chain` on recall); fail-closed
  batch-release / quarantine actuator + idempotency (`_shared/batch_release`).
  Each fires in a `@chain` (which runs after the agent on direct calls
  too) or the module body, not in a prompt.
- **WORM audit terminus** — every terminal human/recall path lands in
  `qa_audit_logger_agent` (hash-chained, insertion/deletion-detectable).
- **Mesh boundary** — customers are never members; field complaints
  enter via the `customer_complaint` / `field_complaint` entry points;
  regulator filings and CAPAs leave via connectors (QMS mcp).
- **No placeholder HITL** — `operator_ids` are commented out so gates
  route to the shared inbox rather than a nonexistent operator.

## Config pointer

`configs/config.yaml` is the department. `agency/*.py` hold the four
deepened agent modules (intake / engineer / SPC / recall) + the shared
deterministic helpers in `_shared/` (`store`, `spc_math`,
`defect_scoring`, `batch_release`, `session_stash`, `audit_logger`).
`validate_config.py` checks targets, conditions, the yields↔prompt
contract, cron shape, and module reconciliation.

## Reflection / decisions

- **Why the modules** — the floors a customer relies on (a severe or
  safety-critical defect cannot ship, an out-of-control line is flagged,
  a systemic cause carries a CAPA, the right regulator is filed within
  the statutory window, a batch ships only on an explicit release) are
  deterministic policy; hallucinating any of them is a recall or a
  compliance failure. They live in `@chain` floors / module bodies that
  fire on direct calls too (verified empirically). Everything
  judgment-shaped (classification narrative, root-cause prose, recall
  scoping) stays prompt-driven.
- **Self-reliant context** — each key agent pulls its own evidence via
  `@pre_compose` from the seeded dev store rather than trusting the
  upstream echo: batch genealogy + lots, the SPC window, the open CAPA
  register, prior recalls + related batches sharing a tainted lot.
- **State out of the template** — CAPAs live in the customer QMS (mcp),
  measurements in the plant historian; `agency/_shared/store.py` is a
  SEEDED dev stand-in (Rule 7), and the JSONL audit file is a dev sink,
  not systems of record.
- **`@compose` only where earned** — intake fans to three roles needing
  different payloads (engineer / manager / inspector), so it carries
  `@compose`. Recall calls one downstream, so it does not (no theater).
- **First-line inspector wire** — a real-but-low-severity defect routes
  to `qa_inspector_human`, mutually exclusive with the quarantine gate,
  so every defect lands with exactly one human role and the inspector
  is a live part of the cast, not a dead role.
- **Parallel engineer + manager** — a severe defect goes to both the
  quality engineer (root cause) and the QA manager (release) by design;
  these edges are intentionally not mutually exclusive.
