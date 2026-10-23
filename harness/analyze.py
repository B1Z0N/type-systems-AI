"""Turn finished runs into the report: tables, the pre-declared comparisons, and charts.

  uv run python -m harness.analyze --experiment main      (or haiku, codex, lint, mutants, mutants-codex)
  uv run python -m harness.analyze --experiment vunnel --agent codex

Writes results/<experiment>/report/{report.md, runs.csv, *.png}. Metrics are recomputed from
the raw event streams, so parser fixes apply to old runs.
"""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .agents import load_agents
from .stats import compare, holm
from .tasks import CATEGORY_ORDER, ROOT, load_tasks
from .traces import summarize

CONDITION_ORDER = ["l0", "l1", "natural", "l3", "l3+tc", "l3+lint"]

# metric -> (label, compared as a ratio?)
METRICS = {
    "passed": ("success rate", False),
    "score": ("score (partial credit)", False),
    "wall_s": ("wall time (s)", True),
    "total_tokens": ("tokens processed", True),
    "uncached_tokens": ("tokens without cache reads", True),
    "cost_usd": ("API-equivalent cost ($)", True),
    "turns": ("model turns", True),
    "tool_calls": ("tool calls", True),
    "unique_files_read": ("source files opened", True),
    "read_chars": ("characters read from files", True),
    "searches": ("searches (grep/glob/ls)", True),
    "edits": ("edit actions", True),
    "test_runs": ("test runs", False),
    "typecheck_runs": ("type-checker runs", False),
    "lint_runs": ("linter runs", False),
    "steps_before_first_edit": ("steps before first edit", True),
}

# The comparisons declared in DESIGN.md before running (sections 5 and 9).
MAIN_COMPARISONS = [
    ("C1", "Do types help? (where they should)", "l3", "l0", "stratum == 'type_relevant'"),
    ("C1-control", "Do types help? (where they shouldn't: control)", "l3", "l0", "stratum == 'type_neutral'"),
    ("C2", "Does a type checker help on top of types? (code changes)", "l3+tc", "l3", "category in ['bugfix', 'change']"),
    ("C3", "Types vs well-named untyped code", "l3", "natural", "stratum == 'type_relevant'"),
]
MUTANT_COMPARISONS = [
    ("R3", "Do types help on random bugs? (where they should)", "l3", "l0", "stratum == 'type_relevant'"),
    ("R3-control", "...and where they shouldn't", "l3", "l0", "stratum == 'type_neutral'"),
    ("R4a", "Partial typing against none", "l1", "l0", "task == task"),
    ("R4b", "Full typing against partial", "l3", "l1", "task == task"),
    ("R5", "Type checker on bugs mypy catches", "l3+tc", "l3", "checker_catches_at in ['l2', 'l3']"),
]
# experiment -> (result directories it combines, its comparisons)
EXPERIMENTS = {
    "lint": (["main", "lint"], [
        ("R2", "Type checker against an advertised linter (code changes)", "l3+tc", "l3+lint", "category in ['bugfix', 'change']"),
        ("R2-lint", "Advertised linter against nothing (code changes)", "l3+lint", "l3", "category in ['bugfix', 'change']"),
    ]),
    "mutants": (["mutants"], MUTANT_COMPARISONS),
    "mutants-codex": (["mutants-codex"], MUTANT_COMPARISONS[:2]),
    # vunnel ran on the `full` branch's harness; its metrics are taken from result.json as recorded
    "vunnel": (["vunnel"], [
        ("R6", "Types on a real codebase", "l3", "l0", "task == task"),
        ("R6-tc", "Type checker on a real codebase", "l3+tc", "l3", "task == task"),
        ("R4a", "Partial typing against none", "l1", "l0", "task == task"),
        ("R4b", "Full typing against partial", "l3", "l1", "task == task"),
    ]),
}


def read_events(run_dir: Path) -> list[dict] | None:
    """The raw event stream, stored as events.jsonl or (in the committed results) events.jsonl.gz."""
    for name, opener in (("events.jsonl", open), ("events.jsonl.gz", gzip.open)):
        if (run_dir / name).exists():
            with opener(run_dir / name, "rt") as handle:
                return [json.loads(line) for line in handle if line.strip()]
    return None


def load(experiments: list[str]) -> pd.DataFrame:
    tasks = {t.id: t for t in load_tasks() + load_tasks(task_set="mutants")}
    agents = load_agents()
    rows = []
    for experiment in experiments:
        for path in sorted((ROOT / "results" / experiment / "runs").rglob("result.json")):
            row = json.loads(path.read_text())
            events = read_events(path.parent) if row["task"] in tasks else None
            if events is not None:
                metrics = summarize(events, None, tasks[row["task"]].relevant_files, agents[row["agent"]].cli)
                row.update({k: v for k, v in metrics.items() if k not in ("steps", "final_message")})
            rows.append(row)
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["passed"] = frame["passed"].astype(float)
    frame["total_tokens"] = frame["total_input_tokens"].astype(float) + frame["output_tokens"].astype(float)
    frame["uncached_tokens"] = frame["input_tokens"].astype(float) + frame["output_tokens"].astype(float)
    frame["cost_usd"] = pd.to_numeric(frame["cost_usd"], errors="coerce")
    for column in ("lint_runs",):
        frame[column] = frame[column].fillna(0) if column in frame else 0
    return frame


def fmt_comparison(result: dict[str, float], log: bool) -> str:
    if not result["tasks"] or result["estimate"] != result["estimate"]:
        return "n/a"
    p = f"p={result['p']:.3f}" + (f" (Holm {result['p_holm']:.3f})" if result.get("p_holm") == result.get("p_holm") else "")
    if log:
        return f"x{result['estimate']:.2f} [{result['ci_low']:.2f}, {result['ci_high']:.2f}] {p}"
    return f"{result['estimate']:+.2f} [{result['ci_low']:+.2f}, {result['ci_high']:+.2f}] {p}"


def charts(frame: pd.DataFrame, out: Path) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    shown = ["passed", "total_tokens", "wall_s", "unique_files_read", "tool_calls", "steps_before_first_edit"]
    conditions = [c for c in CONDITION_ORDER if c in set(frame["condition"])]
    fig, axes = plt.subplots(2, 3, figsize=(15, 8))
    for ax, metric in zip(axes.flat, shown):
        for i, stratum in enumerate(["type_relevant", "type_neutral"]):
            sub = frame[frame["stratum"] == stratum]
            values = [sub[sub["condition"] == c][metric].astype(float).mean() for c in conditions]
            ax.bar(np.arange(len(conditions)) + i * 0.4, values, 0.4, label=stratum.replace("_", " "))
        ax.set_xticks(np.arange(len(conditions)) + 0.2, conditions)
        ax.set_title(METRICS[metric][0])
        ax.spines[["top", "right"]].set_visible(False)
    axes.flat[0].legend(frameon=False)
    fig.suptitle(f"{', '.join(sorted(set(frame['model'].astype(str))))}: mean per condition, by task stratum")
    fig.tight_layout()
    fig.savefig(out / "overview.png", dpi=150)
    plt.close(fig)
    return ["overview.png"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", default="main")
    parser.add_argument("--agent", help="only this agent's runs (vunnel holds both Sonnet and Codex)")
    args = parser.parse_args()
    sources, comparisons = EXPERIMENTS.get(args.experiment, ([args.experiment], MAIN_COMPARISONS))
    frame = load(sources)
    if args.experiment == "lint":  # R2 compares against Sonnet's main runs of the same tasks
        frame = frame[frame["agent"] == "claude-sonnet"]
    if args.agent:
        frame = frame[frame["agent"] == args.agent]
    if frame.empty:
        raise SystemExit(f"no finished runs under results/{args.experiment}/runs")
    out = ROOT / "results" / args.experiment / "report" / (args.agent or "")
    out.mkdir(parents=True, exist_ok=True)
    frame.drop(columns=["files_read", "files_edited", "tool_calls_by_kind"], errors="ignore").to_csv(out / "runs.csv", index=False)
    frame["condition"] = pd.Categorical(frame["condition"], [c for c in CONDITION_ORDER if c in set(frame["condition"])])
    frame["category"] = pd.Categorical(frame["category"], [c for c in CATEGORY_ORDER if c in set(frame["category"])])

    sections = [f"# Results: {args.experiment}{' (' + args.agent + ')' if args.agent else ''}\n\n{len(frame)} finished runs, {frame['task'].nunique()} tasks, "
                f"conditions: {', '.join(map(str, frame['condition'].cat.categories))}\n"]

    lines = ["## Comparisons (declared in DESIGN.md before running)", "",
             "Each cell: paired-by-task estimate [95% bootstrap interval], sign-flip p-value, and in brackets the "
             "p-value Holm-adjusted across the metrics of that comparison (DESIGN.md section 9). Success rate and "
             "score are differences in proportion (+0.10 = 10 points better); the other metrics are ratios "
             "(x0.80 = 20% less).", ""]
    header = "| metric | " + " | ".join(f"{name}: {a} vs {b}" for name, _, a, b, _ in comparisons) + " |"
    lines += [header, "|---" * (len(comparisons) + 1) + "|"]
    plain = frame.assign(condition=frame["condition"].astype(str), category=frame["category"].astype(str))
    results = {}
    for name, _, a, b, q in comparisons:
        column = {metric: compare(plain.query(q), metric, a, b, log) for metric, (_, log) in METRICS.items()}
        tested = [m for m, r in column.items() if r["tasks"] and r["p"] == r["p"]]
        for metric, adjusted in zip(tested, holm([column[m]["p"] for m in tested])):
            column[metric]["p_holm"] = adjusted
        results[name] = column
    for metric, (label, log) in METRICS.items():
        cells = [fmt_comparison(results[name][metric], log) for name, *_ in comparisons]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines += [""] + [f"- **{name}**: {desc} ({a} vs {b}; tasks: `{q}`)" for name, desc, a, b, q in comparisons]
    sections.append("\n".join(lines))

    summary = frame.groupby("condition", observed=True).agg(
        runs=("passed", "size"),
        **{metric: (metric, "mean" if not log else "median") for metric, (_, log) in METRICS.items()},
    )
    sections.append("## Per condition (success: mean; costs: median)\n\n" + summary.round(3).to_markdown())
    by_stratum = frame.pivot_table(index=["stratum", "category"], columns="condition", values="passed", aggfunc="mean", observed=True)
    sections.append("## Success rate by stratum and category\n\n" + by_stratum.round(2).to_markdown())
    by_task = frame.pivot_table(index=["stratum", "task"], columns="condition", values="passed", aggfunc="mean", observed=True)
    sections.append("## Success rate by task\n\n" + by_task.round(2).to_markdown())
    tokens = frame.pivot_table(index=["stratum", "task"], columns="condition", values="total_tokens", aggfunc="median", observed=True)
    sections.append("## Median tokens by task\n\n" + tokens.round(0).to_markdown())
    sections.append("## Charts\n\n" + "\n".join(f"![{c}]({c})" for c in charts(frame, out)))
    (out / "report.md").write_text("\n\n".join(sections) + "\n")
    print(f"wrote {out / 'report.md'} ({len(frame)} runs)")


if __name__ == "__main__":
    main()
