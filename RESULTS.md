# Results

766 graded agent runs, collected 2026-10-21 to 2026-10-23. The design and every comparison
below were written down before the runs they use ([DESIGN.md](DESIGN.md), sections 5, 8 and 9),
except partial typing for Codex on vunnel, added after everything else had run (section 9).
Every number here comes from the reports under `results/<experiment>/report/`, which are
regenerated from the raw runs by `harness/analyze.py` and `tools/code_quality.py`.

## What ran

| Experiment | Agent | Codebase | Tasks | Versions | Reps | Runs |
|---|---|---|---|---|---|---|
| `main` | Claude Sonnet | billing | 16 authored | untyped, well-named, typed, typed + mypy | 3 | 192 |
| `codex` | Codex | billing | 16 authored | untyped, well-named, typed, typed + mypy | 2 | 128 |
| `haiku` | Claude Haiku | billing | 16 authored | untyped, typed | 3 | 96 |
| `lint` | Claude Sonnet | billing | 10 code changes | typed + ruff | 3 | 30 |
| `mutants` | Claude Sonnet | billing | 20 random bugs | untyped, partial, typed, typed + mypy | 2 | 160 |
| `mutants-codex` | Codex | billing | 20 random bugs | untyped, typed | 2 | 80 |
| `vunnel` | Sonnet and Codex | vunnel (real open-source repo) | 5 from its history | untyped, partial, typed, typed + mypy | 2 | 80 |

Agents: Claude Code 2.1.306 with `claude-sonnet-5-5` and `claude-haiku-5-5`; Codex CLI 0.162.1 with
`gpt-6-sol` at medium reasoning effort. Six Codex vunnel runs that overlapped a system sleep were
moved aside and rerun (`results/vunnel/excluded_sleep.jsonl`); no other run overlapped one.

**Codex saw personal skills.** `--ignore-user-config` does not hide the skills and plugins installed
in the user's Codex home, and in 57 of the 248 Codex runs the agent opened some of them (guides to testing
or debugging): 10 of 82 untyped runs, 7 of 32 well-named, 6 of 10 partial,
22 of 82 typed and 12 of 42 typed + mypy. Claude Code ran with `--setting-sources project` and
never did (0 of 518). The runs were kept. The extra reading falls more often on the typed
versions, so if anything it raised Codex's typed costs.

**Statistics.** Each comparison is paired by task: mean per-task difference (success) or ratio
(costs), a bootstrap 95% interval, an exact sign-flip permutation p-value, and that p-value
Holm-adjusted across the metrics of the comparison. A result is called significant only if it
survives the adjustment.

## 1. Success

| Experiment | untyped | partial (`l1`) | well-named | typed | typed + mypy |
|---|---|---|---|---|---|
| Sonnet, authored tasks | 100% | | 93.8% | 100% | 100% |
| Codex, authored tasks | 96.9% | | 96.9% | 96.9% | 100% |
| Haiku, authored tasks | 93.8% | | | 100% | |
| Sonnet, typed + ruff | | | | | 100% (with ruff) |
| Sonnet, random bugs | 100% | 100% | | 100% | 100% |
| Codex, random bugs | 100% | | | 100% | |
| Sonnet, vunnel | 10/10 | 8/10 | | 9/10 | 9/10 |
| Codex, vunnel | 10/10 | 10/10 | | 8/10 | 10/10 |

Success is at or near the ceiling everywhere. The differences that exist come from single tasks:
`m2_usage_seconds` (store usage timestamps in seconds) failed 1 of 2 times for Codex without mypy
and 0 of 2 with it, and 3 of 3 for Haiku untyped against 0 of 3 typed. No success difference is
significant.

## 2. Cost

Typed against untyped, type-relevant tasks (ratio, 95% interval, Holm-adjusted p):

| | tokens | tokens without cache reads | wall time | characters read |
|---|---|---|---|---|
| Sonnet, authored | ×0.98 [0.88, 1.11] 1.00 | ×1.00 [0.92, 1.12] 1.00 | ×1.01 [0.85, 1.21] 1.00 | ×0.92 [0.61, 1.26] 1.00 |
| Codex, authored | ×1.05 [0.96, 1.16] 1.00 | ×1.26 [1.02, 1.58] 1.00 | ×0.97 [0.87, 1.08] 1.00 | ×1.13 [0.82, 1.50] 1.00 |
| Haiku, authored | ×1.21 [1.11, 1.31] 0.27 | ×1.04 [0.99, 1.10] 1.00 | ×1.08 [1.03, 1.13] 0.27 | ×1.29 [1.12, 1.54] 0.19 |
| Sonnet, random bugs | ×1.05 [0.86, 1.26] 1.00 | ×0.97 [0.83, 1.14] 1.00 | ×1.03 [0.90, 1.16] 1.00 | ×1.23 [0.95, 1.56] 1.00 |
| Codex, random bugs | ×1.29 [1.09, 1.55] 0.19 | ×1.19 [1.03, 1.39] 0.78 | ×1.11 [1.00, 1.23] 0.97 | ×1.13 [0.89, 1.44] 1.00 |
| Sonnet, vunnel (all tasks) | ×1.07 [0.97, 1.19] 1.00 | ×1.06 [0.98, 1.17] 1.00 | ×0.90 [0.64, 1.27] 1.00 | ×0.85 [0.59, 1.08] 1.00 |
| Codex, vunnel (all tasks) | ×1.05 [0.80, 1.37] 1.00 | ×1.10 [0.92, 1.35] 1.00 | ×1.04 [0.80, 1.44] 1.00 | ×1.28 [0.91, 1.81] 1.00 |

On the control tasks (types should not matter) the token ratios range from ×0.97 to ×1.21, none
significant. The typed billing code is 28% longer than the untyped code (106,928 against 83,525
characters; the partial version is 7% longer, the well-named rewrite 36%), so an agent reading
the same share of it pays more for it. Typed code was never measurably faster or cheaper; it was
sometimes more expensive, and no cost difference survives the adjustment.

**Types against good naming** (Sonnet and Codex, type-relevant tasks): no metric differs
significantly. Tokens ×1.07 (Sonnet) and ×0.97 (Codex).

**Partial typing**: `l1` against untyped, tokens ×1.00 on random bugs (Sonnet), ×1.17 on vunnel
(Sonnet) and ×1.06 on vunnel (Codex); typed against `l1`, ×1.03, ×0.92 and ×0.99. Nothing
significant; partial typing does not sit measurably between the two ends, for either agent.

## 3. The type checker

| | type-checker runs per code-change task | Holm p |
|---|---|---|
| Sonnet, authored, typed + mypy against typed | +1.37 [0.97, 1.77] | 0.047 |
| Codex, authored, typed + mypy against typed | +1.50 [1.15, 1.95] | 0.044 |
| Sonnet, random bugs mypy can catch | +1.31 [0.81, 1.81] | 0.29 |
| Codex, vunnel | +1.80 [1.20, 2.40] | 1.00 (5 tasks) |

When mypy is installed and named in the instructions, both agents run it on essentially every code
change. This is the only result that survives the adjustment, and it holds for both agents.

**Is it the checker, or any advertised tool?** Typed + ruff (a linter that knows nothing about
types) against typed + mypy on the same 10 tasks: tokens ×0.99, characters read ×0.97, success
equal. Sonnet ran ruff 1.13 times per task, about as often as it ran mypy. The lower reading cost
seen with mypy appears with ruff too, so it comes from having a checker to run, not from type
checking.

**What only the type checker does:** code the agents wrote that introduced new mypy errors
(`tools/code_quality.py`: the agent's diff applied to its starting version, mypy --strict on the
typed versions):

| | typed | typed + ruff | typed + mypy |
|---|---|---|---|
| Sonnet, authored code changes | 3 of 30 | 3 of 30 | 0 of 30 |
| Codex, authored code changes | 1 of 20 | | 0 of 20 |
| Haiku, authored code changes | 3 of 30 | | |

All three Sonnet failures were the same bug: passing `str | None` where `refund` takes `str`.
No version's edits introduced ruff findings.

## 4. Where the agents looked

In 96 typed Sonnet runs on the authored tasks, the agent opened `models.py`, where every type
definition lives, 8 times; it reads annotations where they are used. On the one task whose answer
is spelled out by the annotations (`i3_optional_returns`, "which functions can return None"), the
median Sonnet run used 212k tokens untyped, 149k typed and 67k with mypy.

## Summary

- Types do not make Claude Sonnet, Claude Haiku or Codex faster or cheaper on this work; when the
  cost moves, it moves up, and never significantly.
- Correctness is at the ceiling for current agents on these tasks, typed or not, including on random
  bugs and on a real repository. The few misses cluster on unit and optionality contracts.
- A type checker the agent is told about gets used every time, and it is the only thing that kept
  every agent edit type-clean. A linter gets used just as often but lets type errors through.

## Limits

- Billing is small enough (about 3,000 lines) to read whole, and was written for this benchmark by
  Claude, as were the authored tasks. The random bugs, the vunnel tasks and Codex reduce that bias.
- The typed and untyped versions are the two ends; the partial version is one mechanical middle,
  not the typing found in real projects.
- 5 to 20 tasks per comparison detect only large effects; intervals that cross zero are
  inconclusive, not evidence of no effect.
- The follow-ups (sections 8 and 9) were declared after the main run's result was known.
