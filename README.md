# Do type annotations help an AI coding agent?

Coding agents (Claude Code with Sonnet and Haiku, and Codex) get the same tasks on several versions
of one codebase: no types, untyped but well written, partially typed, fully typed, and fully typed
with a type checker. We measure success, time, tokens, files opened, characters read, how often
they run tests and checkers, and the quality of the code they write.

- **[RESULTS.md](RESULTS.md)**: what we found, across 766 graded runs.
- **[DESIGN.md](DESIGN.md)**: the design, and every comparison, written down before the runs it uses.

## Layout

```
subject/l3/billing/        the strictly typed codebase (source of truth for every version)
subject/natural/           idiomatic untyped rewrite, how it was made (BRIEF.md), bugs mapped onto it (ports.yaml)
subject/visible_tests/     the tests the agent can see and run
hidden_tests/              grading tests, never shown to the agent
tasks/                     the 16 authored tasks (prompt, answer shape, grader, planted bug)
tasks/mutants/             20 random bugs, sampled from generated mutants
solutions/                 reference solutions for the change tasks
harness/                   workspace builder, agent runner (Claude Code, Codex), metrics, grading, stats, report
tools/                     version derivation, verification, task checks, natural-rewrite validation, code quality
results/<experiment>/      every run: prompt, events (gzipped), metrics, grade, diff, and the report
```

The vunnel runs (a real open-source repository as the codebase) were made with the harness on the
`full` branch, which supports repository subjects; their results are in `results/vunnel/`.

## Setup

```bash
uv sync
```

```bash
claude auth login
```

```bash
codex login
```

```bash
uv run python -m harness.preflight
```

## Checks (no agent usage)

```bash
uv run python tools/verify.py
```

```bash
uv run python tools/check_tasks.py
```

```bash
uv run python tools/check_tasks.py --set mutants
```

```bash
uv run python tools/validate_natural.py
```

## Run and report

Every run command resumes: running it again after an interruption skips finished runs, and an
attempt refused by a subscription limit is discarded and retried after the reset.

```bash
uv run python -m harness.run --experiment main --reps 3
```

```bash
uv run python -m harness.run --experiment codex --agent codex --conditions l0 natural l3 l3+tc --reps 2
```

```bash
uv run python -m harness.run --experiment mutants --task-set mutants --conditions l0 l1 l3 l3+tc --reps 2
```

```bash
uv run python -m harness.analyze --experiment main
```

```bash
uv run python tools/code_quality.py main codex
```

The exact command behind each experiment is in DESIGN.md (sections 5, 8 and 9).
