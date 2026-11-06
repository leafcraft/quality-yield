# quality-yield — example scenarios

Four realistic scenarios that exercise the major paths through this mesh.

## How to run

```bash
create-leafmesh --load .   # once: loads the team into the Redis in .env (or use create-leafmesh --start .)
python examples/run_examples.py                 # all scenarios
python examples/run_examples.py 01_             # one scenario (substring match)
```

## Scenario map

Every scenario runs on the seeded dev store (`agency/_shared/store.py`) — the
batches, events and tickets below are seeded records.

- **01_routine_inspection** — `report_signal` — light flow lines on consumer batch `B-3300`, inside the finish tolerance — intake scores it cosmetic / low severity and the **QA Inspector** does the first-line check (pass / clear / escalate).
- **02_defect_cluster** — `report_signal` — bore diameter 10.071 mm on safety-critical batch `B-4471` (spec 10.00 ± 0.05) — intake's hard-stop forces quarantine, the engineer traces it to the out-of-spec titanium lot and drafts a CAPA, the finisher routes the batch to the **QA Manager**. A release there is the first signature; the **Quality Director** signs second.
- **03_field_complaint_recall** — `field_complaint` — ticket `CS-88120`, surface peeling on shipped batch `B-2210` (13,950 units in the field) — recall_traceability_agent scopes the recall and stamps the regulator routing.
- **04_regulatory_audit_pull** — `regulatory_batch_audit` — an FDA inspector asks for the chain of custody of `B-4470` / `B-4471` (both built from titanium lot `LOT-TI-9920`) — assemble the traceability dossier.

## Human gates

The gates answer in **Studio → Inbox**. If nobody answers in time, the card
closes and the ticket records that no reply came — nothing is released on
silence.

## Audit trail

Every scenario appends one hash-chained entry to the template's
`logs/<audit>.jsonl` file. Tail it during a run to see the
ledger build up across scenarios.
