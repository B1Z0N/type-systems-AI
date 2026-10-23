"""Turning a raw Claude Code or Codex event stream into per-run metrics.

Every tool call becomes a step:
    {"kind": read|search|edit|test|typecheck|lint|run|vcs|plan|answer|other, "tool": str,
     "paths": [str], "read_paths": [str], "kinds": [str], "command": str|None,
     "output_chars": int, "error": bool}
Shell commands are classified by parsing the command line; a compound command such as
`pytest; mypy billing` counts towards every kind it contains.
"""

from __future__ import annotations

import os
import re
import shlex
from collections import Counter
from pathlib import Path
from typing import Any

READ_PROGRAMS = {"cat", "head", "tail", "nl", "less", "more", "bat", "awk", "wc", "view"}
SEARCH_PROGRAMS = {"grep", "rg", "ag", "ack", "find", "fd", "ls", "tree", "egrep", "fgrep"}
WRITE_PROGRAMS = {"apply_patch", "tee", "patch", "cp", "mv", "rm", "touch", "mkdir"}
SHELLS = {"bash", "sh", "zsh"}
FILE_LIKE = re.compile(r"[\w./-]+\.(py|md|toml|json|txt|yaml|yml|cfg|ini)$")


def _split_commands(command: str) -> list[list[str]]:
    """Split a shell string into simple commands (on && || ; |), unwrapping `bash -lc '...'`."""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    if len(tokens) >= 3 and Path(tokens[0]).name in SHELLS and tokens[1] in ("-c", "-lc", "-ic"):
        return _split_commands(tokens[2])
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        pass
    commands, current = [], []
    for token in tokens:
        if token in ("&&", "||", ";", "|", ";;", "&", "(", ")"):
            if current:
                commands.append(current)
            current = []
        else:
            current.append(token)
    if current:
        commands.append(current)
    return commands


def _program(argv: list[str]) -> tuple[str, list[str]]:
    while argv and ("=" in argv[0] and not argv[0].startswith("-")):
        argv = argv[1:]
    if not argv:
        return "", []
    prog = Path(argv[0]).name
    if prog in ("time", "timeout", "env", "nice") and len(argv) > 1:
        return _program(argv[1:] if prog != "timeout" else argv[2:])
    return prog, argv[1:]


def shell_kinds(command: str) -> set[str]:
    """Every kind of action inside a (possibly compound) shell command."""
    kinds = set()
    for argv in _split_commands(command):
        kind, _, _ = classify_shell_detailed(" ".join(shlex.quote(a) for a in argv))
        kinds.add(kind)
    return kinds


def classify_shell_detailed(command: str) -> tuple[str, list[str], list[str]]:
    """Return (overall kind, all file paths, paths that were read) for a shell command string."""
    kinds: list[str] = []
    paths: list[str] = []
    read_paths: list[str] = []
    redirects = re.findall(r"(?<![0-9&>])>{1,2}\s*([\w./-]+)", command)
    redirects = [r for r in redirects if r != "/dev/null"]
    for argv in _split_commands(command):
        prog, args = _program(argv)
        files = [a for a in args if FILE_LIKE.search(a) and not a.startswith("-")]
        joined = " ".join(args)
        if prog in ("python", "python3") or prog.startswith("python3."):
            if "-m pytest" in joined or args[:1] == ["-m"] and args[1:2] == ["pytest"]:
                kinds.append("test")
            elif "-m mypy" in joined or "-m pyright" in joined:
                kinds.append("typecheck")
            elif "-m ruff" in joined:
                kinds.append("lint")
            else:
                kinds.append("run")
        elif prog in ("pytest", "py.test"):
            kinds.append("test")
        elif prog in ("mypy", "pyright", "basedpyright", "dmypy"):
            kinds.append("typecheck")
        elif prog in ("ruff", "flake8", "pylint"):
            kinds.append("lint")
        elif prog == "sed":
            in_place = any(a.startswith("-i") for a in args)
            kinds.append("edit" if in_place else "read")
            paths += files
            if not in_place:
                read_paths += files
        elif prog in ("perl", "ruby") and any(a.startswith("-i") or a == "-pi" for a in args):
            kinds.append("edit")
            paths += files
        elif prog in READ_PROGRAMS:
            kinds.append("read")
            paths += files
            read_paths += [f for f in files if f not in redirects]
        elif prog in SEARCH_PROGRAMS:
            kinds.append("search")
        elif prog in WRITE_PROGRAMS:
            kinds.append("edit")
            paths += files
        elif prog == "git":
            kinds.append("search" if args[:1] == ["grep"] else "vcs")
        elif prog == "cd":
            continue
        else:
            kinds.append("other")
    if redirects:
        kinds.append("edit")
        paths += redirects
    priority = ["edit", "test", "typecheck", "lint", "run", "read", "search", "vcs", "other"]
    kind = next((k for k in priority if k in kinds), "other")
    return kind, paths, read_paths


def _relative(path: str, repo: Path | None) -> str:
    if repo is not None:
        try:
            return os.path.relpath(os.path.realpath(path), os.path.realpath(repo)) if os.path.isabs(path) else path
        except ValueError:
            return path
    return path


def _text_len(content: Any) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(_text_len(item.get("text", "")) if isinstance(item, dict) else len(str(item)) for item in content)
    return len(str(content or ""))


# --------------------------------------------------------------------------- Claude Code


CLAUDE_KINDS = {
    "Read": "read", "NotebookRead": "read",
    "Grep": "search", "Glob": "search", "LS": "search",
    "Edit": "edit", "MultiEdit": "edit", "Write": "edit", "NotebookEdit": "edit",
    "TodoWrite": "plan", "Task": "subagent", "Agent": "subagent",
}


def claude_steps(events: list[dict[str, Any]], repo: Path | None) -> list[dict[str, Any]]:
    steps: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for event in events:
        message = event.get("message") or {}
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                name = block.get("name", "")
                tool_input = block.get("input") or {}
                kind = CLAUDE_KINDS.get(name, "other")
                paths: list[str] = []
                command = None
                read_paths: list[str] = []
                if name == "Bash":
                    command = str(tool_input.get("command", ""))
                    kind, paths, read_paths = classify_shell_detailed(command)
                for key in ("file_path", "notebook_path", "path"):
                    if tool_input.get(key) and kind in ("read", "edit"):
                        paths.append(str(tool_input[key]))
                        if kind == "read":
                            read_paths.append(str(tool_input[key]))
                steps[block["id"]] = {
                    "kind": kind,
                    "tool": name,
                    "paths": [_relative(p, repo) for p in paths],
                    "read_paths": [_relative(p, repo) for p in read_paths],
                    "kinds": sorted(shell_kinds(command)) if command else [kind],
                    "command": command,
                    "output_chars": 0,
                    "error": False,
                    "subagent": event.get("parent_tool_use_id") is not None,
                }
                order.append(block["id"])
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in steps:
                step = steps[block["tool_use_id"]]
                step["output_chars"] = _text_len(block.get("content"))
                step["error"] = bool(block.get("is_error"))
    return [steps[i] for i in order]


def claude_usage(events: list[dict[str, Any]]) -> dict[str, Any]:
    result = next((e for e in reversed(events) if e.get("type") == "result"), None)
    if result is None:
        # stopped before the final summary (killed, timed out): sum the per-message usage instead
        per_message: dict[str, dict[str, Any]] = {}
        for event in events:
            message = event.get("message") or {}
            if event.get("type") == "assistant" and isinstance(message, dict) and message.get("usage"):
                per_message[message.get("id", str(len(per_message)))] = message["usage"]
        usage_sum = Counter()
        for usage in per_message.values():
            for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                usage_sum[key] += usage.get(key) or 0
        return {
            "completed": False, "stop_reason": "stopped", "turns": len(per_message), "cost_usd": None,
            "duration_ms": None, "api_duration_ms": None,
            "input_tokens": usage_sum["input_tokens"], "output_tokens": usage_sum["output_tokens"],
            "cache_read_tokens": usage_sum["cache_read_input_tokens"],
            "cache_write_tokens": usage_sum["cache_creation_input_tokens"], "final_message": None, "models": [],
        }
    usage = result.get("usage") or {}
    model_usage = result.get("modelUsage") or {}
    totals = Counter()
    for stats in model_usage.values():
        totals["input"] += stats.get("inputTokens", 0)
        totals["output"] += stats.get("outputTokens", 0)
        totals["cache_read"] += stats.get("cacheReadInputTokens", 0)
        totals["cache_write"] += stats.get("cacheCreationInputTokens", 0)
    if not model_usage:
        totals.update(
            input=usage.get("input_tokens", 0),
            output=usage.get("output_tokens", 0),
            cache_read=usage.get("cache_read_input_tokens", 0),
            cache_write=usage.get("cache_creation_input_tokens", 0),
        )
    return {
        "completed": result.get("subtype") == "success" and not result.get("is_error"),
        "stop_reason": result.get("subtype"),
        "turns": result.get("num_turns"),
        "duration_ms": result.get("duration_ms"),
        "api_duration_ms": result.get("duration_api_ms"),
        "cost_usd": result.get("total_cost_usd"),
        "input_tokens": totals["input"],
        "output_tokens": totals["output"],
        "cache_read_tokens": totals["cache_read"],
        "cache_write_tokens": totals["cache_write"],
        "final_message": result.get("result"),
        "models": sorted(model_usage),
    }


# --------------------------------------------------------------------------- Codex


def codex_steps(events: list[dict[str, Any]], repo: Path | None) -> list[dict[str, Any]]:
    steps = []
    for event in events:
        if event.get("type") != "item.completed":
            continue
        item = event.get("item") or {}
        kind = item.get("type") or item.get("item_type")
        if kind == "command_execution":
            command = str(item.get("command", ""))
            step_kind, paths, read_paths = classify_shell_detailed(command)
            steps.append({
                "kind": step_kind, "tool": "shell",
                "paths": [_relative(p, repo) for p in paths], "read_paths": [_relative(p, repo) for p in read_paths],
                "kinds": sorted(shell_kinds(command)), "command": command,
                "output_chars": len(item.get("aggregated_output") or ""), "error": (item.get("exit_code") or 0) != 0,
            })
        elif kind == "file_change":
            paths = [str(change.get("path", "")) for change in item.get("changes", [])]
            steps.append({
                "kind": "edit", "tool": "apply_patch", "paths": [_relative(p, repo) for p in paths], "read_paths": [],
                "kinds": ["edit"], "command": None, "output_chars": 0, "error": item.get("status") == "failed",
            })
        elif kind in ("mcp_tool_call", "web_search", "todo_list"):
            steps.append({
                "kind": "plan" if kind == "todo_list" else "other", "tool": kind, "paths": [], "read_paths": [],
                "kinds": ["other"], "command": None, "output_chars": 0, "error": False,
            })
    return steps


def codex_usage(events: list[dict[str, Any]]) -> dict[str, Any]:
    totals = Counter()
    turns, failed, final = 0, False, None
    for event in events:
        kind = event.get("type")
        if kind == "turn.completed":
            turns += 1
            usage = event.get("usage") or {}
            for key in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens"):
                totals[key] += usage.get(key) or 0
        elif kind in ("turn.failed", "error"):
            failed = True
        elif kind == "item.completed" and (event.get("item") or {}).get("type") == "agent_message":
            final = event["item"].get("text")
    model_steps = sum(1 for e in events if e.get("type") == "item.completed"
                      and (e.get("item") or {}).get("type") in ("agent_message", "reasoning", "command_execution", "file_change"))
    return {
        "completed": turns > 0 and not failed,
        "stop_reason": "failed" if failed else "success" if turns else "stopped",
        "turns": model_steps, "duration_ms": None, "api_duration_ms": None,
        "cost_usd": None,  # Codex reports no price; compare tokens instead
        # Codex counts cached tokens inside input_tokens; split them like Claude Code does.
        "input_tokens": totals["input_tokens"] - totals["cached_input_tokens"],
        "output_tokens": totals["output_tokens"],
        "cache_read_tokens": totals["cached_input_tokens"], "cache_write_tokens": 0,
        "reasoning_tokens": totals["reasoning_output_tokens"],
        "final_message": final, "models": [],
    }


# --------------------------------------------------------------------------- shared


def summarize(events: list[dict[str, Any]], repo: Path | None, task_files: list[str], cli: str = "claude") -> dict[str, Any]:
    steps = claude_steps(events, repo) if cli == "claude" else codex_steps(events, repo)
    for step in steps:
        if step["kind"] == "edit" and step["paths"] and all(Path(p).name == "answer.json" for p in step["paths"]):
            step["kind"] = "answer"
    usage = claude_usage(events) if cli == "claude" else codex_usage(events)
    kinds = Counter(step["kind"] for step in steps)
    read_paths = [p for step in steps for p in step.get("read_paths", [])]
    source_reads = [p for p in read_paths if p.endswith(".py") and not p.startswith("tests") and "/tests/" not in p]
    unique_source = sorted({Path(p).name for p in source_reads})
    edited = sorted({Path(p).name for step in steps if step["kind"] == "edit" for p in step["paths"]
                     if p.endswith(".py") and "test" not in p})
    relevant = set(task_files)
    hits = [name for name in unique_source if name in relevant]
    first_edit = next((i for i, step in enumerate(steps) if step["kind"] == "edit"), None)
    total_input = (usage.get("input_tokens") or 0) + (usage.get("cache_read_tokens") or 0) + (usage.get("cache_write_tokens") or 0)
    return {
        **{k: v for k, v in usage.items() if k != "final_message"},
        "total_input_tokens": total_input,
        "tool_calls": len(steps),
        "tool_calls_by_kind": dict(kinds),
        "reads": kinds["read"],
        "searches": kinds["search"],
        "edits": kinds["edit"],
        "test_runs": sum("test" in step.get("kinds", [step["kind"]]) for step in steps),
        "typecheck_runs": sum("typecheck" in step.get("kinds", [step["kind"]]) for step in steps),
        "lint_runs": sum("lint" in step.get("kinds", [step["kind"]]) for step in steps),
        "script_runs": kinds["run"],
        "tool_errors": sum(step["error"] for step in steps),
        "subagent_calls": sum(step.get("subagent", False) for step in steps) + kinds["subagent"],
        "files_read": unique_source,
        "files_edited": edited,
        "unique_files_edited": len(edited),
        "unique_files_read": len(unique_source),
        "rereads": len(source_reads) - len(set(source_reads)),
        "read_chars": sum(step["output_chars"] for step in steps if step["kind"] == "read"),
        "search_chars": sum(step["output_chars"] for step in steps if step["kind"] == "search"),
        "tool_output_chars": sum(step["output_chars"] for step in steps),
        "relevant_files_read": len(hits),
        "exploration_precision": len(hits) / len(unique_source) if unique_source else None,
        "exploration_recall": len(hits) / len(relevant) if relevant else None,
        "steps_before_first_edit": first_edit,
        "final_message": usage.get("final_message"),
        "steps": steps,
    }
