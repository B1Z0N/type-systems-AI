<!-- How subject/natural/billing was produced: claude-opus-5-5 (not the model under test), run with
     Claude Code in a fresh workspace that contained only the l0 code, the visible tests and this brief.
     It never saw the typed code. The result was checked with tools/validate_natural.py. -->

# Brief: rewrite this package as idiomatic, well-documented untyped Python

You are given `billing/`, a working Python package with **no type annotations**, and its
tests in `tests/`. Rewrite it the way an experienced team that never used type hints would
have written it: code that is easy for a new teammate to understand.

## Do
- Use descriptive names for local variables and private helpers. If a value has a unit or a
  scale, say so in the name where it helps (for example `amount_cents`, `timestamp_ms`,
  `rate_bp`).
- Write docstrings for public functions and classes where a teammate would want one: what it
  does, what the arguments mean, what it returns or raises, units and edge cases.
- Add `assert`s or explicit validation where a careful developer would, as long as behavior
  for valid input stays exactly the same (see Don't).
- Keep comments short and useful; explain *why*, not *what*.

## Don't
- **Don't change behavior.** Every test must still pass, and for any sequence of valid
  calls, results and raised exception types must be identical.
- **Don't rename anything public:** module names, public function, class and method names,
  parameter names and defaults, dictionary keys, constant names, exception messages.
  Renaming local variables and `_private` helpers is fine.
- Don't add type annotations, type comments, `typing` imports or stub files.
- Don't restructure modules or move functions between files.

## Done when
`.venv/bin/python -m pytest -q` passes. The result is checked by a validator for identical
public API and identical behavior on thousands of random operation sequences.
