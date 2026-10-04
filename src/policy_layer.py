"""src/policy_layer.py — Policy Layer: ALLOW / DENY / REQUIRE_APPROVAL in code (Task 2.1).

Rules are code, not prompts: no LLM, network or file access happens here. An action is
exactly `{"tool": <name>, "params": {...}}`; its target is `params["table"]`. Any other
top-level key, or any parameter the tool does not define (a second table, a URL, a path),
is denied — nothing in an action can name a target the policy did not check.

- ALLOW: a schema-fix or backfill tool targeting a local PipelineState table.
- REQUIRE_APPROVAL: a destructive tool targeting a local PipelineState table. It routes
  to the human-approval stub, which returns PENDING; no MVP scenario triggers it
  (ARCHITECTURE.md D6).
- DENY: everything else — external network calls, file writes (anywhere), targets outside
  the PipelineState tables, unknown tools and malformed actions. Fail closed (INV-S2's
  decision function; Task 2.4 enforces zero execution on DENY).
"""

from enum import StrEnum

from pipeline_tables import PIPELINE_TABLES


class PolicyDecision(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"


PENDING = "PENDING"

# Rule categories (by tool name); a tool in no category is denied.
LOCAL_REPAIR_TOOLS = ("add_column", "rename_column", "backfill_column")
APPROVAL_REQUIRED_TOOLS = ("drop_column", "delete_rows", "truncate_table")
# The only parameters each known tool may carry. Missing parameters are Tool Validation's
# concern (REJECTED); unknown ones are denied here.
TOOL_PARAMETERS = {
    "add_column": {"table", "column", "column_type"},
    "rename_column": {"table", "old_name", "new_name"},
    "backfill_column": {"table", "column", "value"},
    "drop_column": {"table", "column"},
    "delete_rows": {"table", "column", "value"},
    "truncate_table": {"table"},
}
ACTION_KEYS = {"tool", "params"}


def evaluate(action) -> PolicyDecision:
    """Return the policy decision for action; anything not positively allowed is DENY."""
    tool, target = _tool_and_target(action)
    if target not in PIPELINE_TABLES:
        return PolicyDecision.DENY
    if tool in LOCAL_REPAIR_TOOLS:
        return PolicyDecision.ALLOW
    if tool in APPROVAL_REQUIRED_TOOLS:
        return PolicyDecision.REQUIRE_APPROVAL
    return PolicyDecision.DENY


def request_approval(action) -> str:
    """Human-approval stub for REQUIRE_APPROVAL actions: always PENDING (no approver in the MVP)."""
    return PENDING


def _tool_and_target(action) -> tuple:
    """Return (tool name, target table), or (None, None) if the action is malformed or
    carries a key or parameter the tool does not define."""
    if not isinstance(action, dict) or set(action) != ACTION_KEYS:
        return None, None
    tool, params = action["tool"], action["params"]
    if not isinstance(tool, str) or not isinstance(params, dict):
        return None, None
    if not set(params) <= TOOL_PARAMETERS.get(tool, set()):
        return None, None
    target = params.get("table")
    return (tool, target) if isinstance(target, str) else (None, None)
