"""src/policy_layer.py — Policy Layer: ALLOW / DENY / REQUIRE_APPROVAL in code (Task 2.1).

Rules are code, not prompts: no LLM, network or file access happens here. An action is
exactly `{"tool": <n>, "params": {...}}`; its target is `params["table"]`. Any other
top-level key, or any parameter the tool does not define (a second table, a URL, a path),
is denied — nothing in an action can name a target the policy did not check.

- ALLOW: a schema-fix or backfill tool targeting a local PipelineState table.
- REQUIRE_APPROVAL: a destructive tool targeting a local PipelineState table. It routes
  to the human-approval hook (request_approval), which returns PENDING unless an approver
  has been installed with set_approver(); the default build has no approver (ARCHITECTURE.md D6).
- DENY: everything else — external network calls, file writes (anywhere), targets outside
  the PipelineState tables, unknown tools and malformed actions. Fail closed (INV-S2's
  decision function; Task 2.4 enforces zero execution on DENY).

Additions for the interactive control center (all default to the original behaviour):
- explain(action): the same decision as evaluate(), plus a human-readable reason and the
  individual checks that led to it. evaluate() is defined as explain(action).decision, so
  there is exactly one rule implementation.
- set_approval_required(tools): a STRICTER policy — the named allowlisted tools are routed to
  REQUIRE_APPROVAL instead of ALLOW. It can only tighten; it never turns a DENY into anything else.
- set_approver(fn): installs the human-approval callback. fn(action) returns APPROVED, REJECTED
  or anything else (treated as PENDING). Only an APPROVED answer lets the harness continue, and
  only through Tool Validation, Execute and Verification like any other action.
"""

from enum import StrEnum

from pipeline_tables import PIPELINE_TABLES


class PolicyDecision(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"


PENDING = "PENDING"
APPROVED = "APPROVED"
REJECTED = "REJECTED"

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

_extra_approval_tools = ()  # a stricter policy, off by default (set_approval_required)
_approver = None  # the human-approval callback, none by default (set_approver)


class PolicyExplanation:
    """A decision with the reason for it and the individual checks behind it (plain class: this module imports
    nothing that could reach outside itself)."""

    __slots__ = ("decision", "reason", "checks")

    def __init__(self, decision: PolicyDecision, reason: str, checks: tuple):
        self.decision = decision
        self.reason = reason
        self.checks = checks  # ((name, "pass" | "fail" | "warn"), ...)


def evaluate(action) -> PolicyDecision:
    """Return the policy decision for action; anything not positively allowed is DENY."""
    return explain(action).decision


def explain(action) -> PolicyExplanation:
    """Return evaluate()'s decision together with why — the single implementation of the rules."""
    facts = _analyze(action)
    decision, reason = _decide(facts)
    return PolicyExplanation(decision, reason, _checks(facts, decision))


def set_approval_required(tools) -> None:
    """Route these ALLOW-listed tools to REQUIRE_APPROVAL (a stricter policy). () restores the default."""
    global _extra_approval_tools
    _extra_approval_tools = tuple(t for t in tools if t in LOCAL_REPAIR_TOOLS)


def set_approver(approver) -> None:
    """Install the human-approval callback (None restores the always-PENDING stub)."""
    global _approver
    _approver = approver


def request_approval(action) -> str:
    """Ask the approver about a REQUIRE_APPROVAL action: APPROVED, REJECTED or PENDING (no approver, or no answer)."""
    if _approver is None:
        return PENDING
    answer = _approver(action)
    return answer if answer in (APPROVED, REJECTED) else PENDING


def _decide(facts: dict) -> tuple:
    """Return (decision, reason) from the analysed action; anything not positively allowed is DENY."""
    tool = facts["tool"]
    if not facts["shape_ok"]:
        return PolicyDecision.DENY, "Action is not exactly {tool, params}; malformed actions are denied."
    if not facts["known"]:
        return PolicyDecision.DENY, f"Tool '{tool}' is outside the approved capability set."
    if facts["undeclared"]:
        return PolicyDecision.DENY, f"Tool '{tool}' does not define parameter(s) {facts['undeclared']}."
    if not facts["target_ok"]:
        return PolicyDecision.DENY, f"Target {facts['target']!r} is not a pipeline table."
    return _decide_by_category(facts)


def _decide_by_category(facts: dict) -> tuple:
    """Decide for a known tool with declared parameters and a pipeline-table target."""
    tool = facts["tool"]
    if tool in _extra_approval_tools:
        return PolicyDecision.REQUIRE_APPROVAL, f"'{tool}' is allowlisted, but this policy requires human approval for it."
    if tool in LOCAL_REPAIR_TOOLS:
        return PolicyDecision.ALLOW, "Approved operation with valid scope and parameters."
    if tool in APPROVAL_REQUIRED_TOOLS:
        return PolicyDecision.REQUIRE_APPROVAL, f"'{tool}' is destructive and needs human approval."
    return PolicyDecision.DENY, f"Tool '{tool}' is outside the approved capability set."


def _checks(facts: dict, decision: PolicyDecision) -> tuple:
    """The individual checks, for display: what was true of this action."""
    category = "pass" if decision is PolicyDecision.ALLOW else "warn" if decision is PolicyDecision.REQUIRE_APPROVAL else "fail"
    return (
        ("Action is exactly a tool and its parameters", "pass" if facts["shape_ok"] else "fail"),
        ("Tool is on the capability list", "pass" if facts["known"] else "fail"),
        ("Tool declares every parameter it carries", "pass" if facts["known"] and not facts["undeclared"] else "fail"),
        ("Target is a pipeline table", "pass" if facts["target_ok"] else "fail"),
        ("Tool may run without a human", category),
    )


def _analyze(action) -> dict:
    """Collect the facts the rules look at; the rules themselves are in _decide."""
    tool, target = _tool_and_target(action)
    shape_ok = isinstance(action, dict) and set(action) == ACTION_KEYS
    raw_tool = action.get("tool") if isinstance(action, dict) else None
    raw_params = action.get("params") if isinstance(action, dict) else None
    known = shape_ok and isinstance(raw_tool, str) and raw_tool in TOOL_PARAMETERS
    params = raw_params if isinstance(raw_params, dict) else {}
    undeclared = sorted(map(str, set(params) - TOOL_PARAMETERS.get(raw_tool, set()))) if known else []
    return {"shape_ok": shape_ok, "tool": tool if tool is not None else raw_tool, "known": known,
            "undeclared": undeclared, "target": target if target is not None else params.get("table"),
            "target_ok": target in PIPELINE_TABLES}


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
