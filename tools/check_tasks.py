"""Sanity-check every task before spending agent time on it.

- questions and lists: a perfect answer scores 1.0 and an empty answer scores 0.0
- bug fixes: the bug applies to every version (l3, l0, natural) and breaks exactly the same
  hidden tests in each; the target tests pass without the bug; mypy catches the bug at L3
  exactly when the task says so
- changes: the hidden task tests fail on the original code of every version and pass with the
  reference solution (solutions/<id>.yaml) with no regressions; the solution passes mypy --strict
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from harness.grading import grade, run_hidden_tests  # noqa: E402
from harness.tasks import ROOT, Task, load_tasks  # noqa: E402
from tools.derive import SOURCE, MutationNotPortable, apply_mutations, available_arms, derive  # noqa: E402


def perfect_answer(task: Task) -> dict[str, object]:
    answer: dict[str, object] = {}
    for key, spec in task.grader["expected"].items():
        if isinstance(spec, dict) and "required" in spec:
            answer[key] = spec["required"]
        elif isinstance(spec, dict) and "value" in spec:
            answer[key] = spec["value"]
        else:
            answer[key] = spec
    return answer


def mypy_errors(package_root: Path) -> int:
    out = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", "--no-incremental", "billing"],
        cwd=package_root, capture_output=True, text=True,
    )
    return 0 if out.returncode == 0 else out.stdout.count(": error:")


def check(task: Task, tmp: Path, arms: tuple[str, ...]) -> list[str]:
    problems: list[str] = []
    work = tmp / task.id
    if task.read_only:
        work.mkdir()
        (work / "answer.json").write_text(json.dumps(perfect_answer(task)))
        if grade(task, work)["score"] != 1.0:
            problems.append("perfect answer does not score 1.0")
        (work / "answer.json").write_text("{}")
        if grade(task, work)["score"] != 0.0:
            problems.append("empty answer scores above 0")
        return problems

    if task.category == "bugfix":
        for arm in arms:
            try:
                derive(arm, work / arm, task.mutations)
            except MutationNotPortable as error:
                problems.append(f"bug cannot be applied to the {arm} version: {error}")
        if problems:
            return problems
        buggy = grade(task, work / "l3")
        if buggy["target_passed"] == buggy["target_total"]:
            problems.append("target tests pass with the bug planted")
        if buggy["regressions"]:
            problems.append(f"bug breaks non-target tests: {buggy['regressions']}")
        reference = run_hidden_tests(work / "l3", [])
        for arm in arms:
            # the bug must break exactly the same hidden tests in every version
            if run_hidden_tests(work / arm, []) != reference:
                problems.append(f"bug behaves differently in the {arm} version")
        derive("l3", work / "fixed")
        if not grade(task, work / "fixed")["passed"]:
            problems.append("target tests fail on the original code")
        errors = mypy_errors(work / "l3")
        if task.checker_catches_at in ("l2", "l3") and errors == 0:
            problems.append("expected mypy to catch the bug at L3")
        if task.checker_catches_at == "none" and errors:
            problems.append(f"mypy unexpectedly reports {errors} errors at L3")
        return problems

    for arm in available_arms():
        original = grade(task, derive(arm, work / f"original_{arm}").parent)
        if original["target_passed"] == original["target_total"]:
            problems.append(f"task tests already pass on the original {arm} code")
    solution = ROOT / "solutions" / f"{task.id}.yaml"
    if not solution.exists():
        return problems + ["no reference solution"]
    staged = work / "solution_src" / "billing"
    shutil.copytree(SOURCE, staged)
    apply_mutations(staged, yaml.safe_load(solution.read_text())["edits"])
    derive("l3", work / "solved", source=staged)
    result = grade(task, work / "solved")
    if not result["passed"]:
        problems.append(f"reference solution fails: {result.get('failed_targets')} regressions={result.get('regressions')}")
    errors = mypy_errors(work / "solved")
    if errors:
        problems.append(f"reference solution has {errors} mypy --strict errors")
    for arm in ("l3", "l0"):  # the reference solution is written against L3; natural is a separate rewrite
        derive(arm, work / f"solved_{arm}", source=staged)
        if not grade(task, work / f"solved_{arm}")["passed"]:
            problems.append(f"reference solution fails in arm {arm}")
    return problems


def main() -> int:
    """  uv run python tools/check_tasks.py [ids...]            the main task set, every version
       uv run python tools/check_tasks.py --set mutants      the random bugs, in the versions they run in"""
    args = sys.argv[1:]
    task_set = "main"
    if args[:1] == ["--set"]:
        task_set, args = args[1], args[2:]
    tasks = load_tasks(args or None, task_set)
    arms = available_arms() if task_set == "main" else ("l3", "l1", "l0")
    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        for task in tasks:
            problems = check(task, Path(tmp), arms)
            failed += bool(problems)
            print(f"{'ok ' if not problems else 'FAIL'} {task.id:28s} {task.category:15s} {'; '.join(problems)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
