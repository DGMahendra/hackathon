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
