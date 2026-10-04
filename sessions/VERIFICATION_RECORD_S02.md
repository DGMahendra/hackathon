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

---

## Task 2.2 — Tool Validation

### Test Cases Applied
Source: docs/EXECUTION_PLAN.md

| Case | Scenario | Expected | UI Tests | Result |
|------|----------|----------|----------|--------|
| TC-1 | Well-formed tool call | VALID | N/A | PASS |
| TC-2 | Tool call with a missing required parameter | REJECTED with reason | N/A | PASS |
| TC-3 | Tool call to an unregistered tool name | REJECTED with reason | N/A | PASS |

Verification command: `python -m pytest tests/session2/test_tool_validation.py -v`
- Run 1: **131 passed** (exit 0).
- Run 2 (after the Challenge Finding 1–2 fixes): **160 passed** (exit 0).
Beyond TC-1 to TC-3, the tests cover: SQL-looking text is a VALID backfill *value* (it is data,
bound by the primitive); malformed structure and non-dict params are rejected; unknown
parameters (`sql`, `url`, `source_table`, `callback`) are rejected; tables outside the
pipeline are rejected (harness, `sqlite_master`, schema-qualified, wrong case, injected);
unsafe identifiers are rejected (quotes, `;`, spaces, a leading digit, 64 characters, the
`sqlite_` prefix, newline, non-ASCII); bad column types and bad values are rejected (bool,
out-of-range int, NaN, inf, text over 1024 characters, list, dict, bytes); renaming to the
same name is rejected; all problems are reported together; the call is not mutated; the
module neither imports nor calls `policy_layer`; no tool has a SQL or callable parameter.

### Challenge Agent Output
Command: `./tools/challenge.sh S02 "Task 2.2"` (task files staged; exit 0). Full output, verbatim:

Running challenge agent for S02 Task 2.2...
## CC Challenge — Task 2.2 — Challenge Agent

**Challenger:** Independent agent — no build session context
**Session:** S02

*Note: I found these by reading the code. I could not run anything to confirm them, because the command needed approval in this non-interactive session. Finding 1 follows directly from how Python 3 `sorted()` behaves.*

### Untested Scenarios
| # | Scenario | Why it matters | Invariant at risk |
|---|----------|----------------|-------------------|
| 1 | `params` has unknown keys of mixed types, e.g. `{**WELL_FORMED["backfill_column"], "sql": "x", 1: "y"}`. `_parameter_problem` calls `sorted(set(params) - set(schema))`, which compares `int` with `str` and raises `TypeError` instead of returning REJECTED. | The agent controls the parsed tool-call JSON, so it can crash the validator. How safe this is depends on how Task 2.4 handles exceptions, so it is not a closed REJECTED path. Only string keys are tested. | INV-S1 (the validation stage does not return a result) |
| 2 | SQLite rowid aliases (`rowid`, `oid`, `_rowid_`) are accepted as `column` / `old_name` / `new_name`. They match `IDENTIFIER` and are not `sqlite_`-prefixed. `backfill_column(table, "rowid", 1)` passes and would target the hidden rowid of every row. | This is an agent-chosen identifier that reaches a write against implicit row identity, not a declared pipeline column. No test covers it. | INV-S8 (scope of pipeline writes, indirectly) |
| 3 | SQL reserved words as identifiers (`SELECT`, `FROM`, `TABLE`, `NULL`) are accepted. Nothing tests them, and nothing records that the Task 2.4 primitive must quote identifiers. | If the primitive puts identifiers in unquoted, a keyword column produces broken or reinterpreted DDL. "Safe identifier" is claimed but only checked for character class. | NONE |
| 4 | `rename_column` with names that differ only in case (`Amount` → `amount`) passes the "identical" check. SQLite identifiers are case-insensitive. | The "rename to same name is rejected" rule is only partly enforced. The test covers exact equality only. | NONE |
| 5 | An unregistered tool name of unbounded length or with injection text is echoed verbatim into `reason` via `{tool!r}`. Neither length nor content is tested. | `reason` flows into the trace and back to the agent, so agent-controlled text is amplified into the judge-inspected evidence and the model context. | INV-D4 (quality of trace evidence) |

### Unverified Assumptions
| # | Assumption in code | Basis | Testable within task scope |
|---|--------------------|-------|---------------------------|
| 1 | Every key in `params` is a string, so `sorted()` works on the missing and unknown sets. | `_parameter_problem` sorts set differences without coercing types. | YES |
| 2 | Any identifier that matches `[A-Za-z_][A-Za-z0-9_]{0,62}` and is not `sqlite_*` is a legitimate user column. | The `_identifier` check has no deny-list for `rowid`/`oid`/`_rowid_` or reserved words. | YES |
| 3 | `TOOL_SCHEMAS` is a mutable module-level `dict`, assumed never to change at runtime. Nothing freezes it or tests for it. | The allowlist is a plain dict, so any importer can add a tool. | YES |
| 4 | `ValidationResult` keeps status and reason consistent: VALID ⇒ reason None, REJECTED ⇒ reason non-null. Nothing enforces this; `ValidationResult(VALID, "x")` or `ValidationResult("OK")` can be constructed. | Frozen dataclass with no `__post_init__` check. This matters for INV-D3 (failure_reason non-null iff failed) once it is consumed downstream. | YES |
| 5 | `int`/`float`/`str` subclasses (e.g. `IntEnum`, str subclasses with overridden `__str__`) are safe to bind. | `isinstance` checks accept subclasses. | YES |
| 6 | The backfill value's type is compatible with the target column's declared type. | Not checked: the validator has no DB state. | NO |

### Invariant Coverage Gaps
| Invariant | Enforcement point touched | Tested in verification record |
|-----------|--------------------------|-------------------------------|
| INV-S1 | YES — the validation stage must always return a result for the funnel to record on the Attempt | NO — no test shows `validate()` never raises on arbitrary JSON-shaped input (see Untested #1) |
| INV-D3 | YES — `ValidationResult.reason` is the source of `failure_reason` for validation failures | NO — status/reason consistency isn't enforced or tested at the type level |

### Known Untested Scenarios (out of scope — not findings)
| Scenario | Reason out of scope |
|----------|---------------------|
| Each `TOOL_SCHEMAS` name maps 1:1 to a harness-owned implementation | Requires the Task 2.4 pipeline-write primitive (set-equality test deferred to 2.4) |
| Funnel runs Policy before Validation; REQUIRE_APPROVAL/DENY never reach `validate` | Requires the Task 2.4 funnel |
| Backfill values are bound as parameters, never interpolated | Requires the Task 2.4 primitive |
| Identifiers are quoted when interpolated into DDL/DML | Requires the Task 2.4 primitive |
| Value type matches the target column's affinity; target column/table exists | Requires DB state at execution time |
| End-to-end PROMPT_INJECTION: injected text never becomes executable SQL | Requires Session 3+ scenario/agent integration |

### Challenge Verdict

FINDINGS — 3 item(s) require engineer disposition before commit.
  **Finding 1:** `validate()` raises `TypeError` instead of returning REJECTED when `params` has unknown keys of mixed types (e.g. `{"table":…, "column":…, "value":…, "sql":"x", 1:"y"}`), because `sorted()` compares `int` with `str` in `_parameter_problem` (`src/tool_validation.py`). Add a test that `validate()` returns REJECTED for non-string or mixed-type param keys. This also covers the INV-S1 gap: the validator must always produce a recordable result.
  **Finding 2:** The rowid aliases `rowid`, `oid` and `_rowid_` pass `_identifier` and are accepted as `column` in `backfill_column`, `add_column` and `rename_column`. Either reject them, with a test, or record an ACCEPT rationale explaining why writes to a table's implicit rowid are safe.
  **Finding 3:** `ValidationResult` does not enforce status/reason consistency (VALID with a reason, REJECTED without one, or an arbitrary status string are all constructible). Its `reason` feeds INV-D3's `failure_reason`. Either add a `__post_init__` guard with tests, or ACCEPT with rationale.

**Verdict:** FINDINGS — 3

**Finding dispositions (FINDINGS verdict only):**

*Dispositioned by CC under the engineer's standing instruction (2026-10-04): TEST for findings touching INV-S1/S2/S3/S5/S8/D1/D2 or execute_and_checkpoint atomicity; ACCEPT with a one-line rationale otherwise.*

| Finding # | Disposition | Rationale / Test case added | Test result |
|-----------|-------------|------------------------------|-------------|
| 1 | TEST (INV-S1) | `_structure_problem` now rejects non-string parameter names ("parameter names must be strings") before any sorting, so `validate()` always returns a result the funnel can record. Tests: `test_non_string_parameter_names_rejected_not_raised` (3 tools × 5 key types: int, None, tuple, float plus str, bytes), `test_validate_never_raises_on_json_shaped_input` (4) | PASS |
| 2 | TEST (INV-S8) | `_identifier` rejects the SQLite rowid aliases `rowid`, `oid` and `_rowid_` in any case (`ROWID_ALIASES`). Tests: `test_rowid_alias_rejected_as_identifier` (6 spellings × 4 parameter positions), `test_names_merely_containing_rowid_are_allowed` (4) | PASS |
| 3 | ACCEPT | INV-D3 is not on the TEST list. `ValidationResult` is only built inside `validate()`, which always pairs VALID with None and REJECTED with a reason; the funnel (Task 2.4) writes `failure_reason` from it, and the schema's INV-D3 CHECK rejects any inconsistent Attempt row at write time | N/A |

### Code Review
Task 2.2 enforces no invariant directly (INV-S1 is enforced structurally by Task 2.4's
funnel, which calls this module). Items to review in `src/tool_validation.py` (results left blank):
- Tool name checked against an allowlist; parameters checked against a per-tool schema
  (required, no extras, types, value domains).
- No parameter can carry raw SQL that is executed verbatim: identifiers must match a
  strict pattern; values are data only (bound by the Task 2.4 primitive).
- The module does not call `policy_layer` — the funnel enforces ordering.
- CQ-001: single stateable purpose per function; conditional nesting ≤ 2 levels.

### Pre-Commit Declaration

PRE-COMMIT DECLARATION — Task 2.2
-----------------------------------
Files modified:     sessions/SESSION_LOG_S02.md, sessions/VERIFICATION_RECORD_S02.md,
                    src/tool_validation.py (new), tests/session2/test_tool_validation.py (new)
                    (`git diff --name-only HEAD` after `git add`; all within Claude.md §3)
Functions added:    src/tool_validation.py — validate, _structure_problem, _parameter_problem,
                    _table, _identifier, _column_type, _scalar_value (+ ValidationResult,
                    TOOL_SCHEMAS)
Functions modified: NONE
Functions deleted:  NONE
Schema changes:     NONE
Config changes:     NONE

Everything above is within the task prompt scope: YES — with the CC choices under
Scope Decisions.

### Scope Decisions
CC implementation choices (not separately specified):
- Allowlist = the Policy Layer's ALLOW tools: `add_column(table, column, column_type)`,
  `rename_column(table, old_name, new_name)`, `backfill_column(table, column, value)`. The
  REQUIRE_APPROVAL tools are not executable (the funnel stops before validation), so they are
  not allowlisted here.
- Every parameter is required; unknown parameters are rejected. Table ∈ `PIPELINE_TABLES`
  (exact). Identifiers match `[A-Za-z_][A-Za-z0-9_]{0,62}` and may not start with `sqlite_`.
  Identifiers may not be a rowid alias (`rowid`, `oid`, `_rowid_`, any case); parameter
  names must be strings. `column_type` ∈ TEXT / INTEGER / REAL / NUMERIC. A backfill value is None, an int within
  SQLite range (bool excluded), a finite float, or text of at most 1024 characters.
- `ValidationResult(status, reason)` is a frozen dataclass; reason is None when VALID.
- Not findings, carried to Task 2.4: the primitive must quote identifiers (reserved words
  such as `SELECT` are valid identifiers here) and bind values as parameters.
- Mapping to harness-owned implementations: each `TOOL_SCHEMAS` name is implemented by the
  Task 2.4 pipeline-write primitive. Task 2.4 adds a test that the two tool sets are equal.

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

---

## Task 2.3 — Deterministic Verification

### Test Cases Applied
Source: docs/EXECUTION_PLAN.md

| Case | Scenario | Expected | UI Tests | Result |
|------|----------|----------|----------|--------|
| TC-1 | Correctly fixed schema-drift scenario | PASS | N/A | PASS |
| TC-2 | Column still missing after an attempted fix | FAIL with reason | N/A | PASS |
| TC-3 | Row count outside the expected bounds | FAIL with reason | N/A | PASS |

Verification command: `python -m pytest tests/session2/test_verification.py -v`
- Run 1: **47 passed** (exit 0). Whole suite (`tests/`): 621 passed (Session 1's 257 still
  pass with the new INV-S5 guard in `src/state_manager.py`).
- Run 2 (after the Challenge Finding 1–3 fixes): 2 existing tests failed as expected — they
  recorded PASS on an attempt that never executed, which Finding 2's guard now rejects. The
  helper was changed to drive real execution.
- Run 3: **76 passed** (exit 0). Whole suite: 650 passed.
Beyond TC-1 to TC-3, the tests cover: unfixed drift fails; row-count boundaries (0, 2, 3, 10,
11, 50); the null-rate threshold, which is inclusive; wrong column type; an untyped column
checks presence only; every failure is reported together; a missing table; a missing
expectation fails closed for every scenario type; expectations are per scenario type; an
unknown run, a missing database or a corrupt database raise `VerificationError` (not a
verdict), and the missing database is not created; invalid expectations are rejected (10
shapes, including harness tables and an injected column name); verify takes only
scenario_run_id, never writes, and opens the database read-only; only the funnel
(`harness.py`) may checkpoint the verification stage. INV-S5 guard: RECOVERED is accepted
with a passing attempt and rejected with FAIL or no verification (database unchanged),
without attempt_id, citing another run's attempt, or citing a non-passing attempt when an
earlier one passed; UNRECOVERED needs no verification; verify() drives recovery end to end.

### Challenge Agent Output
Command: `./tools/challenge.sh S02 "Task 2.3"` (task files staged; exit 0). Full output, verbatim:

Running challenge agent for S02 Task 2.3...
## CC Challenge — Task 2.3 — Challenge Agent

**Challenger:** Independent agent — no build session context
**Session:** S02

### Untested Scenarios
| # | Scenario | Why it matters | Invariant at risk |
|---|----------|----------------|-------------------|
| 1 | Once a run is RECOVERED, a new `"verification"` checkpoint on the same attempt sets `verification_result` from PASS to FAIL. Nothing in `src/state_manager.py` stops this. The INV-D5 terminal trigger only blocks status changes, not Attempt updates. | The run ends up RECOVERED with no passing attempt. That breaks INV-S5's own detection rule ("every RECOVERED run has ≥1 Attempt with verification_result = PASS"). The guard only checks at the moment status is written, never afterwards. | INV-S5 |
| 2 | RECOVERED is accepted for an attempt that has PASS recorded but was never executed: no `post_execute`, no `tool_validation`. The policy decision may even be DENY or REQUIRE_APPROVAL. `_require_verified_for_recovery` reads only `verification_result`. The accepted-path test (`_attempt_with_verification(run_id, "PASS")`) itself records PASS with no execution, which shows the gap rather than testing it. | "Verification passes for that attempt" is satisfied by a bare column value, not by a verification that followed an executed recovery. | INV-S5, INV-S2 |
| 3 | The `"verification"` stage takes any caller-supplied `verification_result` ("PASS"). Nothing at runtime ties the value to a `verify()` call. The only protection is the static AST test. | The INV-S5 guard trusts a column that any caller of `sm.checkpoint` can set. "Sole authority" holds only by convention. | INV-S5 |
| 4 | `test_only_the_funnel_records_verification_results` only matches a positional constant second argument. A call like `checkpoint(run, stage="verification", ...)` or `checkpoint(run, STAGE_VAR, ...)` is not detected. The check is also `<= {"harness.py"}`, which passes trivially today because no caller exists yet. | The structural guard can be bypassed by changing the call syntax, so it does not actually enforce "only the funnel". | INV-S5 |
| 5 | Malformed `Expectation.columns` entries (a bare string, a 1-tuple or 3-tuple, a non-string type) are not tested. `_expectation_problem` unpacks `for name, _ in ...` and would raise `ValueError`/`TypeError` instead of `VerificationError`. `_check_schema` calls `declared.upper()` on a non-string type and would raise `AttributeError` at verify time. | Registration-time validation is meant to reject bad shapes. Instead, some bad shapes fail with the wrong exception, and some get through and break `verify()` later. | NONE |

### Unverified Assumptions
| # | Assumption in code | Basis | Testable within task scope |
|---|--------------------|-------|---------------------------|
| 1 | A PASS stored on the Attempt row stays valid for the life of the run (it is checked once, when status is written). | `_require_verified_for_recovery` runs only on the RECOVERED write; `verification` stage writes have no terminal-state or immutability guard. | YES |
| 2 | Any PASS in `verification_result` came from `verify()`. | `STAGE_FIELDS["verification"]` accepts the value as given; there is no provenance check. | YES (runtime behaviour can be shown; enforcing provenance is a design choice) |
| 3 | An exact uppercase string match on SQLite declared types is the right schema check. For example, `DOUBLE`, `FLOAT` or `REAL ` with trailing spaces would FAIL against `REAL`, even though SQLite gives them the same affinity. | `_check_schema` compares `actual[name] != declared.upper()`. | YES |
| 4 | `min_rows`/`max_rows` are ints and `max_null_rate` is numeric. A bool, a float or a string passes or crashes unpredictably. | `_expectation_problem` only checks ranges, not types. | YES |
| 5 | Row count and per-column null counts come from one consistent snapshot. They are separate statements with no explicit read transaction. | `_check_rows` runs separate `SELECT`s. | NO (needs a concurrent writer; INV-S7 / Task 2.4) |

### Invariant Coverage Gaps
| Invariant | Enforcement point touched | Tested in verification record |
|-----------|--------------------------|-------------------------------|
| INV-S5 (no RECOVERED run without a passing attempt after the status write) | YES | NO |
| INV-S5 (a PASS must come from `verify()`, not from any checkpoint caller) | YES | NO (static AST test only, and it can be bypassed) |

### Known Untested Scenarios (out of scope — not findings)
| Scenario | Reason out of scope |
|----------|---------------------|
| The expectation registry is per-process. After a kill and restart, `resume_scenario.py` starts with an empty registry, so every check fails closed unless the scenario definitions re-register. | Needs the Session 3 scenario definitions and the resume CLI |
| The funnel actually writes `verify()` output (status and `failure_reason`) to the Attempt row, and calls `verify()` only after `post_execute`. | Task 2.4 (`harness.py` does not exist yet) |
| `verify()` called while the execute transaction is still open would read pre-commit state. | Task 2.4 funnel ordering |
| Real SCHEMA_DRIFT, MISSING_COLUMN and PROMPT_INJECTION expectations are correct for the real PipelineState columns. | Session 3 |
| The naive baseline cannot reach `verification.py`. | INV-S6 / ablation session |

### Challenge Verdict

FINDINGS — 4 item(s) require engineer disposition before commit.
  Finding 1: A RECOVERED run's passing attempt can later be re-checkpointed to `verification_result = FAIL`. This leaves a RECOVERED run with no PASS attempt, which violates INV-S5's stated detection rule. Add a test, and a guard that rejects attempt-field writes on terminal runs or rejects overwriting a recorded verification result.
  Finding 2: The INV-S5 guard accepts RECOVERED for an attempt with PASS recorded but no `post_execute`, and even with `policy_decision` DENY or REQUIRE_APPROVAL. Either add a test showing this is rejected, or accept it with rationale.
  Finding 3: The static caller test for the `"verification"` stage misses keyword and variable stage arguments, and it passes trivially because there are no callers yet. Tighten it to cover `stage=` keywords and non-constant stage arguments, or record the limitation.
  Finding 4: Malformed `Expectation.columns` entries (non-pairs, non-string declared types) raise `ValueError`/`TypeError` at registration or `AttributeError` inside `verify()` instead of `VerificationError`. Add those shapes to `test_invalid_expectation_rejected` and fix the validation to match.

**Verdict:** FINDINGS — 4

**Finding dispositions (FINDINGS verdict only):**

*Dispositioned by CC under the engineer's standing instruction (2026-10-04): TEST for findings touching INV-S1/S2/S3/S5/S8/D1/D2 or execute_and_checkpoint atomicity; ACCEPT with a one-line rationale otherwise.*

| Finding # | Disposition | Rationale / Test case added | Test result |
|-----------|-------------|------------------------------|-------------|
| 1 | TEST (INV-S5) | `src/state_manager.py`: `_require_in_progress` makes a terminal run (RECOVERED / UNRECOVERED) accept no further checkpoints, attempts or executions; `_require_verification_write_once` rejects overwriting a recorded verification_result. Tests: `test_terminal_run_rejects_attempt_writes` (4), `test_terminal_run_rejects_new_attempts_and_run_writes` (2), `test_terminal_run_rejects_execute`, `test_verification_result_is_write_once` (3), `test_every_recovered_run_keeps_a_passing_attempt` (runs INV-S5's own detection query). Each rejection leaves the database unchanged. Mutation check: removing either guard fails 7 and 3 tests respectively | PASS |
| 2 | TEST (INV-S5, INV-S2) | `_require_verified_for_recovery` now requires the cited attempt's (policy_decision, action_applied, verification_result) to be exactly (ALLOW, True, PASS). Tests: `test_recovered_rejected_for_unexecuted_attempt_even_with_pass` (DENY, REQUIRE_APPROVAL, ALLOW-but-not-applied — all with PASS recorded), `test_recovered_accepted_only_for_allow_applied_pass`. The test helper now drives real ALLOW → VALID → `execute_and_checkpoint` → verification flows. Mutation check: removing the guard fails 8 tests | PASS |
| 3 | TEST (INV-S5) | The structural scan (`_checkpoint_stage_problems`) flags any `checkpoint()` call whose stage is "verification" or cannot be determined statically (keyword `stage=`, variables, expressions, f-strings, `*args`, `**kwargs`); allowed only in `harness.py`. Positive controls: `test_caller_scan_flags_verification_or_unknown_stage` (10 forms); negatives: `test_caller_scan_ignores_other_stages_and_calls` (4). Task 2.4 will add the positive check that `harness.py` is the caller | PASS |
| 4 | ACCEPT | Not on the TEST list. Expectations are registered by harness code (Session 3 scenario definitions), never from agent input; a malformed one fails loudly, at registration or on the first `verify()`, rather than producing a wrong verdict | N/A |

### Code Review
Invariant text is embedded in the Task 2.3 CC prompt in `docs/EXECUTION_PLAN.md`.
Items to review (results left blank):
- INV-S5: `src/verification.py` is the sole authority for verification_result — it decides
  from database state only (no agent input reaches it) and never writes.
- INV-S5 status-write guard (`src/state_manager.py`): a checkpoint setting status RECOVERED
  is rejected unless it names an attempt of that run whose verification_result is PASS.
- Checks: expected schema present on the target table, row count within bounds, null rate
  of required columns at or below the threshold; a missing expectation fails closed.
- CQ-001: single stateable purpose per function; conditional nesting ≤ 2 levels.

### Pre-Commit Declaration

PRE-COMMIT DECLARATION — Task 2.3
-----------------------------------
Files modified:     sessions/SESSION_LOG_S02.md, sessions/VERIFICATION_RECORD_S02.md,
                    src/verification.py (new), src/state_manager.py,
                    tests/session2/test_verification.py (new)
                    (`git diff --name-only HEAD` after `git add`; all within Claude.md §3)
Functions added:    src/verification.py — init, register_expectation, verify, _run_checks,
                    _expectation_problem, _scenario_type, _table_columns, _check_schema,
                    _check_rows (+ Expectation, VerificationResult, VerificationError);
                    src/state_manager.py — _require_verified_for_recovery,
                    _require_in_progress, _require_verification_write_once
Functions modified: src/state_manager.py — _write_checkpoint (calls the three guards),
                    start_attempt (rejects terminal runs)
Functions deleted:  NONE
Schema changes:     NONE
Config changes:     NONE

Everything above is within the task prompt scope: YES — with the CC choices under
Scope Decisions.

### Scope Decisions
CC implementation choices (not separately specified):
- Expectations come from a registry keyed by scenario_type (`register_expectation`), because
  real PipelineState columns are decided in Session 3 (Task 1.2's prompt). Session 3's
  scenario definitions register them; Session 2 tests register fixtures. A scenario type
  with no registered expectation → FAIL (fail closed). Registering again replaces the
  expectation.
- `Expectation(table, columns=((name, declared_type or None), ...), min_rows, max_rows,
  max_null_rate)`. Validated on registration: PipelineState table only, identifier column
  names, 0 ≤ min ≤ max, null rate within [0, 1].
- Null-rate threshold is inclusive (rate ≤ max_null_rate); with zero rows the null rate is 0
  and the row-count check decides. All checks run and all failures are reported.
- `VerificationResult(status, details)`; `failure_reason` is None on PASS and the joined
  details on FAIL, matching INV-D3.
- Database errors and unknown runs raise `VerificationError` (chained), never a PASS or FAIL.
- INV-S5's enforcement point is a "status-write guard". The only status write path is
  `src/state_manager.py`, so the guard lives there: status RECOVERED requires an attempt_id
  of that run that is ALLOW-decided, applied and verified PASS ("for that attempt",
  Challenge Finding 2). A terminal run accepts no further writes, and verification_result is
  write-once (Finding 1), so a RECOVERED run can never lose its passing attempt.

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

---

## Task 2.4 — Execute Funnel Function

### Test Cases Applied
Source: docs/EXECUTION_PLAN.md

| Case | Scenario | Expected | UI Tests | Result |
|------|----------|----------|----------|--------|
| TC-1 | DENY action | Never reaches the pipeline-write primitive (asserted with a spy) | N/A | PASS |
| TC-2 | ALLOW + valid tool call | Executes and checkpoints twice (pre_execute, post_execute) | N/A | PASS |
| TC-3 | Structural grep/AST test | Exactly one call site to the pipeline-write primitive in the codebase | N/A | PASS |
| TC-4 | Direct call to the pipeline-write primitive targeting `ScenarioRun`, `Attempt` or `TraceEvent` | Rejected at runtime, independent of the static check | N/A | PASS |

Verification command: `python -m pytest tests/session2/test_harness_funnel.py -v && python scripts/assert_single_execute_caller.py && python scripts/assert_write_scope_isolation.py`
- State Manager changes first, whole suite: 9 failures, both causes expected. Session 1's
  kill-test child processes load `state_manager` by path without `src/` on `sys.path` (it now
  imports `pipeline_tables`), so the child preamble was fixed. `test_terminal_run_rejects_execute`
  hit the new INV-S1 cleared-for-execution check first, so its attempt is now fully cleared
  before the run ends. → 650 passed.
- Funnel tests, run 1: 2 failures. The spy order did not include the primitive's own
  re-validation (by design), so the expectation was corrected. `assert_single_execute_caller.py`
  flagged `scripts/assert_write_scope_isolation.py`, which calls the primitive on purpose to
  test it; it is now a named, printed exemption (`CHECK_SCRIPTS`), like `tests/`. It also flagged
  `scripts/simulate_deny_path.py` for importing `pipeline_write` to spy on it; that script now
  reaches it via `harness.pipeline_write`, so it needs no exemption.
- Whole suite, run 2: 1 failure. Session 1's INV-S3 write-path scan flagged the same check
  script: its harness-table SQL strings are inputs it expects to be *denied*. Same named
  exemption, plus a test that the exemption list is exactly that file.
- Mutation checks (each restored afterwards):
  - DENY treated as ALLOW: 7 funnel tests fail and `simulate_deny_path` exits 1.
  - Authorizer allows everything: 20 tests fail and `assert_write_scope_isolation` exits 1.
  - Primitive table check removed: 28 tests fail and the isolation check exits 1.
  - Cleared-for-execution check removed: 5 tests fail.
- Final: funnel tests **95 passed**; `assert_single_execute_caller.py` and
  `assert_write_scope_isolation.py` exit 0 → **verification exit 0**. Whole suite (`tests/`):
  746 passed. `simulate_deny_path.py --assert-no-execution` exits 0.
- After the Challenge Finding 1–4 fixes: funnel tests **120 passed** (exit 0); both scripts
  exit 0 (the isolation check now denies 13 statements); whole suite 771 passed.

### Challenge Agent Output
Command: `./tools/challenge.sh S02 "Task 2.4"` (task files staged; exit 0). Full output, verbatim:

Running challenge agent for S02 Task 2.4...
## CC Challenge — Task 2.4 — Challenge Agent

**Challenger:** Independent agent — no build session context
**Session:** S02

### Untested Scenarios
| # | Scenario | Why it matters | Invariant at risk |
|---|----------|----------------|-------------------|
| 1 | A second caller reaches the primitive through an attribute chain, e.g. `import harness; harness.pipeline_write.write(conn, ...)`, or through a dynamic import (`importlib.import_module("pipeline_write")`, `getattr`). `calls_named` only matches calls whose owner is an `ast.Name` in `modules`, and rule 1 only fires on a direct `import pipeline_write`. `scripts/simulate_deny_path.py` already reaches the primitive this way to spy on it, without importing it. `test_single_call_site_detector_catches_a_second_caller` has no case for this. | `assert_single_execute_caller.py` would report "INV-S1 OK" while a second write path exists. | INV-S1 |
| 2 | A caller of the primitive or of `execute_and_checkpoint` placed under `tools/` or `verification/`. `assert_single_execute_caller.py` scans only `("src", "scripts")`. Both directories are allowed by Claude.md §3, and the Session 1 write-path scan in `tests/session1/test_state_manager.py` already covers them. | A whole permitted code area is invisible to the single-caller check. | INV-S1 |
| 3 | An aliased import of `execute_and_checkpoint`, e.g. `from state_manager import execute_and_checkpoint as run; run(...)`. The `execute_and_checkpoint` scan passes no `functions` aliases, so a bare alias only matches if its name is literally `execute_and_checkpoint`. The detector test only covers the unaliased bare and attribute forms. | A second caller of the Execute mechanism goes undetected. | INV-S1 |
| 4 | apply_fn runs a direct `UPDATE sqlite_master ...` (or `UPDATE sqlite_temp_master`). `_apply_fn_authorizer` explicitly allows `SQLITE_UPDATE` on both tables. The denial relies entirely on SQLite's built-in protection while `writable_schema` is off, and that is never exercised. `FORBIDDEN_APPLY_SQL` and `test_apply_fn_writes_outside_pipeline_denied` test `PRAGMA writable_schema = ON` but not the direct UPDATE. | This is the one write the authorizer allows outside PipelineState. If it succeeded, it could rewrite harness table definitions or triggers. | INV-S8 |
| 5 | `verification.verify()` raises, or the verification checkpoint fails, after `post_execute` has committed. | The attempt would be left applied with `verification_result` NULL after a `tool_call` trace event was already emitted. No test pins what happens to the outcome or the attempt. | INV-S3, INV-S5 |
| 6 | `policy_layer.request_approval()` raises after the REQUIRE_APPROVAL checkpoint has committed. | The decision is committed but no `policy_decision` trace event is written, so the trace is missing a committed transition. | INV-D4 (trace completeness) |
| 7 | `attempt_action` is called with an `attempt_id` that belongs to a different `scenario_run_id`. | No funnel test confirms the policy, validation or execute checkpoints reject a cross-run attempt before anything is written. | INV-S1, INV-D4 |

### Unverified Assumptions
| # | Assumption in code | Basis | Testable within task scope |
|---|--------------------|-------|---------------------------|
| 1 | The `action` object does not change between `policy_layer.evaluate`, `tool_validation.validate` and `_execute`. It is never copied, and `apply_fn` captures `params` by reference. The primitive re-runs validation and the scope check, but not Policy. | `harness.attempt_action` / `_execute` | YES |
| 2 | `column_type`, which is inlined unquoted into `ALTER TABLE ... ADD COLUMN`, is restricted by Tool Validation to plain type names. Only one injection form (`"TEXT; DROP TABLE Attempt"`) is tested. Clause-style values such as `TEXT REFERENCES ScenarioRun` or `TEXT DEFAULT (...)` are not. | `pipeline_write._add_column` | YES |
| 3 | `ALTER TABLE <pipeline_table> RENAME TO <other>` is denied. The authorizer's `SQLITE_ALTER_TABLE` branch would allow it on `arg2 in PIPELINE_TABLES`, so the denial must come from some other authorizer action. The test asserts that it is denied but nothing pins why, so an authorizer change could quietly allow a table to be renamed out of PipelineState. | `_apply_fn_authorizer`; `test_apply_fn_writes_outside_pipeline_denied` | YES |
| 4 | INV-S8 static check (a) only covers `src/pipeline_write.py`. The `apply_fn` closure in `src/harness.py` gets a full connection and is not statically checked for a write path to harness tables. Only the runtime authorizer guards it. | `assert_write_scope_isolation.static_violations` | YES |

### Invariant Coverage Gaps
| Invariant | Enforcement point touched | Tested in verification record |
|-----------|--------------------------|-------------------------------|
| INV-S1 | YES | PARTIAL: the structural detector is not tested against attribute-chain or dynamic access, `tools/`/`verification/` callers, or an aliased `execute_and_checkpoint` |
| INV-S8 | YES | PARTIAL: the allowed `sqlite_master` UPDATE exemption is never tested against a direct UPDATE from apply_fn |
| INV-S3 | YES | PARTIAL: no test for a failure between `post_execute` commit and the verification checkpoint |

### Known Untested Scenarios (out of scope — not findings)
| Scenario | Reason out of scope |
|----------|---------------------|
| An execution error in apply_fn leaves the attempt ALLOW + VALID at `pre_execute`, with budget consumed and no `failure_reason` | Already logged; the retry/failure recording is Session 4 |
| ALLOW at the budget cap raises IntegrityError from the policy checkpoint | Already logged; budget exhaustion → UNRECOVERED is Task 4.1 |
| Expectations must be re-registered after a kill and restart | Session 3/4 scenario registration and resume |
| Bounded re-plan after REJECTED | Session 4 |
| Kill-and-restart between the `pre_execute` commit and the apply/`post_execute` transaction | Session 4 resume path / live demo |

### Challenge Verdict

FINDINGS — 4 item(s) require engineer disposition before commit.
  Finding 1: `scripts/assert_single_execute_caller.py` misses calls to the primitive through an attribute chain (`harness.pipeline_write.write`) or a dynamic import. `scripts/simulate_deny_path.py` shows the path is reachable without importing `pipeline_write`. Add a detector test case for this and extend the detection.
  Finding 2: `scripts/assert_single_execute_caller.py` scans only `src/` and `scripts/`. It skips `tools/` and `verification/`, which are permitted directories and are already covered by the Session 1 write-path scan.
  Finding 3: An aliased bare import of `state_manager.execute_and_checkpoint` is not detected. There is no test case for it.
  Finding 4: The authorizer explicitly allows `SQLITE_UPDATE` on `sqlite_master`/`sqlite_temp_master`, but no test or check-script case attempts a direct `UPDATE sqlite_master` inside apply_fn. Add it to `test_apply_fn_writes_outside_pipeline_denied` and `FORBIDDEN_APPLY_SQL`, and assert that it is rejected and the schema is unchanged.

**Verdict:** FINDINGS — 4

**Finding dispositions (FINDINGS verdict only):**

*Dispositioned by CC under the engineer's standing instruction (2026-10-04): TEST for findings touching INV-S1/S2/S3/S5/S8/D1/D2 or execute_and_checkpoint atomicity; ACCEPT with a one-line rationale otherwise.*

| Finding # | Disposition | Rationale / Test case added | Test result |
|-----------|-------------|------------------------------|-------------|
| 1 | TEST (INV-S1) | `assert_single_execute_caller.py` is now reference-based: any name, attribute (`harness.pipeline_write.write`), import (any alias) or dynamic lookup (`import_module` / `__import__` / `getattr` / `setattr` with the module's name) of `pipeline_write` outside `src/harness.py` is a violation. `scripts/simulate_deny_path.py`, which spies on the primitive, is now a named, printed check-script exemption. Tests: `test_reference_detector_catches_every_access_form` (8 primitive forms), `test_reference_detector_ignores_unrelated_code` (3), `test_single_caller_check_fails_on_a_planted_second_caller` (attribute-chain caller planted in tools/, verification/, scripts/ → exit 1) | PASS |
| 2 | TEST (INV-S1) | The scan covers `src/`, `scripts/`, `tools/`, `verification/`. Tests: `test_single_caller_check_scans_every_permitted_code_directory`, plus the planted-caller test above | PASS |
| 3 | TEST (INV-S1) | `execute_and_checkpoint` is guarded by references too, allowed only in `src/harness.py` and `src/state_manager.py`, so aliased imports (`from state_manager import execute_and_checkpoint as run`), attribute aliasing and `getattr` are caught. 4 cases in `test_reference_detector_catches_every_access_form`; `test_call_site_counter_counts_owner_qualified_calls` | PASS |
| 4 | TEST (INV-S8) | Direct writes to the schema tables from apply_fn are rejected and leave every `sqlite_master` row unchanged. UPDATE fails on SQLite's own protection ("may not be modified", since `writable_schema` cannot be enabled: PRAGMA is denied); INSERT and DELETE fail on the authorizer. Tests: `test_direct_schema_table_writes_from_apply_fn_rejected` (5), `test_rename_table_out_of_pipeline_scope_denied_by_authorizer` (pins Unverified Assumption 3). `FORBIDDEN_APPLY_SQL` in `scripts/assert_write_scope_isolation.py` gained the two UPDATE cases (13 statements now) | PASS |

### Code Review
Invariant text is embedded in the Task 2.4 CC prompt in `docs/EXECUTION_PLAN.md`.
Items to review (results left blank):
- INV-S1: `harness.attempt_action` is the only call path to the pipeline-write primitive
  (`pipeline_write.write`) and to `state_manager.execute_and_checkpoint`; fixed order Policy →
  Tool Validation → Execute → Verify; execution additionally refused by the State Manager
  unless the attempt is ALLOW + VALID.
- INV-S2: DENY (and REQUIRE_APPROVAL) → zero execution; nothing after the policy checkpoint runs.
- INV-S3: every stage is checkpointed before the next; Execute only via `execute_and_checkpoint`.
- INV-D2: only the ALLOW branch increments attempts_used, in the same checkpoint as the decision.
- INV-S8: (a) static — the primitive's module names no harness table, is imported only by
  `harness.py`; (b) runtime — the primitive rejects any non-PipelineState target, and the
  apply_fn authorizer denies any write outside PipelineState tables (including via triggers).
- Trace events emitted only after their commit.
- CQ-001: single stateable purpose per function; conditional nesting ≤ 2 levels.

### Pre-Commit Declaration

PRE-COMMIT DECLARATION — Task 2.4
-----------------------------------
Files modified:     sessions/SESSION_LOG_S02.md, sessions/VERIFICATION_RECORD_S02.md,
                    src/harness.py (new), src/pipeline_write.py (new), src/state_manager.py,
                    scripts/assert_single_execute_caller.py (new),
                    scripts/assert_write_scope_isolation.py (new), scripts/simulate_deny_path.py (new),
                    tests/session2/test_harness_funnel.py (new), tests/session2/test_verification.py,
                    tests/session1/test_state_manager.py
                    (`git diff --name-only HEAD` after `git add`; all within Claude.md §3)
Functions added:    src/harness.py — init, attempt_action, _record_blocked, _record_allow,
                    _record_validation, _execute, _record_verification, _describe (+ AttemptOutcome);
                    src/pipeline_write.py — write, _add_column, _rename_column, _backfill_column
                    (+ WriteScopeError, InvalidToolCall, IMPLEMENTATIONS);
                    src/state_manager.py — _apply_fn_scope, _apply_fn_authorizer,
                    _require_policy_write_once, _require_cleared_for_execution;
                    scripts — one module each (see the files' docstrings)
Functions modified: src/state_manager.py — execute_and_checkpoint (cleared-for-execution check;
                    apply_fn authorizer), _write_checkpoint (policy write-once)
Functions deleted:  src/state_manager.py — _transaction_control_denied, _deny_transaction_control
                    (superseded by _apply_fn_scope / _apply_fn_authorizer, which still deny all
                    transaction control)
Schema changes:     NONE
Config changes:     NONE

Everything above is within the task prompt scope: YES — with the CC choices under
Scope Decisions.

### Scope Decisions
CC implementation choices (not separately specified):
- The pipeline-write primitive is `src/pipeline_write.py::write(conn, tool, params)`, called only
  from `harness._execute`'s apply_fn. It rejects non-PipelineState targets (INV-S8 runtime
  guard #1) and re-runs Tool Validation, refusing anything not VALID, so an unvalidated call can
  never execute even if a future path skips the funnel. Identifiers are quoted; values bound.
- INV-S8 runtime guard #2 (as the prompt suggests): the State Manager's apply_fn authorizer
  now allows only reads and writes to PipelineState tables. Its ALTER TABLE internals
  (`sqlite_master` / `sqlite_temp_master` updates) are allowed because direct schema writes need
  PRAGMA, which is denied. Everything else is denied, including writes to harness tables through
  triggers.
- INV-S1/S2 runtime enforcement at the execute mechanism: `execute_and_checkpoint` refuses an
  attempt that is not ALLOW + VALID. policy_decision is write-once, so an attempt passes through
  the funnel once.
- REQUIRE_APPROVAL: the decision is checkpointed, the approval stub is called (PENDING, recorded
  in the trace payload), and the funnel returns without validation or execution.
- REJECTED: the outcome carries `needs_replan=True`; the bounded re-plan loop is Session 4. The
  ALLOW decision already consumed one unit of budget (INV-D2, ARCHITECTURE D4).
- Trace events: policy → `policy_decision`; validation and verification → `state_transition`
  with a `stage`; execution → `tool_call`. Each is emitted after its checkpoint commits. An action
  that is not strict JSON is traced as its repr.
- `harness.init(db_path, trace_path)` points the State Manager, Trace Logger and Verification at
  one database and trace file. The funnel never sets the run status.
- Execution errors (e.g. renaming a column that does not exist) roll back atomically and
  propagate; the attempt stays at pre_execute and is not applied. Handling them in the retry
  loop is Session 4 (see Out of Scope Observations).
- `scripts/assert_single_execute_caller.py` is reference-based (Challenge Findings 1–3) and
  scans `src/`, `scripts/`, `tools/`, `verification/`; named check-script exemptions:
  `scripts/assert_write_scope_isolation.py` and `scripts/simulate_deny_path.py`.
- `scripts/simulate_deny_path.py` (required by the Session 2 Integration Check; no task prompt
  creates it) sends 5 PROMPT_INJECTION-style actions through the funnel with spies on every
  post-policy entry point.

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
