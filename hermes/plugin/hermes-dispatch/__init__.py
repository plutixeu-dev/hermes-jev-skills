"""Hermes dispatch plugin: the front desk that hands a turn to the agent that should answer it.

The chat model stays the Hermes model. On the first provider call of each fresh user turn,
`llm_execution` middleware asks jevkit.dispatch who should answer:

* this machine: the call goes ahead untouched;
* an agent (Codex on a ChatGPT login, Claude Code, OpenRouter): the turn is handed to it and its
  answer comes back as the assistant message, with who wrote it on the first line.

Shadow decides and logs, and always lets the local call go ahead. Only a `chat_completions`
provider is ever short-circuited, because that is the response shape this plugin builds.
Everything fails open: any error in here is a local answer, never a lost turn.

One classifier per turn: while hermes-jev is loaded and `/jev routing` is on or in shadow, this
plugin stands aside.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from .jevkit import dispatch

_LOCK = threading.Lock()
_TURNS: Dict[str, Dict[str, Any]] = {}      # session -> this turn's text, clean history and decision
_SESSIONS: Dict[str, str] = {}              # Hermes session -> the agent session that continues it
_MAX_SESSIONS = 256
_HISTORY_ROWS = 64                          # a handoff reads at most 21 of them (relay caps it at 20 turns)
_CTX: Any = None


def _home() -> Path:
    """The turn's own profile. A gateway that serves several profiles (`multiplex_profiles`)
    binds each turn's profile with a context-local override and leaves HERMES_HOME at the root:
    read alone, the variable made every profile "default", with its class, its dispatch.json and
    its `/dispatch` switch."""
    try:
        from hermes_constants import get_hermes_home_override  # type: ignore
        override = get_hermes_home_override()
        if override:
            return Path(override)
    except Exception:  # noqa: BLE001 - outside Hermes, or an older one
        pass
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def _root() -> Path:
    home = _home()
    return home.parent.parent if home.parent.name == "profiles" else home


def _profile() -> str:
    home = _home()
    return home.name if home.parent.name == "profiles" else "default"


def _read(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, RecursionError):      # ValueError: not UTF-8, or not JSON
        return {}


def _state_path() -> Path:
    return _home() / "jev" / "dispatch-state.json"


def _plugin_setting(name: str) -> Any:
    """This plugin's own setting in config.yaml (`plugins.entries.hermes-dispatch.settings`), or None."""
    if _CTX is None:
        return None
    try:
        return _CTX.get_config(name, None)
    except Exception:  # noqa: BLE001
        return None


def _setting(name: str, policy: Dict[str, Any], default: str) -> str:
    """A `/dispatch` switch wins, then config.yaml, then dispatch.json, then the default."""
    value = _read(_state_path()).get(name)
    if value is None:
        value = _plugin_setting(name)
    if value is None:
        value = policy.get(name)
    return str(value if value is not None else default).lower()


def _hermes_jev_routing() -> Any:
    """hermes-jev's `routing` in config.yaml, where Hermes keeps plugin settings, or None."""
    try:
        from hermes_cli.config import load_config_readonly  # type: ignore

        config = load_config_readonly() or {}
        entry = config.get("plugins", {}).get("entries", {}).get("hermes-jev", {})
        for section in ("settings", "config"):             # `config` is Hermes's legacy spelling
            value = (entry.get(section) or {}).get("routing")
            if value is not None:
                return value
    except Exception:  # noqa: BLE001 - outside Hermes, or a config shaped some other way
        return None
    return None


def _hermes_jev_loaded() -> bool:
    """Is hermes-jev loaded and enabled here. A Hermes that cannot say is taken to have it."""
    probe = getattr(_CTX, "has_plugin", None)
    if not callable(probe):
        return True
    try:
        return bool(probe("hermes-jev"))
    except Exception:  # noqa: BLE001 - unsure means the other classifier may be running
        return True


def _jev_routing_active() -> bool:
    """hermes-jev's routing, read the way that plugin reads it: a `/jev` switch, then config.yaml.

    Only while hermes-jev is loaded: a `/jev routing on` left behind by a removed plugin must not
    silence dispatch forever.
    """
    if not _hermes_jev_loaded():
        return False
    state = {**_read(_root() / "jev" / "state.json"), **_read(_home() / "jev" / "state.json")}
    value = state.get("routing")
    if value is None:
        value = _hermes_jev_routing()
    return str(value or "off").lower() in ("on", "shadow")


def _middleware_available() -> bool:
    """Does this Hermes have the execution middleware dispatch needs."""
    try:
        from hermes_cli.middleware import LLM_EXECUTION_MIDDLEWARE  # type: ignore  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def _log(entry: Dict[str, Any]) -> None:
    """Decisions only. Never prompt text, never an answer."""
    try:
        path = _home() / "logs" / "jev-decisions.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"ts": round(time.time(), 3), "profile": _profile(), "kind": "dispatch", **entry}
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, separators=(",", ":"), default=str) + "\n")
    except OSError:
        pass


def _summary(decision: Dict[str, Any]) -> Dict[str, Any]:
    """What the log keeps of a decision: who, why and how it went. No text, no stderr."""
    out = {key: decision.get(key) for key in ("agent", "model", "reason", "downgraded", "privacy", "privacy_why",
                                              "would_send_chars") if key in decision}
    triage = decision.get("triage") or {}
    out["triage"] = {key: triage.get(key) for key in ("type", "exit", "signals", "niveau", "repo_werk", "source", "why")}
    out["attempts"] = [{"agent": a.get("agent"), "error": a.get("error")} for a in decision.get("attempts") or []]
    return out


def _completion(text: str, model: str) -> Any:
    """A chat completion in the shape Hermes's chat_completions transport reads."""
    message = SimpleNamespace(role="assistant", content=text, tool_calls=None, reasoning=None, refusal=None)
    choice = SimpleNamespace(index=0, message=message, finish_reason="stop")
    return SimpleNamespace(id=f"dispatch-{int(time.time() * 1000)}", object="chat.completion",
                           created=int(time.time()), model=model, choices=[choice], usage=None)


# Where the context Hermes injects into a turn begins: the recalled memory, then every plugin's.
_INJECTED = "<memory-context>"


def _said(content: Any) -> Any:
    """One row's content without the context Hermes injected after it.

    A turn made of parts keeps that context as a text part of its own (Hermes #71998), and read
    back from the session store it is one text with the context inline. Everything from the
    memory block on is cut. Plugin context sent without recalled memory has no mark to find.
    """
    if isinstance(content, str):
        return content.split(_INJECTED, 1)[0].rstrip() if _INJECTED in content else content
    if not isinstance(content, list):
        return content
    kept = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "text" and _INJECTED in str(part.get("text") or ""):
            part = {**part, "text": str(part.get("text")).split(_INJECTED, 1)[0].rstrip()}
            if not part["text"]:
                continue
        kept.append(part)
    return kept


def _clean_history(rows: Any, text: str) -> Optional[List[Dict[str, Any]]]:
    """The conversation as it was said, from `pre_llm_call`'s history, which ends with this turn.

    A row's `content` is what was said. The wire copy replays each earlier user row's
    `api_content` instead: the recalled `<memory-context>` block and every plugin's
    `pre_llm_call` context. Compaction summaries (`_compressed_summary`) retell tool work, so they
    stay behind too. Rows are copied, because Hermes adds this turn's context to the live row
    later. None when Hermes passed no history (an older Hermes): the wire copy is all there is.
    """
    if not isinstance(rows, list):
        return None
    kept: List[Dict[str, Any]] = []
    for row in reversed(rows):
        if len(kept) >= _HISTORY_ROWS:
            break
        if not isinstance(row, dict) or row.get("_compressed_summary") or row.get("role") not in ("user", "assistant"):
            continue
        content = _said(row.get("content"))
        if dispatch.relay.text_of(content).strip():
            kept.append({"role": row["role"], "content": content})
    kept.reverse()
    if not kept or kept[-1]["role"] != "user":
        kept.append({"role": "user", "content": text})      # the turn's own row went with a compaction summary
    return kept


def _on_pre_llm_call(session_id: str = "", turn_id: Any = None, user_message: Any = "",
                     parent_session_id: str = "", platform: str = "", conversation_history: Any = None,
                     **_: Any) -> Any:
    # A message made of parts is handed over as its text, never as the JSON of its parts.
    text = user_message if isinstance(user_message, str) else dispatch.relay.text_of(user_message)
    history = _clean_history(conversation_history, text)
    key = session_id or "-"
    with _LOCK:
        # Out and back in, so the map runs oldest first; only a new session makes another one go.
        if _TURNS.pop(key, None) is None and len(_TURNS) >= _MAX_SESSIONS:
            _TURNS.pop(next(iter(_TURNS)))
        _TURNS[key] = {"turn_id": turn_id, "text": text, "history": history, "child": bool(parent_session_id),
                       "platform": str(platform or ""), "claimed": False, "decision": None}
    return None


def _find_turn(key: str, turn_id: Any) -> Optional[Dict[str, Any]]:
    """This turn's record, under its session or else by its turn id. Call with _LOCK held.

    Preflight compression can rotate the session id between pre_llm_call and the first provider
    call. Hermes's turn id holds a uuid, so it names one turn whatever the session is called now.
    """
    turn = _TURNS.get(key)
    if turn is not None and turn["turn_id"] == turn_id:
        return turn
    if turn_id:
        return next((other for other in _TURNS.values() if other["turn_id"] == turn_id), None)
    return None


def _on_llm_execution(request: Any = None, next_call: Any = None, session_id: str = "", turn_id: Any = None,
                      api_mode: str = "", **_: Any) -> Any:
    try:
        policy = dispatch.load_policy()
        mode = _setting("mode", policy, "off")
    except Exception:  # noqa: BLE001 - settings that cannot be read dispatch nothing
        return next_call(request)
    if mode not in ("shadow", "on") or not isinstance(request, dict):
        return next_call(request)
    key = session_id or "-"
    with _LOCK:
        turn = _find_turn(key, turn_id)
        first = turn is not None and not turn["claimed"]
        if first:
            turn["claimed"] = True
    if not first or turn["child"]:
        return next_call(request)
    text = turn["text"]
    if turn["platform"] in (policy.get("skip_platforms") or []) or any(
            text.lstrip().startswith(prefix) for prefix in (policy.get("skip_prefixes") or [])):
        return next_call(request)
    if _jev_routing_active():
        _log({"mode": mode, "agent": dispatch.LOCAL, "reason": "stood aside: /jev routing is on (one classifier per turn)"})
        return next_call(request)
    live = mode == "on" and api_mode == "chat_completions"
    try:
        # The wire copy is what the local model reads, so it sizes the context. The handoff is the
        # clean history: the wire carries recalled memory and every plugin's context in its rows.
        # Without a history (an older Hermes) it is the wire, cut the same way where it can be.
        wire = request.get("messages") or []
        decision = dispatch.dispatch_turn(
            text, turn.get("history") or _clean_history(wire, text), profile=_profile(),
            context_tokens=len(json.dumps(wire, default=str)) // 4,
            interactive=True, run=live, session=_SESSIONS.get(key, ""), policy=policy)
    except Exception as error:  # noqa: BLE001 - the turn goes ahead locally, and the log says why
        _log({"mode": mode, "agent": dispatch.LOCAL, "reason": f"dispatch failed ({type(error).__name__})"})
        return next_call(request)
    turn["decision"] = decision
    _log({"mode": mode, "live": live, **_summary(decision)})
    if any(attempt.get("agent") == "claude" for attempt in decision.get("attempts") or []):
        with _LOCK:
            _SESSIONS.pop(key, None)            # a session that failed is not resumed next turn
    if live and decision.get("agent") != dispatch.LOCAL and decision.get("text"):
        if decision.get("session"):
            with _LOCK:
                if key not in _SESSIONS and len(_SESSIONS) >= _MAX_SESSIONS:
                    _SESSIONS.pop(next(iter(_SESSIONS)))
                _SESSIONS[key] = str(decision["session"])
        return _completion(str(decision["text"]), str(decision.get("model") or decision["agent"]))
    return next_call(request)


def _on_transform_output(response_text: str = "", session_id: str = "", turn_id: Any = None, **_: Any) -> Any:
    """Say it when a turn that deserved another agent was answered here. Once per turn."""
    policy = dispatch.load_policy()
    if _setting("notice", policy, "off") != "on" or _setting("mode", policy, "off") != "on":
        return None
    with _LOCK:
        key = session_id or "-"
        turn = (_find_turn(key, turn_id) if turn_id else _TURNS.get(key)) or {}
        decision = turn.get("decision") or {}
        if decision.get("agent") != dispatch.LOCAL or not decision.get("downgraded") or decision.get("noticed"):
            return None
        decision["noticed"] = True
    return f"[dispatch] {decision.get('reason')}\n\n{response_text}"


def _switch(name: str, value: str) -> str:
    path = _state_path()
    state = _read(path)
    state[name] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return f"dispatch {name} = {value} for {_profile()}."


def _dispatch_command(raw_args: str = "") -> str:
    words = (raw_args or "").split()
    if len(words) == 1 and words[0] in ("on", "shadow", "off"):
        return _switch("mode", words[0])
    if len(words) == 2 and words[0] == "notice" and words[1] in ("on", "off"):
        return _switch("notice", words[1])
    policy = dispatch.load_policy()
    report = dispatch.check_agents(policy)
    klass = (policy.get("profiles") or {}).get(_profile()) or policy.get("default_privacy")
    lines = [f"dispatch: {_setting('mode', policy, 'off')} · notice: {_setting('notice', policy, 'off')} · "
             f"profile {_profile()} is {klass}"]
    if not _middleware_available():
        lines.append("  this Hermes has no llm_execution middleware: dispatch cannot act here; update Hermes")
    for path in report.get("broken_files") or []:
        lines.append(f"  {path} cannot be read: it turns every agent off and privacy to the strictest; fix it")
    for name, row in report["agents"].items():
        cooling = f", cooling {row['cooling_s']} s" if row["cooling_s"] else ""
        lines.append(f"  {name}: {'on' if row['enabled'] else 'off'}, {row['kind']} "
                     f"{row['model'] or '(its default model)'}, {'found' if row['available'] else 'NOT FOUND'}{cooling}")
    lines.append("usage: /dispatch on|shadow|off · /dispatch notice on|off")
    return "\n".join(lines)


def register(ctx: Any) -> None:
    global _CTX
    _CTX = ctx
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("transform_llm_output", _on_transform_output)
    try:
        ctx.register_middleware("llm_execution", _on_llm_execution)
    except Exception:  # noqa: BLE001 - an older Hermes: the plugin still loads, and /dispatch says why it idles
        pass
    ctx.register_command("dispatch", _dispatch_command, description="Front desk: which agent answers a turn",
                         args_hint="[on|shadow|off | notice on|off]")
