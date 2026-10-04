"""scripts/assert_single_execute_caller.py — INV-S1 structural check (Task 2.4).

Usage (from repo root):
    python scripts/assert_single_execute_caller.py

Scans every .py file under src/, scripts/, tools/ and verification/ and asserts:
  1. only src/harness.py references the pipeline-write primitive module (pipeline_write) —
     by import (any alias), name, attribute (e.g. harness.pipeline_write.write) or a dynamic
     lookup (importlib.import_module / __import__ / getattr / setattr with that string);
  2. only src/harness.py and src/state_manager.py (its definition) reference
     execute_and_checkpoint, by any of the same means (so aliased imports are caught too);
  3. src/harness.py calls pipeline_write.write exactly once and execute_and_checkpoint exactly
     once — the single call sites.
Test code is deliberately excluded: tests/ and the named CHECK_SCRIPTS, which reach the
primitive on purpose (to spy on it or to prove its runtime guards). They are printed so the
exemption stays visible. Exits 0 if all hold, 1 with a report otherwise.
"""

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCANNED_DIRECTORIES = ("src", "scripts", "tools", "verification")
FUNNEL = "src/harness.py"
CHECK_SCRIPTS = (
    "scripts/assert_write_scope_isolation.py",  # INV-S8 runtime check exercises the primitive
    "scripts/simulate_deny_path.py",  # INV-S2 check spies on the primitive
    "scripts/simulate_crash_resume.py",  # INV-S3/S4 check freezes the primitive mid-transaction to kill it
)
GUARDED = {
    "pipeline_write": ("src/harness.py",),
    "execute_and_checkpoint": ("src/harness.py", "src/state_manager.py"),
}
DYNAMIC_LOOKUPS = ("import_module", "__import__", "getattr", "setattr", "hasattr")


def scanned_files() -> list:
    """Return every scanned .py file, minus the named check scripts."""
    files = []
    for directory in SCANNED_DIRECTORIES:
        files += [p for p in sorted((REPO_ROOT / directory).rglob("*.py")) if "__pycache__" not in p.parts]
    return [p for p in files if p.relative_to(REPO_ROOT).as_posix() not in CHECK_SCRIPTS]


def referenced_names(tree) -> set:
    """Return every identifier the module refers to: names, attributes, imported names and
    string arguments of dynamic lookups."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        if isinstance(node, ast.ImportFrom):
            names |= {alias.name for alias in node.names} | {(node.module or "").split(".")[0]}
        if isinstance(node, ast.Call) and _callee(node) in DYNAMIC_LOOKUPS:
            names |= {a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)}
    return names


def _callee(call) -> str:
    """Return the called function's final name (f for f(), x.f for x.f())."""
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def call_sites(tree, attribute: str, owner: str = None) -> list:
    """Return line numbers of calls to <owner>.<attribute> (owner None: any owner or a bare call)."""
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _callee(node) != attribute:
            continue
        value = getattr(node.func, "value", None)
        if owner is None or isinstance(value, ast.Name) and value.id == owner:
            lines.append(node.lineno)
    return lines


def violations() -> list:
    """Return every INV-S1 structural violation found."""
    problems = []
    for path in scanned_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        names = referenced_names(ast.parse(path.read_text(encoding="utf-8")))
        problems += [f"{relative} references {guarded} — only {', '.join(allowed)} may"
                     for guarded, allowed in GUARDED.items() if guarded in names and relative not in allowed]
    funnel = ast.parse((REPO_ROOT / FUNNEL).read_text(encoding="utf-8"))
    problems += expect_one("pipeline_write.write", call_sites(funnel, "write", owner="pipeline_write"))
    problems += expect_one("state_manager.execute_and_checkpoint", call_sites(funnel, "execute_and_checkpoint"))
    return problems


def expect_one(name: str, lines: list) -> list:
    """Return a violation unless the funnel calls name exactly once."""
    return [] if len(lines) == 1 else [f"{FUNNEL} must call {name} exactly once; found lines {lines}"]


def main() -> int:
    """CLI entry point."""
    problems = violations()
    for problem in problems:
        print(f"INV-S1 VIOLATION: {problem}")
    if problems:
        return 1
    print(f"INV-S1 OK: pipeline_write.write and execute_and_checkpoint each have exactly one call site ({FUNNEL}); "
          f"scanned {', '.join(SCANNED_DIRECTORIES)}; test code excluded: tests/, {', '.join(CHECK_SCRIPTS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
