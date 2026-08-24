# quality-yield — example scenarios

Four realistic scenarios that exercise the major paths through this mesh.

## How to run

```bash
python examples/run_examples.py                 # all scenarios
python examples/run_examples.py 01_             # one scenario (substring match)
```

## Scenario map

- **01_routine_inspection** — `report_signal` — QA pulls a sample batch — the Coordinator opens the case, inspection_intake classifies no defects, the batch is released.
- **02_defect_cluster** — `report_signal` — Inspection finds 4 defective units in one sample — the Coordinator opens the case, inspection_intake classifies the cluster, quality_engineer_agent runs 5-whys and drafts the CAPA.
- **03_field_complaint_recall** — `field_complaint` — Customer-reported defect — recall_traceability_agent finds the production lot, scopes the recall.
- **04_regulatory_audit_pull** — `regulatory_batch_audit` — Auditor requests batch chain-of-custody — assemble traceability dossier.

## Audit trail

Every scenario appends one hash-chained entry to the template's
`logs/<audit>.jsonl` file. Tail it during a run to see the
ledger build up across scenarios.
