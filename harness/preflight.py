"""Check that the agent CLI is logged in, pinned and answering, with one tiny prompt.

  uv run python -m harness.preflight
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from .agents import check_version, load_agents, run_agent
from .traces import summarize


def main() -> int:
    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        (repo / ".venv" / "bin").mkdir(parents=True)
        for cfg in load_agents().values():
            problem = check_version(cfg)
            cfg.max_turns, cfg.timeout_s = 2, 180
            run = run_agent(cfg, "Reply with exactly the word: ready", repo)
            reply = (summarize(run.events, repo, []).get("final_message") or "").strip()
            ok = problem is None and "ready" in reply.lower() and not run.limit_hit
            failed += not ok
            detail = problem or ("usage limit reached" if run.limit_hit else reply[:120] or run.stderr.strip()[-300:])
            print(f"{'ok  ' if ok else 'FAIL'} {cfg.name} model={cfg.model} :: {detail}")
    if failed:
        print("\nFor login problems run `claude auth login`.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
