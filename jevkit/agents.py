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

import contextlib
import json
import os
import re
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence

from . import client, keystore

# Read on a failure path only: on success, an answer that talks about rate limits is an answer.
_QUOTA = re.compile(r"(?i)(usage limit|rate[ -]?limit|quota|too many requests|\b429\b|limit reached|"
                    r"out of credits|insufficient credits)")
_AUTH = re.compile(r"(?i)(not logged in|please log ?in|log ?in required|unauthori[sz]ed|\b401\b|"
                   r"invalid api key|authentication)")

CODEX_ARGV = ["codex", "exec", "--json", "--skip-git-repo-check", "--sandbox", "read-only", "--cd", "{workdir}",
              "--model", "{model}", "--output-last-message", "{output}", "-"]
# `--strict-mcp-config` with no `--mcp-config` loads no MCP server; `--tools ""` turns every built-in
# tool off. `--disallowedTools` stays as the fallback for a CLI that does not know `--tools`.
CLAUDE_ARGV = ["claude", "-p", "--output-format", "json", "--model", "{model}", "--max-turns", "{max_turns}",
               "--permission-mode", "plan", "--strict-mcp-config", "--tools", "",
               "--disallowedTools", "{disallowed}", "--resume", "{session}"]
# An answering agent needs none of these: no files, no shell, no web. It gets the handoff and answers.
CLAUDE_DISALLOWED = "Bash,Read,Grep,Glob,Edit,Write,MultiEdit,NotebookEdit,WebFetch,WebSearch,Task"

# The environment a CLI gets: what it needs to run and find its own login, nothing else. An API
# key in Hermes's environment would override the subscription login, and no agent needs Jev's.
# CLAUDE_CODE_OAUTH_TOKEN is the subscription login itself (`claude setup-token`), not an API key.
_ENV_KEEP = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TZ", "TMPDIR", "SHELL",
             "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR",
             "CODEX_HOME", "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY",
             "ALL_PROXY", "https_proxy", "http_proxy", "no_proxy", "all_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR",
             "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS")
# A value that becomes its own argv item must never start like a flag. Brackets are Claude's
# context-window suffix, as in `opus[1m]`.
_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+\[\]-]{0,127}")


def agent_env() -> dict:
    return {name: os.environ[name] for name in _ENV_KEEP if name in os.environ}


def _check_model(model: str) -> str:
    if model and not _MODEL_NAME.fullmatch(model):
        raise AgentError("failed", "the configured model name is not usable")
    return model


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

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


def _run(argv: Sequence[str], stdin_text: str, timeout: float, cwd: Optional[str] = None) -> Any:
    return subprocess.run(list(argv), input=stdin_text, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, check=False, cwd=cwd, env=agent_env())


def _call(argv: Sequence[str], stdin_text: str, timeout: float, runner: Optional[Runner],
          workdir: Optional[str] = None) -> Any:
    if not argv:
        raise AgentError("failed", "the argument list is empty")
    try:
        if runner is not None:
            return runner(argv, stdin_text, timeout)
        return _run(argv, stdin_text, timeout, cwd=workdir)
    except FileNotFoundError:
        raise AgentError("missing", f"{argv[0]} is not installed or not on PATH") from None
    except PermissionError:
        raise AgentError("missing", f"{argv[0]} is not executable") from None
    except subprocess.TimeoutExpired:
        raise AgentError("timeout", f"{argv[0]} gave no answer within {timeout:.0f} s") from None
    except (OSError, ValueError) as error:
        raise AgentError("failed", f"{argv[0]} could not start ({type(error).__name__})") from None


def _failure(tool: str, text: str, returncode: Any) -> AgentError:
    """The failure's code, and in its detail only the fixed phrase that decided it: never output text."""
    for code, pattern in (("quota", _QUOTA), ("auth", _AUTH)):
        found = pattern.search(text)
        if found:
            return AgentError(code, f"{tool} exit {returncode}: {found.group(0).lower()}")
    return AgentError("failed", f"{tool} exit {returncode}")


def _codex_message(stdout: str) -> str:
    """The last agent message in `codex exec --json` events, for when the answer file stayed empty."""
    text = ""
    for line in (stdout or "").splitlines():
        try:
            event = json.loads(line)
        except (ValueError, RecursionError):
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
        workdir = os.path.join(scratch, "work")
        os.mkdir(workdir)
        output = os.path.join(scratch, "answer.txt")
        done = _call(fill(argv or CODEX_ARGV, model=_check_model(model), output=output, workdir=workdir),
                     prompt, timeout, runner, workdir)
        if done.returncode != 0:
            raise _failure("codex", f"{done.stdout or ''}\n{done.stderr or ''}", done.returncode)
        try:
            text = Path(output).read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            text = ""
    text = text or _codex_message(done.stdout)
    if not text:
        raise AgentError("failed", "codex finished without an answer")
    return Result(text=text, model=model)


def _private_base() -> Path:
    """Where claude's directories go: this user's runtime directory when it is really theirs.

    Claude Code reads every CLAUDE.md from its working directory up to `/`, and anyone on the
    machine can leave one in /tmp. XDG_RUNTIME_DIR (/run/user/<uid>) is closed to other users;
    the temp directory is the fallback where there is none.
    """
    runtime, getuid = os.environ.get("XDG_RUNTIME_DIR"), getattr(os, "getuid", None)
    if runtime and getuid is not None:
        try:
            info = os.stat(runtime)
            if stat.S_ISDIR(info.st_mode) and info.st_uid == getuid() and not info.st_mode & 0o077:
                return Path(runtime)
        except OSError:
            pass
    return Path(tempfile.gettempdir())


def _claude_workdir() -> Optional[str]:
    """The one empty directory claude runs in, or None when it cannot be trusted.

    One directory, so `--resume` finds a session where it was made: a fresh directory per call made
    resuming depend on a scan across projects that older CLIs lack, and left one
    `~/.claude/projects/` folder per turn. Never under `~`, because Claude Code reads every
    CLAUDE.md from its working directory up to `/`. Used only while it is a real
    directory (not a symlink), this user's own, closed to others' writes, and empty.
    """
    getuid = getattr(os, "getuid", None)
    if getuid is None:
        return None
    uid = getuid()
    work = _private_base() / f"jev-claude-{uid}" / "work"
    try:
        for path in (work.parent, work):
            with contextlib.suppress(FileExistsError):
                os.mkdir(path, 0o700)
            info = os.lstat(path)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022:
                return None
        if os.listdir(work):
            return None
    except OSError:
        return None
    return str(work)


def run_claude(prompt: str, *, model: str = "", session: str = "", max_turns: int = 8, timeout: float = 600.0,
               argv: Optional[Sequence[str]] = None, runner: Optional[Runner] = None) -> Result:
    """One `claude -p` run on the Claude Code login. Plan mode: it answers and edits nothing.

    It runs in one empty directory of its own, so the next turn resumes its session there. When
    that directory cannot be trusted it runs in a fresh one and starts no session it could not
    resume: isolation beats continuity. Never `--bare`: that mode ignores the subscription login
    and needs an API key.
    """
    model = _check_model(model)
    session = session if session and _SESSION_ID.fullmatch(session) else ""
    workdir = _claude_workdir() if runner is None else None       # a runner of its own takes no directory
    fresh = runner is None and workdir is None
    if fresh:
        session = ""
    scratch = tempfile.TemporaryDirectory(prefix="jev-claude-", dir=str(_private_base())) if fresh else None
    with (scratch if scratch is not None else contextlib.nullcontext(workdir)) as cwd:
        done = _call(fill(argv or CLAUDE_ARGV, model=model, session=session, max_turns=max_turns,
                          disallowed=CLAUDE_DISALLOWED), prompt, timeout, runner, cwd)
    try:
        data = json.loads(done.stdout or "")
    except (ValueError, RecursionError):
        data = None
    if done.returncode != 0 or not isinstance(data, dict) or data.get("is_error"):
        said = str(data.get("result") or "") if isinstance(data, dict) else ""
        raise _failure("claude", f"{said}\n{done.stdout or ''}\n{done.stderr or ''}", done.returncode)
    text = str(data.get("result") or "").strip()
    if not text:
        raise AgentError("failed", "claude finished without an answer")
    returned = "" if fresh else str(data.get("session_id") or "")
    return Result(text=text, model=model, session=returned if _SESSION_ID.fullmatch(returned) else "")


def run_openrouter(prompt: str, *, model: str, timeout: float = 120.0,
                   transport: Optional[Callable[..., bytes]] = None, key: Optional[str] = None) -> Result:
    """One chat completion on OpenRouter, with the key `jev setup-key --provider openrouter` stored."""
    if not model:
        raise AgentError("failed", "no OpenRouter model configured")
    key = key or keystore.resolve("openrouter")
    if not key:
        raise AgentError("auth", "no OpenRouter key: run `jev setup-key --provider openrouter`")
    if not key.isprintable() or any(char.isspace() for char in key):
        raise AgentError("auth", "the stored OpenRouter key is malformed; store it again")
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "HTTP-Referer": "https://github.com/kerpopule/hermes-jev-skills", "X-Title": "Hermes Jev Skills"}
    try:
        raw = (transport or client.post)(OPENROUTER_URL, body, headers, timeout)
    except client.JevError as error:
        code = {"rate_limited": "quota", "credits_exhausted": "quota", "auth_failed": "auth"}.get(error.code, "failed")
        raise AgentError(code, f"openrouter {error.code}") from None
    except (ValueError, OSError):
        raise AgentError("failed", "openrouter request could not be sent") from None
    try:
        data = json.loads(raw)
        text = str(data["choices"][0]["message"]["content"] or "").strip()
    except (ValueError, KeyError, IndexError, TypeError, RecursionError):
        raise AgentError("failed", "openrouter replied without an answer") from None
    if not text:
        raise AgentError("failed", "openrouter replied with an empty answer")
    return Result(text=text, model=str(data.get("model") or model))
