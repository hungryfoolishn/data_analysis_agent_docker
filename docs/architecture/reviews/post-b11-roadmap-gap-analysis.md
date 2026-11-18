# Post-B11 Roadmap Gap Analysis

**Date:** 2026-11-18
**Authority:** `docs/newMain/toDo.md` (re-read in full; no reliance on memory)

## 1. Roadmap Completion Status

| Phase | Status |
|---|---|
| B0–B3 | Complete (historical) |
| B4/B5 | FROZEN |
| B6 Golden | FROZEN; two P2 data-quality issues found by B11.6 and remediated post-B11 |
| B7 Adapter / B8 Acquisition | FROZEN |
| B9 Macro Pack | FROZEN |
| B10 Financial Pack | FROZEN (OCR deferred to B10.12) |
| B11 Multi-Agent | PASS / FROZEN (`7c95912`) |

## 2. Next-Phase Search Results

Full-text scan of `docs/newMain/toDo.md`:

- No `B12` definition exists.
- No post-B11 stage numbering, goals, dependencies, boundaries, or
  acceptance criteria exist.
- No pending "下一阶段" directive beyond generic batch rules.

No other authoritative roadmap document defines a phase after B11.

## 3. Decision

```text
Next Phase: NOT DEFINED IN AUTHORITATIVE ROADMAP

No new phase was created.
No unapproved feature implementation was performed.
```

## 4. Known Technical Debt (for user evaluation; not authorized work)

| Item | Origin | Suggested value / risk |
|---|---|---|
| OCR / scanned-PDF financial parsing | B10.12 (explicitly deferred) | Completes B10 breadth; medium effort |
| Bank-specific revenue indicator | Post-freeze backlog (non-breaking) | Domain breadth; low risk |
| B6 golden breadth expansion (beyond current 50 sales cases; macro/financial golden cases exist separately under B9/B10) | B6 scope | Better coverage of planner semantics; low risk |
| B11.6 ablation study (no-reviewer / no-auditor variants) | B11.6 §9.7 (budget-permitting) | Identifies which component provides quality value; low risk |
| LLM-in-the-loop benchmark (current benchmark is deterministic) | B11.6 limitation | Measures real production path quality; requires model budget |
| Known `test_dashboard.py` ×2 baseline failures | Historical | Flaky/environment-dependent; should be triaged |
| B11.6 report count erratum (12 vs 6 change_pct metrics) | Documented in root-cause analysis | Cosmetic; frozen report intentionally untouched |

## 5. Required Product Decisions

1. Whether to open a new phase (candidate: B12 document intelligence/OCR per
   B10.12 deferral, or B6/B11 depth items above).
2. Whether the two historical dashboard failures should be fixed or formally
   quarantined.
3. Whether benchmark quality gates should be extended with LLM-in-the-loop
   evaluation (cost trade-off).
