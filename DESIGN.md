# Design: do type annotations help an AI coding agent?

Everything here was decided **before** running. The comparisons in section 5 are the ones
reported as results. Anything else found in the data is called exploratory.

## 1. Question

Does the same agent do better, faster or more cheaply when the code it works on carries
static type information? Does it help to also have a type checker? And is that different
from simply having well-named, well-documented untyped code?

## 2. Setup

- **Agent:** Claude Code with Claude Sonnet (`claude-sonnet-5-5`). The CLI version is pinned
  in `harness/agents.yaml`.
- **Code:** `billing`, a ~3k-line subscription billing engine (customers, plans, invoices,
  coupons, tax, payments, dunning, refunds, webhooks, reports). It is invented for this
  experiment, so no model can have seen it in training.
- **Isolation:** every run starts in a fresh, isolated git repository containing only that
  version of the code, 25 visible tests and a two-line `CLAUDE.md`.
  - The agent can't see other versions, the grading tests or the solutions.
  - Your personal Claude settings, plugins, MCP servers and web tools are switched off.

## 3. The four conditions

| Condition | What the agent gets |
|---|---|
| `l0` | the code with every type annotation removed |
| `natural` | untyped, but rewritten idiomatically (descriptive names, docstrings) by a different model (Opus) that saw only `l0` |
| `l3` | the original, strictly typed code: `InvoiceId`, `Cents`, `EpochMillis`, exact status values, record shapes; passes `mypy --strict` |
| `l3+tc` | `l3`, plus mypy installed and one line in `CLAUDE.md` saying how to run it |

- `l0` is produced from `l3` mechanically (`tools/derive.py`). `tools/verify.py` proves the
  two are the same program.
- `natural` was checked to behave identically on 200 random scenarios
  (`tools/validate_natural.py`).

## 4. The 16 tasks

Half are **type-relevant** (the type annotations state the answer); half are
**type-neutral** (they shouldn't matter). The neutral half is the control: an "effect" of
types there would mean noise or bias.

| Task | Kind | Stratum | Gist |
|---|---|---|---|
| c3_usage_window | question | relevant | in what unit is a usage timestamp, which records count in a period |
| c2_dunning | question | neutral | what happens when every charge of an invoice fails |
| n1_webhook_backoff | question | neutral | the retry schedule of a failing webhook |
| i1_usage_seconds_impact | list | relevant | which functions change if usage timestamps move to seconds |
| i3_optional_returns | list | relevant | which functions can return `None` |
| n5_updated_emitters | list | neutral | which functions emit `subscription.updated` (findable with grep) |
| b1_refund_fails | bug | relevant | wrong id passed (mypy catches it at L3) |
| b5_no_retries | bug | relevant | status typo (mypy catches it at L3) |
| b2_usage_not_billed | bug | relevant | seconds/milliseconds mix-up (mypy doesn't catch it) |
| b3_no_card_dunning | bug | relevant | unhandled `None` (mypy doesn't catch it) |
| mut25_dunning_bool_swap | bug | neutral | retry schedule ignored (logic bug, randomly generated) |
| mut46_reports_arith_swap | bug | neutral | MRR comes out negative (logic bug, randomly generated) |
| m2_usage_seconds | change | relevant | store usage timestamps in seconds |
| f1_partial_refunds | change | neutral | add partial refunds |
| m3_net_terms | change | neutral | per-customer payment terms |
| n4_extend_trial | change | neutral | extend a subscription's trial |

**Grading** is automatic, with no judgement involved:
- questions: exact match of each answer field
- lists: precision/recall (F1)
- bugs and changes: the hidden tests pass with no regressions

`tools/check_tasks.py` proves every task is gradable and solvable, and that each bug breaks
exactly the same hidden tests in every version.

**Repetitions:** 3 per task and condition, so 16 × 4 × 3 = **192 runs**. Run order is shuffled
so that time effects (load, rate limits) hit all conditions alike.

## 5. What is compared

Every comparison is **paired by task**: a task is compared with itself under the two
conditions, averaged over repetitions. For each comparison we report:
- the mean per-task difference (success, score) or the per-task ratio (all cost metrics)
- a bootstrap 95% interval
- an exact sign-flip permutation p-value (`harness/stats.py`)

| | Question | Conditions | Tasks |
|---|---|---|---|
| **C1** | Do types help where they should? | `l3` vs `l0` | type-relevant (8) |
| **C1-control** | ...and do they "help" where they shouldn't? (expected: no) | `l3` vs `l0` | type-neutral (8) |
| **C2** | Does a type checker help on top of types? | `l3+tc` vs `l3` | bugs and changes (10) |
| **C3** | Types vs good naming | `l3` vs `natural` | type-relevant (8) |

**Metrics, each compared in all four comparisons:**

| Metric | Meaning |
|---|---|
| success rate, score | did it get it right (score gives partial credit for lists) |
| wall time | seconds from start to finish |
| tokens processed | input including cached, plus output |
| API-equivalent cost | as reported by Claude Code |
| model turns, tool calls | how much back-and-forth |
| source files opened, characters read | how much code it had to look at |
| searches | grep, glob, ls |
| edit actions | how many edits it made |
| test runs, type-checker runs | how often it verified its work |
| steps before first edit | how long it explored before changing anything |

**How to read the results:** with 8–10 tasks per comparison, only fairly large effects reach
p < 0.05. Small effects will show as intervals crossing zero, and are reported as
inconclusive, not as "no effect". The cost metrics (time, tokens, files) vary less than
success, so they are where an effect is most likely to show.

## 6. Durability

- Each finished run is saved immediately under `results/<experiment>/runs/...` (its
  `result.json` is written last, atomically), and appended to `results/<experiment>/runs.jsonl`.
- If the subscription's usage limit is hit, the attempt is discarded (it doesn't count as a
  failure), and the runner waits for the reset and continues.
- The runner is a detached process, so it keeps going even if the Claude session that started
  it stops. Re-running the same command resumes and skips finished runs.

## 7. Known limitations (to state in the post)

- One codebase, one agent, 16 tasks: a focused case study, not a benchmark.
- I wrote the codebase and most tasks knowing the hypothesis. Mitigations: the neutral
  control half, two randomly generated bugs, and answers computed from the code rather than
  written by hand.
- The natural rewrite is one model's idea of "good untyped code".
- `l3` files are ~28% longer than `l0` files, so some token difference is just file size.
  That's a real cost of types, and it's reported as such.

The larger version of this experiment, with Codex, a real repository (vunnel), more versions,
randomly generated bugs and a full pre-registration, is on the `full` branch.

## 8. Follow-up checks (declared 2026-10-22, after the main run, before running these)

The main run was at the ceiling: Sonnet solved almost everything in every condition, and
types changed no metric. A cheap check looks for an effect where one is plausible: the same
16 tasks with a weaker model, comparing `l3` vs `l0` only (comparisons C1 and C1-control),
3 repetitions, 96 runs.

| Check | What changes | Why |
|---|---|---|
| **haiku** (`results/haiku`) | Claude Haiku (`claude-haiku-5-5`) instead of Sonnet | A weaker model may infer less from context and lean more on annotations. |

**Reading:**
- If it stays null, the claim "types don't measurably help current Claude agents on this kind
  of work" gets considerably stronger.
- If it shows an effect, that marks the boundary and is reported as such.

Either way these are follow-ups, labelled as such next to the main result.

A free re-analysis of the main run's traces is reported alongside:
- reads of the type definitions in `models.py`
- per-kind costs on the type-relevant tasks
- whether the code agents wrote in the typed versions still passes `mypy --strict`

## 9. Robustness checks (declared 2026-10-22, before running them)

These address the weaknesses listed after the main run and the follow-ups: a ceiling on success,
few tasks, one agent family, a codebase written by the same model family that is tested, a
codebase small enough to read whole, only the two extreme versions, and a type-checker version
that also adds an advertised tool. Finished runs are reused wherever the conditions are
identical; nothing that already ran is rerun.

**New versions**

| Version | What the agent gets |
|---|---|
| `l1` | partial typing: signature annotations only, each erased to its outer builtin and keeping optionality (`CustomerId` → `str`, `PaymentMethod \| None` → `dict \| None`); no type definitions, no variable annotations. Derived by `tools/derive.py`, verified to be the same program, and never states a wrong type (plain mypy reports only missing variable annotations). |
| `l3+lint` | `l3` plus ruff installed and one line in the instructions saying how to run it (pyflakes and syntax rules only, which know nothing about types; the package is clean under them). The control for `l3+tc`: the same "an extra checker is advertised" change, without a type checker. |

**New agent:** Codex CLI 0.162.1 with `gpt-6-sol` at medium reasoning effort
(`harness/agents.yaml`). It gets the same instruction text, in `AGENTS.md` (the file Codex reads)
instead of `CLAUDE.md`. Codex reports no price, so it is compared on tokens, not cost.

**New tasks:** 20 random bugs, sampled with seed 2026 from the 68 mutants generated on the `full`
branch (`tools/mutants.py` there, seed 2026), 10 per stratum, excluding the two already in the main
set. Their prompts are the mechanical bug reports (the failing test), not hand-written. They live
in `tasks/mutants/` and pass `tools/check_tasks.py --set mutants`.

**Runs**

| Experiment | Agent | Tasks | Versions | Reps | New runs | Reused |
|---|---|---|---|---|---|---|
| `codex` | Codex | main 16 | l0, natural, l3, l3+tc | 2 | 128 | |
| `lint` | Sonnet | main bug fixes and changes (10) | l3+lint | 3 | 30 | l3, l3+tc from `main` |
| `mutants` | Sonnet | 20 random bugs | l0, l1, l3, l3+tc | 2 | 160 | |
| `mutants-codex` | Codex | 20 random bugs | l0, l3 | 2 | 80 | |
| `vunnel` (`full` harness) | Sonnet | 5 vunnel tasks | l1 | 2 | 10 | l0, l3, l3+tc (30 runs) |
| `vunnel` (`full` harness) | Codex | 5 vunnel tasks | l0, l3, l3+tc | 2 | 30 | |
| `vunnel` (`full` harness), added 2026-10-23 | Codex | 5 vunnel tasks | l1 | 2 | 10 | l0, l3, l3+tc (30 runs) |

**Comparisons**, paired by task as in section 5, reported for every metric:

| | Question | Conditions | Tasks |
|---|---|---|---|
| R1 | Do the main findings hold for another agent? | C1, C1-control, C2, C3 | Codex, main tasks |
| R2 | Is the mypy effect the checker, or just an advertised tool? | l3+tc vs l3+lint | Sonnet, the 10 code-change tasks |
| R3 | Do types help on bugs nobody wrote for this experiment? | l3 vs l0, split by stratum | random bugs, Sonnet and Codex |
| R4 | Does partial typing land between the extremes? | l1 vs l0, l3 vs l1 | random bugs (Sonnet), vunnel (Sonnet; Codex added 2026-10-23) |
| R5 | Does a type checker help on random bugs mypy can catch? | l3+tc vs l3 | random bugs with `checker_catches_at` = l2/l3 (Sonnet) |
| R6 | Does the codebase size effect hold for Codex? | l3 vs l0, l3+tc vs l3 | vunnel, Codex |

**Added after the fact (2026-10-23).** Partial typing on vunnel was first run for Sonnet only.
After every run above had finished, the same 10 `l1` runs were added for Codex, so that R4 does
not rest on one agent. They use the same harness and settings as the other Codex vunnel runs.

**Multiple comparisons.** From here on, p-values are also reported Holm-adjusted within each
comparison across its metrics, for this section and retroactively for sections 5 and 8. A result
is called significant only if it survives the adjustment; unadjusted p-values stay in the tables.

**Re-analysis of existing runs, no agent time:** tokens without cache reads; tokens per 1,000
characters of source in the version; ruff (`F`, `E4`, `E7`, `E9`) and mypy findings in the code
agents wrote, for every version; diff size.
