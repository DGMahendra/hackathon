**Session:** Session 1 — Foundation & Scaffolding
**Date:** 2026-10-02
**Engineer:** 

*Entries for Tasks 1.1 and 1.2 were backfilled after the tasks were completed; Manual
mode was declared after Task 1.2. From Task 1.3 onward, each entry is created before the
task starts.*

## Task 1.1 — Repository Scaffolding

### Test Cases Applied
Source: docs/EXECUTION_PLAN.md Session 1

| Case | Scenario | Expected | UI Tests | Result |
|------|----------|----------|----------|--------|
| TC-1 | Directory structure exists as specified | `src/`, `tests/`, `docs/`, `scripts/`, `data/`, `verification/`, `tools/` present | N/A | PASS — verification command exit 0 (b3939b6) |
| TC-2 | `PROJECT_MANIFEST.md` parses with all required fields present | METHODOLOGY_VERSION, INVARIANT_AUTHORSHIP_MODE: ASSISTED, APPLICATION_SURFACE: BACKGROUND_SERVICE each present | N/A | PASS — each field present exactly once as plain `KEY: VALUE` (b3939b6) |

Verification command: `test -d src && test -d tests && test -d docs && test -d verification && grep -q "APPLICATION_SURFACE: BACKGROUND_SERVICE" PROJECT_MANIFEST.md`
- Before the change: exit 1 — directories absent, and the existing manifest wrote the
  field as `**APPLICATION_SURFACE:** BACKGROUND_SERVICE`, which the grep does not match.
- After the change: exit 0.

### Prediction Statement
Not recorded at the time — backfilled. (CC stated an expected result before each run;
that is not an engineer prediction.)

### Challenge Agent Output
Not run — `tools/challenge.sh` does not exist in this repository.

**Verdict:** NOT RUN

**Untested scenarios:**
Not run.

**Unverified assumptions:**
Not run.

**Invariant coverage gaps:**
Not run.

**Scope boundary observations:**
Not run.

**Finding dispositions (FINDINGS verdict only):**

| Finding # | Disposition | Rationale / Test case added | Test result |
|-----------|-------------|------------------------------|-------------|
|           |             |                              |             |

### Code Review
Not required — Task 1.1 touches no invariant.

### Scope Decisions
- No harness logic implemented (task is structure only).
- Dependency file withheld at commit b3939b6: `Claude.md` v1.0 did not allow a
  repo-root dependency file. Engineer chose `requirements.txt` (not
  `pyproject.toml`); it was permitted by `Claude.md` v1.1 and committed in 35bc09d.
- `.gitkeep` added to each scaffolded directory so empty directories are tracked.

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

## Task 1.2 — SQLite Schema (ScenarioRun, Attempt, TraceEvent, PipelineState)

### Test Cases Applied
Source: docs/EXECUTION_PLAN.md Session 1

| Case | Scenario | Expected | UI Tests | Result |
|------|----------|----------|----------|--------|
| TC-1 | Schema creates on an empty DB | Creates cleanly | N/A | PASS |
| TC-2 | Insert an Attempt with `attempt_number > 3` | Rejected | N/A | PASS |
| TC-3 | Insert a TraceEvent with a non-existent `scenario_run_id` | Rejected | N/A | PASS |
| TC-4 | Insert an Attempt with `policy_decision = 'DENY'` and non-null `verification_result` | Succeeds; `attempts_used` unaffected | N/A | PASS |

Verification command: `python scripts/init_db.py --db data/harness_test.db && python -m pytest tests/session1/test_schema.py -v`

Run history (the test suite also covers each invariant beyond TC-1–TC-4):

| Run | Change | Result | Commit |
|-----|--------|--------|--------|
| 1 | Initial schema | 27/27 passed | 6af742a |
| 2 | `attempt_number` CHECK widened 1–3 → 0–3; 4 tests added | 31/31 passed | 35bc09d |
| 3 | INV-D2 correction — trigger `scenario_run_attempts_used_allow_only` counts ALLOW only; net 5 tests added | 36/36 passed | 35bc09d |
| 4 | Re-run on staged files immediately before commit 35bc09d | 36/36 passed | 35bc09d |

### Prediction Statement
Not recorded at the time — backfilled. (CC stated an expected result before each run;
that is not an engineer prediction.)

### Challenge Agent Output
Not run — `tools/challenge.sh` does not exist in this repository.

**Verdict:** NOT RUN

**Untested scenarios:**
Not run.

**Unverified assumptions:**
Not run.

**Invariant coverage gaps:**
Not run.

**Scope boundary observations:**
Not run.

**Finding dispositions (FINDINGS verdict only):**

| Finding # | Disposition | Rationale / Test case added | Test result |
|-----------|-------------|------------------------------|-------------|
|           |             |                              |             |

### Code Review
Invariant text is embedded in the Task 1.2 CC prompt in `docs/EXECUTION_PLAN.md`.
Items to review, all in `src/schema.sql`:
- INV-D1: `Attempt.attempt_number` CHECK 0–3; `ScenarioRun.max_attempts` CHECK 1–3;
  `ScenarioRun` CHECK `attempts_used BETWEEN 0 AND max_attempts`.
- INV-D2: trigger `scenario_run_attempts_used_allow_only` — blocks any
  `attempts_used` increase above the count of `policy_decision = 'ALLOW'` Attempts.
- INV-D3: `Attempt` row CHECK — `failure_reason IS NOT NULL` iff
  `tool_validation_result = 'REJECTED'` or `verification_result = 'FAIL'`.
- INV-D4: `TraceEvent.scenario_run_id` NOT NULL FK; composite FK
  `(attempt_id, scenario_run_id)` → `Attempt(id, scenario_run_id)`.
- INV-D5: `status` CHECK; triggers `scenario_run_status_initial` and
  `scenario_run_status_terminal`.
- CQ-001: function purpose and nesting depth in `scripts/init_db.py`.

### Scope Decisions
- All invariant checks implemented in SQLite (CHECK constraints and triggers); no
  separate application-layer validation module was needed.
- PipelineState tables are placeholders (`pipeline_bronze`, `pipeline_silver`,
  `pipeline_gold` with `id`, `record`) — real columns decided in Session 3.
- TraceEvent append-only behaviour is not enforced at the DB layer (not in Task 1.2's
  prompt) — see Out of Scope Observations in `sessions/SESSION_LOG_S01.md`.
- Foreign keys are enforced per connection; every connection must run
  `PRAGMA foreign_keys = ON` (`scripts/init_db.py` does; Task 1.3 must).

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

## Task 1.3 — State Manager & Checkpointing

### Test Cases Applied
Source: docs/EXECUTION_PLAN.md Session 1

| Case | Scenario | Expected | UI Tests | Result |
|------|----------|----------|----------|--------|
| TC-1 | Checkpoint call, then `resume()` | Persisted state retrievable via `resume()` | N/A | PASS |
| TC-2 | `kill -9` during a checkpoint write, then reopen the database | File uncorrupted and readable | N/A | PASS |
| TC-3 | `kill -9` injected between `apply_fn` running and the enclosing transaction's commit (stub `apply_fn`, replaced by Task 2.4) | `resume()` reports `action_applied=False`; pipeline mutation rolled back — provably not applied | N/A | PASS |
| TC-4 | `kill -9` injected after the transaction commits | `resume()` reports `action_applied=True`; mutation present | N/A | PASS |
| TC-5 | Every kill test | No state where the pipeline was mutated but `action_applied` is False, or vice versa | N/A | PASS |

*Test cases re-sourced from the revised Task 1.3 in `docs/EXECUTION_PLAN.md` (engineer
revision, 2026-10-02, after the first build and before commit).*

Verification command: `python -m pytest tests/session1/test_state_manager.py -v`
- Run 1 (original Task 1.3 prompt): 26 passed in 7.10s.
- Run 2 (revised Task 1.3 prompt — `execute_and_checkpoint()` writes pre_execute itself;
  kill-after-commit case added): **28 passed in 6.22s (exit 0).**
Kills use `Popen.kill()` (TerminateProcess on Windows, SIGKILL on POSIX). TC-2: 4 kills at
0.05 / 0.2 / 0.5 / 1.0 s into a checkpoint-write loop. TC-3: kill inside the stub
`apply_fn` after its uncommitted pipeline insert. TC-4: kill after
`execute_and_checkpoint()` returns.

### Challenge Agent Output
Command: `./tools/challenge.sh S01 "Task 1.3"` (run after `git add` of the task files;
exit 0). Full output, verbatim:

Running challenge agent for S01 Task 1.3...
## CC Challenge — Task 1.3 — Challenge Agent

**Challenger:** Independent agent — no build session context
**Session:** S01

### Untested Scenarios
| # | Scenario | Why it matters | Invariant at risk |
|---|----------|----------------|-------------------|
| 1 | `apply_fn` commits on the connection it is given (`conn.commit()` or `conn.execute("COMMIT")`), then the process is killed before the post_execute checkpoint is written | The "must not commit" rule appears only in a docstring. Nothing enforces it. A committing `apply_fn` leaves the mutation in the pipeline with `action_applied=False`, which is exactly the combination TC-5 says must never happen | INV-S3, INV-S4 |
| 2 | Policy is recorded as ALLOW in one `checkpoint()` call, and `attempts_used` is raised in a *later* checkpoint (for example at `tool_validation`) | The task prompt says the increment must never come "in a separate, later transaction". `_write_checkpoint` allows it, and no test checks for it | INV-D2 |
| 3 | Two checkpoints for the same ALLOW Attempt raise `attempts_used` (0→1, then 1→2) | One ALLOW decision could spend the budget twice. No test shows whether the trigger or the State Manager blocks this | INV-D2, INV-D1 |
| 4 | Structural check that no module other than `src/state_manager.py` writes to `ScenarioRun` or `Attempt` | The task prompt requires "no direct write path to these tables outside this module". This is only a code-review checklist item; no test asserts it | INV-S3 |
| 5 | Two `execute_and_checkpoint()` calls for the same attempt, run concurrently in separate processes | The second `_require_not_applied` check, inside `BEGIN IMMEDIATE`, is the only thing preventing a double apply. No test exercises that path | INV-S4 |
| 6 | `PRAGMA integrity_check` after the TC-3 and TC-4 kills | Only TC-2 checks that the file is uncorrupted. TC-3's kill happens while uncommitted WAL frames exist, the case most likely to stress recovery, and no integrity check follows it | INV-S3 |
| 7 | `_connect()` cannot get WAL mode (for example `:memory:`, where `journal_mode` returns `memory`) | The `CheckpointError` raise branch never runs in any test | INV-S3 |

### Unverified Assumptions
| # | Assumption in code | Basis | Testable within task scope |
|---|--------------------|-------|---------------------------|
| 1 | TC-2's kills at 0.05, 0.2, 0.5 and 1.0 s land *during* a checkpoint write rather than between transactions | Timing only. Nothing confirms a transaction was open when the kill landed, so the "mid-write" claim is likely but not proven | YES (for example, add a sleep-holding child mid-transaction, as TC-3 does) |
| 2 | `apply_fn` returns a non-None value. If it returns `None`, `execution_result` stays NULL while `action_applied=True` | `post_state["execution_result"] = result` is stored without any check | YES |
| 3 | `action_applied` in `resume()` refers only to the latest Attempt by id. Once a new Attempt is started, an earlier applied attempt shows `action_applied=False` | `_read_latest_attempt` uses `ORDER BY id DESC LIMIT 1` | YES |
| 4 | `action_applied` comes from a JSON flag carried forward in `checkpoint_state`, not from whether a post_execute record exists. A later checkpoint that rewrites `checkpoint_state` is trusted to keep the flag | `_write_attempt` ORs the previous flag. Only one later stage (`verification`) is tested | YES |
| 5 | `start_attempt()` and `start_run()` inserts count as state transitions but write no `checkpoint_state` or stage | No stage is recorded for row creation | YES |

### Invariant Coverage Gaps
| Invariant | Enforcement point touched | Tested in verification record |
|-----------|--------------------------|-------------------------------|
| INV-S3 (no write path to ScenarioRun/Attempt outside the module) | YES | NO |
| INV-S3 (atomicity when `apply_fn` commits on its own) | YES | NO |
| INV-S3 (WAL-failure branch) | YES | NO |
| INV-D2 (increment in the same transaction as the ALLOW decision; one increment per ALLOW) | YES | NO |
| INV-S4 (guard under a concurrent double invocation) | YES | NO |

### Known Untested Scenarios (out of scope — not findings)
| Scenario | Reason out of scope |
|----------|---------------------|
| `execute_and_checkpoint()` called for an Attempt with `policy_decision` DENY, REQUIRE_APPROVAL, or NULL (it does not check the decision) | Funnel enforcement belongs to Task 2.4 (INV-S1, INV-S2) |
| `apply_fn` writing to `ScenarioRun`, `Attempt` or `TraceEvent` | Pipeline-write scope restriction belongs to the Execute task (INV-S8) |
| `run_complete` writing RECOVERED without a PASS result, or a terminal status changing afterwards | Verification and status-machine tasks (INV-S5, INV-D5) |
| `start_run()` while another run is IN_PROGRESS | ScenarioRun lifecycle task (INV-S7) |
| Kill tests using real POSIX SIGKILL | The recorded run used Windows `TerminateProcess` only; this needs a different environment |
| Power loss or OS crash durability (`synchronous=FULL`) | Needs external hardware or OS fault injection |
| The verification record given to this agent stops at the "Pre-Commit Declaration" heading | Caused by the 60-line truncation in `tools/challenge.sh`, already logged as FRAGILITY |

### Challenge Verdict

FINDINGS — 6 item(s) require engineer disposition before commit.
  Finding 1: `execute_and_checkpoint()` does not stop `apply_fn` from committing early. A committing `apply_fn` followed by a kill gives "mutated but `action_applied=False`", and no test covers it. Either guard against it (for example, check `conn.in_transaction` after `apply_fn` returns, or wrap the connection) and add a kill test, or accept it with a rationale.
  Finding 2: There is no structural test that `ScenarioRun` and `Attempt` are written only by `src/state_manager.py`, which the task prompt requires.
  Finding 3: INV-D2 ordering is tested only for the single-checkpoint case. Two cases are untested: an increment in a later checkpoint after an earlier ALLOW checkpoint, and a second increment on the same ALLOW Attempt.
  Finding 4: TC-3 and TC-4 do not run `PRAGMA integrity_check` after their kills. TC-2 does not confirm that its kill landed inside an open transaction.
  Finding 5: The INV-S4 re-check inside the second transaction is never tested under concurrent `execute_and_checkpoint()` calls on the same attempt.
  Finding 6: The `_connect()` branch that raises when WAL mode cannot be set is never tested, and nothing validates an `apply_fn` that returns `None` (which gives `action_applied=True` with a NULL `execution_result`).

**Verdict:** FINDINGS — 6

**Untested scenarios:**
See challenge output above (7 rows).

**Unverified assumptions:**
See challenge output above (5 rows).

**Invariant coverage gaps:**
See challenge output above — INV-S3 (x3), INV-D2, INV-S4.

**Scope boundary observations:**
NONE raised by the challenge agent. Its evidence package stopped at the record's
"Pre-Commit Declaration" heading (60-line truncation — logged FRAGILITY).

**Finding dispositions (FINDINGS verdict only):**

*Dispositions set by the engineer (2026-10-04, resume after interruption); applied by CC.*

| Finding # | Disposition | Rationale / Test case added | Test result |
|-----------|-------------|------------------------------|-------------|
| 1 | TEST (revised 2026-10-04 — prevention, engineer option (a)) | First attempt (detection only): `conn.in_transaction` guard after `apply_fn` → **FAIL**: the guard raised, but the early commit had already landed (pipeline mutated, `action_applied=False`). Revised: `_transaction_control_denied` installs a SQLite authorizer immediately before `apply_fn` that denies `SQLITE_TRANSACTION` / `SQLITE_SAVEPOINT`, and removes it in a `finally` before `execute_and_checkpoint()` commits or rolls back; the `in_transaction` guard stays as a backstop. Tests: `test_apply_fn_transaction_control_refused` (7 cases: `commit()`, `execute` COMMIT / ROLLBACK / BEGIN / SAVEPOINT / RELEASE, `executescript("COMMIT")`) — refused, pipeline clean, `action_applied=False`; `test_refused_transaction_control_cannot_split_the_transaction` (same 7 cases, refusal swallowed by `apply_fn`) — mutation and post_execute still commit together; `test_apply_fn_write_through_second_connection_fails` (busy_timeout 100 ms) — "database is locked", nothing committed; `test_authorizer_removed_before_commit_on_success` and `test_authorizer_removed_before_rollback_on_exception` — the connection's event order is install → remove → COMMIT / ROLLBACK; `test_in_transaction_backstop_rejects_closed_transaction` | PASS |
| 2 | TEST | `test_no_write_path_to_run_or_attempt_outside_state_manager` scans `src/` and `scripts/` (`.py`, `.sql`) for INSERT / REPLACE / UPDATE / DELETE on `ScenarioRun` or `Attempt` outside `src/state_manager.py`; `test_write_scan_detects_guarded_table_writes` (5 cases) confirms the pattern catches writes | PASS |
| 3 | TEST | `_require_increment_with_allow`: an `attempts_used` increase is accepted only in the checkpoint that first records `policy_decision=ALLOW` for that attempt. Tests: `test_increment_in_later_checkpoint_than_allow_rejected`, `test_rerecording_allow_to_increment_again_rejected`, `test_double_increment_of_one_allow_attempt_blocked`, `test_run_level_checkpoint_cannot_increment`, `test_non_allow_cannot_increment` (DENY, REQUIRE_APPROVAL) | PASS |
| 4 | TEST | `PRAGMA integrity_check` added after the TC-3 and TC-4 kills. New `test_kill_confirmed_mid_transaction_leaves_db_uncorrupted`: the child holds the checkpoint transaction open after its Attempt write; the parent confirms the write lock is held (`BEGIN IMMEDIATE` with `timeout=0` reports "locked") before the kill, then checks integrity, that the lock is released, and that the in-flight write rolled back. The four timing-based kills are kept as stress cases | PASS |
| 5 | ACCEPT | Engineer: INV-S7 (built in Task 4.3) makes concurrent `execute_and_checkpoint()` calls against the same `scenario_run_id` structurally impossible by design. A timing-based two-process test would add flakiness risk for a scenario the architecture already rules out elsewhere | N/A |
| 6 | TEST | `test_wal_unavailable_raises` (`:memory:` database, so `journal_mode` reports `memory`) covers the WAL-failure branch. New rule: `apply_fn` must not return None; `_require_apply_contract` raises `CheckpointError` (INV-D4) and the transaction rolls back. Test `test_apply_fn_returning_none_rejected` | PASS |

Verification command (run 3, after dispositions): `python -m pytest tests/session1/test_state_manager.py -v`
→ **1 failed, 42 passed** (exit 1). `tests/session1/` as a whole: 1 failed, 91 passed.
Run 4 (after Finding 1 was re-dispositioned to authorizer prevention): **60 passed** (exit 0).
Run 5 (against `docs/EXECUTION_PLAN.md` at dfc8f17, immediately before the challenge re-run): **60 passed** (exit 0).

#### Challenge Agent Output — run 2 (2026-10-04)
Command: `./tools/challenge.sh S01 "Task 1.3"` (Task 1.3 files staged; exit 0). Full output, verbatim:

Running challenge agent for S01 Task 1.3...
## CC Challenge — Task 1.3 — Challenge Agent

**Challenger:** Independent agent — no build session context
**Session:** S01

### Untested Scenarios
| # | Scenario | Why it matters | Invariant at risk |
|---|----------|----------------|-------------------|
| 1 | `apply_fn` calls `conn.set_authorizer(None)` (or installs its own allow-all authorizer) on the connection it is given, then runs `conn.commit()`, and the process is killed before the post_execute write | `_transaction_control_denied` protects the transaction only while its own authorizer is installed. `apply_fn` has the connection, so it can remove the authorizer. The early commit then lands. The `in_transaction` backstop raises only after the mutation has been committed. The result is the TC-5 forbidden state (pipeline mutated, `action_applied=False`). None of the 7 `TRANSACTION_CONTROL_ATTEMPTS` cases cover this | INV-S3, INV-S4 |
| 2 | A checkpoint *decreases* `attempts_used` (for example 1 → 0) for any stage | `_require_increment_with_allow` returns early whenever the new value is `<=` the current one, so a decrease is accepted without any check. Lowering `attempts_used` gives budget back and allows more than 3 ALLOW attempts. No test shows whether the State Manager or the Task 1.2 trigger rejects it | INV-D1, INV-D2 |
| 3 | One ALLOW checkpoint raises `attempts_used` by more than 1 (0 → 2), or past `max_attempts` (for example a 4th ALLOW to 4) | The guard checks that the value went up, not that it went up by exactly 1 or stayed within the cap. Every test increments by exactly 1, starting from 0 | INV-D1, INV-D2 |
| 4 | Run-level checkpoint (`run_started` / `run_complete`) that passes Attempt fields (for example `verification_result`, `failure_reason`) but no `attempt_id` | `_validate_checkpoint` accepts these keys. `_write_checkpoint` then skips `_write_attempt`, so the fields are silently dropped while the checkpoint reports success. That is lost state with no error | INV-S3 |
| 5 | Write-path scan with a schema-qualified or quoted-and-qualified target (`INSERT INTO main.Attempt`, `UPDATE main."ScenarioRun"`), and with writers outside `src/` and `scripts/` (`tools/`, `tests/` helpers) | `WRITE_TO_GUARDED_TABLE` requires the table name right after the verb, with an optional quote. A `main.` prefix is not matched. The scan covers only two directories. `test_write_scan_detects_guarded_table_writes` has no qualified-name case, so the "no write path outside this module" claim is only partly checked | INV-S3 |

### Unverified Assumptions
| # | Assumption in code | Basis | Testable within task scope |
|---|--------------------|-------|---------------------------|
| 1 | `PRAGMA synchronous = FULL` and `PRAGMA foreign_keys = ON` are in effect on every State Manager connection | `_connect()` sets both. No test asserts either one; only `journal_mode` is asserted | YES |
| 2 | The `state` argument of `execute_and_checkpoint()` contains only post_execute-appropriate keys | `post_state` accepts any `STATE_KEYS`, including `status` (for example RECOVERED), `policy_decision` and `failure_reason`. All of them are written in the post_execute checkpoint. Only `execution_result` is overridden | YES |
| 3 | `apply_fn` never uses `conn.executescript(...)` for an ordinary mutation | Under legacy transaction control, `executescript` issues an implicit COMMIT first, which the authorizer denies. Any `executescript`-based `apply_fn` (Task 2.4) would therefore always fail. This contract is not stated, and only `executescript("COMMIT")` is tested | YES |
| 4 | `action_applied` in `resume()` describes only the latest Attempt (`ORDER BY id DESC LIMIT 1`). After `start_attempt()`, an earlier applied attempt shows as not applied | Raised previously as an assumption; it has no disposition or test in the record | YES |
| 5 | Creating a row with `start_run()` or `start_attempt()` is not a checkpointed stage transition. These calls write no `checkpoint_state` or stage | Raised previously; still not addressed in tests or the record | YES |

### Invariant Coverage Gaps
| Invariant | Enforcement point touched | Tested in verification record |
|-----------|--------------------------|-------------------------------|
| INV-S3 / INV-S4 (atomicity when `apply_fn` removes or replaces the authorizer) | YES | NO |
| INV-D1 / INV-D2 (`attempts_used` decrease, increment > 1, increment past `max_attempts`) | YES | NO |
| INV-S3 (attempt fields silently dropped on run-level checkpoints) | YES | NO |
| INV-S3 (write-path scan completeness: qualified names, directories scanned) | YES | NO |

### Known Untested Scenarios (out of scope — not findings)
| Scenario | Reason out of scope |
|----------|---------------------|
| Concurrent `execute_and_checkpoint()` on the same attempt from two processes | Finding 5 was ACCEPTed by the engineer. INV-S7 is built in Task 4.3 |
| `execute_and_checkpoint()` for an Attempt whose policy is DENY, REQUIRE_APPROVAL, or NULL, or which has not passed tool validation | Funnel enforcement belongs to Task 2.4 (INV-S1, INV-S2) |
| `apply_fn` writing to `ScenarioRun`, `Attempt` or `TraceEvent` (an authorizer table-scope check) | Belongs to the Task 2.4 Execute write scope (INV-S8); logged as MISSING |
| RECOVERED without a PASS verification, or a terminal status changing afterwards | Belongs to the Verification and status-machine tasks (INV-S5, INV-D5) |
| `start_run()` while another run is IN_PROGRESS | ScenarioRun lifecycle task (INV-S7) |
| Kill tests with real POSIX SIGKILL; power loss or OS crash durability | Needs a different environment or hardware fault injection |
| Verification record seen by this agent stops partway through the previous challenge output | Caused by the `tools/challenge.sh` 60-line truncation, already logged as FRAGILITY |

### Challenge Verdict

FINDINGS — 4 item(s) require engineer disposition before commit.
  Finding 1: The authorizer can be bypassed by `apply_fn` itself. A test where `apply_fn` calls `conn.set_authorizer(None)` (or replaces the authorizer), commits, and is then killed or raises would show the pipeline mutated while `action_applied=False`. Either take the raw connection out of `apply_fn`'s reach (for example pass a wrapper that does not expose `set_authorizer`, `commit` or `close`) and test that, or ACCEPT with a rationale based on the trust model for `apply_fn`.
  Finding 2: `_require_increment_with_allow` accepts any `attempts_used` decrease, and any increase of any size, including past `max_attempts`. No test covers decrement, a jump of more than 1, or going over the cap (INV-D1, INV-D2).
  Finding 3: Run-level checkpoints silently drop Attempt fields when `attempt_id` is absent, and the post_execute `state` of `execute_and_checkpoint()` accepts arbitrary run and attempt keys (including `status`). Neither path is rejected or tested.
  Finding 4: `WRITE_TO_GUARDED_TABLE` does not detect schema-qualified targets (`main.Attempt`), and the scan covers only `src/` and `scripts/`. The structural "no write path outside `src/state_manager.py`" test has gaps that no detector case covers.

**Verdict (run 2):** FINDINGS — 4

CC probe of the run 2 findings (throwaway database outside the repo, no code changed),
recorded as evidence for disposition:
- Finding 1: an `apply_fn` that calls `conn.set_authorizer(None)` then `conn.commit()` →
  the `in_transaction` backstop raises, but the pipeline row stays committed with
  `action_applied=False`. Confirmed.
- Finding 2: a decrease of `attempts_used` (1 → 0) is ACCEPTED. A jump of 0 → 2 on one ALLOW
  is rejected by the Task 1.2 INV-D2 trigger. A 4th ALLOW to 4 is rejected by the
  `attempts_used BETWEEN 0 AND max_attempts` CHECK. The State Manager guard itself
  covers neither, and there are no tests.
- Finding 3: `run_complete` with `verification_result` and no `attempt_id` is ACCEPTED, and
  the field is silently dropped (stored value stays NULL). Confirmed.
- Finding 4: not probed. The regex requires the table name directly after the verb, so a
  `main.` prefix is not matched.

**Finding dispositions — run 2 (FINDINGS verdict only):**

*Dispositions set by the engineer (2026-10-04): Findings 1–2 in one message, Findings 3–4 in the next. The engineer directed that the Challenge Agent not be run a third time: all findings are dispositioned, which is what the methodology requires.*

| Finding # | Disposition | Rationale / Test case added | Test result |
|-----------|-------------|------------------------------|-------------|
| 1 | ACCEPT | Engineer: `apply_fn` is harness-authored code, never agent-supplied, so the authorizer plus the `in_transaction` backstop guard against mistakes, not malice. A narrow wrapper would not close the gap, because `cursor.connection` exposes the raw connection regardless. Observation logged for Tasks 2.2/2.4 | N/A |
| 2 | TEST | `_is_single_step_increase` in `src/state_manager.py`: rejects any decrease of `attempts_used` (INV-D1) and any increase other than exactly +1 (INV-D2). `src/schema.sql` not modified. Tests: `test_attempts_used_decrease_rejected`, `test_attempts_used_jump_greater_than_one_rejected`, `test_attempts_used_unchanged_value_accepted`, `test_attempts_used_cannot_exceed_cap` (a 4th ALLOW to 4 is rejected by the schema CHECK; state unchanged) | PASS — 67 passed |
| 3 | TEST | (a) `STAGE_FIELDS` in `src/state_manager.py` maps each stage to the fields it may write. It replaces the plain `STAGES` tuple (now derived from it) and the global `STATE_KEYS` check, so there is no parallel mechanism. Any field outside the stage's allowlist raises `CheckpointError`, so nothing is silently dropped. run_started / run_complete accept no attempt-level field. Status is written only by run_complete, verification_result only by verification (INV-S5), and attempts_used only by policy. (b) `execute_and_checkpoint()` validates `state` against the post_execute allowlist (`execution_result` only) before anything is written, so it rejects status, verification_result, policy_decision, tool_validation_result, attempts_used and failure_reason. Tests: `test_run_level_stage_rejects_attempt_fields` (7 fields × 2 stages, with and without attempt_id), `test_attempt_fields_without_attempt_id_rejected` (7), `test_stage_rejects_fields_outside_its_allowlist` (every stage × every field it does not own), `test_execute_and_checkpoint_rejects_non_execution_fields` (6), `test_attempts_used_written_only_by_policy` (5 stages). Every rejection asserts a full-table snapshot is unchanged. Flows that must still work: `test_full_attempt_flow_within_stage_allowlists`, `test_failed_attempt_flow_records_failure_reason` | PASS |
| 4 | TEST | `WRITE_TO_GUARDED_TABLE` now matches case-insensitively, with an optional schema prefix (`main.`, `"main".`, `[main].`, `` `main`. ``, spaces around the dot), and quoting `"x"`, `'x'`, `` `x` ``, `[x]`. The scan covers `src/`, `scripts/`, `tools/` and `verification/` (`.py`, `.sql`, `.sh`); `tests/` is excluded deliberately. The docstring states that dynamically built SQL cannot be caught statically and that the runtime guard (INV-S8, Task 2.4) is the real boundary. Tests: `test_write_scan_detects_guarded_table_writes` (20 variants), `test_write_scan_ignores_non_writes_and_other_tables` (6, including triggers and `AttemptLog`), `test_write_scan_sees_the_state_manager_itself`. Mutation check: a planted `tools/zz_mutation_probe.py` containing `UPDATE [main].[Attempt]` made the repo scan fail; the probe was then removed | PASS |

Two existing tests were rewritten because the new INV-D2 guard rejects these writes before
the DB trigger runs: `test_require_approval_cannot_increment` became
`test_non_allow_cannot_increment` (it now expects `CheckpointError`), and
`test_failed_checkpoint_rolls_back_whole_write` now fails the write on the ScenarioRun
status CHECK, so it still covers a rollback after a partial write.

Test changes forced by Finding 3's per-stage allowlist (all intents kept):
- `test_failed_checkpoint_rolls_back_whole_write`: the partial write is now a `run_complete`
  with `status='BOGUS'` (the Attempt `checkpoint_state` update succeeds, then the ScenarioRun
  CHECK aborts).
- INV-D2 later-checkpoint and run-level increment tests now expect the allowlist error
  (`attempts_used` is accepted only at policy).
- Decrease and unchanged-value tests use a second Attempt at the policy stage.
  `test_run_level_decrease_rejected` was folded into `test_attempts_used_written_only_by_policy`.
- TC-2 kill tests checkpoint `plan` only. The old `plan == execution_result` torn-write check
  compared two columns of one UPDATE statement, so it proved little. The loop test now
  asserts the stored plan is a complete committed value. Cross-statement rollback is covered
  by `test_kill_confirmed_mid_transaction_leaves_db_uncorrupted` (Attempt write done, run write
  pending at kill → Attempt rolled back).

Run 6 (after TEST 3 and TEST 4): `python -m pytest tests/session1/test_state_manager.py -v`
→ **160 passed** (exit 0). `tests/session1/`: 209 passed.

### Code Review
Invariant text is embedded in the Task 1.3 CC prompt in `docs/EXECUTION_PLAN.md`.
Items to review in `src/state_manager.py` (results left blank):
- INV-S3: every stage transition calls `checkpoint()` before proceeding; Execute is
  checkpointed only via `execute_and_checkpoint()` (pre_execute committed alone, then
  apply_fn + post_execute in one transaction); `checkpoint()` rejects both execute stages.
- INV-S3: each checkpoint write runs inside a single SQLite transaction; the connection
  is opened in WAL mode.
- INV-S4: `resume()` reads persisted checkpoint state only and never re-invokes an
  action already marked applied.
- INV-D2 write ordering: `policy_decision` recorded before `attempts_used` is increased,
  in the same transaction.
- No write path to `ScenarioRun` / `Attempt` outside `src/state_manager.py`.
- CQ-001: single stateable purpose per function; conditional nesting ≤ 2 levels.

### Pre-Commit Declaration

PRE-COMMIT DECLARATION — Task 1.3
-----------------------------------
Files modified:     sessions/SESSION_LOG_S01.md, sessions/VERIFICATION_RECORD_S01.md,
                    src/state_manager.py (new), tests/session1/test_state_manager.py (new)
                    (`git diff --name-only HEAD` after `git add`; all within Claude.md §3)
Functions added:    src/state_manager.py — init, start_run, start_attempt, checkpoint,
                    execute_and_checkpoint, resume, _connect, _transaction,
                    _validate_checkpoint, _transaction_control_denied,
                    _deny_transaction_control, _require_apply_contract, _write_checkpoint,
                    _require_increment_with_allow, _is_single_step_increase, _write_attempt,
                    _write_run, _sync_attempt_number, _require_not_applied, _read_run,
                    _read_attempt, _read_latest_attempt, _checkpoint_progress, _attempt_view
Functions modified: NONE
Functions deleted:  NONE
Schema changes:     NONE
Config changes:     NONE

Everything above is within the task prompt scope: YES — with the engineer-approved
design decisions under Scope Decisions.

### Scope Decisions
Engineer-approved (2026-10-02, before build):
- `init(db_path)` sets the database file; `checkpoint()` / `resume()` keep the
  prompt's signatures. Default `data/harness.db`.
- `start_run(scenario_type)` and `start_attempt(scenario_run_id)` added so that
  ScenarioRun/Attempt rows have no write path outside `src/state_manager.py`.
- Fixed stage allowlist: run_started, plan, policy, tool_validation, pre_execute,
  post_execute, verification, run_complete.
- `execute_and_checkpoint()` commits the pipeline mutation and the post_execute
  checkpoint in one transaction (adopted into the revised Task 1.3 prompt, which also
  has it commit the pre_execute checkpoint first as its own transaction).
  `checkpoint()` rejects both pre_execute and post_execute, per the revised prompt
  ("Execute's checkpointing is NOT two separate checkpoint() calls").
CC implementation choices (not separately specified):
- A checkpoint that changes `attempts_used` also sets that attempt's `attempt_number`
  to the new value (docs/ARCHITECTURE.md §8).
- run_started / run_complete may omit `attempt_id`; every other stage requires it.
- Per-stage field allowlist (`STAGE_FIELDS`, Challenge run 2 Finding 3 — engineer TEST): a
  field the stage does not own is rejected.
- `execute_and_checkpoint()`'s `state` argument is written with the post_execute
  checkpoint and may carry only post_execute fields; the pre_execute checkpoint records
  only `attempt_id`.
Engineer-directed after Challenge run 1 / run 2 (2026-10-04):
- While `apply_fn` runs, a SQLite authorizer denies transaction control;
  `conn.in_transaction` is the backstop; `apply_fn` must not return None.
- `attempts_used` changes only at policy, by exactly +1, together with the first recording
  of policy_decision=ALLOW; it never decreases.
- `execute_and_checkpoint()` refuses (INV-S4) if the action is already applied, checked
  before both transactions.

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

## Task 1.4 — Trace Logger

### Test Cases Applied
Source: docs/EXECUTION_PLAN.md Session 1

| Case | Scenario | Expected | UI Tests | Result |
|------|----------|----------|----------|--------|
| TC-1 | Valid event emitted | Exactly one parseable JSON line written | N/A | PASS |
| TC-2 | Event with a non-existent `scenario_run_id` | Rejected before anything is written | N/A | PASS |
| TC-3 | 100 sequential emits | 100 valid, independently parseable lines | N/A | PASS |

Verification command: `python scripts/emit_test_trace.py | python -m json.tool && python -m pytest tests/session1/test_trace_logger.py -v`
- Run 1 (pytest only, before the command): 26 passed, 1 failed —
  `test_nonexistent_scenario_run_rejected_before_writing["1"]`: the string `"1"` was accepted
  as a run id, because SQLite matches it to integer id 1, and the trace would have recorded
  `"scenario_run_id": "1"`. Fixed: ids must be `int` (bool excluded); covered by
  `test_non_integer_ids_rejected_before_writing` (6 cases).
- Run 2: `emit_test_trace.py` output parsed by `json.tool`; **31 passed** (exit 0).
  `tests/session1/`: 240 passed.
- Run 3 (after the Finding 1–4 dispositions): `json.tool` exit 0; **48 passed** (exit 0).
  `tests/session1/`: 257 passed.
Beyond TC-1 to TC-3, the tests cover: each allowed event_type; a run-level event with
attempt_id None; the file is JSONL, not an array; newlines inside the payload stay on one
line; appends preserve existing lines; emit never writes the database; an attempt that does
not exist, or belongs to another run, is rejected; unknown event_type; payloads that cannot
be serialized, plus NaN and Infinity; a missing database is rejected and not created; an
existing trace is unchanged after a rejection; the script prints exactly one line and leaves
`data/trace.jsonl` untouched.

### Challenge Agent Output
Command: `./tools/challenge.sh S01 "Task 1.4"` (Task 1.4 files staged; exit 0). The diff it saw
also included `dataops-agent-pbvi-artifacts.zip` as a binary change. That change was made
outside this task and is unstaged. Full output, verbatim:

Running challenge agent for S01 Task 1.4...
## CC Challenge — Task 1.4 — Challenge Agent

**Challenger:** Independent agent — no build session context
**Session:** S01

### Untested Scenarios
| # | Scenario | Why it matters | Invariant at risk |
|---|----------|----------------|-------------------|
| 1 | The trace file already exists but doesn't end in `\n`, for example after a kill or partial write during an earlier `_append_line`. `open(..., "ab")` then attaches the next record to the broken line. | One line comes out unparseable, and it swallows a valid event. That breaks the "each line valid standalone JSON" requirement. The kill-and-restart demo is the most likely way to hit it. No test seeds a broken last line. | INV-D4 (event becomes unreadable, so it can't be attributed) |
| 2 | An integer id outside SQLite's 64-bit range (e.g. `2**63`) passes `_is_id`, then makes `conn.execute` raise `OverflowError`. That is not caught, so it doesn't come back as `TraceError`. | Nothing is written, but the rejection contract ("invalid event → TraceError") is broken and untested. Callers in later gates that catch `TraceError` won't handle it. | NONE (nothing written) |
| 3 | The database file exists but isn't a SQLite database (corrupt or garbage). This raises `sqlite3.DatabaseError`, which `except sqlite3.OperationalError` doesn't catch. Also untested: a valid SQLite file with no harness tables (raises "no such table", which is caught). | Same as #2: the error escapes outside `TraceError`. Only a *missing* database is tested. | NONE |
| 4 | `tool_call` or `policy_decision` events sent with `attempt_id=None` are accepted. The 100-emit test does this on purpose for half its events. | INV-D4 says "attempt_id when applicable". Nothing defines or enforces which event types need an attempt, so attempt-scoped events can be traced without the attempt they belong to. | INV-D4 |
| 5 | A rejection when the trace file already exists and the database is missing or unreadable. Only the case with no trace file is tested. | It's minor, but "existing trace unchanged after rejection" is only checked for the unknown-run path. | INV-D4 |

### Unverified Assumptions
| # | Assumption in code | Basis | Testable within task scope |
|---|--------------------|-------|---------------------------|
| 1 | `f"file:{_db_path.as_posix()}?mode=ro"` is a valid SQLite URI for any path. A path containing `?`, `#` or `%` gets misread: the query or fragment starts too early, or `%xx` is decoded. The database opened then isn't the configured one, or the read fails. | `_read_references` builds the URI with no percent-encoding. Tests only use `tmp_path`. | YES |
| 2 | A read-only connection can always read the WAL-mode database while the State Manager's connection is open. This needs the `-shm`/`-wal` sidecar files to be reachable. | `mode=ro` together with INV-S3 WAL mode. Only covered indirectly, through fixtures where the database is writable. | YES (partially) |
| 3 | A single `write()` of one line is never partially applied. `fsync` comes after the write, but nothing recovers from a short write or an error between write and fsync. | `_append_line` | NO (needs fault injection or a real kill) |
| 4 | `payload` can be any JSON-serializable value: a list, string, `None` or a number, not just a dict. All tests use dicts, and no type contract is stated. | `emit` signature and `_serialize` | YES |

### Invariant Coverage Gaps
| Invariant | Enforcement point touched | Tested in verification record |
|-----------|--------------------------|-------------------------------|
| INV-D4: the stated enforcement point is "Foreign-key constraint or referential integrity check on TraceEvent writes". No TraceEvent row is ever written, so the database's foreign keys are never used and only the application-level check applies. (Already logged as MISSING in the Session Log; listed here for traceability.) | NO (DB-level) / YES (app-level) | YES (app-level only) |
| INV-D4: "attempt_id when applicable" | NO (no rule for when it applies) | NO |

### Known Untested Scenarios (out of scope — not findings)
| Scenario | Reason out of scope |
|----------|---------------------|
| Atomicity between a State Manager checkpoint and its matching `emit`: a rolled-back transition gets traced, or a kill between checkpoint and emit leaves it untraced | Already logged as FRAGILITY; needs gate integration (Session 2) |
| Kill during write or fsync in a real process | Needs external process control or fault injection (the kill-and-restart demo, later session) |
| Concurrent emitters appending to the same trace file | Concurrent execution is excluded by Claude.md §1 and INV-S7 |
| Disk full or permission denied on `data/trace.jsonl` | Needs external filesystem state |
| Judges reading the trace in practice | Human |

### Challenge Verdict

FINDINGS — 4 item(s) require engineer disposition before commit.
- **Finding 1:** If the trace file doesn't end in `\n` (left over from an earlier partial write or kill), the next `emit()` attaches its record to the broken line. That leaves an unparseable line and loses a valid event. Untested. Testable by writing a truncated line into the trace file, calling emit, and asserting every line parses. Possible fix: check or repair the trailing newline before appending.
- **Finding 2:** Some invalid events raise errors other than `TraceError`, and these paths are untested. Out-of-range integer ids (`2**63`) raise `OverflowError`. A corrupt or non-SQLite database file raises `sqlite3.DatabaseError`, which `except sqlite3.OperationalError` doesn't catch. A database with no schema (caught) is also untested.
- **Finding 3:** INV-D4's "attempt_id when applicable" is neither defined nor enforced. Attempt-scoped `tool_call` and `policy_decision` events are accepted with `attempt_id=None`, and the tests rely on that. The engineer needs to decide the applicability rule, or accept and record that every event type may be run-level.
- **Finding 4:** The read-only URI is built from the raw path without percent-encoding. A database path containing `?`, `#` or `%` makes the logger read the wrong file or fail. Untested; testable with a `tmp_path` subdirectory whose name contains those characters.

**Verdict:** FINDINGS — 4

CC probe (throwaway files outside the repo, no code changed):
- Finding 1: the trace file was seeded with a truncated line `{"partial": tru` and no newline;
  after `emit()` the file holds one line, and it does not parse. The valid event is lost.
  Confirmed.
- Finding 2: id `2**63` → `OverflowError`; a garbage database file → `sqlite3.DatabaseError`.
  Neither comes back as `TraceError`. Nothing is written in either case. Confirmed.
- Finding 3: by design in the current build (run-level events may omit attempt_id); the
  applicability rule is undefined in the task prompt and INV-D4.
- Finding 4: valid databases in directories named `hash#dir` and `pct%41dir` → `TraceError`
  "cannot read database". Valid events are rejected. Confirmed. (`?` cannot be tested on
  Windows file names.)

**Untested scenarios:**
See challenge output above (5 rows).

**Unverified assumptions:**
See challenge output above (4 rows).

**Invariant coverage gaps:**
See challenge output above — INV-D4 (DB-level FK unused; "when applicable" undefined).

**Scope boundary observations:**
NONE raised by the challenge agent.

**Finding dispositions (FINDINGS verdict only):**

*Dispositions set by the engineer (2026-10-04). The engineer directed that the Challenge Agent not be run again after they were applied.*

| Finding # | Disposition | Rationale / Test case added | Test result |
|-----------|-------------|------------------------------|-------------|
| 1 | TEST | `_separator_for_partial_line`: if the trace file is non-empty and its last byte is not `
`, a newline goes in the same write as the new line. The partial line is never deleted or altered. Tests: `test_truncated_final_line_is_isolated_and_new_event_parses` (the fragment is kept byte-for-byte on its own line; the events before and after both parse), `test_no_separator_added_when_file_ends_cleanly`, `test_empty_existing_file_gets_no_leading_newline`. Mutation check: dropping the separator fails the truncated-line test | PASS |
| 2 | TEST | `_read_references` turns `sqlite3.DatabaseError` (which includes OperationalError) and `OverflowError` into `TraceError`, chained with `from`, never swallowed. Tests: `test_out_of_range_id_raises_trace_error_from_overflow` (2**63 as run id, 2**63 as attempt id, -(2**63)-1), `test_corrupt_database_raises_trace_error_from_database_error`, `test_database_without_harness_tables_raises_trace_error`, and the missing-database test now asserts the cause. Each asserts `__cause__` type and that no trace file was written | PASS |
| 3 | TEST | Engineer rule: tool_call and policy_decision require attempt_id; state_transition may omit it (run_started / run_complete are run-level). `ATTEMPT_REQUIRED_EVENT_TYPES` in `src/trace_logger.py`. Tests: `test_every_event_type_accepted_with_attempt_id` (3), `test_attempt_scoped_event_without_attempt_id_rejected` (2), `test_run_level_event_without_attempt_accepted` (state_transition), `test_attempt_rule_covers_exactly_tool_call_and_policy_decision`, and `test_attempt_from_another_run_rejected` now runs for all 3 event types. The 100-emit test now uses attempt_id None only for state_transition | PASS |
| 4 | TEST | The read-only URI is built with `Path(db).resolve().as_uri() + "?mode=ro"` (uri=True), not string concatenation. Test: `test_database_path_with_hash_percent_and_space`: directory `run #1 100% done %41`, database `harness db.db`, Windows path. Emit succeeds; validation provably reads that database (an unknown id is rejected); no stray file. Mutation check: the old concatenated URI fails this test | PASS |

### Code Review
Invariant text is embedded in the Task 1.4 CC prompt in `docs/EXECUTION_PLAN.md`.
Items to review in `src/trace_logger.py` (results left blank):
- INV-D4: `scenario_run_id` (and `attempt_id` when given, belonging to that run) is
  validated against the database before any byte is written to the trace file.
- `event_type` restricted to tool_call, state_transition, policy_decision.
- Each emit writes exactly one standalone JSON line (no wrapping array).
- CQ-001: single stateable purpose per function; conditional nesting ≤ 2 levels.

### Pre-Commit Declaration

PRE-COMMIT DECLARATION — Task 1.4
-----------------------------------
Files modified:     sessions/SESSION_LOG_S01.md, sessions/VERIFICATION_RECORD_S01.md,
                    src/trace_logger.py (new), scripts/emit_test_trace.py (new),
                    tests/session1/test_trace_logger.py (new)
                    (`git diff --name-only HEAD` after `git add`; all within Claude.md §3)
Functions added:    src/trace_logger.py — init, emit, _validate_references, _is_id,
                    _read_references, _serialize, _append_line,
                    _separator_for_partial_line;
                    scripts/emit_test_trace.py — emit_synthetic_event, main
Functions modified: NONE
Functions deleted:  NONE
Schema changes:     NONE
Config changes:     NONE

Everything above is within the task prompt scope: YES — with the CC choices under
Scope Decisions.

### Scope Decisions
CC implementation choices (not separately specified):
- `emit()` writes the JSONL file only. It does not insert `TraceEvent` rows: the task prompt
  specifies a JSONL append, with ids "validated against the DB before writing". No code writes
  the `TraceEvent` table yet; logged as an Out of Scope Observation.
- `init(db_path, trace_path)` sets the database and trace file, mirroring
  `state_manager.init()`. Defaults: `data/harness.db`, `data/trace.jsonl`.
- The database is opened read-only (`Path.resolve().as_uri()` + `?mode=ro`, Challenge
  Finding 4), so a missing database is an error, never silently created. Every database
  failure or id overflow is raised as `TraceError`, chained to its cause (Finding 2).
- A partial final line is isolated with a newline, never altered (Finding 1).
- INV-D4 "when applicable": tool_call / policy_decision require attempt_id (engineer
  decision, Finding 3).
- Ids must be integers (bool excluded); attempt_id may be None only for state_transition.
- Each line holds timestamp (UTC, `...Z`), scenario_run_id, attempt_id, event_type and
  payload. It is strict JSON (`allow_nan=False`), written in a single append and fsynced.
- `emit()` returns the record it wrote.
- `scripts/emit_test_trace.py` uses a temporary database and trace file and creates its
  run and attempt through the State Manager (the only write path), so the judge-facing
  `data/trace.jsonl` never holds synthetic events.

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
