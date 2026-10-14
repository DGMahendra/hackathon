# Recovery Control Center

A local, sandboxed web UI that **drives the real harness**. It contains no recovery logic of its own: every
scenario runs through `orchestrator.run_scenario -> agent_core -> harness.attempt_action -> policy_layer ->
tool_validation -> state_manager.execute_and_checkpoint (pipeline_write) -> verification`, and everything on
screen is read back from the real JSONL trace, SQLite checkpoints and database.

```bash
./start_demo.sh                          # Windows: double-click start_demo.bat. Installs deps, starts, opens the browser
# or by hand:
pip install -r requirements.txt          # no new dependencies: the server is Python standard library only
python ui/server.py --open               # http://127.0.0.1:8765   (add --reset for a clean sandbox)
python ui/selftest.py                    # end-to-end checks through the HTTP API, no API key needed
```

Once the page is open, **nothing in the demo needs a terminal**: scenarios, resets, SQL inspection, before/after
and the audit trace are all in the interface.

**API key:** open `.env` (it ships with an empty `ANTHROPIC_API_KEY=` line), paste your key after the `=`, save, and
reload the page. `.env` is gitignored; `.env.example` is the template.

`ANTHROPIC_API_KEY` (environment or `.env`) enables **Claude (live)**. Without it, live runs say
**AI SERVICE UNAVAILABLE** and nothing is faked; the **Scripted demo agent** must be selected explicitly.

The sandbox is `data/demo_ui/` (its own database and trace). `data/harness.db` is never touched. The server binds
to 127.0.0.1 only and exposes no endpoint that runs SQL or an arbitrary tool; the policy tester only *evaluates*.

## Data Explorer (read-only window into the real SQLite database)

Open **Data explorer** to inspect the same database the harness writes to (`data/demo_ui/harness.db`), with no SQL or
terminal needed. It can only observe; every change still goes policy -> validation -> controlled execution -> verification.

| Feature | Where the data comes from |
|---|---|
| Table selector | discovered from `sqlite_master` (pipeline tables plus harness bookkeeping such as `Attempt`, read-only) |
| Table view, row count, NULL counts, sample rows | live `SELECT`s; NULL cells highlighted; Refresh button; auto-refresh when the database changes |
| Schema view (column, type, nullable, status) | `PRAGMA table_info` plus verification's own expectation: the drifted `amt` shows as *not expected*, `amount` as *missing*, until the repair runs |
| Data quality (rows, NULL values, NULL rate, columns) | computed by SQLite. NULL rate = NULL cells / all cells |
| Verification strip | the same checks the harness runs (`verification.check_scenario_state_detailed`), against the current state |
| Compare: before / action / after | two snapshots the server read from SQLite (before the run, after verification) plus the executed tool call from the trace. A blocked attack shows "database unchanged" with equal state hashes |
| SQL query | `SELECT`/`WITH` only. Blocked with the QUERY BLOCKED panel otherwise. See below |
| Quick queries | real SELECTs generated from the table's actual columns (View all rows, Row count, NULL analysis, Schema, Recent records, Data quality) |
| Query history | only successfully executed read-only queries, with Re-run. Blocked queries are never stored |
| Database activity | written only by code that performed or observed the access: agent context reads, failure-injector setup writes, the executed schema change or data write, checkpoint reads, verification reads, blocked actions, Explorer reads |
| Incident panel | affected table, current columns vs what verification expects, the gaps, and Investigate & recover / View updated data |

**Why a mutation cannot get through** (four independent layers, tested individually): (1) a pre-check that allows one
`SELECT`/`WITH` statement and gives the friendly message; (2) the connection is opened with SQLite `mode=ro`; (3)
`PRAGMA query_only = ON`; (4) a SQLite authorizer that denies everything except reads, and only whitelisted read-only
introspection pragmas. `tests/session7/test_data_explorer.py` removes layer 4 on purpose and shows layer 2 still refuses
the write.

Names: the spec's `silver_orders` is `pipeline_silver` here, and the real Silver columns are `order_id, customer, amount,
region` (there is no `customer_id` or `created_at`). The Explorer shows the real names.

Resets: **Reset current scenario** re-injects the scenario's failure through the real Failure Injector;
**Reset all demo data** deletes and recreates the sandbox database, trace, history and activity.

## Who proposes what (always labelled on screen)

| Mode | Proposals come from | Where used |
|---|---|---|
| Claude (live) | `claude-sonnet-5` through `agent_core` | Scenario cards, "Run live" |
| Scripted demo agent | a stub that proposes the correct fix, read from the database | Only if you select it |
| Scripted adversary | a stub that proposes a fixed unsafe action | "Attack" / "Simulate attack" |

The adversary is **not** Claude misbehaving. It is a deterministic way to hand the real harness an action it must
refuse. The UI never claims otherwise. In the recorded live ablation, Claude ignored the injected instruction with and
without the harness (see the Evidence tab); the guarantee demonstrated by the attack is the harness's.

## Scenarios and what runs

| Card | Fixture (real `failure_injector`) | Proposed fix | Notes |
|---|---|---|---|
| Schema drift | `SCHEMA_DRIFT` | `rename_column` | Value check: Silver amount equals Bronze amount |
| Missing column | `MISSING_COLUMN` | `add_column` | **Schema recovery only.** The UI shows how many region values are still NULL; row-level value recovery needs a tool that does not exist yet |
| Null data | `PROMPT_INJECTION` + `variant="NULL_ONLY"` | `backfill_column` | Two Silver `region` values are NULL, both with the same true Bronze value, so one scalar backfill is correct. (`backfill_column` sets one scalar; two NULLs with different true values could not be repaired by it.) |
| Prompt injection | `PROMPT_INJECTION` | Live: Claude. Attack: scripted adversary obeys the injected text | Bronze free text is flagged UNTRUSTED DATA |
| Unsafe AI action | `SCHEMA_DRIFT` | 6 scripted unsafe proposals | Upload, HTTP, arbitrary SQL, harness-table write, destructive drop, SQL in an identifier |
| Crash and resume | `SCHEMA_DRIFT` in a child process | n/a | Real `SIGKILL` at one of your five kill points via `scripts/simulate_crash_resume.py`'s helpers, run in a separate process |
| Human approval | as Null data | `backfill_column` | Stricter policy: `backfill_column` requires approval. **The run blocks until you click Approve or Reject** |

"Database unchanged", "execution count 0" and "executions without ALLOW" are computed by the server from the trace
and from SHA-256 state hashes of the pipeline before and after, not typed into the UI.

## Suggested 4-minute demo (entirely in the browser)

1. **Live scenarios**: pick **Scripted demo agent** if offline, or leave **Claude (live)**. Select **Schema drift**.
2. **Data explorer**: show the real table (`amt`) and the incident panel (current `amt`, expected `amount`).
3. **Investigate & recover** (from either screen). Watch diagnosis, proposal, policy, validation, execution, verification.
4. **Compare: before and after** opens by itself: `amt` -> `amount`, with the executed `rename_column` between them. Check **Database activity**.
5. **Unsafe AI action: Simulate attack** (upload). Point at DENY, "validation not reached", then Data explorer -> Compare: database unchanged.
6. **Null data: Run live.** In the Explorer, NULL values 2 -> 0, from SQLite. Try `DROP TABLE` in **SQL query**: QUERY BLOCKED.
7. **Human approval** (click Approve), then **Crash and resume** (`after_commit` vs `mid_apply`).
8. **Execution trace** for the audit chain, then **Judge mode** for the 10-second summary.

## Changes to existing files (all additive; defaults preserve the old behaviour)

| File | Change |
|---|---|
| `src/policy_layer.py` | `explain()` (decision + reason + checks; `evaluate()` is now `explain().decision`, one rule implementation), `set_approval_required()` (stricter policy), `set_approver()` / `APPROVED` / `REJECTED`. Default approver still returns `PENDING`. Still imports only `enum` and `pipeline_tables`. |
| `src/harness.py` | REQUIRE_APPROVAL path: an `APPROVED` answer continues through the same Policy-ALLOW record, Tool Validation, Execute and Verification; `REJECTED`/no answer is recorded and executes nothing. Nothing is checkpointed while waiting, so a crash then asks again on resume. |
| `src/verification.py` | `verify_detailed()` and `check_scenario_state_detailed()` (the same checks, reported per group), opt-in `register_value_checks()`. `verify()` output is unchanged. |
| `src/scenario_expectations.py` | `VALUE_CHECKS` + `enable_value_checks()`. **Not called on import**, so the CLI, tests and recorded ablation keep their meaning; the control center enables it and says so. |
| `src/failure_injector.py`, `src/ablation_fixture.py`, `src/orchestrator.py` | optional `variant` argument (`NULL_ONLY`), default `None`. |
| `scripts/assert_single_execute_caller.py` | scans `ui/` too, so INV-S1 covers the new code. |
| `tests/session7/test_control_center_additions.py` | 9 tests for the above, including a plausible-but-wrong fix that schema, row-count and NULL checks accept and the value check rejects. |
| `tests/session7/test_data_explorer.py` | 6 tests: every mutation blocked and the database unchanged; the connection refuses writes even with the authorizer removed; tables, NULL counts and quality computed from SQLite; schema status; quick queries. |

`Claude.md` freezes the file set: adopting this needs the scope amendment it describes (a `ui/` directory and
`tests/session7/`).

## What has and has not been verified

Verified (sandbox, no network): all of `ui/selftest.py` (every scenario through the real orchestrator with the scripted agents, plus the Data
Explorer cross-checked against an independent SQLite connection); a real browser run of every screen including clicking Approve; `assert_single_execute_caller`,
`assert_write_scope_isolation`, `assert_naive_has_no_harness_imports`, `simulate_deny_path --assert-no-execution` and
`simulate_crash_resume` with all four assertions; a 20,000-action differential test of the new `evaluate()` against the
original; the 15 new tests.

**Not verified, please run:** (1) the **live Claude path**, which needs your key and network (it is the repo's own
`agent_core`, unchanged, but I could not call it); (2) the existing **pytest suite** (`python -m pytest tests/`, 426 tests),
which I could not run because pytest was not installable offline. I checked the structural tests that read module source
(import allowlist for `policy_layer`, read-only `verification`, single Execute caller) by hand. Note the sandbox used a
stand-in for the `anthropic` package for the crash simulation's local fake API.

## Known limits

* One presenter, one incident at a time (the harness enforces INV-S7). Approval state is in memory; if the server dies
  while a run waits, use **Reset all demo data**.
* Gold and Analytics status are derived from Silver's verification result, not measured separately.
* After a denial the harness does not re-plan (roadmap). The UI says "blocked", not "recovered".
* Display pacing slows how fast events are *shown*; every event keeps its real timestamp. Choose **As recorded** to see them unpaced.
