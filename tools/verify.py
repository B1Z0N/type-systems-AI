"""Check that every version of the code is a faithful variant of the L3 source.

1. Same program: after erasing annotations, docstrings, imports and type-only definitions
   (with Python's own `ast`, independently of derive.py's libcst code), L3 reduces to exactly
   the same program as L0. (The natural rewrite renames locals, so it is checked by behavior
   instead: tools/validate_natural.py and tools/difftest.py.)
2. Behavior: the hidden test suite passes on every version.
3. Types: L3 passes `mypy --strict`.
4. No leaks: the code never mentions the experiment.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from derive import ROOT, SOURCE, available_arms, collect_registry, derive  # noqa: E402

LEAK_WORDS = re.compile(r"\b(untyped|type level|experiment|benchmark|l[0-3] arm)\b", re.I)


class Erase(ast.NodeTransformer):
    def __init__(self, typedefs: set[str], newtypes: set[str]) -> None:
        self.typedefs = typedefs
        self.newtypes = newtypes

    def _strip_doc(self, body: list[ast.stmt]) -> list[ast.stmt]:
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            if isinstance(body[0].value.value, str):
                body = body[1:]
        return body or [ast.Pass()]

    def visit_Module(self, node: ast.Module) -> ast.AST:
        node.body = self._strip_doc(node.body)
        self.generic_visit(node)
        node.body = [s for s in node.body if not isinstance(s, (ast.Import, ast.ImportFrom))]
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        node.returns = None
        node.body = self._strip_doc(node.body)
        for arg in (*node.args.args, *node.args.kwonlyargs, *node.args.posonlyargs, node.args.vararg, node.args.kwarg):
            if arg is not None:
                arg.annotation = None
        self.generic_visit(node)
        return node

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST | None:
        if node.name in self.typedefs:
            return None
        node.body = self._strip_doc(node.body)
        self.generic_visit(node)
        return node

    def visit_AnnAssign(self, node: ast.AnnAssign) -> ast.AST | None:
        if node.value is None:
            return None
        return ast.Assign(targets=[node.target], value=self.visit(node.value), lineno=0)

    def visit_Assign(self, node: ast.Assign) -> ast.AST | None:
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id in self.typedefs:
            return None
        self.generic_visit(node)
        return node

    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        if isinstance(node.func, ast.Name) and node.func.id in self.newtypes and len(node.args) == 1:
            return node.args[0]
        return node


def normalized(package: Path, typedefs: set[str], newtypes: set[str]) -> dict[str, str]:
    """Each module reduced to its runtime skeleton, keyed by path relative to the package."""
    out = {}
    for path in sorted(p for p in package.rglob("*.py") if "__pycache__" not in p.parts):
        tree = Erase(typedefs, newtypes).visit(ast.parse(path.read_text()))
        out[str(path.relative_to(package))] = ast.dump(tree, annotate_fields=False)
    return out


def run(cmd: list[str], env: dict[str, str] | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=cwd)


def main() -> int:
    reg = collect_registry(SOURCE)
    typedefs = set(reg.newtypes) | set(reg.literals) | reg.typeddicts | reg.protocols | set(reg.aliases) | reg.typevars
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        built = {arm: derive(arm, Path(tmp) / arm) for arm in available_arms()}
        reference = normalized(built["l0"], typedefs, set(reg.newtypes))
        for arm, package in built.items():
            problems = []
            if arm != "natural" and normalized(package, typedefs, set(reg.newtypes)) != reference:
                diff = [
                    name
                    for name, dump in normalized(package, typedefs, set(reg.newtypes)).items()
                    if reference.get(name) != dump
                ]
                problems.append(f"runtime differs from l0 in {diff}")
            env = {**os.environ, "PYTHONPATH": str(package.parent)}
            tests = run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(ROOT / "hidden_tests" / "base")], env)
            if tests.returncode != 0:
                problems.append("hidden tests fail: " + tests.stdout.strip().splitlines()[-1])
            mypy = run([sys.executable, "-m", "mypy", "--strict", "--no-incremental", "billing"], cwd=package.parent)
            errors = re.search(r"Found (\d+) error", mypy.stdout)
            n_errors = int(errors.group(1)) if errors else 0
            if arm == "l3" and mypy.returncode != 0:
                problems.append(f"mypy --strict fails with {n_errors} errors")
            for path in package.glob("*.py"):
                hit = LEAK_WORDS.search(path.read_text())
                if hit:
                    problems.append(f"leak word {hit.group(0)!r} in {path.name}")
            lines = sum(len(p.read_text().splitlines()) for p in package.glob("*.py"))
            status = "ok " if not problems else "FAIL"
            print(f"{status} {arm:8s} {lines:5d} lines  mypy-strict errors: {n_errors:3d}  {'; '.join(problems)}")
            ok = ok and not problems
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
