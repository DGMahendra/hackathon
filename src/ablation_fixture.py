"""src/ablation_fixture.py — seed / failure-state parity for the ablation (Task 5.2, INV-D6).

get_seed_state(scenario_type, seed) is the ONE source of seeded pipeline data and injected
failure state, used by both src/orchestrator.py (harnessed) and src/naive_baseline.py (naive) —
there are not two independently maintained copies. It injects through failure_injector and
returns a SHA-256 of the resulting PipelineState (schema and rows of every pipeline table).

require_parity() compares the two configurations' initial states before either agent is asked
for anything and raises AblationIntegrityError on any mismatch. It is an explicit raise, never an
`assert`: assertions are stripped under `python -O`, which would make this check silently vanish.

Imports only failure_injector (no policy_layer, tool_validation or verification), so the naive
baseline may use it (INV-S6).
"""

import hashlib
from dataclasses import dataclass

import failure_injector


class AblationIntegrityError(Exception):
    """Naive and harnessed runs of a pair did not start from identical state (INV-D6)."""


@dataclass(frozen=True)
class SeedState:
    """The initial state one configuration starts from."""

    scenario_type: str
    seed: int
    state_hash: str  # SHA-256 of failure_injector.pipeline_state() right after injection
    description: str  # what was injected — for traces, never for the agent


def get_seed_state(scenario_type: str, seed: int, db_path=None) -> SeedState:
    """Seed and inject scenario_type into db_path (default: failure_injector's database); return its state."""
    injection = failure_injector.inject(scenario_type, seed, db_path=db_path)
    return SeedState(scenario_type, seed, state_hash(db_path), injection.description)


def state_hash(db_path=None) -> str:
    """SHA-256 of the canonical dump of every PipelineState table (schema and rows)."""
    return hashlib.sha256(failure_injector.pipeline_state(db_path).encode("utf-8")).hexdigest()


def require_parity(naive: SeedState, harnessed: SeedState) -> None:
    """Raise AblationIntegrityError unless both configurations start from the same scenario, seed and state."""
    naive_key = (naive.scenario_type, naive.seed, naive.state_hash)
    harnessed_key = (harnessed.scenario_type, harnessed.seed, harnessed.state_hash)
    if naive_key != harnessed_key:
        raise AblationIntegrityError(
            f"INV-D6: initial state mismatch — naive {naive_key} vs harnessed {harnessed_key}"
        )
