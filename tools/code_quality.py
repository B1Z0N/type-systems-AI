"""Score the code agents wrote: findings their edits introduced, per run (DESIGN.md section 9).

For every finished bug-fix or change run, rebuild the version the agent started from (with the
task's bug), apply the agent's diff, and count what ruff (F, E4, E7, E9) and mypy report before and
after. mypy runs with --strict on the typed versions and with defaults on the others, which is what
a developer of each codebase would run. Writes results/<experiment>/report/code_quality.csv.

  uv run python tools/code_quality.py main haiku codex
"""

from __future__ import annotations

import csv
import functools
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from harness.tasks import ROOT, load_tasks  # noqa: E402
from tools.derive import derive, derive_natural  # noqa: E402

RUFF = [sys.executable, "-m", "ruff", "check", "--isolated", "--select", "F,E4,E7,E9", "--output-format", "concise"]
TYPED = {"l3"}


def count_findings(root: Path, arm: str) -> tuple[int, int]:
    ruff = subprocess.run([*RUFF, "billing"], cwd=root, capture_output=True, text=True)
    ruff_n = sum(1 for line in ruff.stdout.splitlines() if re.match(r"billing/.+:\d+:\d+: ", line))
    strict = ["--strict"] if arm in TYPED else []
    mypy = subprocess.run([sys.executable, "-m", "mypy", *strict, "--no-incremental", "--ignore-missing-imports", "billing"],
                          cwd=root, capture_output=True, text=True)
    found = re.search(r"Found (\d+) error", mypy.stdout)
    return ruff_n, int(found.group(1)) if found else 0


@functools.cache
def baseline(arm: str, task_id: str, task_set: str) -> tuple[Path, tuple[int, int]]:
    """The package the agent started from, and its findings; cached per (version, task)."""
    task = next(t for t in load_tasks(task_set=task_set) if t.id == task_id)
    out = Path(tempfile.mkdtemp(prefix="cq-base-"))
    (derive_natural if arm == "natural" else functools.partial(derive, arm))(out, task.mutations)
    return out, count_findings(out, arm)


def score_run(run: Path, row: dict[str, object], task_set: str) -> dict[str, object] | None:
    patch = run / "diff.patch"
    if not patch.exists() or not patch.read_text().strip():
        return None
    arm = str(row["condition"]).split("+")[0]
    base, (ruff_before, mypy_before) = baseline(arm, str(row["task"]), task_set)
    work = Path(tempfile.mkdtemp(prefix="cq-run-"))
    try:
        shutil.copytree(base / "billing", work / "billing")
        applied = subprocess.run(["git", "apply", "--include=billing/*", str(patch.resolve())], cwd=work, capture_output=True, text=True)
        if applied.returncode != 0:
            return {"applied": False}
        ruff_after, mypy_after = count_findings(work, arm)
        return {"applied": True, "ruff_new": ruff_after - ruff_before, "mypy_new": mypy_after - mypy_before,
                "mypy_strict": arm in TYPED}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    sets = {t.id: "main" for t in load_tasks()} | {t.id: "mutants" for t in load_tasks(task_set="mutants")}
    for experiment in sys.argv[1:] or ["main"]:
        rows = []
        for path in sorted((ROOT / "results" / experiment / "runs").rglob("result.json")):
            row = json.loads(path.read_text())
            if row.get("category") not in ("bugfix", "change") or row["task"] not in sets:
                continue
            scored = score_run(path.parent, row, sets[row["task"]])
            if scored is None:
                continue
            rows.append({"experiment": experiment, "condition": row["condition"], "task": row["task"], "rep": row["rep"],
                         "passed": row["passed"], "lines_added": row.get("lines_added"),
                         "lines_removed": row.get("lines_removed"), **scored})
        out = ROOT / "results" / experiment / "report" / "code_quality.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=sorted({k for r in rows for k in r}))
            writer.writeheader()
            writer.writerows(rows)
        print(f"{experiment}: {len(rows)} runs scored -> {out}")


if __name__ == "__main__":
    main()
