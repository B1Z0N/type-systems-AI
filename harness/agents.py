"""Running Claude Code or Codex headlessly and capturing the event stream."""

from __future__ import annotations

import functools
import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from .tasks import ROOT

CONFIG = ROOT / "harness" / "agents.yaml"


@dataclass
class AgentConfig:
    name: str
    model: str
    cli_version: str  # expected `<cli> --version`; runs refuse to start on drift
    cli: str = "claude"  # claude | codex
    reasoning_effort: str | None = None  # Codex only: model_reasoning_effort
    max_turns: int = 60
    timeout_s: int = 1500


@dataclass
class AgentRun:
    events: list[dict[str, Any]]
    stderr: str
    exit_code: int | None
    wall_s: float
    timed_out: bool
    cli_version: str
    limit_hit: bool = False  # subscription usage/rate limit: an infrastructure failure, not an agent failure
    retry_at: datetime | None = None


def load_agents() -> dict[str, AgentConfig]:
    raw = yaml.safe_load(CONFIG.read_text())
    return {name: AgentConfig(name=name, **spec) for name, spec in raw.items()}


INHERITED_PREFIXES = ("CLAUDE", "ANTHROPIC_BASE_URL", "CODEX", "VIRTUAL_ENV")


def clean_env() -> dict[str, str]:
    """The parent environment minus anything an enclosing Claude Code session injected.

    When the harness is started from inside Claude Code or Codex, variables such as CLAUDE_CODE_SIMPLE
    (bare mode) or a proxied ANTHROPIC_BASE_URL would change how the child agent behaves.
    """
    return {k: v for k, v in os.environ.items() if not k.startswith(INHERITED_PREFIXES)}


# ------------------------------------------------------------------ subscription limits

LIMIT_PATTERNS = re.compile(
    r"usage limit|hit your limit|limit reached|rate limit|rate_limit|too many requests|quota|credit balance|overloaded",
    re.I,
)


class UsageLimit(Exception):
    """The subscription refused the run; retry after `retry_at`."""

    def __init__(self, retry_at: datetime | None, message: str) -> None:
        super().__init__(message)
        self.retry_at = retry_at


def _parse_reset(text: str) -> datetime | None:
    """Find a reset time like 'resets 5pm' or 'try again at 5:30 PM' and return the next such moment."""
    match = re.search(r"(?:at|resets?(?: at)?)\s+(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)", text, re.I)
    if not match:
        return None
    hour, minute = int(match.group(1)) % 12, int(match.group(2) or 0)
    if match.group(3).lower().startswith("p"):
        hour += 12
    now = datetime.now()
    moment = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return moment if moment > now else moment + timedelta(days=1)


def detect_limit(events: list[dict[str, Any]], stderr: str) -> tuple[bool, datetime | None]:
    """Only error events and stderr are searched, never the agent's own tool output."""
    texts = [stderr[-2000:]]
    for event in events:
        if event.get("type") == "error" or event.get("error") or event.get("is_error"):
            texts.append(json.dumps(event))
    blob = "\n".join(texts)
    if LIMIT_PATTERNS.search(blob):
        return True, _parse_reset(blob)
    return False, None


def wait_for(limit: UsageLimit, log=print) -> None:
    """Sleep until the reset time (plus a margin), or 30 minutes if none was given."""
    until = (limit.retry_at + timedelta(minutes=3)) if limit.retry_at else datetime.now() + timedelta(minutes=30)
    log(f"usage limit hit; waiting until {until:%Y-%m-%d %H:%M}")
    while datetime.now() < until:
        time.sleep(min(60, max(1, (until - datetime.now()).total_seconds())))


# ------------------------------------------------------------------ running

@functools.cache
def installed_version(cli: str = "claude") -> str:
    out = subprocess.run([cli, "--version"], capture_output=True, text=True, env=clean_env())
    return out.stdout.strip() or out.stderr.strip()


def check_version(cfg: AgentConfig) -> str | None:
    """A problem description if the installed CLI differs from the pinned version."""
    found = installed_version(cfg.cli)
    if cfg.cli_version not in found:
        return f"{cfg.name}: pinned claude version {cfg.cli_version!r} but found {found!r}"
    return None


# A plain coding setup: no web access and none of the scheduling, messaging, design or skill
# tools a local installation may expose.
DISALLOWED_TOOLS = (
    "WebFetch", "WebSearch", "CronCreate", "CronDelete", "CronList", "RemoteTrigger", "PushNotification",
    "ScheduleWakeup", "SendMessage", "ListAgents", "DesignSync", "ReportFindings", "EnterWorktree",
    "ExitWorktree", "Skill", "Workflow",
)


def _codex_command(cfg: AgentConfig, prompt: str, repo: Path) -> list[str]:
    options = [
        "--json", "--ignore-user-config", "--skip-git-repo-check", "--ephemeral",  # your Codex config and history stay out
        "-c", 'approval_policy="never"', "-c", 'sandbox_mode="workspace-write"',
        "-c", f'model="{cfg.model}"',
    ]
    if cfg.reasoning_effort:
        options += ["-c", f'model_reasoning_effort="{cfg.reasoning_effort}"']
    return ["codex", "exec", *options, "-C", str(repo), prompt]



def _command(cfg: AgentConfig, prompt: str) -> list[str]:
    return [
        "claude", "-p", prompt,
        "--model", cfg.model,
        "--output-format", "stream-json", "--verbose",
        "--permission-mode", "bypassPermissions",
        "--setting-sources", "project",  # ignore your personal settings, hooks and plugins
        "--strict-mcp-config",  # no MCP servers
        "--no-session-persistence",
        "--disallowedTools", *DISALLOWED_TOOLS,
        "--max-turns", str(cfg.max_turns),
    ]


def run_agent(cfg: AgentConfig, prompt: str, repo: Path) -> AgentRun:
    env = clean_env()
    env["VIRTUAL_ENV"] = str(repo / ".venv")
    env["PATH"] = f"{repo / '.venv' / 'bin'}:{env.get('PATH', '')}"
    env["DISABLE_AUTOUPDATER"] = "1"  # keep the Claude Code version pinned during the experiment
    start = time.monotonic()
    cmd = _codex_command(cfg, prompt, repo) if cfg.cli == "codex" else _command(cfg, prompt)
    proc = subprocess.Popen(
        cmd, cwd=repo, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    stderr_lines: list[str] = []
    drain = threading.Thread(target=lambda: stderr_lines.extend(proc.stderr), daemon=True)  # type: ignore[arg-type]
    drain.start()
    timer = threading.Timer(cfg.timeout_s, proc.kill)
    timer.start()
    events: list[dict[str, Any]] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        events.append(event)
    proc.wait()
    timer.cancel()
    drain.join(timeout=5)
    wall = time.monotonic() - start
    stderr = "".join(stderr_lines)
    limit_hit, retry_at = detect_limit(events, stderr)
    return AgentRun(events, stderr, proc.returncode, wall, wall >= cfg.timeout_s, installed_version(cfg.cli),
                    limit_hit, retry_at)
