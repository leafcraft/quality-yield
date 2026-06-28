"""quality-yield — agency package (Rule 1).

YAML-FIRST: the department is configs/config.yaml. Python lives only
where a role needs hands — a deterministic floor the LLM can't waive, a
self-reliant context pull, domain math, or a fail-closed actuator:

  inspection_intake_agent.py   @pre_compose batch context + @chain
                               quarantine gate / safety-critical hard-stop
                               + @compose per-downstream payloads
  quality_engineer_agent.py    @pre_compose SPC/lots/open-CAPAs + @chain
                               CAPA-required floor (register dedupe)
  spc_monitor_agent.py         @pre_compose measurement window + @chain
                               SPC out-of-control floor
  recall_traceability_agent.py @pre_compose genealogy/related-batches +
                               @chain regulator routing + freeze actuator

  _shared/store.py             seeded dev store (MES/LIMS/QMS/historian)
  _shared/spc_math.py          X-bar control chart + Western Electric rules
  _shared/defect_scoring.py    severity scoring + quarantine/hard-stop floor
  _shared/batch_release.py     fail-closed + idempotent disposition actuator
  _shared/session_stash.py     HITL-gap bridge + bounded rework loop
  _shared/audit_logger.py      hash-chained WORM audit sink (main.py registers)

regulatory_compliance_agent is pure-YAML (judgement only). Human agents
(qa_inspector_human, qa_manager_human, plant_engineer_human) have NO
Python — the SDK's human_interface handles them.
"""
