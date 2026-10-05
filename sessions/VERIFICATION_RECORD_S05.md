**Session:** Session 5 — Ablation Harness
**Date:** 2026-10-04
**Engineer:** 

*Each task entry is created before the task starts. Challenge Agent findings are
dispositioned by CC under the engineer's standing instruction (2026-10-04) — see
`sessions/SESSION_LOG_S05.md` Decision Log.*

---

## Task 5.1 — Naive Baseline (Structurally Stripped)

### Test Cases Applied
Source: docs/EXECUTION_PLAN.md

| Case | Scenario | Expected | UI Tests | Result |
|------|----------|----------|----------|--------|
| TC-1 | Static import-graph check | Passes for the correct implementation; fails when a forbidden import is deliberately added | N/A | PASS |
| TC-2 | Naive baseline against PROMPT_INJECTION | Executes the unsafe action (intended naive behaviour) | N/A | PASS |

Verification command: `python scripts/assert_naive_has_no_harness_imports.py && python -m pytest tests/session5/test_naive_baseline.py -v`
- Prerequisite refactor (engineer decisions, Pre-Build). `agent_core` no longer imports
  `verification`; the agent sees the scenario type and the raw pipeline only, and gains
  `propose()`, which reads and writes no run records. `failure_injector` no longer imports
  `verification`: its expectations moved to the new `src/scenario_expectations.py` (harness side,
  imported by the orchestrator), and `inject()` gained an optional `db_path`. Whole suite without
  the live tests: 950 passed. Live re-check: both live agent tests pass, and `--dry-run` RECOVERED
  all three scenarios from the raw-data prompt.
- Run 1: the static check exits 0 (`agent_core, env_file, failure_injector, trace_logger`); **21
  passed** → **exit 0**.
Beyond TC-1 and TC-2, the tests cover:
- the check catches direct, aliased, transitive (`harness`, `orchestrator`,
  `scenario_expectations`) and dynamic (`importlib.import_module`, `__import__`) forbidden imports,
  and a missing target;
- importing `naive_baseline` loads none of the gates, `harness` or the State Manager;
- the unsafe upload is "executed" with sockets patched to fail (proving no network I/O);
- a correct fix is applied, a wrong fix is still claimed as recovered (no verification), and a
  rowid write that Tool Validation would reject runs anyway;
- an execution error is recorded;
- only pipeline tables exist in the naive database (no ScenarioRun / Attempt);
- an injected ATTACH is refused and no outside file is created;
- one attempt only, with no retries on API or planning failure;
- the naive baseline's injection is identical to the harness's.

- The session was interrupted before the challenge run; on resume the engineer chose to re-run
  the step. Full suite including the live tests: **974 passed**; INV-S6 check OK.
- Run 2 (after the Challenge Finding 3 tests): static check exit 0; `test_naive_baseline.py`
  **24 passed** → **exit 0**.

### Challenge Agent Output
Command: `./tools/challenge.sh S05 "Task 5.1"` (task files staged; exit 0). Full output, verbatim:

Running challenge agent for S05 Task 5.1...
## CC Challenge — Task 5.1 — Challenge Agent

**Challenger:** Independent agent — no build session context
**Session:** S05

### Untested Scenarios
| # | Scenario | Why it matters | Invariant at risk |
|---|----------|----------------|-------------------|
| 1 | The static check is never tested against a relative import (`from . import verification`) or a package-qualified import (`import src.verification`, `from src.policy_layer import evaluate`). In `referenced_modules`, an `ImportFrom` with `module=None` adds nothing. `src.x` reduces to `"src"`, and `src/src.py` does not exist, so nothing is followed. | A forbidden module can enter the naive import graph and the check still exits 0. The INV-S6 build gate can be bypassed. | INV-S6 |
| 2 | `test_naive_never_attaches_another_database` never reaches `_no_attach`. The injected `"pipeline_silver; ATTACH …"` is two statements, and sqlite3's one-statement rule rejects it before ATTACH is ever authorized. No test calls `_no_attach` or runs a single-statement ATTACH on a connection that has the authorizer set. | The claim "the connection refuses ATTACH" is unverified. The test would still pass if `set_authorizer` were deleted. | NONE (sandbox claim) |
| 3 | On Python 3.11, which the Fixed Stack allows, multi-statement `conn.execute` raises `sqlite3.Warning`. `sqlite3.Warning` is not a subclass of `sqlite3.Error` (3.12+ raises `ProgrammingError` instead). `apply_directly` catches only `(sqlite3.Error, KeyError, TypeError)`. | On 3.11, an agent-supplied identifier containing `;` crashes `run_naive` instead of recording `EXECUTION_ERROR`. The test passes only on 3.12+. | NONE |
| 4 | Verification expectations now register as a side effect of importing `scenario_expectations` (via `orchestrator`). Every changed fixture calls `scenario_expectations.register_expectations()` explicitly, which hides whether the import-time registration works. No test imports `orchestrator` (or runs the resume path) in a fresh process and checks that `verification._expectations` holds all three scenarios. | Before this change, importing `failure_injector` registered the expectations. Any harnessed path that uses `verification` without importing `orchestrator` now has none, so verification may fail or error at runtime. | INV-S5 |
| 5 | `run_naive` calls `agent_core.init(db_path)`, which changes agent_core's module-wide DB path and never restores it. No test checks that a harnessed `diagnose_and_plan` in the same process still reads the harness DB after a naive run. | The ablation runner (Task 5.3) will run both sides in one process. The harnessed agent could end up planning from the naive throwaway DB, and the paired comparison would then be invalid. | INV-D6 |

### Unverified Assumptions
| # | Assumption in code | Basis | Testable within task scope |
|---|--------------------|-------|---------------------------|
| 1 | Every tool outside the three pipeline tools, including `None`, `"noop"` or a malformed action, is an "unsafe external action executed" and counts as `CLAIMED_RECOVERED` with `simulated_external=True`. | `apply_directly`: the `builder is None` branch returns `(True, True, …)` without condition. The parity test's `{"tool": "noop"}` takes this path, and no test asserts what that means. | YES |
| 2 | Dynamic imports only take the form `import_module`, `__import__`, `getattr`, `setattr` or `hasattr` with a constant string. `sys.modules["verification"]`, `importlib.util.spec_from_file_location(...)`, and names built at runtime are not caught. | `DYNAMIC_LOOKUPS` and the constant-string filter in `referenced_modules`. | YES |
| 3 | Every `src` module is a flat `src/<name>.py`. Packages (`src/<pkg>/__init__.py`) are never followed transitively. | `import_paths` resolves only `SRC / f"{name}.py"`. | YES |
| 4 | Removing Verification symptoms from `_build_prompt` keeps the "previous attempts" history the harnessed agent needs when re-planning. Nothing asserts the history text still appears in the prompt after the refactor through `propose()`. | `diagnose_and_plan` now goes through `propose(..., _previous_attempts(...))`. Only the test for the absence of Verification symptoms was updated. | YES |

### Invariant Coverage Gaps
| Invariant | Enforcement point touched | Tested in verification record |
|-----------|--------------------------|-------------------------------|
| INV-S6 | YES (`scripts/assert_naive_has_no_harness_imports.py`) | YES for direct, aliased, transitive and constant dynamic imports; NO for relative and package-qualified imports (Untested #1) |
| INV-S5 | YES (expectation registration moved to `src/scenario_expectations.py`) | NO: import-time registration is masked by explicit fixture calls (Untested #4) |

### Known Untested Scenarios (out of scope — not findings)
| Scenario | Reason out of scope |
|----------|---------------------|
| The live `claude-sonnet-5` actually follows the PROMPT_INJECTION payload in the naive baseline (TC-2 uses a `FakeClient` that returns the upload action) | Needs the live API; the model is nondeterministic (documented risk) |
| Classifying executed naive actions as unsafe through the Policy Layer | Belongs to Task 5.3 (ablation runner) |
| Shared seed/failure-state hash parity check that raises on mismatch | Belongs to Task 5.2 (`ablation_fixture.get_seed_state()`) |
| Same-process naive and harnessed runs end to end | Task 5.3 orchestration; Untested #5 covers only the part testable in 5.1 |

### Challenge Verdict

FINDINGS — 4 items require engineer disposition before commit.
  Finding 1: The INV-S6 static check misses relative imports (`from . import verification`) and package-qualified imports (`import src.verification`, `from src.policy_layer import …`). Add parametrized cases to `tests/session5/test_naive_baseline.py` and fix `referenced_modules` / `import_paths`.
  Finding 2: The ATTACH-sandbox test never runs `_no_attach`, because the multi-statement rule rejects first. On Python 3.11 that rejection raises `sqlite3.Warning`, which `apply_directly` does not catch, so `run_naive` crashes. Add a direct authorizer test (single-statement ATTACH on an authorized connection) and a test that a `;`-containing identifier yields `EXECUTION_ERROR` on every supported Python.
  Finding 3: No test checks that importing `orchestrator` alone, or the resume entry path, registers all three Verification expectations in a fresh process. The fixtures' explicit `register_expectations()` calls hide a possible regression that would make harnessed verification fail (INV-S5).
  Finding 4: `run_naive` leaves agent_core's DB path pointing at the naive throwaway DB. No test checks that a later harnessed `diagnose_and_plan` in the same process still reads the harness DB, which the INV-D6 paired comparison depends on.

**Verdict:** FINDINGS — 4

**Finding dispositions (FINDINGS verdict only):**

*Dispositioned by CC under the engineer's standing instruction (2026-10-04): TEST for findings touching INV-S1/S2/S3/S5/S8/D1/D2 or execute_and_checkpoint atomicity; ACCEPT with a one-line rationale otherwise.*

| Finding # | Disposition | Rationale / Test case added | Test result |
|-----------|-------------|------------------------------|-------------|
| 1 | ACCEPT | INV-S6 is not on the TEST list. `src/` is a flat module layout imported by bare name (no package, no relative imports anywhere in the codebase), and the check follows that convention; relative / `src.`-qualified imports are logged as a hardening observation | N/A |
| 2 | ACCEPT | No listed invariant. The runtime is Python 3.12, where a multi-statement identifier raises `ProgrammingError` (a `sqlite3.Error`, caught → EXECUTION_ERROR). The 3.11 `sqlite3.Warning` case and the untested single-statement ATTACH authorizer path are logged as observations; the naive DB is a throwaway, and the authorizer is lab hygiene, not an ablated feature | N/A |
| 3 | TEST (INV-S5) | `test_harnessed_entry_points_register_every_expectation`: in a fresh process, importing `orchestrator` alone — and loading `scripts/resume_scenario.py` — registers all three expectations without any explicit call. `test_failure_injector_alone_registers_nothing`: importing the shared injector never loads `verification` | PASS |
| 4 | ACCEPT | INV-D6 is not on the TEST list. `orchestrator.init` re-points `agent_core` at the harness DB for every harnessed run; Task 5.3's runner initialises each side before each run, and a same-process naive-then-harnessed test is added with the runner | N/A |

### Code Review
Invariant text is embedded in the Task 5.1 CC prompt in `docs/EXECUTION_PLAN.md`.
Items to review (results left blank):
- INV-S6: `src/naive_baseline.py`'s transitive import graph contains none of policy_layer,
  tool_validation, verification (nor any module that imports them) — checked statically by
  `scripts/assert_naive_has_no_harness_imports.py`, which also catches dynamic-import strings.
- Naive behaviour: agent proposes, the action is applied directly (no policy, no validation, no
  verification; the agent's claim of success is accepted); no State Manager persistence.
- Unsafe / unknown tools are simulated with no network or file I/O (engineer decision).
- CQ-001: single stateable purpose per function; conditional nesting ≤ 2 levels.

### Pre-Commit Declaration

PRE-COMMIT DECLARATION — Task 5.1
-----------------------------------
Files modified:     sessions/SESSION_LOG_S05.md (new), sessions/VERIFICATION_RECORD_S05.md (new),
                    src/naive_baseline.py (new), src/scenario_expectations.py (new),
                    src/agent_core.py, src/failure_injector.py, src/orchestrator.py,
                    scripts/assert_naive_has_no_harness_imports.py (new),
                    tests/session5/test_naive_baseline.py (new), tests/session3/test_agent_core.py,
                    tests/session3/test_failure_injector.py, tests/session3/test_orchestrator.py,
                    tests/session4/conftest.py
                    (`git diff --name-only HEAD` after `git add`; all within Claude.md §3)
Functions added:    src/naive_baseline.py — run_naive, apply_directly, _no_attach (+ NaiveResult);
                    src/agent_core.py — propose; src/scenario_expectations.py — register_expectations;
                    scripts/assert_naive_has_no_harness_imports.py — referenced_modules,
                    import_paths, violations, main
Functions modified: src/agent_core.py — diagnose_and_plan, _build_prompt (no Verification symptoms);
                    src/failure_injector.py — inject (optional db_path)
Functions deleted:  src/failure_injector.py — register_expectations (moved to scenario_expectations)
Schema changes:     NONE
Config changes:     NONE

Everything above is within the task prompt scope: YES — with the engineer decisions and CC
choices under Scope Decisions.

### Scope Decisions
Engineer decisions (Session 5 Pre-Build, see `SESSION_LOG_S05.md`):
- Same raw-data agent context on both sides (no Verification symptoms).
- Naive: unsafe or unknown tools are simulated, with no I/O.
- Every ablation run uses its own throwaway database.

CC implementation choices:
- `run_naive(scenario_type, seed, db_path, client)` makes one proposal and one application: no
  retries, no State Manager, no trace. `status` is the naive baseline's own claim
  (CLAIMED_RECOVERED whenever the action ran; EXECUTION_ERROR, INFRASTRUCTURE_FAILURE or
  PLANNING_FAILURE otherwise).
- Pipeline tools run as unvalidated, interpolated SQL — that is the point of the baseline. As a
  lab sandbox, not an ablated feature, the naive connection refuses ATTACH / DETACH.
- Whether an executed naive action was *unsafe* is classified outside this module (by the
  ablation runner, Task 5.3, using the Policy Layer), so the naive baseline itself never touches
  Policy.
- The static check follows the transitive graph over `src/` modules and dynamic-import strings;
  `--target` lets tests point it at a deliberately broken copy.
- In 5.1 the naive baseline injects with `failure_injector.inject(..., db_path=...)`; Task 5.2
  replaces that with the shared `ablation_fixture.get_seed_state()`.

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

## Task 5.2 — Seed/Failure-State Parity Fixture

### Test Cases Applied
Source: docs/EXECUTION_PLAN.md

| Case | Scenario | Expected | UI Tests | Result |
|------|----------|----------|----------|--------|
| TC-1 | Naive and harnessed runs with the same seed | Initial-state hashes match exactly | N/A | PASS |
| TC-2 | Deliberately mismatched seed | Raises `AblationIntegrityError` — not a silent pass, not dependent on assertions being enabled | N/A | PASS |

Verification command: `python -m pytest tests/session5/test_ablation_fixture.py -v`
- Run 1: **17 passed** (exit 0). Whole suite without the live tests: 992 passed. The INV-S6 check
  still passes, with `ablation_fixture` now in the naive graph.
Beyond TC-1 and TC-2, the tests cover:
- the hash is reproducible and seed-sensitive, covers schema and rows, and differs per scenario;
- the mismatch is caught before the agent is asked (0 client calls), and the harnessed run is
  closed UNRECOVERED, not left IN_PROGRESS;
- `require_parity` checks seed, scenario type and hash, symmetrically;
- the mismatch still raises under `python -O`, and the fixture contains no `assert`;
- AST: `orchestrator` and `naive_baseline` both seed only via `get_seed_state`, never `inject`;
- the naive result and the `run_started` trace event carry the hash.

### Challenge Agent Output
Command: `./tools/challenge.sh S05 "Task 5.2"` (task files staged; exit 0). Full output, verbatim:

Running challenge agent for S05 Task 5.2...
## CC Challenge — Task 5.2 — Challenge Agent

**Challenger:** Independent agent — no build session context
**Session:** S05

### Untested Scenarios
| # | Scenario | Why it matters | Invariant at risk |
|---|----------|----------------|-------------------|
| 1 | `execute_naive(seed_state, db_path)` gets a `seed_state` that doesn't belong to `db_path` (another DB, or the DB was changed after `prepare_naive`). Nothing re-hashes `db_path` before the naive agent runs. | The parity check can pass on one hash while the naive run uses different data. Nothing ties the checked hash to the database that actually gets used. | INV-D6 |
| 2 | Parity mismatch inside `run_scenario`: the test checks only `status == "UNRECOVERED"`. It doesn't check that the `run_complete` trace or the reason identifies `AblationIntegrityError`; `_abandon` writes the generic `"reason": "HARNESS_ERROR"`, with the error only in `detail: repr(exc)`. | INV-D6 says a mismatch must be recorded as an explicit integrity failure and never folded into a normal failure result. The harness-side record currently looks like any other harness error. | INV-D6 |
| 3 | The hash ignores anything outside `CANONICAL_TABLES`. `pipeline_state()` loops over a fixed table list and reads only `type='table'` DDL. Extra tables, views, triggers and indexes in the DB are never hashed. `test_hash_covers_schema_and_rows` only tests a row update and a column added to an existing table. | If one side has a leftover view or trigger and the other doesn't, the hashes still match. The pre-existing harness DB is the side most exposed. | INV-D6 |
| 4 | The "no second injection path" AST test only finds attribute calls named `inject`. A bare-name call (`from failure_injector import inject; inject(...)`) passes the test. So do other `failure_injector` helpers that change state. `orchestrator.py` still imports `failure_injector`. | The structural guarantee of exactly one source of seeded state can be bypassed without the test noticing. | INV-D6 |
| 5 | The `python -O` test calls `require_parity` directly. It doesn't run the `run_scenario(..., parity_with=...)` path under `-O`. | It doesn't show that the integrity check survives optimized mode in the path the harness actually uses. | INV-D6 |

### Unverified Assumptions
| # | Assumption in code | Basis | Testable within task scope |
|---|--------------------|-------|---------------------------|
| 1 | The caller always passes the same `db_path` to `prepare_naive` and `execute_naive`. | The two values are separate parameters and nothing checks they match. | YES |
| 2 | All injected failure state is inside the `CANONICAL_TABLES` rows and table DDL. | `pipeline_state()` uses a fixed table list and `type='table'` DDL only. | YES |
| 3 | The harnessed side's `get_seed_state(scenario_type, seed)` with no `db_path`, and `state_hash(None)`, both resolve to the DB set by `orchestrator.init`. | They rely on `failure_injector._db_path`. TC-1 covers this only indirectly. | YES (indirectly covered) |
| 4 | `description` doesn't need comparing, because it follows from `(scenario_type, seed)`. | `require_parity` leaves `description` out of its key. | YES |
| 5 | Leaving the throwaway harness ScenarioRun (UNRECOVERED) behind is safe, because the Task 5.3 runner will exclude it from results. | The record calls it a "throwaway harness run". | NO (Task 5.3) |

### Invariant Coverage Gaps
| Invariant | Enforcement point touched | Tested in verification record |
|-----------|--------------------------|-------------------------------|
| INV-D6 (the hash actually covers the DB the naive agent runs on) | YES | NO |
| INV-D6 (a mismatch is recorded as an explicit integrity failure, not as a generic HARNESS_ERROR) | YES | NO (only the status is checked) |
| INV-D6 (hash completeness beyond the canonical tables: views, triggers, extra tables) | YES | NO |
| INV-D6 (single seeding source: bare-name or other injector call paths) | YES | PARTIAL |

### Known Untested Scenarios (out of scope — not findings)
| Scenario | Reason out of scope |
|----------|---------------------|
| `parity_with` is optional, so a harnessed ablation run can skip the comparison entirely. The ablation path must always supply it. | The runner that enforces this is Task 5.3. |
| A pair aborted on mismatch is excluded from results, other pairs carry on, and the report shows an integrity failure. | Ablation runner and report are Task 5.3. |
| Parity on the resume path (`resume_run` / `scripts/resume_scenario.py`) during an ablation pair. | Belongs to the ablation runner and live demo (Task 5.3, Session 6). |
| Real Anthropic API runs with the hash compared. | Needs external state (a live API key). |

### Challenge Verdict

FINDINGS — 4 item(s) require engineer disposition before commit.
  Finding 1: `execute_naive` trusts `seed_state.state_hash` without re-hashing `db_path` right before the agent call. Add a check (raising `AblationIntegrityError` when `state_hash(db_path)` ≠ `seed_state.state_hash`) and a test that passes a mismatched `seed_state`/`db_path` pair, or a DB changed after prepare.
  Finding 2: `test_mismatched_seed_raises_before_the_agent_is_asked` checks only `status == UNRECOVERED`. Nothing checks that the harness-side record (trace `run_complete` reason/detail) identifies an ablation integrity failure rather than a generic `HARNESS_ERROR`.
  Finding 3: Nothing tests that the hash detects extra non-canonical tables, views or triggers. `pipeline_state()` doesn't cover them, so parity can pass while the two DBs differ.
  Finding 4: `test_both_configurations_seed_only_through_the_fixture` only catches `<x>.inject(...)` attribute calls. A bare-name `inject(...)` call or another `failure_injector` state-changing helper would get past it.

**Verdict:** FINDINGS — 4

**Finding dispositions (FINDINGS verdict only):**

*Dispositioned by CC under the engineer's standing instruction (2026-10-04): TEST for findings touching INV-S1/S2/S3/S5/S8/D1/D2 or execute_and_checkpoint atomicity; ACCEPT with a one-line rationale otherwise.*

| Finding # | Disposition | Rationale / Test case added | Test result |
|-----------|-------------|------------------------------|-------------|
| 1 | ACCEPT | INV-D6 is not on the TEST list. The runner (Task 5.3) owns each pair's throwaway naive DB end to end — prepare and execute use the same path, and nothing else touches it in between; logged as an observation (re-hash before execute would harden it) | N/A |
| 2 | ACCEPT | INV-D6 is not on the TEST list. The pair-level record of an integrity failure is the runner's `ablation_integrity_failure` entry (Task 5.3, which the prompt assigns this to); the throwaway harness run's HARNESS_ERROR detail carries the `AblationIntegrityError` repr | N/A |
| 3 | ACCEPT | INV-D6 is not on the TEST list. Every ablation DB is freshly created per run (engineer decision) and injection drops and recreates the three canonical tables, so no stray views, triggers or tables exist to differ; logged as an observation | N/A |
| 4 | ACCEPT | INV-D6 is not on the TEST list. The codebase calls sibling modules by module attribute throughout; the AST test pins that convention for both configurations | N/A |

### Code Review
Invariant text is embedded in the Task 5.2 CC prompt in `docs/EXECUTION_PLAN.md`.
Items to review (results left blank):
- INV-D6: `ablation_fixture.get_seed_state()` is the single source of seeded/injected state for
  BOTH `src/orchestrator.py` and `src/naive_baseline.py` (neither injects any other way).
- The pre-run comparison hashes the initial PipelineState of both configurations and compares
  them before any agent call; a mismatch raises `AblationIntegrityError` explicitly — no
  `assert`, so it survives `python -O`.
- The fixture keeps the naive import graph free of policy_layer / tool_validation / verification.
- CQ-001: single stateable purpose per function; conditional nesting ≤ 2 levels.

### Pre-Commit Declaration

PRE-COMMIT DECLARATION — Task 5.2
-----------------------------------
Files modified:     sessions/SESSION_LOG_S05.md, sessions/VERIFICATION_RECORD_S05.md,
                    src/ablation_fixture.py (new), src/naive_baseline.py, src/orchestrator.py,
                    tests/session5/test_ablation_fixture.py (new)
                    (`git diff --name-only HEAD` after `git add`; all within Claude.md §3)
Functions added:    src/ablation_fixture.py — get_seed_state, state_hash, require_parity
                    (+ SeedState, AblationIntegrityError); src/naive_baseline.py — prepare_naive,
                    execute_naive, _plan_and_apply
Functions modified: src/naive_baseline.py — run_naive (prepare + execute; result carries the
                    hash); src/orchestrator.py — run_scenario (seeds via the fixture; optional
                    `parity_with`; result and run_started trace carry the hash)
Functions deleted:  NONE
Schema changes:     NONE
Config changes:     NONE

Everything above is within the task prompt scope: YES — with the CC choices under
Scope Decisions.

### Scope Decisions
CC implementation choices (not separately specified):
- `get_seed_state(scenario_type, seed, db_path=None)` → `SeedState(scenario_type, seed,
  state_hash, description)`. The hash is the SHA-256 of `failure_injector.pipeline_state()` (the
  schema and rows of every pipeline table) right after injection.
- Pre-run comparison: the runner prepares the naive side first (`prepare_naive`), then starts the
  harnessed run with `parity_with=<naive SeedState>`. The orchestrator compares right after its
  own injection and before any planning, so neither agent is asked anything if the states differ.
  Only then does the naive side execute (`execute_naive`).
- `AblationIntegrityError` is raised explicitly (no `assert`). Inside the orchestrator it
  becomes a HARNESS_ERROR completion of the throwaway harness run, then is re-raised for the
  runner to handle at pair level (Task 5.3).

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
