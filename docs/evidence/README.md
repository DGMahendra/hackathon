# docs/evidence/ — Preserved Ablation Evidence

Byte-exact copies of the two result files that `docs/ABLATION_REPORT.md` and
`docs/EVAL_REPORT.md` report. The originals live under `data/`, which is runtime output:
it is not committed, and each script overwrites its output file on every run. These copies
are kept here so judges can inspect the data behind the reports. They are not regenerated:
re-running a script changes `data/`, not this directory.

| File | SHA-256 |
|---|---|
| `docs/evidence/ablation_results.jsonl` | `fb7797d5647a697220f20f46745fe1ecdc3d1375dda6d0d939cc4c93e0ed97f2` |
| `docs/evidence/mechanism_demo_results.jsonl` | `479899ad10ea046f8fd2c77288503cb45f246a31b45a1a49a9c67da11e913f0e` |

## `docs/evidence/ablation_results.jsonl` — class A, live model

| | |
|---|---|
| Copied from | `data/ablation_results.jsonl` |
| Source script | `scripts/run_ablation.py --repetitions 5` |
| Run date | 2026-10-05 (header `started_at`: `2026-10-05T16:34:19.544600+00:00`) |
| Model | `claude-sonnet-5` (live, Anthropic API), naive and harnessed alike |
| Scenarios | SCHEMA_DRIFT, MISSING_COLUMN, PROMPT_INJECTION |
| Repetitions | 5 pairs per scenario: 15 pairs, 30 runs |
| Seeds | `seed_base` 42: seeds 42, 43, 44, 45, 46 (checked against every run row) |
| Result | naive 15/15, harnessed 15/15 succeeded; 0 unsafe actions executed on either side; 0 integrity failures |
| Recorded in | `sessions/SESSION_LOG_S06.md` (Task 6.1), `sessions/VERIFICATION_RECORD_S06.md` |

Contents: one `ablation_run` header row, 30 `run` rows, one `ablation_summary` row.

**The earlier N=2 run's data was overwritten.** Task 5.3 ran the live ablation at N=2 on
2026-10-04 (12 runs, all succeeded on both sides, no difference). `scripts/run_ablation.py`
overwrites its output file, so the N=5 run of 2026-10-05 replaced that data. No copy of
the N=2 data exists. Its results are recorded only in `sessions/VERIFICATION_RECORD_S05.md`
(Task 5.3) and `sessions/SESSION_LOG_S05.md`.

## `docs/evidence/mechanism_demo_results.jsonl` — class B, scripted agent

| | |
|---|---|
| Copied from | `data/mechanism_demo_results.jsonl` |
| Source script | `scripts/run_mechanism_demo.py` (defaults) |
| Run date | File last written 2026-10-06 18:40:57 +0530. The header has no timestamp, so this date comes from the file's modification time. The script was built and first run on 2026-10-05 (Task 6.7) |
| Model | None. A scripted adversarial stub (`ScenarioClient` from `tests/session5/test_ablation_runner.py`) that always proposes the injected action. **Not a live-model result**: it shows what the harness guarantees whatever the model does |
| Scenario | PROMPT_INJECTION |
| Repetitions | 3 pairs: 6 runs |
| Seeds | `seed_base` 42: seeds 42, 43, 44 (checked against every run row) |
| Result | naive: unsafe action executed 3/3 (simulated, no I/O), claimed success 3/3, measured success 0/3. Harnessed: POLICY_DENY 3/3, 0 executed, measured success 0/3 (blocked, not recovered) |
| Recorded in | `sessions/SESSION_LOG_S06.md` (Task 6.7), `sessions/VERIFICATION_RECORD_S06.md` |

Contents: one `mechanism_demo` header row, 6 `run` rows, one `ablation_summary` row.

## Not preserved here

`data/trace.jsonl`, `data/trace_segments/` and the `data/*.db` files stay uncommitted
runtime output. The committed trace artifacts are in `docs/traces/`.
