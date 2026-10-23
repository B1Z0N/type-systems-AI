"""Produce each version ("arm") of the billing package from the strict L3 source of truth.

l3       the source as written: NewType ids and units, Literal states, TypedDict shapes,
         Protocols, Final; passes mypy --strict
l1       signatures only: each annotation erased to its outer builtin (CustomerId -> str,
         PaymentMethod | None -> dict | None), no type definitions or variable annotations
l0       the same code with every type annotation and type-only definition removed
natural  an idiomatic untyped rewrite (subject/natural/billing; see subject/natural/BRIEF.md)

L0 is made mechanically (libcst) in three steps: semantic types -> their base types
(NewType -> int/str, Literal -> str), shapes -> bare builtins (TypedDict -> dict), then no
annotations at all; L1 is the state after the second step. Task bugs are applied to the L3 text first and stripped along with
everything else. `tools/verify.py` checks that L3 and L0 are the same program.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

import libcst as cst
import yaml
from libcst.metadata import MetadataWrapper, ParentNodeProvider

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "subject" / "l3" / "billing"
NATURAL = ROOT / "subject" / "natural" / "billing"
ARMS = ("l3", "l1", "l0")

TYPE_CONSTRUCTORS = {"Union", "Optional", "Callable", "Literal", "dict", "list", "tuple", "set", "frozenset"}
CONTAINER_BUILTINS = {"list", "dict", "set", "frozenset", "tuple"}


class MutationNotPortable(ValueError):
    """A task's L3 bug has no counterpart in the natural rewrite."""


def available_arms() -> tuple[str, ...]:
    return ARMS + (("natural",) if NATURAL.exists() else ())


# --------------------------------------------------------------------------- what is a type


@dataclass
class Registry:
    """Type-only definitions found in the package."""

    newtypes: dict[str, str] = field(default_factory=dict)  # name -> base builtin
    literals: set[str] = field(default_factory=set)  # Literal aliases (all of str values here)
    typeddicts: set[str] = field(default_factory=set)
    protocols: set[str] = field(default_factory=set)
    aliases: dict[str, cst.BaseExpression] = field(default_factory=dict)  # X = dict[str, Y], Callable[...]
    typevars: set[str] = field(default_factory=set)


def _name_of(expr: cst.BaseExpression) -> str | None:
    if isinstance(expr, cst.Name):
        return expr.value
    if isinstance(expr, cst.Attribute):
        return expr.attr.value
    return None


def _code(node: cst.CSTNode) -> str:
    return cst.Module(body=[]).code_for_node(node)


def _single_assign(stmt: cst.CSTNode) -> tuple[str, cst.BaseExpression] | None:
    if not isinstance(stmt, cst.SimpleStatementLine) or len(stmt.body) != 1:
        return None
    small = stmt.body[0]
    if isinstance(small, cst.Assign) and len(small.targets) == 1 and isinstance(small.targets[0].target, cst.Name):
        return small.targets[0].target.value, small.value
    return None


def _is_type_expr(expr: cst.BaseExpression) -> bool:
    if isinstance(expr, cst.Subscript):
        return _name_of(expr.value) in TYPE_CONSTRUCTORS
    return isinstance(expr, cst.BinaryOperation) and isinstance(expr.operator, cst.BitOr)


def collect_registry(package: Path) -> Registry:
    reg = Registry()
    for path in sorted(package.glob("*.py")):
        for stmt in cst.parse_module(path.read_text()).body:
            if isinstance(stmt, cst.ClassDef):
                bases = {_name_of(arg.value) for arg in stmt.bases}
                if "TypedDict" in bases:
                    reg.typeddicts.add(stmt.name.value)
                if "Protocol" in bases:
                    reg.protocols.add(stmt.name.value)
                continue
            assign = _single_assign(stmt)
            if assign is None:
                continue
            name, value = assign
            if isinstance(value, cst.Call) and _name_of(value.func) == "NewType":
                reg.newtypes[name] = _code(value.args[1].value)
            elif isinstance(value, cst.Call) and _name_of(value.func) == "TypeVar":
                reg.typevars.add(name)
            elif isinstance(value, cst.Subscript) and _name_of(value.value) == "Literal":
                reg.literals.add(name)
            elif _is_type_expr(value):
                reg.aliases[name] = value
    return reg


# --------------------------------------------------------------------------- step 1: semantic types -> base types


class Structural(cst.CSTTransformer):
    """NewType -> base type, Literal -> str, Final[T] -> T; NewType calls `Cents(x)` -> `x`."""

    METADATA_DEPENDENCIES = (ParentNodeProvider,)

    def __init__(self, reg: Registry) -> None:
        super().__init__()
        self.reg = reg
        self.type_depth = 0  # > 0 inside an annotation or a type-alias definition

    def visit_Annotation(self, node: cst.Annotation) -> None:
        self.type_depth += 1

    def leave_Annotation(self, original: cst.Annotation, updated: cst.Annotation) -> cst.Annotation:
        self.type_depth -= 1
        return updated

    def visit_Assign(self, node: cst.Assign) -> None:
        if _is_type_expr(node.value):
            self.type_depth += 1

    def leave_Assign(self, original: cst.Assign, updated: cst.Assign) -> cst.Assign:
        if _is_type_expr(original.value):
            self.type_depth -= 1
        return updated

    def leave_Name(self, original: cst.Name, updated: cst.Name) -> cst.BaseExpression:
        if self.type_depth and updated.value in self.reg.newtypes:
            return cst.Name(self.reg.newtypes[updated.value])
        if self.type_depth and updated.value in self.reg.literals:
            return cst.Name("str")
        return updated

    def leave_Subscript(self, original: cst.Subscript, updated: cst.Subscript) -> cst.BaseExpression:
        if not self.type_depth:
            return updated
        base = _name_of(updated.value)
        if base == "Literal":
            return cst.Name("str")
        if base == "Final" and len(updated.slice) == 1 and isinstance(updated.slice[0].slice, cst.Index):
            return updated.slice[0].slice.value
        return updated

    def leave_Call(self, original: cst.Call, updated: cst.Call) -> cst.BaseExpression:
        if self.type_depth or _name_of(updated.func) not in self.reg.newtypes or len(updated.args) != 1:
            return updated
        return _maybe_paren(updated.args[0].value, self.get_metadata(ParentNodeProvider, original, None))

    def leave_SimpleStatementLine(
        self, original: cst.SimpleStatementLine, updated: cst.SimpleStatementLine
    ) -> cst.SimpleStatementLine | cst.RemovalSentinel:
        assign = _single_assign(original)
        if assign and (assign[0] in self.reg.newtypes or assign[0] in self.reg.literals):
            return cst.RemoveFromParent()
        return updated


ATOMIC = (cst.Name, cst.Attribute, cst.Call, cst.Subscript, cst.Integer, cst.Float, cst.SimpleString, cst.FormattedString)
TIGHT_PARENTS = (cst.BinaryOperation, cst.UnaryOperation, cst.Attribute, cst.Subscript, cst.Await)


def _maybe_paren(inner: cst.BaseExpression, parent: cst.CSTNode | None) -> cst.BaseExpression:
    """Parenthesize `a + b` when it replaces `Cents(a + b)` inside a tighter expression."""
    if isinstance(inner, ATOMIC) or inner.lpar or not isinstance(parent, TIGHT_PARENTS):
        return inner
    return inner.with_changes(lpar=[cst.LeftParen()], rpar=[cst.RightParen()])


# --------------------------------------------------------------------------- step 2: shapes -> bare builtins


def erase(expr: cst.BaseExpression, reg: Registry) -> cst.BaseExpression | None:
    """The outer builtin a hurried developer would write (dict, list, int | None), or None to drop it.

    Optionality is kept: erasing `PaymentMethod | None` to `dict` would make the annotation false,
    and L1 is meant to carry less information, never wrong information."""
    if isinstance(expr, cst.Name):
        if expr.value in reg.typeddicts:
            return cst.Name("dict")
        if expr.value in reg.aliases:
            return erase(reg.aliases[expr.value], reg)
        if expr.value in reg.protocols or expr.value in reg.typevars or expr.value in ("object", "Any"):
            return None
        return cst.Name(expr.value)
    if isinstance(expr, cst.Attribute):
        return expr
    if isinstance(expr, cst.Subscript):
        base = _name_of(expr.value)
        inner = [el.slice.value for el in expr.slice if isinstance(el.slice, cst.Index)]
        if base in CONTAINER_BUILTINS:
            return cst.Name(base)
        if base == "Final" and inner:
            return erase(inner[0], reg)
        if base == "Optional" and inner:
            erased = erase(inner[0], reg)
            return None if erased is None else _or_none(erased)
        return None
    if isinstance(expr, cst.BinaryOperation) and isinstance(expr.operator, cst.BitOr):
        members = _union_members(expr)
        optional = any(isinstance(m, cst.Name) and m.value == "None" for m in members)
        erased = [erase(m, reg) for m in members if not (isinstance(m, cst.Name) and m.value == "None")]
        if not erased or any(e is None for e in erased) or len({_code(e) for e in erased if e is not None}) != 1:
            return None
        return _or_none(erased[0]) if optional else erased[0]
    return None


def _or_none(expr: cst.BaseExpression) -> cst.BaseExpression:
    return cst.BinaryOperation(left=expr, operator=cst.BitOr(), right=cst.Name("None"))


def _union_members(expr: cst.BaseExpression) -> list[cst.BaseExpression]:
    if isinstance(expr, cst.BinaryOperation) and isinstance(expr.operator, cst.BitOr):
        return _union_members(expr.left) + _union_members(expr.right)
    return [expr]


def _plain_param(param: cst.Param) -> cst.Param:
    equal = param.equal
    if param.default is not None:  # `x: int = 0` -> `x=0`
        equal = cst.AssignEqual(whitespace_before=cst.SimpleWhitespace(""), whitespace_after=cst.SimpleWhitespace(""))
    return param.with_changes(annotation=None, equal=equal)


def _typed_param(param: cst.Param, annotation: cst.BaseExpression) -> cst.Param:
    equal = param.equal
    if param.default is not None:
        equal = cst.AssignEqual(whitespace_before=cst.SimpleWhitespace(" "), whitespace_after=cst.SimpleWhitespace(" "))
    return param.with_changes(annotation=cst.Annotation(annotation), equal=equal)


class RemoveTypeDefs(cst.CSTTransformer):
    """Delete TypedDict and Protocol classes, type aliases and TypeVars."""

    def __init__(self, reg: Registry) -> None:
        super().__init__()
        self.reg = reg

    def leave_ClassDef(self, original: cst.ClassDef, updated: cst.ClassDef) -> cst.ClassDef | cst.RemovalSentinel:
        if original.name.value in self.reg.typeddicts or original.name.value in self.reg.protocols:
            return cst.RemoveFromParent()
        return updated

    def leave_SimpleStatementLine(
        self, original: cst.SimpleStatementLine, updated: cst.SimpleStatementLine
    ) -> cst.SimpleStatementLine | cst.RemovalSentinel:
        assign = _single_assign(original)
        if assign and (assign[0] in self.reg.aliases or assign[0] in self.reg.typevars):
            return cst.RemoveFromParent()
        return updated


class StripVariableAnnotations(cst.CSTTransformer):
    """`x: T = v` -> `x = v`; bare `x: T` declarations are removed."""

    def leave_SimpleStatementLine(
        self, original: cst.SimpleStatementLine, updated: cst.SimpleStatementLine
    ) -> cst.SimpleStatementLine | cst.RemovalSentinel:
        body: list[cst.BaseSmallStatement] = []
        for small in updated.body:
            if isinstance(small, cst.AnnAssign):
                if small.value is not None:
                    body.append(cst.Assign(targets=[cst.AssignTarget(target=small.target)], value=small.value))
            else:
                body.append(small)
        return updated.with_changes(body=body) if body else cst.RemoveFromParent()


class Shallow(cst.CSTTransformer):
    """Signature annotations erased to bare builtins."""

    def __init__(self, reg: Registry) -> None:
        super().__init__()
        self.reg = reg

    def leave_Param(self, original: cst.Param, updated: cst.Param) -> cst.Param:
        if updated.annotation is None:
            return updated
        erased = erase(updated.annotation.annotation, self.reg)
        return _plain_param(updated) if erased is None else _typed_param(updated, erased)

    def leave_FunctionDef(self, original: cst.FunctionDef, updated: cst.FunctionDef) -> cst.FunctionDef:
        if updated.returns is None:
            return updated
        erased = erase(updated.returns.annotation, self.reg)
        return updated.with_changes(returns=None if erased is None else cst.Annotation(erased))


# --------------------------------------------------------------------------- step 3: no annotations


class Untyped(cst.CSTTransformer):
    def leave_Param(self, original: cst.Param, updated: cst.Param) -> cst.Param:
        return _plain_param(updated) if updated.annotation is not None else updated

    def leave_FunctionDef(self, original: cst.FunctionDef, updated: cst.FunctionDef) -> cst.FunctionDef:
        return updated.with_changes(returns=None)


# --------------------------------------------------------------------------- pipeline


def _transform(code: str, *transformers: cst.CSTTransformer) -> str:
    module = cst.parse_module(code)
    for transformer in transformers:
        module = MetadataWrapper(module).visit(transformer) if transformer.METADATA_DEPENDENCIES else module.visit(transformer)
    return module.code


def to_l1(code: str, reg: Registry) -> str:
    """Signatures only: every annotation erased to its outer builtin, type definitions and variable annotations gone."""
    code = _transform(code, Structural(reg))
    return _transform(code, RemoveTypeDefs(reg), StripVariableAnnotations(), Shallow(reg))


def to_l0(code: str, reg: Registry) -> str:
    return _transform(to_l1(code, reg), Untyped())


def apply_mutations(package: Path, mutations: list[dict[str, str]]) -> None:
    """Apply find/replace edits (a task's planted bug, or a reference solution) to a package."""
    for mutation in mutations:
        path = package / mutation["file"]
        text = path.read_text()
        # YAML block scalars strip common indentation, so also try the snippet re-indented.
        for width in (0, 4, 8, 12):
            find = textwrap.indent(mutation["find"], " " * width)
            if text.count(find) == 1:
                path.write_text(text.replace(find, textwrap.indent(mutation["replace"], " " * width)))
                break
        else:
            raise ValueError(f"mutation must match exactly once in {mutation['file']}: {mutation['find']!r}")


def format_package(package: Path) -> None:
    """Same formatter for every version, so style never gives a version away; drops unused imports."""
    ruff = [sys.executable, "-m", "ruff"]
    config = ["--config", str(ROOT / "tools" / "ruff.toml")]
    subprocess.run([*ruff, "check", "--quiet", *config, "--select", "F401", "--fix", str(package)], check=False)
    subprocess.run([*ruff, "format", "--quiet", *config, str(package)], check=True)


def derive(arm: str, out: Path, mutations: list[dict[str, str]] | None = None, source: Path = SOURCE) -> Path:
    """Write the package for `arm` (with a task's bug applied, if any) into out/billing."""
    if arm == "natural":
        return derive_natural(out, mutations, source)
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}; choose from {available_arms()}")
    staging = out / "_l3_staging"
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(source, staging, ignore=shutil.ignore_patterns("__pycache__", ".mypy_cache"))
    apply_mutations(staging, mutations or [])
    reg = collect_registry(staging)
    target = out / source.name
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    for path in sorted(staging.glob("*.py")):
        code = path.read_text()
        (target / path.name).write_text({"l3": lambda c, r: c, "l1": to_l1, "l0": to_l0}[arm](code, reg))
    shutil.rmtree(staging)
    format_package(target)
    return target


# --------------------------------------------------------------------------- the natural rewrite


def mutation_key(mutations: list[dict[str, str]]) -> str:
    return hashlib.sha256(json.dumps(mutations, sort_keys=True).encode()).hexdigest()[:16]


def _hunks(before: str, after: str) -> list[tuple[str, str]]:
    """Line-level (find, replace) pairs turning `before` into `after`, each unique in `before`."""
    old, new = before.splitlines(keepends=True), after.splitlines(keepends=True)
    pairs = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        lo, hi = i1, i2
        while not ("".join(old[lo:hi]) and before.count("".join(old[lo:hi])) == 1):
            if lo == 0 and hi == len(old):
                raise MutationNotPortable("cannot make the change unique")
            lo, hi = max(0, lo - 1), min(len(old), hi + 1)
        pairs.append(("".join(old[lo:hi]), "".join(old[lo:i1]) + "".join(new[j1:j2]) + "".join(old[i2:hi])))
    return pairs


def derive_natural(out: Path, mutations: list[dict[str, str]] | None, source: Path = SOURCE) -> Path:
    """The natural rewrite, with a task's bug applied.

    The bug is applied to L3, both versions are stripped to L0, and the resulting L0 diff is
    applied to the natural code. Where the rewrite renamed the changed lines, a port from
    subject/natural/ports.yaml is used instead (each one verified by tools/check_tasks.py to
    break exactly the same hidden tests as in l3 and l0).
    """
    if not NATURAL.exists():
        raise FileNotFoundError(f"{NATURAL} does not exist")
    target = out / "billing"
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(NATURAL, target, ignore=shutil.ignore_patterns("__pycache__"))
    if not mutations:
        return target
    ports_file = NATURAL.parent / "ports.yaml"
    ports = (yaml.safe_load(ports_file.read_text()) or {}) if ports_file.exists() else {}
    hunks = [(h["file"], h["find"], h["replace"]) for h in ports.get(mutation_key(mutations), [])]
    if not hunks:
        plain = derive("l0", out / "_plain", None, source)
        mutated = derive("l0", out / "_mutated", mutations, source)
        for path in sorted(plain.glob("*.py")):
            before, after = path.read_text(), (mutated / path.name).read_text()
            if before != after:
                hunks += [(path.name, find, replace) for find, replace in _hunks(before, after)]
        shutil.rmtree(plain.parent)
        shutil.rmtree(mutated.parent)
    for file, find, replace in hunks:
        text = (target / file).read_text()
        if text.count(find) != 1:
            raise MutationNotPortable(f"{file}: changed lines not found verbatim in the natural code: {find!r}")
        (target / file).write_text(text.replace(find, replace))
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("arms", nargs="*", default=list(available_arms()))
    parser.add_argument("--out", type=Path, default=ROOT / "build" / "arms")
    args = parser.parse_args()
    for arm in args.arms:
        target = derive(arm, args.out / arm)
        print(f"{arm:8s} -> {target} ({sum(len(p.read_text().splitlines()) for p in target.glob('*.py'))} lines)")


if __name__ == "__main__":
    main()
