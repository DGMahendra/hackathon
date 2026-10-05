"""scripts/assert_naive_has_no_harness_imports.py — INV-S6 static check (Task 5.1).

Usage (from repo root):
    python scripts/assert_naive_has_no_harness_imports.py [--target PATH]

Builds the TRANSITIVE import graph of src/naive_baseline.py (or --target) over the repo's src/
modules and fails the build if policy_layer, tool_validation or verification appears anywhere
in it — directly, through another src module, or as a dynamic import string
(importlib.import_module / __import__ / getattr / setattr / hasattr). The naive baseline must be
structurally incapable of invoking them, not merely configured to skip them.
Exits 0 if the graph is clean, 1 with every offending path otherwise.
"""

import argparse
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
DEFAULT_TARGET = SRC / "naive_baseline.py"
FORBIDDEN = ("policy_layer", "tool_validation", "verification")
DYNAMIC_LOOKUPS = ("import_module", "__import__", "getattr", "setattr", "hasattr")


def referenced_modules(path: Path) -> set:
    """Return every top-level module name path imports, plus strings passed to dynamic lookups."""
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
        if isinstance(node, ast.Call) and _callee(node) in DYNAMIC_LOOKUPS:
            names |= {arg.value for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)}
    return names


def _callee(call) -> str:
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def import_paths(target: Path) -> dict:
    """Map every module reachable from target (src modules followed transitively) to one import chain."""
    chains, frontier = {}, [(target, (target.stem,))]
    while frontier:
        path, chain = frontier.pop()
        for name in sorted(referenced_modules(path)):
            if name in chains or name == target.stem:
                continue
            chains[name] = chain + (name,)
            module_path = SRC / f"{name}.py"
            if module_path.is_file():
                frontier.append((module_path, chains[name]))
    return chains


def violations(target: Path) -> list:
    """Return one ' -> '-joined import chain per forbidden module in target's graph."""
    chains = import_paths(target)
    return [" -> ".join(chains[name]) for name in FORBIDDEN if name in chains]


def main(argv=None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="INV-S6: the naive baseline must not reach Policy, Tool Validation or Verification.")
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET, help="module to check (default src/naive_baseline.py)")
    args = parser.parse_args(argv)
    if not args.target.is_file():
        print(f"INV-S6 VIOLATION: {args.target} does not exist")
        return 1
    problems = violations(args.target)
    for chain in problems:
        print(f"INV-S6 VIOLATION: forbidden module in the naive import graph: {chain}")
    if problems:
        return 1
    graph = sorted(name for name in import_paths(args.target) if (SRC / f"{name}.py").is_file())
    print(f"INV-S6 OK: {args.target.name} import graph (src modules: {', '.join(graph)}) "
          f"contains none of {', '.join(FORBIDDEN)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
