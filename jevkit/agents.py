"""Agents a turn can be handed to: one per login, each a program or an endpoint.

Nothing here decides anything; jevkit/dispatch.py picks the agent. This module runs it within
a time limit and turns every way it can fail into one AgentError code the caller acts on:

    quota    the seat is full: the ladder cools it for every lane
    auth     not logged in, or the key is refused
    missing  the program is not installed
    timeout  no answer within the time limit
    failed   anything else

The prompt goes in on stdin, never on the command line: argv is readable by every process on
the machine, and a long one fails with "Argument list too long". Argument lists are templates
that dispatch.json can replace, so a changed CLI flag is a config edit, not a code change.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence

# Read on a failure path only: on success, an answer that talks about rate limits is an answer.
_QUOTA = re.compile(r"(?i)(usage limit|rate[ -]?limit|quota|too many requests|\b429\b|limit reached|"
                    r"out of credits|insufficient credits)")
_AUTH = re.compile(r"(?i)(not logged in|please log ?in|log ?in required|unauthori[sz]ed|\b401\b|"
                   r"invalid api key|authentication)")

CODEX_ARGV = ["codex", "exec", "--json", "--skip-git-repo-check", "--sandbox", "read-only",
              "--model", "{model}", "--output-last-message", "{output}", "-"]
CLAUDE_ARGV = ["claude", "-p", "--output-format", "json", "--model", "{model}", "--max-turns", "{max_turns}",
               "--permission-mode", "plan", "--resume", "{session}"]

Runner = Callable[[Sequence[str], str, float], Any]   # (argv, stdin, timeout) -> CompletedProcess-like


class AgentError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}"[:300])
        self.code = code
        self.detail = str(detail)[:300]


@dataclass
class Result:
    text: str
    model: str = ""
    session: str = ""


def fill(template: Sequence[str], **values: Any) -> List[str]:
    """Put values into an argument template. An empty value drops its placeholder and the flag before it."""
    out: List[str] = []
    for token in template:
        name = token[1:-1] if token.startswith("{") and token.endswith("}") else None
        if name is None or name not in values:
            out.append(token)
            continue
        value = values[name]
        if value in ("", None):
            if out and out[-1].startswith("-"):
                out.pop()
            continue
        out.append(str(value))
    return out


def _run(argv: Sequence[str], stdin_text: str, timeout: float) -> Any:
    return subprocess.run(list(argv), input=stdin_text, capture_output=True, text=True,
                          timeout=timeout, check=False)


def _call(argv: Sequence[str], stdin_text: str, timeout: float, runner: Optional[Runner]) -> Any:
    try:
        return (runner or _run)(argv, stdin_text, timeout)
    except FileNotFoundError:
        raise AgentError("missing", f"{argv[0]} is not installed or not on PATH") from None
    except subprocess.TimeoutExpired:
        raise AgentError("timeout", f"{argv[0]} gave no answer within {timeout:.0f} s") from None


def _failure(tool: str, text: str, returncode: Any) -> AgentError:
    code = "quota" if _QUOTA.search(text) else "auth" if _AUTH.search(text) else "failed"
    last = next((line.strip() for line in reversed(text.strip().splitlines()) if line.strip()), "")
    return AgentError(code, f"{tool} exit {returncode}: {last}")


def _codex_message(stdout: str) -> str:
    """The last agent message in `codex exec --json` events, for when the answer file stayed empty."""
    text = ""
    for line in (stdout or "").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") in ("agent_message", "assistant_message") and item.get("text"):
            text = str(item["text"])
        message = event.get("msg")
        if isinstance(message, dict) and message.get("type") == "agent_message" and message.get("message"):
            text = str(message["message"])
    return text.strip()


def run_codex(prompt: str, *, model: str = "", timeout: float = 600.0, argv: Optional[Sequence[str]] = None,
              runner: Optional[Runner] = None) -> Result:
    """One `codex exec` run on the ChatGPT login Codex already holds. Read-only by default."""
    with tempfile.TemporaryDirectory(prefix="jev-codex-") as scratch:
        output = os.path.join(scratch, "answer.txt")
        done = _call(fill(argv or CODEX_ARGV, model=model, output=output), prompt, timeout, runner)
        if done.returncode != 0:
            raise _failure("codex", f"{done.stdout or ''}\n{done.stderr or ''}", done.returncode)
        try:
            text = Path(output).read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
    text = text or _codex_message(done.stdout)
    if not text:
        raise AgentError("failed", "codex finished without an answer")
    return Result(text=text, model=model)


def run_claude(prompt: str, *, model: str = "", session: str = "", max_turns: int = 8, timeout: float = 600.0,
               argv: Optional[Sequence[str]] = None, runner: Optional[Runner] = None) -> Result:
    """One `claude -p` run on the Claude Code login. Plan mode: it answers and edits nothing.

    Never `--bare`: that mode ignores the subscription login and needs an API key.
    """
    done = _call(fill(argv or CLAUDE_ARGV, model=model, session=session, max_turns=max_turns),
                 prompt, timeout, runner)
    try:
        data = json.loads(done.stdout or "")
    except json.JSONDecodeError:
        data = None
    if done.returncode != 0 or not isinstance(data, dict) or data.get("is_error"):
        said = str(data.get("result") or "") if isinstance(data, dict) else ""
        raise _failure("claude", f"{said}\n{done.stdout or ''}\n{done.stderr or ''}", done.returncode)
    text = str(data.get("result") or "").strip()
    if not text:
        raise AgentError("failed", "claude finished without an answer")
    return Result(text=text, model=model, session=str(data.get("session_id") or ""))
