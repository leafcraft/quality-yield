# quality-yield — Quality & Yield Pod

A quality-inspection **pod**: a coordinator + specialists + a finisher + human
members + a supervisor, running a defect end to end — signal → classify →
root-cause → **the batch-release authority decides** → the finisher issues the
release certificate (fail-closed) → every decision lands in a tamper-evident
audit log. Read `configs/config.yaml` and you see the whole department: every
wire is conditional, nothing is released on silence, the finisher
produces the real artifact and fails closed. The floors that protect a customer
(quarantine on severity, safety-critical hard-stop, SPC out-of-control, regulator
routing, fail-closed release, two signatures on a safety-critical release) live
in deterministic Python the LLM cannot waive.
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
an allow/deny token list (unknown fails closed) and re-derives the safety
invariant in code: `B-4471` is safety-critical, so the QA Manager's `release`
is only the first signature — the batch then waits for the **Quality
Director**'s, and ships only when two different people have signed. On a real
release it renders the certificate to `./out`, deleting any partial on a
tripped floor. Every decision — release, signature, quarantine, CAPA, recall —
is hash-chained in the **Quality Records Registrar** (`qa_audit_logger_agent`).

Had a low-severity cosmetic defect come in instead, intake would have routed it
to the **QA Inspector** for a first-line check. Separately, the SPC analyst
sweeps every line at 06:00 (plant time), a scheduled backlog sweeper fans out
across the pending-inspection queue in parallel copies, and a customer field
complaint opens a recall trace — all feeding the same release authority and the
same audit terminus. A Class I/II recall goes to the **Quality Director**, the
accountable owner; repeated agent failures reach the Director through the
Manager supervisor. A request nobody answers is not escalated: its Inbox card
closes and the ticket records that no reply came — nothing ships on silence.

## Why it works in an organization

This is a `qual × goods` ROI cell: pooled ~10% of revenue, ~70% routine,
internal-visibility. Scrap and rework cost material and labor; a defect caught
in-line removes that loss, and a defect caught *at the right severity* removes the
far larger cost of a field recall. The pod automates the routine 70% —
normalising heterogeneous signals (vision, SPC, complaints), classifying defect
class/severity, correlating root cause, drafting CAPAs — while reserving the
irreversible 30% (batch release, recall scope, regulatory filing) for human
authority — nothing is released on silence. **One human, one agent, one system per
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
| *(no setting)* | Studio Inbox | The human gates answer in Studio's Inbox. To notify an external tool (Teams / Slack relay) instead, create a vault connection (an "Add custom" record on the Infrastructure page, service type 'Outbound webhook') and set `webhook_config: {connection: <name>}` on the seat. |

### Tier 3 — Connectors (wire your systems of record)
| Var | Required by | Purpose |
|---|---|---|
| vault connection `lims_mcp` | `inspection_intake_agent` (commented `mcp:` block) | LIMS / vision-system MCP server. Create the connection on Infrastructure → Integrations → MCP (URL + token live there, never in config or `.env`), then uncomment the block (in `configs/config.yaml` before the first start, in Studio's YAML editor after it). Until then intake reads the seed store. |
| vault connection `qms_mcp` | `corrective_action_tracker_agent` (commented `integration: mcp`) | QMS MCP server (`track_corrective_actions`). CAPAs live in your QMS; create the connection on Infrastructure → Integrations → MCP (or an "Add custom" record, service type Tools, base URL = the server's address), then uncomment the block (in `configs/config.yaml` before the first start, in Studio's YAML editor after it). Until wired, the tracker runs the register off the dev store. |

## Agents

| Agent | Role (job title) | Responsibilities | Can perform |
|---|---|---|---|
| `qa_coordinator_agent` | QA Case Coordinator | Opens the case, orchestrates the pod, keeps live status, single point of contact. Pure orchestration — never classifies or releases. `llm` (pure-YAML). | → `inspection_intake_agent` (open case); → `quality_director_human` (escalate) |
| `inspection_intake_agent` | Incoming-Quality Inspector / Defect Classifier | Normalise any inspection signal; classify defect class + severity. `llm` + super_agent + playbook + tools; module `@pre_compose` pulls batch genealogy, `@chain` enforces the quarantine gate + safety hard-stop, `@compose` shapes per downstream. Daily intake sweep. | → `quality_engineer_agent` (real defect); → `batch_release_agent` (quarantine / severity ≥ 0.6 / safety-critical); → `qa_inspector_human` (real but low-severity, first-line) |
| `quality_engineer_agent` | Quality Engineer | Root-cause analysis then draft a CAPA — never auto-applied. `llm` (claude-sonnet-4-6, thinking) + playbook; module `@pre_compose` pulls SPC window + lots + open CAPAs, `@chain` enforces the CAPA-required floor + dedupes the register. | → `plant_engineer_human` (multi-source / review); → `batch_release_agent` (single-source disposition); → `corrective_action_tracker_agent` (CAPA required) |
| `batch_inspection_sweeper_agent` | Batch-Inspection Sweeper | Scheduled backlog fan-out — runs as `scaling.copies.count: 3` parallel copies, each claiming a disjoint slice of the pending-inspection queue via the atomic `claim_inspection` tool. `llm` + `@chain` normalise. Daily cron (plant timezone). | → `inspection_intake_agent` (claimed_count > 0) |
| `spc_monitor_agent` | SPC / Process-Control Analyst | Deterministic X-bar / UCL / LCL + Western Electric rules over the measurement window. `programmatic` + module (`_shared/spc_math.py`); `@chain` out-of-control floor. Daily sweep (plant timezone). | → `plant_engineer_human` (line out of control) |
| `recall_traceability_agent` | Recall & Traceability Officer | Trace complaints/audits to batch/line/shift; scope recalls; draft the customer response. `llm` + playbook; module `@chain` stamps regulator routing + statutory filing windows and freezes related batches (fail-closed, idempotent). | → `quality_director_human` (Class I/II — executive escalation); → `qa_manager_human` (other recalls); → `qa_audit_logger_agent` (trace-only) |
| `regulatory_compliance_agent` | Regulatory Compliance Officer | Per-framework audit-readiness scorecard (FDA 21CFR11, ISO 13485, EU MDR, GMP, ISO 22000). `llm`; module `@chain` forces regulator routing on any reportable class. | → `quality_engineer_agent` (gaps need CAPA); → `qa_manager_human` (review); → `recall_traceability_agent` (statutory filing) |
| `corrective_action_tracker_agent` | CAPA Register Coordinator | Open / update / report CAPAs with effectiveness verification. `programmatic` + dev-store module; the QMS `mcp` connector ships commented. | → `qa_audit_logger_agent` (record) |
| `batch_release_agent` | Batch Disposition & Release (the FINISHER) | The actuator behind the QA-manager gate. Re-checks the release (allow/deny tokens, unknown fails closed), re-derives the safety invariant in code, holds a safety-critical batch for the second signature, renders the certificate to `./out`, deletes on a tripped floor, records the ledger, and bounds the rework loop. `programmatic` + `@chain` release floor. | → `qa_manager_human` (route for decision, or the missing signature); → `quality_director_human` (loop exhausted, or the second signature); → `qa_audit_logger_agent` (terminus) |
| `qa_manager_human` | QA Manager (batch-release authority) | Releases / quarantines / reworks / halts a batch; on a safety-critical batch the release is the first of two signatures. Answers within 4h. | → `batch_release_agent` (release); → `quality_engineer_agent` (rework); → `qa_audit_logger_agent` (held_for_review / quarantine / halt) |
| `qa_inspector_human` | QA Inspector (first-line verification) | First-line check of a real but low-severity defect; escalate or clear. Answers within 2h. | → `batch_release_agent` (escalate); → `qa_audit_logger_agent` (pass / clear / held_for_review) |
| `plant_engineer_human` | Plant Engineer | Process-side root-cause + corrective directive on the floor. Answers within a shift (6h). | → `qa_audit_logger_agent` (every answer) |
| `quality_director_human` | Quality Director (accountable owner) | Second signature on a safety-critical release; Class I/II recall decisions; exhausted rework loops; the Manager's escalation target. Answers within 4h (same day). | → `batch_release_agent` (release); → `qa_audit_logger_agent` (quarantine / acknowledged / held_for_review) |
| `qa_audit_logger_agent` | Quality Records Registrar | Hash-chained WORM audit of every batch decision, CAPA, and recall. `external` / `framework: custom`, registered in `main.py`. | terminal (audit terminus) |

## The finisher (`batch_release_agent`)

The single most load-bearing pattern — the artifact-producing executor wired
*after* the human gate (`agency/batch_release_agent.py`):

1. **Re-checks the release** — reaching the finisher never means released. An
   allow-token allowlist (`release`, `approve`, `ship`…) + block-token denylist
   (`quarantine`, `rework`, `halt`, `held_for_review`…); an unknown verdict
   **fails closed**.
2. **Deterministic invariants a human can't waive** — a batch on record, and the
   fail-closed actuator (`_shared/batch_release.py`), idempotent per
   batch+decision.
2b. **Two signatures on a safety-critical batch** — the QA Manager's `release`
   is recorded as the first signature and the batch waits
   (`status: awaiting_second_signature`) for the Quality Director's. It ships
   only when both seats have released it, by two identified, different
   people: one person signing twice blocks it, and so does a signer the Inbox
   did not record (name each seat's people with `operator_ids` on
   `qa_manager_human` and `quality_director_human`, their Studio logins). A
   quarantine, rework, hold or rejection voids earlier signatures.
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
- **Two signatures on a safety-critical release** — `signature_status()` in
  `agency/batch_release_agent.py` (QA Manager + Quality Director, two people).

### If nobody answers

Each human request waits for the time in the Agents table. If nobody answers
within it, the request waits no longer: its Inbox card closes and the ticket
records that no reply came. Nothing is released on silence, and nobody is
chased automatically. (Each seat's `fallback_response` — `held_for_review` →
audit — states the intended default-deny; leafmesh 2.4.486 does not route it
for these seats yet.) The Manager escalates to the Quality Director only on
a non-retryable error, when 5 different agents fail in one case, or when
restarts run out; a malformed hand-off only raises an alert.

## Modern capabilities

- **scaling.copies** — `batch_inspection_sweeper_agent` runs 3 parallel copies per
  activation (`scaling.copies.count: 3`); the atomic `claim_inspection` tool
  (`agency/tools.py`) splits the backlog so copies never duplicate work (merge is
  lists-concat / counts-sum). 2.4.484's `copies.mode: auto` with `claim_tool:` could
  replace this manual split — an owner decision, not done here.
- **timezone** — every live `wake_up` (SPC sweep 06:00, intake sweep 08:00,
  batch sweep 07:00) declares `timezone: Asia/Kolkata` (set it to your plant's
  timezone) so crons don't silently fire in UTC.
- **super_agent** — dict form on `inspection_intake_agent`
  (`{cost_ceiling, wall_clock}`).
- **playbook** — per-intent craft on intake / engineer / recall: only the
  `description` rides in the prompt; the `responsibility` loads on demand.
- **mcp** — declared but **commented**: each row picks a vault connection
  (`lims_mcp`, `qms_mcp`) that does not exist on day 0; dev-store tools carry
  day-0. Create the connection, then uncomment — in `configs/config.yaml` before the
  first start, in Studio's YAML editor after it (see *Changing the configuration after the first start*).

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
the finisher routes the batch to the QA manager (answer it in Studio → Inbox). A
`release` there is only the first signature on this safety-critical batch: the
finisher then asks the Quality Director, and the certificate is issued only
after both have signed. The example scenarios in `examples/`
exercise the routine-inspection, defect-cluster, field-complaint, and
regulatory-audit paths; `tests/` asserts the topology and floors.

## Changing the configuration after the first start

`configs/config.yaml` is only the first-start seed. On the first start (an empty store) the agency imports it as version 1 and from then on keeps its configuration in the store. After that the file is not read again: an edit to it is **not** applied, even after a restart (the boot log says "that edit was NOT applied").

To change a running agency:

- **Studio** — its settings pages, or the raw YAML editor.
- **API** — `PUT /api/yaml/raw` with an `X-API-Key` header and the body `{"yaml_content": "<the whole YAML>", "save_to_disk": true}`, or `PATCH /api/yaml/config` with `{"updates": {...}, "save_to_disk": true}`.

Each save is a new version and applies live on every pod — no restart. (A few settings, such as brokers, make the agency reload itself in place within seconds.) So wherever this README or a comment says "uncomment X in `configs/config.yaml`", do it in the file before the first start, or in Studio's YAML editor after it.

## Changing a running project (Docker)

What reaches a project that is already running (leafmesh 2.4.497). The
image holds the Python code and the first-start `configs/config.yaml`;
`.env` is passed in when the container is created; the store (Redis) keeps
its data on its own volume.

| You change | When it applies |
|---|---|
| An agent in Studio (prompt, model, tools, routes, human seats, schedules, …) | At once, no restart |
| A human seat's `channels` (Slack, email, …) | The agency reloads itself in place within seconds |
| Add or delete an agent in Studio | At once, including routes and schedules |
| A connection in the vault (model keys, MCP, webhooks, connectors, Slack, email) | At once. An agent switched off for a missing connection or address switches back on by itself |
| Anything in `.env` (licence, agency name, store, ports) | After `docker compose up -d` (the container is recreated) |
| Python code (`agency/*.py`: programmatic agents, tools, `@pre_compose`) | After a rebuild: `docker compose up -d --build` |
| `configs/config.yaml` | Never after the first start. It is read only when the store is empty |
| Several app containers on one store | A Studio save reaches every one within about 15 seconds |

- Keep model keys in the vault. A key there wins over the same key in
  `.env` (the log warns), and a change applies at once. From 2.4.497 that
  holds for every value the vault holds (endpoints, organisation / project
  ids, cloud identities); the `.env` value returns when the record goes.
- Code the image does not have: an agent marked pure Python
  (`integration: python`) is switched off until its function is in the image;
  any other programmatic agent stays on, and each call to code it does not
  have fails. Rebuild the image with the code first.
- A new Redis volume is an empty store, so `configs/config.yaml` is imported
  again as version 1.
- **If the agency refuses to start after an upgrade** (a new leafmesh pin,
  then a rebuild: "Configuration has N error(s) in the stored
  configuration"), repair the stored configuration with the agency stopped:

  ```bash
  docker compose run --rm quality-yield leafmesh config check      # each problem, with its line
  docker compose run --rm quality-yield leafmesh config versions   # kept versions: which pass, which is in use
  docker compose run --rm quality-yield leafmesh config rollback   # back to the newest version that passes (saved as a new version)
  ```

  Or `leafmesh config export > config.yaml`, fix it by hand, then
  `leafmesh config import config.yaml` (it is checked before it is saved).

## Where the data store is set

From leafmesh 2.4.482 the store (Redis or Postgres) is set **only in `.env`** — never in `configs/config.yaml`. A `persistence:` block in the YAML is ignored with a warning on leafmesh 2.4.482–2.4.485; from 2.4.486 the agency refuses to start if that block differs from `.env`, and names each `.env` line to add. The project's `.env` sets the store explicitly (`LEAFMESH_STORE`, `LEAFMESH_AGENCY_NAME`, `REDIS_*`).

- Redis (default): `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, `REDIS_DB`; optional `REDIS_DEFAULT_TTL`, `REDIS_SESSION_TTL`, `REDIS_SSL*`, `REDIS_CLUSTER_NODES`.
- Postgres instead: `LEAFMESH_STORE=postgres` plus `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` (optional `POSTGRES_SSLMODE`).
- Or the whole server in one value: `REDIS_URL=redis://user:pw@host:6379/0` (`rediss://` turns TLS on) or `DATABASE_URL=postgresql://user:pw@host:5432/db`. The single names fill in what the URL leaves out.
- With Docker Compose the app reaches Redis as `redis` (the service name); compose sets that for you.
- Check it in Studio → **Infrastructure → Datastores**: it shows whether the store is reachable (never the address or password).

## This mesh's sign-in (leafmesh 2.4.497)

From leafmesh 2.4.497 each mesh signs in to LeafCraft with its own key, not
a key shared by every install.

- **First start.** The mesh makes a key pair and registers it with
  `LEAFMESH_LICENSE_KEY` and `LEAFMESH_ENV_TOKEN` from `.env` (copy the
  environment token from Studio — a placeholder such as `development` is
  refused). With both valid it works at once. If your organisation turned on
  "require approval", the first start stops and says so: approve it in
  Studio → **Keys → SDK access**, then start again. Organisation admins get an
  email for every new registration.
- **Where the key lives.** In the vault, in the store, encrypted with
  `LEAFMESH_VAULT_KEY`: it survives rebuilds and every copy of the app shares
  it. A new Redis volume, or a new vault key, makes a new key pair that
  registers again — revoke the old one in Studio.
- **Check or change it** (in Docker: `docker compose run --rm quality-yield leafmesh auth status`):
  - `leafmesh auth status` — credential id, key fingerprint, registration and
    mode, never a key; `--check` signs in now and reports the tokens.
  - `leafmesh auth register` — register now.
  - `leafmesh auth rotate` — a new key pair: once it is active the mesh
    switches to it and revokes the old one. A revoked or expired key is never
    revived; rotate instead.
- **Lost the environment token?** From 5 Dec 2026 Studio shows the licence
  key and environment token only once, when they are made (then only a hint).
  Rotate the environment token in Studio and put the new one in `.env`.
  Rotating it changes where the agency's data is kept in the store, so do it
  before the agency holds data.
- **An existing project whose `.env` says `LEAFMESH_ENV_TOKEN=development`.**
  2.4.497 needs the real token. The token also names where the agency's data
  sits in the store, so with the real token the agency starts on an empty
  store (vault, sessions, board) — the old data stays under the old name. Set
  the real token before you put data in the agency, or contact us to move it.

## Your vault key — back it up

`create-leafmesh` put a `LEAFMESH_VAULT_KEY` in your `.env` when it made this project. It is the master key that encrypts every connection and credential you store in the vault (in Redis / Postgres).

- **Back it up like a master password.** Lose it and every stored connection has to be entered again.
- **Never change or regenerate it** for an existing project — a new key locks the existing vault. To change it, use `leafmesh vault rotate-key`.
- Every copy of the app (every replica) must get the **same** value. Docker Compose passes it in from `.env` as an environment variable.
- Never commit `.env` (it is already in `.gitignore`).
