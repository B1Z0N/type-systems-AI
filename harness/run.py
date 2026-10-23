"""Run the experiment: every task x every condition x N repetitions, with one agent (Claude Code or Codex).

  uv run python -m harness.run --experiment main --reps 3            # run (resumable)
  uv run python -m harness.run --experiment main --reps 3 --dry-run  # show what is left

Durability: each finished run is saved the moment it ends, in two places:
  results/<experiment>/runs/<condition>/<task>/rep<N>/   prompt, raw events, metrics, grade, diff, result.json
  results/<experiment>/runs.jsonl                        one line per finished run, appended and flushed
result.json is written last and atomically, so a run either counts completely or not at all.
Re-running the same command skips finished runs, so an interruption (crash, reboot, usage
limit, this machine sleeping) loses at most the runs that were in flight.

Usage limits: when the subscription refuses a run, the attempt is discarded (it is not an
agent failure) and the runner waits until the reset time, then continues.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.derive import MutationNotPortable

from .agents import AgentConfig, UsageLimit, check_version, load_agents, run_agent, wait_for
from .grading import grade
from .tasks import ROOT, Task, load_tasks
from .traces import summarize
from .workspace import CONDITIONS, Condition, build, cleanup, diff

_log_lock = threading.Lock()


def _write_json(path: Path, data: Any) -> None:
    """Write atomically: a crash leaves either the old file or the complete new one."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    os.replace(tmp, path)


def _append_log(results: Path, row: dict[str, Any]) -> None:
    with _log_lock, open(results / "runs.jsonl", "a") as log:
        log.write(json.dumps(row, default=str) + "\n")
        log.flush()
        os.fsync(log.fileno())


def run_one(agent: AgentConfig, condition: Condition, task: Task, rep: int, out: Path, results: Path) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    try:
        repo = build(task, condition, cli=agent.cli)
    except MutationNotPortable as error:
        # the natural rewrite has no counterpart for this bug: recorded, retried on the next invocation
        _write_json(out / "skipped.json", {"reason": str(error)})
        return {"skipped": True}
    prompt = task.render_prompt()
    started = datetime.now(timezone.utc).isoformat()
    try:
        result = run_agent(agent, prompt, repo)
        if result.limit_hit:
            raise UsageLimit(result.retry_at, "subscription limit")
        (out / "prompt.txt").write_text(prompt)
        (out / "events.jsonl").write_text("\n".join(json.dumps(e) for e in result.events))
        (out / "stderr.txt").write_text(result.stderr[-20000:])
        metrics = summarize(result.events, repo, task.relevant_files, agent.cli)
        metrics.update(wall_s=round(result.wall_s, 1), timed_out=result.timed_out, exit_code=result.exit_code)
        grading = grade(task, repo)
        patch, diffstat = diff(repo)
        (out / "diff.patch").write_text(patch)
        if (repo / "answer.json").exists():
            shutil.copy(repo / "answer.json", out / "answer.json")
        _write_json(out / "metrics.json", metrics)
        _write_json(out / "grade.json", grading)
        row = {
            "experiment": results.name, "agent": agent.name, "model": agent.model, "cli_version": result.cli_version,
            "condition": condition.name, "task": task.id, "category": task.category, "stratum": task.stratum,
            "checker_catches_at": task.checker_catches_at, "rep": rep, "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            **{k: v for k, v in metrics.items() if k not in ("steps", "final_message", "files_read", "files_edited")},
            **diffstat,
            "score": grading.get("score"), "passed": grading.get("passed"),
            "location_correct": grading.get("location_correct"), "regressions": len(grading.get("regressions") or []),
            "answer_error": grading.get("answer_error"),
            "modified_source_on_read_only": task.read_only and diffstat["files_changed"] > 0,
            "files_read": metrics.get("files_read"), "files_edited": metrics.get("files_edited"),
        }
        _write_json(out / "result.json", row)  # last: marks the run as done
        _append_log(results, row)
        return row
    finally:
        cleanup(repo)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--experiment", default="main")
    parser.add_argument("--agent", default="claude-sonnet")
    parser.add_argument("--conditions", nargs="+", default=["l0", "l3", "l3+tc", "natural"], choices=CONDITIONS)
    parser.add_argument("--tasks", nargs="+", help="task ids, categories or strata (default: all)")
    parser.add_argument("--task-set", default="main", choices=["main", "mutants"])
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--parallel", type=int, default=2)
    parser.add_argument("--seed", type=int, default=0, help="run order is shuffled so time effects hit all conditions alike")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    agent = load_agents()[args.agent]
    problem = check_version(agent)
    if problem and not args.dry_run:
        raise SystemExit(problem + " (update harness/agents.yaml)")
    conditions = [Condition.parse(c) for c in args.conditions]
    tasks = load_tasks(args.tasks, args.task_set)
    results = ROOT / "results" / args.experiment
    results.mkdir(parents=True, exist_ok=True)
    jobs = [(c, t, r) for c in conditions for t in tasks for r in range(args.reps)]
    random.Random(args.seed).shuffle(jobs)
    todo = [(c, t, r, results / "runs" / c.name / t.id / f"rep{r}") for c, t, r in jobs]
    todo = [j for j in todo if not (j[3] / "result.json").exists()]
    print(f"{len(jobs)} runs planned, {len(jobs) - len(todo)} done, {len(todo)} to go", flush=True)
    if args.dry_run:
        return

    def work(item: tuple[Condition, Task, int, Path]) -> dict[str, Any]:
        condition, task, rep, out = item
        while True:
            try:
                return run_one(agent, condition, task, rep, out, results)
            except UsageLimit as limit:
                with _log_lock, open(results / "limits.log", "a") as log:
                    log.write(f"{datetime.now(timezone.utc).isoformat()} {task.id} retry_at={limit.retry_at}\n")
                wait_for(limit, lambda msg: print(msg, flush=True))

    done = 0
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futures = {pool.submit(work, item): item for item in todo}
        for future in as_completed(futures):
            condition, task, rep, out = futures[future]
            done += 1
            try:
                row = future.result()
                if row.get("skipped"):
                    print(f"[{done}/{len(todo)}] {condition.name:8s} {task.id:28s} rep{rep} skipped (bug not portable)", flush=True)
                    continue
                print(
                    f"[{done}/{len(todo)}] {condition.name:8s} {task.id:28s} rep{rep} passed={row['passed']!s:5s} "
                    f"tokens={row['total_input_tokens']:>8,} files={row['unique_files_read']:2d} wall={row['wall_s']:.0f}s",
                    flush=True,
                )
            except Exception:
                (out / "error.txt").write_text(traceback.format_exc())
                print(f"[{done}/{len(todo)}] {condition.name} {task.id} rep{rep} ERROR (see {out / 'error.txt'})", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
