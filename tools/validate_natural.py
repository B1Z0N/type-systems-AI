"""Validate the hand-written `natural` arm (subject/natural/billing).

Checks: no type annotations or typing imports; public API identical to L0 (modules, public
functions/classes/methods, parameter names, kinds and defaults, public constants);
hidden tests pass; differential testing identical to L3; reports size and which bug tasks
can be ported to it.

  uv run python tools/validate_natural.py
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from harness.tasks import load_tasks  # noqa: E402
from tools.derive import NATURAL, ROOT, MutationNotPortable, derive  # noqa: E402
from tools.difftest import compare  # noqa: E402


def annotations(tree: ast.AST) -> list[str]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns is not None:
                found.append(f"return annotation on {node.name} (line {node.lineno})")
            for arg in (*node.args.args, *node.args.kwonlyargs, *node.args.posonlyargs):
                if arg.annotation is not None:
                    found.append(f"annotation on {node.name}({arg.arg}) (line {node.lineno})")
        elif isinstance(node, ast.AnnAssign):
            found.append(f"variable annotation (line {node.lineno})")
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            module = getattr(node, "module", None) or ""
            names = [a.name for a in node.names]
            if module in ("typing", "typing_extensions") or "typing" in names:
                found.append(f"typing import (line {node.lineno})")
    return found


def _signature(fn: ast.FunctionDef) -> str:
    args = fn.args
    defaults = [ast.dump(d) for d in args.defaults]
    kw_defaults = [ast.dump(d) if d else None for d in args.kw_defaults]
    return repr((
        [a.arg for a in args.posonlyargs], [a.arg for a in args.args], args.vararg and args.vararg.arg,
        [a.arg for a in args.kwonlyargs], args.kwarg and args.kwarg.arg, defaults, kw_defaults,
        [ast.dump(d) for d in fn.decorator_list],
    ))


def surface(package: Path) -> dict[str, str]:
    api = {}
    for path in sorted(package.glob("*.py")):
        module = str(path.relative_to(package))
        tree = ast.parse(path.read_text())
        api[module] = "module"
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                api[f"{module}:{node.name}"] = _signature(node)
            elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                api[f"{module}:{node.name}"] = "class " + repr([ast.dump(b) for b in node.bases])
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and (not item.name.startswith("_") or item.name == "__init__"):
                        api[f"{module}:{node.name}.{item.name}"] = _signature(item)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and not target.id.startswith("_"):
                        api[f"{module}:{target.id}"] = "constant"
    return api


def main() -> int:
    if not NATURAL.exists():
        print(f"{NATURAL.relative_to(ROOT)} does not exist; see subject/natural/BRIEF.md for how it was produced")
        return 1
    problems: list[str] = []
    for path in sorted(NATURAL.glob("*.py")):
        for issue in annotations(ast.parse(path.read_text())):
            problems.append(f"{path.name}: {issue}")
    with tempfile.TemporaryDirectory() as tmp:
        l0 = derive("l0", Path(tmp) / "l0")
        l3 = derive("l3", Path(tmp) / "l3")
        reference, candidate = surface(l0), surface(NATURAL)
        for key in sorted(set(reference) | set(candidate)):
            if reference.get(key) != candidate.get(key):
                problems.append(f"API differs at {key}: l0={reference.get(key)!r:.80} natural={candidate.get(key)!r:.80}")
        env = {**os.environ, "PYTHONPATH": str(NATURAL.parent)}
        run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(ROOT / "hidden_tests" / "base")],
                             env=env, capture_output=True, text=True)
        if run.returncode:
            problems.append("hidden tests fail: " + run.stdout.strip().splitlines()[-1])
        diffs = compare(l3.parent, NATURAL.parent, list(range(200)), 150)
        if diffs:
            problems.append(f"differential testing: {len(diffs)}/200 seeds differ, e.g. seed {diffs[0][0]}: {diffs[0][1]}")
        size = lambda p: sum(len(f.read_text()) for f in sorted(p.glob("*.py")))  # noqa: E731
        print(f"size: natural {size(NATURAL)} chars = {size(NATURAL) / size(l0):.2f}x l0, l3 is {size(l3) / size(l0):.2f}x l0")
        portable, not_portable = [], []
        for task in load_tasks(["bugfix"]):
            try:
                derive("natural", Path(tmp) / "port" / task.id, task.mutations)
                portable.append(task.id)
            except MutationNotPortable:
                not_portable.append(task.id)
        print(f"bug tasks portable to natural: {len(portable)}/{len(portable) + len(not_portable)}")
        if not_portable:
            print("  not portable (excluded from the natural arm): " + ", ".join(not_portable))
    for problem in problems[:40]:
        print("FAIL", problem)
    print("natural arm is valid" if not problems else f"{len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
