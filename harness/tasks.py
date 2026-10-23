"""Loading task definitions and rendering their prompts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .ground_truth import compute

ROOT = Path(__file__).resolve().parent.parent
TASK_DIR = ROOT / "tasks"
CATEGORY_ORDER = ["question", "list", "bugfix", "change"]


@dataclass
class Task:
    id: str
    category: str  # question | list | bugfix | change
    stratum: str  # type_relevant (types state the answer) | type_neutral (control)
    prompt: str
    answer_schema: str
    grader: dict[str, Any]
    read_only: bool
    relevant_files: list[str]
    mutations: list[dict[str, str]] = field(default_factory=list)  # the planted bug, applied to the L3 source
    expected_location: dict[str, str] | None = None
    checker_catches_at: str | None = None  # does mypy --strict flag the bug at L3? ("l2"/"l3" yes, "none" no)

    def render_prompt(self) -> str:
        schema = self.answer_schema.strip()
        if self.read_only:
            footer = (
                "Do not modify any files other than `answer.json`. When you are done, write your final "
                "answer as JSON to `answer.json` in the repository root, using exactly this shape:\n"
            )
        else:
            footer = "When you are done, also write `answer.json` in the repository root with this shape:\n"
        return f"{self.prompt.strip()}\n\n{footer}{schema}\n"


def _resolve(value: Any, truth: dict[str, Any]) -> Any:
    """Replace {auto: key} with the answer computed from the source (harness/ground_truth.py)."""
    if isinstance(value, dict):
        if "auto" in value:
            return {**value, "value": truth[value["auto"]]}
        return {key: _resolve(item, truth) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve(item, truth) for item in value]
    return value


def load_task(path: Path) -> Task:
    raw = yaml.safe_load(path.read_text())
    grader = raw["grader"]
    if "auto" in str(grader.get("expected", {})):
        grader = {**grader, "expected": _resolve(grader["expected"], compute())}
    return Task(
        id=raw["id"],
        category=raw["category"],
        stratum=raw["stratum"],
        prompt=raw["prompt"],
        answer_schema=raw["answer_schema"],
        grader=grader,
        read_only=raw["read_only"],
        relevant_files=raw.get("relevant_files", []),
        mutations=raw.get("mutations", []),
        expected_location=raw.get("expected_location"),
        checker_catches_at=raw.get("checker_catches_at"),
    )


TASK_SETS = {"main": TASK_DIR, "mutants": TASK_DIR / "mutants"}  # mutants: 20 random bugs (DESIGN.md section 9)


def load_tasks(ids: list[str] | None = None, task_set: str = "main") -> list[Task]:
    """All tasks of a set, optionally filtered by task ids, categories or strata."""
    tasks = [load_task(path) for path in sorted(TASK_SETS[task_set].glob("*.yaml"))]
    if ids:
        unknown = [i for i in ids if not any(i in (t.id, t.category, t.stratum) for t in tasks)]
        if unknown:
            raise SystemExit(f"unknown tasks or filters: {unknown}")
        tasks = [t for t in tasks if any(i in (t.id, t.category, t.stratum) for i in ids)]
    return sorted(tasks, key=lambda t: (CATEGORY_ORDER.index(t.category), t.id))
