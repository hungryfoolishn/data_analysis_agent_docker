# R2 Capability Inventory Contract Tests

`tests/test_capability_inventory.py` locks the real registry against the documented inventory:

1. The exact registered tool names per toolset match the inventory.
2. There are no duplicate capability/tool names.
3. The Financial Domain Pack contract is code-anchored to:
   - `pack_id=financial`;
   - `pack_version=0.2.0`;
   - capabilities `financial_models`, `financial_normalization`;
   - Macro import boundary;
   - unknown normalization policy `reject (never guess)`.

This prevents silent capability drift during R2.5 and R3.

## Related validation

The R2 docs were cross-checked against:

- `langgraph_langchain/tools/registry.py`
- all `langgraph_langchain/tools/tool_*.py` registrations
- `runtime/analysis_operations.py`
- `runtime/analysis_workflow.py`
- `runtime/domain_packs.py`
- `runtime/financial/pack.py`
- `runtime/macro/*`
- `runtime/multi_agent.py`
- Runtime history/evidence/report modules

## Gate

| Gate | Result |
| --- | --- |
| Scope compliance | PASS — inventory only, no frozen behavior change |
| Code anchors | PASS — every class has entry path |
| Contract lock | PASS — registry test |
| Migration candidates | PASS — R2.5 list established |
| Regression | PASS — inventory tests passed; no production code changed |