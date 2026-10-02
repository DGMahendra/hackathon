-- src/schema.sql — DataOps Agent harness schema (Task 1.2)
--
-- Idempotent: every object uses IF NOT EXISTS so scripts/init_db.py can be re-run.
-- Foreign keys are enforced per connection in SQLite; every connection that writes
-- to this database must run `PRAGMA foreign_keys = ON` (scripts/init_db.py does).

-- ---------------------------------------------------------------------------
-- ScenarioRun
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ScenarioRun (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_type  TEXT    NOT NULL
                   CHECK (scenario_type IN ('SCHEMA_DRIFT', 'MISSING_COLUMN', 'PROMPT_INJECTION')),
    -- INV-D5: status domain restricted to the three state-machine states.
    status         TEXT    NOT NULL DEFAULT 'IN_PROGRESS'
                   CHECK (status IN ('IN_PROGRESS', 'RECOVERED', 'UNRECOVERED')),
    -- INV-D1: attempts_used <= MAX_SCENARIO_ATTEMPTS (3) at all times.
    attempts_used  INTEGER NOT NULL DEFAULT 0,
    max_attempts   INTEGER NOT NULL DEFAULT 3
                   CHECK (max_attempts BETWEEN 1 AND 3),
    created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (attempts_used BETWEEN 0 AND max_attempts)
);

-- INV-D5: a ScenarioRun enters the state machine at IN_PROGRESS only.
CREATE TRIGGER IF NOT EXISTS scenario_run_status_initial
BEFORE INSERT ON ScenarioRun
WHEN NEW.status <> 'IN_PROGRESS'
BEGIN
    SELECT RAISE(ABORT, 'INV-D5: ScenarioRun must be created with status IN_PROGRESS');
END;

-- INV-D5: no transition out of a terminal state (RECOVERED | UNRECOVERED).
CREATE TRIGGER IF NOT EXISTS scenario_run_status_terminal
BEFORE UPDATE OF status ON ScenarioRun
WHEN OLD.status IN ('RECOVERED', 'UNRECOVERED') AND NEW.status <> OLD.status
BEGIN
    SELECT RAISE(ABORT, 'INV-D5: no transition out of a terminal ScenarioRun status');
END;

-- INV-D2: only ALLOW-decided attempts may increment attempts_used (DENY and
-- REQUIRE_APPROVAL never do, regardless of downstream outcome). attempts_used may
-- not exceed the number of Attempts whose recorded policy_decision is ALLOW.
CREATE TRIGGER IF NOT EXISTS scenario_run_attempts_used_allow_only
BEFORE UPDATE OF attempts_used ON ScenarioRun
WHEN NEW.attempts_used > (
    SELECT COUNT(*) FROM Attempt
    WHERE scenario_run_id = NEW.id
      AND policy_decision = 'ALLOW'
)
BEGIN
    SELECT RAISE(ABORT, 'INV-D2: attempts_used exceeds count of ALLOW attempts');
END;

-- ---------------------------------------------------------------------------
-- Attempt
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS Attempt (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_run_id        INTEGER NOT NULL REFERENCES ScenarioRun(id),
    -- INV-D1: no Attempt row with attempt_number > MAX_SCENARIO_ATTEMPTS (3).
    -- attempt_number = ScenarioRun.attempts_used at write time (ARCHITECTURE.md §8):
    -- 0 is valid for a DENY / REQUIRE_APPROVAL row written before any real attempt.
    attempt_number         INTEGER NOT NULL CHECK (attempt_number BETWEEN 0 AND 3),
    plan                   TEXT,
    -- Nullable: the Attempt row exists before the Policy Layer has decided.
    policy_decision        TEXT
                           CHECK (policy_decision IN ('ALLOW', 'DENY', 'REQUIRE_APPROVAL')),
    tool_validation_result TEXT CHECK (tool_validation_result IN ('VALID', 'REJECTED')),
    execution_result       TEXT,
    verification_result    TEXT CHECK (verification_result IN ('PASS', 'FAIL')),
    failure_reason         TEXT,
    checkpoint_state       TEXT,
    -- Target for TraceEvent's composite FK (attempt must belong to the same run).
    UNIQUE (id, scenario_run_id),
    -- INV-D3: failure_reason is non-null iff tool validation or verification failed.
    CHECK (
        (COALESCE(tool_validation_result, '') = 'REJECTED'
         OR COALESCE(verification_result, '') = 'FAIL')
        = (failure_reason IS NOT NULL)
    )
);

-- ---------------------------------------------------------------------------
-- TraceEvent
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS TraceEvent (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    -- INV-D4: every TraceEvent references a valid scenario_run_id ...
    scenario_run_id INTEGER NOT NULL REFERENCES ScenarioRun(id),
    -- ... and a valid attempt_id of that same run when applicable (nullable).
    attempt_id      INTEGER,
    event_type      TEXT    NOT NULL
                    CHECK (event_type IN ('tool_call', 'state_transition', 'policy_decision')),
    payload         TEXT,
    timestamp       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (attempt_id, scenario_run_id) REFERENCES Attempt(id, scenario_run_id)
);

-- ---------------------------------------------------------------------------
-- PipelineState — PLACEHOLDER tables only.
-- Exact Bronze/Silver/Gold columns are decided in Session 3 with the scenario
-- designs; these exist solely to prove the migration runs cleanly.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pipeline_bronze (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    record TEXT
);

CREATE TABLE IF NOT EXISTS pipeline_silver (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    record TEXT
);

CREATE TABLE IF NOT EXISTS pipeline_gold (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    record TEXT
);
