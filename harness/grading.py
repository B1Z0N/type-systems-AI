"""Scoring an agent's workspace against a task's ground truth."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .tasks import ROOT, Task

HIDDEN = ROOT / "hidden_tests"


def read_answer(workspace: Path) -> tuple[Any, str | None]:
    path = workspace / "answer.json"
    if not path.exists():
        return None, "answer.json missing"
    text = path.read_text().strip()
    try:
        return json.loads(text), None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            try:
                return json.loads(match.group(0)), "answer.json had extra text"
            except json.JSONDecodeError:
                pass
        return None, "answer.json is not valid JSON"


def _norm(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return int(value) if float(value).is_integer() else value
    if isinstance(value, str):
        text = value.strip().strip("`").strip().lower()
        if text in ("true", "yes"):
            return True
        if text in ("false", "no"):
            return False
        if re.fullmatch(r"-?\d+", text):
            return int(text)
        return re.sub(r"[\s\-]+", "_", text)
    if isinstance(value, list):
        return [_norm(v) for v in value]
    return value


def grade_fields(task: Task, answer: Any) -> dict[str, Any]:
    expected: dict[str, Any] = task.grader["expected"]
    results = {}
    for key, spec in expected.items():
        want = spec["value"] if isinstance(spec, dict) and "value" in spec else spec
        got = answer.get(key) if isinstance(answer, dict) else None
        ok = _norm(got) == _norm(want)
        results[key] = {"ok": ok, "got": got, "want": want}
    score = sum(r["ok"] for r in results.values()) / len(results)
    return {"score": score, "passed": score == 1.0, "fields": results}


def _function_variants(name: str) -> set[str]:
    parts = name.split(".")
    variants = {name}
    if len(parts) == 3:  # module.Class.method
        variants |= {f"{parts[0]}.{parts[2]}", f"{parts[1]}.{parts[2]}"}
    return {v.lower() for v in variants}


def _clean_name(name: str) -> str:
    name = str(name).strip().strip("`").removesuffix("()")
    name = re.sub(r"\(.*\)$", "", name)
    for prefix in ("billing.", "billing/"):
        name = name.removeprefix(prefix)
    return name.replace("/", ".").replace(".py.", ".").replace(".py:", ".").replace("::", ".").lower()


def grade_set(spec: Any, got: Any) -> dict[str, Any]:
    if isinstance(spec, dict) and "required" in spec:
        required, allowed = list(spec["required"]), list(spec.get("allowed", []))
    else:
        required = list(spec["value"] if isinstance(spec, dict) else spec)
        allowed = []
    answers = [str(a) for a in got] if isinstance(got, list) else []

    def matches(a: str, want: str) -> bool:
        return _clean_name(a) in _function_variants(want) or _norm(a) == _norm(want)

    hit_required = {w for w in required if any(matches(a, w) for a in answers)}
    false_positives = [a for a in answers if not any(matches(a, w) for w in required + allowed)]
    tp = len(hit_required)
    precision = tp / (tp + len(false_positives)) if answers else 0.0
    recall = tp / len(required) if required else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "missed": sorted(set(required) - hit_required),
        "false_positives": false_positives,
    }


def grade_sets(task: Task, answer: Any) -> dict[str, Any]:
    results = {}
    for key, spec in task.grader["expected"].items():
        got = answer.get(key) if isinstance(answer, dict) else None
        results[key] = grade_set(spec, got)
    score = sum(r["f1"] for r in results.values()) / len(results)
    return {"score": score, "passed": all(r["f1"] == 1.0 for r in results.values()), "sets": results}


def run_hidden_tests(package_root: Path, extra: list[str], timeout: int = 300) -> dict[str, str]:
    """Run hidden base tests plus `extra` test files against package_root/billing. Returns nodeid -> outcome."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        shutil.copytree(package_root / "billing", tmpdir / "billing", ignore=shutil.ignore_patterns("__pycache__"))
        tests = tmpdir / "hidden"
        tests.mkdir()
        shutil.copy(HIDDEN / "conftest.py", tests / "conftest.py")
        shutil.copytree(HIDDEN / "base", tests / "base")
        for rel in extra:
            (tests / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(HIDDEN / rel, tests / rel)
        report = tmpdir / "report.xml"
        env = {**os.environ, "PYTHONPATH": str(tmpdir)}
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={report}", "--rootdir", str(tests), str(tests)]
        try:
            subprocess.run(cmd, cwd=tmpdir, env=env, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"__timeout__": "failed"}
        if not report.exists():
            return {"__collection__": "failed"}
        outcomes = {}
        for case in ET.parse(report).getroot().iter("testcase"):
            module = case.get("classname", "").replace(".", "/")
            nodeid = f"{module}.py::{case.get('name')}"
            failed = any(child.tag in ("failure", "error") for child in case)
            skipped = any(child.tag == "skipped" for child in case)
            outcomes[nodeid] = "failed" if failed else "skipped" if skipped else "passed"
        return outcomes


def grade_tests(task: Task, workspace: Path) -> dict[str, Any]:
    task_files = task.grader.get("task_tests", [])
    outcomes = run_hidden_tests(workspace, task_files)
    if "__timeout__" in outcomes or "__collection__" in outcomes:
        return {"score": 0.0, "passed": False, "error": next(iter(outcomes)), "target_passed": 0, "target_total": 0, "regressions": []}
    targets = list(task.grader.get("target_tests", []))
    for rel in task_files:
        targets += [node for node in outcomes if node.startswith(rel.removesuffix(".py"))]
    targets = [t for t in targets if t in outcomes] or targets
    target_passed = sum(outcomes.get(t) == "passed" for t in targets)
    superseded = set(task.grader.get("superseded_tests", []))  # old behavior the task deliberately changes
    regressions = sorted(n for n, o in outcomes.items() if o != "passed" and n not in targets and n not in superseded)
    score = target_passed / len(targets) if targets else 0.0
    return {
        "score": score,
        "passed": score == 1.0 and not regressions,
        "target_passed": target_passed,
        "target_total": len(targets),
        "regressions": regressions,
        "failed_targets": sorted(t for t in targets if outcomes.get(t) != "passed"),
    }


def grade(task: Task, workspace: Path) -> dict[str, Any]:
    answer, answer_error = read_answer(workspace)
    kind = task.grader["type"]
    if kind == "fields":
        result = grade_fields(task, answer)
    elif kind == "sets":
        result = grade_sets(task, answer)
    elif kind == "tests":
        result = grade_tests(task, workspace)
    else:
        raise ValueError(f"unknown grader {kind}")
    if task.expected_location:
        loc = answer if isinstance(answer, dict) else {}
        file_ok = str(loc.get("file", "")).replace("billing/", "").strip() == task.expected_location["file"]
        func = str(loc.get("function", "")).strip().removesuffix("()").split(".")[-1]
        result["location_correct"] = file_ok and func == task.expected_location["function"]
    result["answer"] = answer
    result["answer_error"] = answer_error
    return result
