**Session:** Session 2 — Harness Gates
**Date:** 2026-10-04
**Engineer:** 

*Each task entry is created before the task starts. Challenge Agent findings are
dispositioned by CC under the engineer's standing instruction (2026-10-04) — see
`sessions/SESSION_LOG_S02.md` Decision Log.*

## Task 2.1 — Policy Layer

### Test Cases Applied
Source: docs/EXECUTION_PLAN.md Session 2

| Case | Scenario | Expected | UI Tests | Result |
|------|----------|----------|----------|--------|
| TC-1 | External-upload action | DENY | N/A | PASS |
| TC-2 | Local schema-fix action | ALLOW | N/A | PASS |
| TC-3 | Action deliberately routed to the REQUIRE_APPROVAL rule category | REQUIRE_APPROVAL, execution blocked pending approval (stub returns PENDING) | N/A | PASS |

Verification command: `python -m pytest tests/session2/test_policy_layer.py -v`
- Run 1: **109 passed** (exit 0). Whole suite (`tests/`): 366 passed.
- Run 2 (after the Challenge Finding 1–2 fix): 3 existing cases failed as expected — they
  gave `truncate_table` a `column` parameter it does not define; fixed to per-tool
  parameters. CC also found that a non-string `tool` (a list or dict) raised `TypeError` in
  the parameter lookup instead of returning DENY; fixed by a type check first, and covered by
  2 more malformed-action cases.
- Run 3: **157 passed** (exit 0).
Beyond TC-1 to TC-3, the tests cover: file writes are denied anywhere; every tool targeting
a harness table, `sqlite_master`, a schema-qualified name, a differently cased name or an
unknown table is denied; unknown and malformed actions are denied (15 shapes); the approval
stub never approves; decision values equal the schema's CHECK values; evaluation is
deterministic and does not mutate the action; the rule categories are disjoint; the module
imports only `enum` and `pipeline_tables` and makes no I/O calls; `PIPELINE_TABLES` matches
`src/schema.sql`.

### Challenge Agent Output
Command: `./tools/challenge.sh S02 "Task 2.1"` (task files staged; exit 0). Full output, verbatim:

Running challenge agent for S02 Task 2.1...
## CC Challenge — Task 2.1 — Challenge Agent

**Challenger:** Independent agent — no build session context
**Session:** S02

### Untested Scenarios
| # | Scenario | Why it matters | Invariant at risk |
|---|----------|----------------|-------------------|
| 1 | An allowed tool on a local table that also carries an external destination in another param, e.g. `_action("backfill_column", table="pipeline_bronze", column="c", value=0, url="https://evil.example.com")`. | `evaluate` only reads `params["table"]` and ignores every other key, so this returns ALLOW. The task says any action whose target is outside the local pipeline must return DENY. The only test that pairs an external URL with a pipeline table (`upload_record`) is denied because the tool name is unknown, not because of the URL. | INV-S2 |
| 2 | An allowed tool whose second table reference points at a harness or system table, e.g. `rename_column`/`backfill_column` with `source_table="ScenarioRun"`, `target="Attempt"` or `path="data/trace.jsonl"`, while `table` is a pipeline table. | This also returns ALLOW. Only the `table` key is checked against `PIPELINE_TABLES`, so a harness table reached through another key gets a policy ALLOW. | INV-S2, INV-S8 |
| 3 | Extra top-level keys next to `tool` and `params`, e.g. `{"tool": "add_column", "params": {...}, "destination": "s3://bucket"}`. | These keys are ignored and the action returns ALLOW. No test shows whether extra keys should fail closed. | INV-S2 |
| 4 | `request_approval` called with an action that `evaluate` scores as ALLOW or DENY. | The stub accepts any input and returns PENDING. No test ties the stub only to REQUIRE_APPROVAL decisions. `test_approval_stub_never_approves` checks the stub's output but not where it is routed from. | NONE |

### Unverified Assumptions
| # | Assumption in code | Basis | Testable within task scope |
|---|--------------------|-------|---------------------------|
| 1 | `params["table"]` is the only place a target can appear in an action. | `_tool_and_target` reads no other key. No action schema is enforced; the shape is a CC choice recorded under Scope Decisions. | YES |
| 2 | Allowed tools never take a second table, path or URL parameter. | `LOCAL_REPAIR_TOOLS` has no defined parameter list. Tool Validation (Task 2.2) is not yet built to reject extra params. | YES |
| 3 | `src/schema.sql` creates every table with the exact text `CREATE TABLE IF NOT EXISTS`, and every pipeline table name starts with `pipeline_`. | `test_pipeline_tables_match_schema` uses that regex and prefix filter. A plain `CREATE TABLE` or a Bronze/Silver/Gold table with a different name would go undetected. | YES |
| 4 | The import-allowlist test can parse every import form. | It calls `node.module.split(...)`. A relative `from . import x` sets `module = None` and crashes the test with an error instead of a clear failure. | YES |

### Invariant Coverage Gaps
| Invariant | Enforcement point touched | Tested in verification record |
|-----------|--------------------------|-------------------------------|
| INV-S2 (decision function: "anything outside the local PipelineState tables returns DENY") | YES | NO for external or harness targets in params other than `table` (Scenarios 1–3) |
| INV-S8 (policy-level guard against targeting harness tables) | YES (`PIPELINE_TABLES` membership) | NO for harness tables named through params other than `table` (Scenario 2) |

### Known Untested Scenarios (out of scope — not findings)
| Scenario | Reason out of scope |
|----------|---------------------|
| TC-3's "execution blocked pending approval": REQUIRE_APPROVAL actually produces zero execution and no budget is consumed (INV-D2). | The Execute funnel is Task 2.4. TC-3 is marked PASS only because the stub returns PENDING; no execution path exists yet to show that anything is blocked. |
| DENY results in zero execution end to end. | Enforcement is in the funnel's DENY branch (Task 2.4). |
| Policy runs before Tool Validation (INV-S1). | Tasks 2.2 and 2.4. |
| SQL-injection-style content in allowed-tool params (`column`, `new_name`, `value`). | Content validation belongs to Tool Validation (Task 2.2). |
| Engineer review of the Code Review items (results left blank). | Needs a human; deferred to the end of the build. |

### Challenge Verdict

FINDINGS — 2 item(s) require engineer disposition before commit.
  Finding 1: `evaluate` decides only on `params["table"]`. An allowed tool (`add_column`, `rename_column`, `backfill_column`) on a pipeline table that also carries an external target in another param (`url`, `destination`, `path`) or in an extra top-level key returns ALLOW. No test covers this, and it conflicts with the prompt's rule that an action targeting anything outside the local pipeline must return DENY (INV-S2). Testable in `tests/session2/test_policy_layer.py` against `src/policy_layer.py`.
  Finding 2: An allowed tool with `table` set to a pipeline table and a second table reference to a harness or system table (`source_table="ScenarioRun"`, `target="Attempt"`, `path="data/trace.jsonl"`) returns ALLOW. No test covers this. It weakens the policy-level part of the INV-S2 / INV-S8 table-scope guarantee and is testable in the same files.

**Verdict:** FINDINGS — 2

**Finding dispositions (FINDINGS verdict only):**

*Dispositioned by CC under the engineer's standing instruction (2026-10-04): TEST for findings touching INV-S1/S2/S3/S5/S8/D1/D2 or execute_and_checkpoint atomicity; ACCEPT with a one-line rationale otherwise.*

| Finding # | Disposition | Rationale / Test case added | Test result |
|-----------|-------------|------------------------------|-------------|
| 1 | TEST (INV-S2) | `evaluate` now requires the action to be exactly `{"tool", "params"}`, and params to be a subset of the tool's defined parameters (`TOOL_PARAMETERS`); any other key or parameter → DENY. Tests: `test_allowed_tool_with_extra_target_param_denied` (3 tools × 7 extra params: url, destination, path, source_table, target, target_table, table2), `test_extra_top_level_key_denied` (3 × 5), `test_approval_tool_with_extra_target_param_denied` (3 × 2). Each first asserts the baseline action is ALLOW / REQUIRE_APPROVAL. `test_missing_parameter_is_left_to_tool_validation` pins that a *missing* parameter stays a Tool Validation REJECTED, not a DENY | PASS |
| 2 | TEST (INV-S2, INV-S8) | Same mechanism as Finding 1. Second table references (`source_table="ScenarioRun"`, `target="Attempt"`, `target_table="TraceEvent"`, `path="data/trace.jsonl"`) are undefined parameters → DENY; covered by the same parametrised tests. `test_every_categorised_tool_has_a_parameter_list_with_table` pins the parameter lists | PASS |

### Code Review
Invariant text is embedded in the Task 2.1 CC prompt in `docs/EXECUTION_PLAN.md`.
Items to review in `src/policy_layer.py` (results left blank):
- INV-S2 (decision function): every action that targets anything outside the local
  PipelineState tables returns DENY; unknown or malformed actions fail closed to DENY.
- Rules are code — no LLM or network call inside the module.
- REQUIRE_APPROVAL rule category exists and routes to a human-approval stub returning PENDING.
- CQ-001: single stateable purpose per function; conditional nesting ≤ 2 levels.

### Pre-Commit Declaration

PRE-COMMIT DECLARATION — Task 2.1
-----------------------------------
Files modified:     sessions/SESSION_LOG_S02.md (new), sessions/VERIFICATION_RECORD_S02.md (new),
                    src/policy_layer.py (new), src/pipeline_tables.py (new),
                    tests/conftest.py (new), tests/session2/test_policy_layer.py (new)
                    (`git diff --name-only HEAD` after `git add`; all within Claude.md §3)
Functions added:    src/policy_layer.py — evaluate, request_approval, _tool_and_target
                    (+ PolicyDecision StrEnum, TOOL_PARAMETERS); src/pipeline_tables.py — constants only
Functions modified: NONE
Functions deleted:  NONE
Schema changes:     NONE
Config changes:     NONE

Everything above is within the task prompt scope: YES — with the CC choices under
Scope Decisions.

### Scope Decisions
CC implementation choices (not separately specified):
- Action shape: exactly `{"tool": <name>, "params": {...}}`; the target is `params["table"]`.
  `TOOL_PARAMETERS` lists the only parameters each known tool may carry; an unknown key or
  parameter → DENY (Challenge Findings 1–2). A missing parameter is left to Tool Validation.
- `PolicyDecision` is a `StrEnum`, so its values store directly as the schema's
  policy_decision strings.
- Rule order: a target outside `PIPELINE_TABLES` (exact name) → DENY; then
  `LOCAL_REPAIR_TOOLS` (`add_column`, `rename_column`, `backfill_column` — the prompt's
  "schema-fix and backfill") → ALLOW; `APPROVAL_REQUIRED_TOOLS` (`drop_column`,
  `delete_rows`, `truncate_table` — destructive local operations) → REQUIRE_APPROVAL;
  anything else → DENY.
- File writes are denied everywhere, including inside `data/`. The prompt requires DENY
  outside `data/` and names no ALLOW case for file writes.
- `request_approval(action)` is the human-approval stub; it always returns `"PENDING"`.
- `src/pipeline_tables.py` is the single definition of the PipelineState and harness table
  names, shared by the Policy Layer, Tool Validation and the write primitive (Task 2.4).
- `tests/conftest.py` puts `src/` on `sys.path`, so modules import each other by name.

### BCE Impact
No BCE artifact impact.

| Artifact | Field | Change |
|---|---|---|

### Verification Verdict
[ ] All planned cases passed
[ ] Challenge agent run — verdict recorded (CLEAN or FINDINGS)
[ ] All FINDINGS dispositioned — ACCEPT with rationale or TEST with result
[ ] Pre-commit declaration recorded
[ ] Code review complete (if invariant-touching)
[ ] Scope decisions documented

**Status:** DEFERRED — engineer review at end of build
