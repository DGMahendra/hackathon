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
