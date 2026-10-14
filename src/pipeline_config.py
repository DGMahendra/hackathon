"""src/pipeline_config.py — the one place scenario settings come from (config/pipeline.json).

What lives here is *what the scenarios break* and the demo's run settings: which Silver column
drifts, goes missing or goes NULL, the seed, the seed data and the model name. Everything that
used to hard-code `amount`, `amt` or `region` reads these values instead.

What deliberately does NOT live here (it is a safety boundary, so it stays in reviewed code):
  - the tool allowlist and per-tool parameter schemas  (src/tool_validation.py)
  - the ALLOW / REQUIRE_APPROVAL / DENY rules          (src/policy_layer.py)
  - which tables an action may ever target             (src/pipeline_tables.py)
  - the attempt budget (max 3, a CHECK in src/schema.sql)

Imports only the standard library, so the naive baseline may use it (INV-S6).
The file is read and validated once, at import; a bad value fails at startup with a clear message.
"""

import json
import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "pipeline.json"
CONFIG_PATH = Path(os.environ.get("DATAOPS_CONFIG") or DEFAULT_CONFIG_PATH)

# The canonical Silver schema. The seed generator, Gold aggregation and verification are built on it.
SILVER_COLUMNS = (("order_id", "INTEGER"), ("customer", "TEXT"), ("amount", "REAL"), ("region", "TEXT"))
KEY_COLUMN = "order_id"  # joins Silver to Bronze for the value checks; never a scenario target
_NAMES = tuple(name for name, _ in SILVER_COLUMNS)
_TARGETS = tuple(name for name in _NAMES if name != KEY_COLUMN)
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}")  # the same rule Tool Validation enforces


class ConfigError(Exception):
    """config/pipeline.json is missing a value or holds one the scenarios cannot work with."""


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc


def _get(data: dict, dotted: str):
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise ConfigError(f"{CONFIG_PATH}: missing '{dotted}'")
        node = node[part]
    return node


def _target(data: dict, dotted: str) -> str:
    value = _get(data, dotted)
    if value not in _TARGETS:
        raise ConfigError(f"{CONFIG_PATH}: '{dotted}' must be one of {list(_TARGETS)}, got {value!r}")
    return value


def _new_identifier(data: dict, dotted: str) -> str:
    value = _get(data, dotted)
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value) or value in _NAMES or value.lower() in (
            "id", "rowid", "oid", "_rowid_") or value.lower().startswith("sqlite_"):
        raise ConfigError(f"{CONFIG_PATH}: '{dotted}' must be a new column name (letters, digits, _), "
                          f"not an existing column, got {value!r}")
    return value


def _non_empty_strings(data: dict, dotted: str) -> tuple:
    value = _get(data, dotted)
    if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value):
        raise ConfigError(f"{CONFIG_PATH}: '{dotted}' must be a non-empty list of strings")
    return tuple(value)


def _int(data: dict, dotted: str, low: int, high: int) -> int:
    value = _get(data, dotted)
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ConfigError(f"{CONFIG_PATH}: '{dotted}' must be an integer between {low} and {high}, got {value!r}")
    return value


_data = _load(CONFIG_PATH)

SEED = _int(_data, "run.seed", 0, 2**31 - 1)
MODEL = os.environ.get("DATAOPS_MODEL") or _get(_data, "run.model")

ROW_COUNT = _int(_data, "seed_data.row_count", 2, 1000)
CUSTOMERS = _non_empty_strings(_data, "seed_data.customers")
REGIONS = _non_empty_strings(_data, "seed_data.regions")
AMOUNT_MIN = _get(_data, "seed_data.amount_min")
AMOUNT_MAX = _get(_data, "seed_data.amount_max")
if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (AMOUNT_MIN, AMOUNT_MAX)) or AMOUNT_MIN >= AMOUNT_MAX:
    raise ConfigError(f"{CONFIG_PATH}: seed_data.amount_min must be a number below amount_max")

DRIFT_COLUMN = _target(_data, "scenarios.SCHEMA_DRIFT.column")
DRIFT_RENAMED_TO = _new_identifier(_data, "scenarios.SCHEMA_DRIFT.renamed_to")
MISSING_COLUMN = _target(_data, "scenarios.MISSING_COLUMN.column")
INJECTION_NULL_COLUMN = _target(_data, "scenarios.PROMPT_INJECTION.null_column")
NULL_ONLY_COLUMN = _target(_data, "scenarios.NULL_ONLY.null_column")
if dict(SILVER_COLUMNS)[NULL_ONLY_COLUMN] != "TEXT":
    raise ConfigError(f"{CONFIG_PATH}: 'scenarios.NULL_ONLY.null_column' must be a categorical TEXT column "
                      f"(two NULL rows must share one true value), got {NULL_ONLY_COLUMN!r}")

HUMAN_APPROVAL_TOOLS = tuple(_get(_data, "control_center.human_approval_tools"))
APPROVAL_TIMEOUT_SECONDS = _int(_data, "control_center.approval_timeout_seconds", 10, 86400)


def column_type(column: str) -> str:
    """The canonical SQLite type of a Silver column."""
    return dict(SILVER_COLUMNS)[column]


def summary() -> dict:
    """What the UI and docs show about the current configuration."""
    return {"config_path": str(CONFIG_PATH), "seed": SEED, "model": MODEL, "row_count": ROW_COUNT,
            "scenarios": {"SCHEMA_DRIFT": {"column": DRIFT_COLUMN, "renamed_to": DRIFT_RENAMED_TO},
                          "MISSING_COLUMN": {"column": MISSING_COLUMN},
                          "PROMPT_INJECTION": {"null_column": INJECTION_NULL_COLUMN},
                          "NULL_ONLY": {"null_column": NULL_ONLY_COLUMN}},
            "human_approval_tools": list(HUMAN_APPROVAL_TOOLS)}
