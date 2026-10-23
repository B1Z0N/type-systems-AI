"""Export everything the blog post's interactive figures show into one JSON file.

  uv run python tools/export_blog_data.py <out.json>

Contents:
- runs: every graded run of every experiment, with its metrics and a shortened trace (one entry per
  tool call: kind, tool, label, output size, error, tokens spent so far where the agent reports it)
- comparisons: every pre-declared comparison (DESIGN.md sections 5, 8, 9), computed with
  harness/stats.py exactly as in the reports, including the Holm-adjusted p-values
- quality: tools/code_quality.py's per-run findings
- prompts and titles of every task
Temporary paths and home directories are removed from commands, so no local path leaves the machine.
"""

from __future__ import annotations

import csv
import gzip
import json
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from harness.analyze import EXPERIMENTS, MAIN_COMPARISONS, METRICS as REPORT_METRICS, load  # noqa: E402
from harness.stats import compare, holm  # noqa: E402
from harness.tasks import ROOT  # noqa: E402

# experiment key in the post -> (results directory, agent)
SOURCES = {
    "main": ("main", "claude-sonnet"), "codex": ("codex", "codex"), "haiku": ("haiku", "claude-haiku"),
    "lint": ("lint", "claude-sonnet"),
    "mutants": ("mutants", "claude-sonnet"), "mutants-codex": ("mutants-codex", "codex"),
    "vunnel-sonnet": ("vunnel", "claude-sonnet"), "vunnel-codex": ("vunnel", "codex"),
}
METRICS = {"total_tokens": True, "uncached_tokens": True, "wall_s": True, "tool_calls": True,
           "unique_files_read": True, "read_chars": True, "passed": False, "typecheck_runs": False}
TMP = re.compile(r"/(?:private/)?(?:var/folders|tmp)/\S*?/(?=(?:billing|src|tests|schema|billing-service|vunnel)\b)")
OTHER = re.compile(r"/(?:private/)?(?:var/folders|Users|tmp)/[^\s'\"]*")


def short(text: object, n: int = 150) -> str:
    text = OTHER.sub("<tmp>", TMP.sub("", " ".join(str(text or "").split())))
    return text if len(text) <= n else text[: n - 1] + "…"


def events_of(run: Path) -> list[dict]:
    for name, opener in (("events.jsonl.gz", gzip.open), ("events.jsonl", open)):
        if (run / name).exists():
            with opener(run / name, "rt") as handle:
                return [json.loads(line) for line in handle if line.strip()]
    return []


def claude_tokens_per_call(events: list[dict]) -> list[int]:
    """Tokens spent so far when each tool call was issued (Claude Code reports usage per message)."""
    seen, total, out, ids = set(), 0, [], set()
    for event in events:
        message = event.get("message") or {}
        if event.get("type") != "assistant" or not isinstance(message, dict):
            continue
        if message.get("id") not in seen:
            seen.add(message.get("id"))
            usage = message.get("usage") or {}
            total += sum(usage.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens",
                                                       "cache_creation_input_tokens", "output_tokens"))
        for block in message.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block["id"] not in ids:
                ids.add(block["id"])
                out.append(total)
    return out


def trace(run: Path, cli: str) -> list[list]:
    metrics = json.loads((run / "metrics.json").read_text()) if (run / "metrics.json").exists() else {}
    steps = metrics.get("steps") or []
    cumulative = claude_tokens_per_call(events_of(run)) if cli == "claude" else []
    out = []
    for i, step in enumerate(steps):
        label = step.get("command") or ", ".join(step.get("paths") or []) or step.get("tool", "")
        kind = step.get("kind", "other")
        if step.get("paths") and all(Path(p).name == "answer.json" for p in step["paths"]) and kind == "edit":
            kind = "answer"
        out.append([kind, step.get("tool", ""), short(label), step.get("output_chars", 0), int(bool(step.get("error"))),
                    cumulative[i] if i < len(cumulative) else None])
    return out


def comparisons() -> dict[str, dict]:
    """Every declared comparison, as {key: {name, agent, a, b, rows: {metric: [est, lo, hi, p, p_holm, tasks]}}}."""
    groups = {
        "main": ("main", None, MAIN_COMPARISONS), "codex": ("codex", None, MAIN_COMPARISONS),
        "haiku": ("haiku", None, MAIN_COMPARISONS[:2]),
        "lint": ("lint", "claude-sonnet", EXPERIMENTS["lint"][1]),
        "mutants": ("mutants", None, EXPERIMENTS["mutants"][1]),
        "mutants-codex": ("mutants-codex", None, EXPERIMENTS["mutants-codex"][1]),
        "vunnel-sonnet": ("vunnel", "claude-sonnet", EXPERIMENTS["vunnel"][1]),
        "vunnel-codex": ("vunnel", "codex", EXPERIMENTS["vunnel"][1]),
    }
    out = {}
    for key, (experiment, agent, comps) in groups.items():
        sources = EXPERIMENTS.get(experiment, ([experiment], None))[0]
        frame = load(sources)
        if agent:
            frame = frame[frame["agent"] == agent]
        frame = frame.assign(condition=frame["condition"].astype(str), category=frame["category"].astype(str))
        for name, desc, a, b, query in comps:
            # adjust across the same metric family as the reports, then keep the metrics the post shows
            rows = {m: compare(frame.query(query), m, a, b, log) for m, (_, log) in REPORT_METRICS.items()}
            tested = [m for m, r in rows.items() if r["tasks"] and r["p"] == r["p"]]
            adjusted = dict(zip(tested, holm([rows[m]["p"] for m in tested])))
            out[f"{key}:{name}"] = {
                "group": key, "name": name, "desc": desc, "a": a, "b": b,
                "rows": {m: [round(r["estimate"], 4), round(r["ci_low"], 4), round(r["ci_high"], 4), round(r["p"], 4),
                             round(adjusted.get(m, float("nan")), 4), r["tasks"]]
                         for m, r in rows.items() if m in METRICS and r["tasks"] and r["estimate"] == r["estimate"]},
            }
    return out


def main(out: Path) -> None:
    runs = []
    for key, (directory, agent) in SOURCES.items():
        cli = "codex" if agent == "codex" else "claude"
        for path in sorted((ROOT / "results" / directory / "runs").rglob("result.json")):
            row = json.loads(path.read_text())
            if row.get("agent") != agent or (key == "lint" and row["condition"] != "l3+lint"):
                continue
            run = path.parent
            metrics = json.loads((run / "metrics.json").read_text()) if (run / "metrics.json").exists() else {}
            total = (row.get("total_input_tokens") or 0) + (row.get("output_tokens") or 0)
            runs.append({
                "x": key, "c": row["condition"], "t": row["task"], "k": row.get("category"), "s": row.get("stratum"),
                "r": row["rep"], "p": 1 if row.get("passed") else 0, "sc": round(row.get("score") or 0, 3),
                "tok": total, "w": round(row.get("wall_s") or 0, 1), "f": row.get("unique_files_read") or 0,
                "tc": row.get("tool_calls") or 0, "ch": row.get("read_chars") or 0,
                "ty": row.get("typecheck_runs") or 0, "li": metrics.get("lint_runs") or 0, "te": row.get("test_runs") or 0,
                "fin": short(metrics.get("final_message"), 420), "steps": trace(run, cli),
            })
    prompts, titles = {}, {}
    for path in [*(ROOT / "tasks").glob("*.yaml"), *(ROOT / "tasks" / "mutants").glob("*.yaml")]:
        task = yaml.safe_load(path.read_text())
        titles[task["id"]] = task.get("title", "")
    for path in sorted((ROOT / "results").rglob("prompt.txt")):
        task = path.parent.parent.name
        prompts.setdefault(task, path.read_text().strip())
    quality = []
    for experiment in ("main", "codex", "haiku", "lint", "mutants", "mutants-codex"):
        report = ROOT / "results" / experiment / "report" / "code_quality.csv"
        for row in csv.DictReader(report.open()):
            if row.get("applied") == "True":
                quality.append({"x": experiment, "c": row["condition"], "t": row["task"],
                                "mypy": int(row["mypy_new"]), "ruff": int(row["ruff_new"])})
    data = {"runs": runs, "comparisons": comparisons(), "quality": quality, "prompts": prompts, "titles": titles}
    out.write_text(json.dumps(data, separators=(",", ":")))
    print(f"{len(runs)} runs, {len(data['comparisons'])} comparisons, {len(quality)} quality rows -> {out} "
          f"({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
