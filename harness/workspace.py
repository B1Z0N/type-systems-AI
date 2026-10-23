"""Building the isolated repository an agent works in."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from tools.derive import derive

from .tasks import ROOT, Task

VISIBLE_TESTS = ROOT / "subject" / "visible_tests"
CONDITIONS = ("l0", "l1", "l3", "l3+tc", "l3+lint", "natural")

README = """# billing-service

Subscription billing engine: customers, plans and prices, subscriptions, invoices, coupons,
tax, payments and dunning, refunds, webhooks and reports.

- Code: `billing/`
- Tests: `tests/`
"""

GUIDE = """# Working in this repository

- Python environment: `.venv/` (already set up, no network needed).
- Run the tests: `.venv/bin/python -m pytest -q`
"""

CHECKER_GUIDE = "- Type-check: `.venv/bin/python -m mypy billing`\n"
LINT_GUIDE = "- Lint: `.venv/bin/python -m ruff check billing`\n"

PYPROJECT = """[project]
name = "billing-service"
version = "0.3.0"
requires-python = ">=3.11"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
"""

MYPY_STRICT = """
[tool.mypy]
strict = true
"""

# Pyflakes plus syntax-level checks: the package is clean under them, and none of them reads types.
RUFF_LINT = """
[tool.ruff]
line-length = 120

[tool.ruff.lint]
select = ["F", "E4", "E7", "E9"]
"""

# Each agent reads its own instruction file; the text is the same.
INSTRUCTIONS = {"claude": "CLAUDE.md", "codex": "AGENTS.md"}


@dataclass
class Condition:
    """A version of the code (l0, l1, l3, natural) plus an advertised extra checker: mypy (+tc) or ruff (+lint)."""

    arm: str
    tool: str = ""  # "" | "tc" | "lint"

    @property
    def checker(self) -> bool:
        return self.tool == "tc"

    @property
    def name(self) -> str:
        return f"{self.arm}+{self.tool}" if self.tool else self.arm

    @classmethod
    def parse(cls, text: str) -> "Condition":
        arm, _, tool = text.partition("+")
        if tool not in ("", "tc", "lint"):
            raise ValueError(f"bad condition {text!r}; use e.g. l3, l3+tc or l3+lint")
        return cls(arm, tool)


def _git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "dev",
        "GIT_AUTHOR_EMAIL": "dev@example.com",
        "GIT_COMMITTER_NAME": "dev",
        "GIT_COMMITTER_EMAIL": "dev@example.com",
    }
    out = subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True, text=True, check=True)
    return out.stdout


def _make_venv(repo: Path, tool: str) -> None:
    packages = ["pytest>=8"] + {"": [], "tc": ["mypy>=1.11"], "lint": ["ruff>=0.6"]}[tool]
    subprocess.run(["uv", "venv", "-q", "--python", sys.executable, str(repo / ".venv")], check=True, capture_output=True)
    cmd = ["uv", "pip", "install", "-q", "--python", str(repo / ".venv" / "bin" / "python"), *packages]
    if subprocess.run([*cmd, "--offline"], capture_output=True).returncode != 0:
        subprocess.run(cmd, check=True, capture_output=True)


def build(task: Task, condition: Condition, base_dir: Path | None = None, cli: str = "claude") -> Path:
    """Create a fresh git repository for one run and return its path."""
    parent = Path(tempfile.mkdtemp(prefix="bsvc-", dir=base_dir))
    repo = parent / "billing-service"
    repo.mkdir()
    staging = parent / "_derive"
    derive(condition.arm, staging, task.mutations)
    shutil.move(str(staging / "billing"), repo / "billing")
    shutil.rmtree(staging)
    shutil.copytree(VISIBLE_TESTS, repo / "tests", ignore=shutil.ignore_patterns("__pycache__"))
    guide = GUIDE + {"": "", "tc": CHECKER_GUIDE, "lint": LINT_GUIDE}[condition.tool]
    (repo / "README.md").write_text(README)
    (repo / INSTRUCTIONS[cli]).write_text(guide)
    pyproject = PYPROJECT + (MYPY_STRICT if condition.checker and condition.arm == "l3" else "")
    pyproject += RUFF_LINT if condition.tool == "lint" else ""
    (repo / "pyproject.toml").write_text(pyproject)
    (repo / ".gitignore").write_text(".venv/\n__pycache__/\n.pytest_cache/\n.mypy_cache/\nanswer.json\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "Initial commit")
    _make_venv(repo, condition.tool)
    return repo


def diff(repo: Path) -> tuple[str, dict[str, int]]:
    """Return the working-tree diff against the initial commit and simple stats."""
    _git(repo, "add", "-A")
    patch = _git(repo, "diff", "--cached", "--", ".", ":(exclude)answer.json")
    numstat = _git(repo, "diff", "--cached", "--numstat", "--", ".", ":(exclude)answer.json")
    added = removed = files = 0
    for line in numstat.splitlines():
        a, r, _ = line.split("\t", 2)
        files += 1
        added += int(a) if a.isdigit() else 0
        removed += int(r) if r.isdigit() else 0
    return patch, {"files_changed": files, "lines_added": added, "lines_removed": removed}


def cleanup(repo: Path) -> None:
    shutil.rmtree(repo.parent, ignore_errors=True)
