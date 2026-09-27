"""The front desk: which agent answers a fresh user turn.

Three parts, kept apart on purpose:

* classify: what the turn is. Jev answers today; a local receptionist writing a TRIAGE line
  is the other classifier this schema is built for. One classifier per turn, never both.
* policy: which agents the turn may go to (privacy, context, cooldowns) and which comes
  first. Code and a JSON file, never a model.
* run: hand the turn to that agent and relay its answer unchanged (jevkit/relay.py).

The TRIAGE record is the reasoning library's routing contract, so both classifiers produce the
same thing and one policy serves both. Every unsure, failed or blocked path answers on this
machine: the chat never waits on an agent that is not there.
"""
from __future__ import annotations

import copy
import json
import os
import re
import shlex
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import catalog as catalog_mod
from . import agents, client, keystore, ladder, privacy, relay, route

TYPES = ("EXPLAIN", "CREATE", "CHANGE", "FIX", "DECIDE", "RESEARCH", "REVIEW", "TALK")
EXITS = ("PROCEED", "ASSUME", "ASK", "ESCALATE")
SIGNALS = tuple(f"G{n}" for n in range(1, 10))
NIVEAUS = ("tiny", "fast", "standard", "max_lokaal", "frontier")
PRIVACY = ("public", "private", "highly_sensitive")

_TRIAGE_LINE = re.compile(r"^[ \t]*TRIAGE[ \t]+(\{.*)[ \t]*$", re.MULTILINE)


def check_triage(record: Any) -> List[str]:
    """Everything wrong with one TRIAGE record. An empty list means the policy may use it."""
    if not isinstance(record, dict):
        return ["TRIAGE must be one JSON object"]
    errors: List[str] = []
    types = record.get("type")
    types = types if isinstance(types, list) else [types]
    if not types or any(t not in TYPES for t in types):
        errors.append("type must be one or more of " + ", ".join(TYPES))
    decision = record.get("exit")
    if decision not in EXITS:
        errors.append("exit must be one of " + ", ".join(EXITS))
    signals = record.get("signals")
    if not isinstance(signals, list) or any(s not in SIGNALS for s in signals):
        errors.append("signals must be a list of G1 to G9, empty when there are none")
    niveau = record.get("niveau")
    if not (decision == "ASK" and niveau is None) and niveau not in NIVEAUS:
        errors.append("niveau must be one of " + ", ".join(NIVEAUS) + " (null only for ASK)")
    if record.get("privacy") not in PRIVACY:
        errors.append("privacy must be public, private or highly_sensitive")
    if not isinstance(record.get("interactief"), bool):
        errors.append("interactief must be true or false")
    tokens = record.get("context_tokens")
    if tokens is not None and (isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0):
        errors.append("context_tokens must be a whole number, 0 or more")
    if "repo_werk" in record and not isinstance(record["repo_werk"], bool):
        errors.append("repo_werk must be true or false")
    question, assumption, reason = (record.get(key) for key in ("question", "assumption", "reason"))
    if decision == "ASK" and not (isinstance(question, str) and question.count("?") == 1
                                  and len(question.strip()) >= 4):
        errors.append("ASK needs a question with exactly one question mark")
    if decision == "ASSUME" and not (isinstance(assumption, str) and len(assumption.strip()) >= 4):
        errors.append("ASSUME needs an assumption")
    if decision == "ESCALATE" and not (isinstance(reason, str) and len(reason.strip()) >= 4):
        errors.append("ESCALATE needs a reason")
    return errors


def parse_triage(text: str) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """The one TRIAGE line in a model's output, validated: (record, []) or (None, errors)."""
    if not isinstance(text, str):
        return None, ["TRIAGE input is not text"]
    found = _TRIAGE_LINE.findall(text)
    if not found:
        return None, ["no TRIAGE line"]
    if len(found) > 1:
        return None, ["more than one TRIAGE line"]
    try:
        record = json.loads(found[0])
    except json.JSONDecodeError as error:
        return None, [f"TRIAGE is not valid JSON ({error.msg})"]
    except (ValueError, RecursionError):
        return None, ["TRIAGE is not valid JSON (too deep or too large)"]
    errors = check_triage(record)
    return (None, errors) if errors else (record, [])


# Words that put someone else's health, money, record or file into the turn. They make a turn
# highly sensitive: only this machine may answer it. A long term counts anywhere, so plurals and
# compounds do too ("dossiers", "zorgdossier"); a short one only as a whole word. Deliberately
# not "client", "token" or "diagnose": in a coding chat those are ordinary words, and a gate that
# fires on every other coding turn gets switched off. "clienten" and "patienten" are the Dutch
# words typed without the trema; neither is an English word. dispatch.json can add terms
# (`sensitive_terms`); it never removes these.
DEFAULT_SENSITIVE_TERMS = (
    "cliënt", "patiënt", "clienten", "patienten", "dossier", "gespreksverslag", "behandelplan", "anamnese",
    "medicatie", "strafblad", "schulden", "burgerservicenummer", "bsn", "iban",
)


def _mentions(lowered: str, term: str) -> bool:
    term = term.lower()
    if len(term) >= 6:
        return term in lowered
    return re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", lowered) is not None


def _extra_terms(policy: Dict[str, Any]) -> Tuple[str, ...]:
    """`sensitive_terms` from dispatch.json: one string is one term, never a string of letters."""
    extra = policy.get("sensitive_terms")
    if isinstance(extra, str):
        return (extra,)
    if isinstance(extra, (list, tuple)):
        return tuple(term for term in extra if isinstance(term, str) and term.strip())
    return ()


def privacy_class(text: str, *, profile: Optional[str], policy: Dict[str, Any]) -> Tuple[str, str]:
    """(class, why) for one turn: the stricter of what the profile is and what the text shows.

    A profile nobody classified gets `default_privacy`, highly sensitive unless the policy says
    otherwise: an unknown lane stays on this machine.
    """
    profiles = policy.get("profiles") if isinstance(policy.get("profiles"), dict) else {}
    name = profile or "default"
    classified = profiles.get(name)
    base = classified or policy.get("default_privacy") or "highly_sensitive"
    if base not in PRIVACY:
        base = "highly_sensitive"
    probe = privacy.normalize(text or "")
    if privacy.has_secret_value(probe):
        return "highly_sensitive", "holds a secret value"
    lowered = probe.lower()
    for term in DEFAULT_SENSITIVE_TERMS + _extra_terms(policy):
        if _mentions(lowered, privacy.normalize(term)):
            return "highly_sensitive", f"mentions {term}"
    if privacy.has_iban(probe):
        return "highly_sensitive", "holds an IBAN"
    if base == "public" and privacy.has_contact_details(probe):
        return "private", "holds contact details"
    if classified in PRIVACY:
        return base, f"profile {name}"
    return base, f"profile {name} is not classified, so {base}"


LOCAL = "local"

DEFAULT_POLICY: Dict[str, Any] = {
    "mode": "off",                            # off | shadow | on; `/dispatch` in Hermes overrides it
    "default_privacy": "highly_sensitive",    # a profile nobody classified stays on this machine
    "profiles": {},                           # {"default": "private", "coding": "public"}
    "sensitive_terms": [],                    # added to DEFAULT_SENSITIVE_TERMS, never replacing them
    "jev_text_for": ["public"],               # classes whose redacted text Jev reads; the rest send features
    "tier_to_niveau": {"simple": "tiny", "medium": "standard", "hard": "frontier"},
    "frontier_order": {"repo": ["claude", "openai"], "default": ["openai", "claude"]},
    "last_resort": "openrouter",              # public turns only, whatever its own settings say
    "agents": {
        "openai": {"kind": "codex", "enabled": False, "model": "", "privacy": ["public", "private"],
                   "timeout": 600, "cooldown": 1800, "context_tokens": 0},
        "claude": {"kind": "claude", "enabled": False, "model": "", "privacy": ["public", "private"],
                   "only_repo": True, "max_turns": 8, "timeout": 600, "cooldown": 1800, "context_tokens": 0},
        "openrouter": {"kind": "openrouter", "enabled": False, "model": "", "privacy": ["public"],
                       "timeout": 120, "cooldown": 600, "context_tokens": 0},
    },
    "handoff": {"max_messages": 6, "max_chars": 12000},
    "turn_budget": 600,                       # seconds one turn may spend on agents, under Hermes's 900 s idle warning
    "timeout_cooldown": 300,                  # an agent that timed out is skipped this long, not the full cooldown
    "skip_prefixes": ["[kanban]", "[SESSION HANDOFF"],
    "skip_platforms": ["cron"],
}


def policy_paths() -> List[Path]:
    """Least specific first, like routing.json: the shared file, then this profile's own."""
    override = os.environ.get("JEV_DISPATCH_POLICY")
    if override:
        return [Path(override)]
    paths = [Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "jev" / "dispatch.json"]
    root, home = catalog_mod.hermes_root(), catalog_mod.hermes_home()
    if root.is_dir():
        paths.append(root / "jev" / "dispatch.json")
        if home != root:
            paths.append(home / "jev" / "dispatch.json")
    return paths


# Every agent setting the code reads, with the type a file must give it. A value of another
# type is dropped and the default stays: `"enabled": "false"` is a string, so it is not a yes.
_AGENT_SHAPE: Dict[str, Any] = {"kind": "", "enabled": False, "model": "", "privacy": [], "only_repo": False,
                                "max_turns": 8, "timeout": 600, "cooldown": 1800, "context_tokens": 0, "argv": []}


def _coerce(default: Any, value: Any) -> Any:
    """One string where a list of strings is expected is a list of one, never dropped.

    Dropping it would be the unsafe direction: `"sensitive_terms": "salaris"` would lose the term.
    """
    return [value] if isinstance(default, list) and isinstance(value, str) else value


def _fits(default: Any, value: Any) -> bool:
    """Does a value from a file have the type of the default it would replace."""
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, (int, float)):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(default, str):
        return isinstance(value, str)
    if isinstance(default, list):
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    if isinstance(default, dict):
        return isinstance(value, dict)
    return True


def _orders(value: Any) -> Dict[str, List[str]]:
    """`frontier_order` with every order a list of agent names. One name alone is an order of one."""
    out: Dict[str, List[str]] = {}
    for kind, order in (value.items() if isinstance(value, dict) else ()):
        if isinstance(order, str):
            order = [order]
        if isinstance(order, list):
            out[str(kind)] = [name for name in order if isinstance(name, str)]
    return out


# What a broken value becomes, where keeping the previous file's value could loosen privacy.
_BROKEN_AGENT = {"enabled": False, "privacy": []}
_BROKEN_TOP = {"profiles": {}, "default_privacy": "highly_sensitive", "jev_text_for": []}


def load_policy(path: Optional[Path] = None) -> Dict[str, Any]:
    """The defaults with each file laid over them. A missing or broken file is skipped, never fatal."""
    policy = copy.deepcopy(DEFAULT_POLICY)
    for candidate in ([path] if path else policy_paths()):
        try:
            layer = json.loads(Path(candidate).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(layer, dict):
            continue
        agents = layer.get("agents")
        if isinstance(agents, dict):
            for name, settings in agents.items():
                if not isinstance(settings, dict):
                    continue
                merged = dict(policy["agents"].get(name, {}))
                for key, value in settings.items():
                    if key == "argv" and isinstance(value, str):
                        try:
                            value = shlex.split(value)     # a command line, as a person writes one
                        except ValueError:                 # an unbalanced quote: keep the default
                            continue
                    if key in _AGENT_SHAPE:
                        value = _coerce(_AGENT_SHAPE[key], value)
                    if key not in _AGENT_SHAPE or _fits(_AGENT_SHAPE[key], value):
                        merged[key] = value
                    elif key in _BROKEN_AGENT:
                        merged[key] = copy.deepcopy(_BROKEN_AGENT[key])   # a broken yes is a no
                policy["agents"][name] = merged
        for key, value in layer.items():
            if key in DEFAULT_POLICY:
                value = _coerce(DEFAULT_POLICY[key], value)
            if key == "agents":
                continue
            if key in DEFAULT_POLICY and not _fits(DEFAULT_POLICY[key], value):
                if key in _BROKEN_TOP:
                    policy[key] = copy.deepcopy(_BROKEN_TOP[key])
                continue
            # One level deep, so a file that changes one order or one profile keeps the others.
            if isinstance(value, dict) and isinstance(policy.get(key), dict):
                policy[key] = {**policy[key], **value}
            else:
                policy[key] = value
    policy["frontier_order"] = _orders(policy.get("frontier_order"))
    return policy


def _int(value: Any, default: int = 0) -> int:
    """A number from a hand-edited file. Anything that is not one counts as the default."""
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _local(reason: str, considered: List[Dict[str, Any]], downgraded: bool = False) -> Dict[str, Any]:
    return {"agent": LOCAL, "kind": LOCAL, "model": "", "reason": reason, "considered": considered,
            "downgraded": downgraded}


def _skip(name: str, agent: Dict[str, Any], triage: Dict[str, Any], klass: str, last_resort: bool,
          cooling: Callable[[str], float]) -> str:
    """Why this agent may not take the turn, or "" when it may."""
    if not agent:
        return "not configured"
    if not agent.get("enabled"):
        return "not enabled"
    if (last_resort or agent.get("kind") == "openrouter") and klass != "public":
        return "OpenRouter and the last resort take public turns only"
    if klass not in (agent.get("privacy") or []):
        return f"not allowed for {klass} turns"
    if agent.get("only_repo") and not triage.get("repo_werk"):
        return "only for repository work"
    if agent.get("kind") == "openrouter" and not agent.get("model"):
        return "no model configured"
    window = _int(agent.get("context_tokens"))
    if window and _int(triage.get("context_tokens")) * 1.25 > window:
        return "the conversation does not fit its context window"
    left = cooling(name)
    if left > 0:
        return f"cooling down, {round(left)} s left"
    return ""


def choose_route(triage: Dict[str, Any], policy: Dict[str, Any], *,
                 cooling: Optional[Callable[[str], float]] = None) -> Dict[str, Any]:
    """Which agent answers this turn: this machine, unless every hurdle lets the turn out.

    In order: a question goes to the person; a level below frontier stays here; a highly
    sensitive turn stays here; then the frontier agents in the policy's order for this kind of
    work, then the last resort. When none of them may take it this machine answers, and the
    decision says it was a downgrade. Never silently.
    """
    cooling = cooling or ladder.cooling
    klass = triage.get("privacy") if triage.get("privacy") in PRIVACY else "highly_sensitive"
    niveau = triage.get("niveau") or "standard"          # missing means standard, never the cloud
    considered: List[Dict[str, Any]] = []
    if triage.get("exit") == "ASK":
        return _local("a question for the person, not for another model", considered)
    if niveau != "frontier":
        if niveau == "max_lokaal" and triage.get("interactief", True):
            return _local("max_lokaal is for background work; the chat model answers now", considered)
        return _local(f"{niveau} work stays on this machine", considered)
    if klass == "highly_sensitive":
        return _local("highly sensitive: nothing leaves this machine", considered, downgraded=True)
    agents = policy.get("agents") or {}
    order = list((policy.get("frontier_order") or {}).get("repo" if triage.get("repo_werk") else "default") or [])
    last = str(policy.get("last_resort") or "")
    if last and last not in order:
        order.append(last)
    for name in order:
        agent = agents.get(name) or {}
        reason = _skip(name, agent, triage, klass, name == last, cooling)
        if reason:
            considered.append({"agent": name, "skipped": reason})
            continue
        return {"agent": name, "kind": agent.get("kind", ""), "model": agent.get("model") or "",
                "reason": f"frontier work for {name}" + (" (last resort)" if name == last else ""),
                "considered": considered, "downgraded": False}
    return _local("no frontier agent may take this turn; this machine answers", considered, downgraded=True)


_KIND_TO_TYPE = {"coding": "CHANGE", "writing": "CREATE", "research": "RESEARCH", "general": "EXPLAIN"}


def classify_with_jev(text: str, *, privacy_class: str, policy: Dict[str, Any], context_tokens: int = 0,
                      interactive: bool = True, config: Optional[Dict[str, Any]] = None,
                      transport: Optional[client.Transport] = None, timeout: float = 2.5,
                      answers: Optional[Dict[str, Any]] = None, profile: Optional[str] = None) -> Dict[str, Any]:
    """One TRIAGE record from Jev's three routing answers, judged by the router's own rules.

    Jev is never asked about a highly sensitive turn. For a class not in `jev_text_for` Jev reads
    coarse features only, never the text, as a private profile does in routing. Jev down, slow,
    or answering in a shape we cannot read: niveau `standard`, which the policy keeps here.
    """
    record: Dict[str, Any] = {"type": "EXPLAIN", "exit": "PROCEED", "signals": [], "niveau": "standard",
                              "privacy": privacy_class, "context_tokens": max(0, _int(context_tokens)),
                              "repo_werk": False, "interactief": bool(interactive)}
    if privacy_class not in PRIVACY or privacy_class == "highly_sensitive":
        return {**record, "privacy": "highly_sensitive", "source": "policy",
                "why": "highly sensitive: Jev is not asked"}
    config = config or route.load_config()
    inner = route.unwrap(text, config)
    limit = int(config.get("ask_chars", 2500))
    # Jev reads text only where routing would too: never for a private profile or routing set to
    # features, and never for a turn that looks like it holds a secret (privacy.has_secret_value
    # may still let a question about secrets leave, as text for an agent, not for Jev).
    features_only = (privacy_class not in (policy.get("jev_text_for") or [])
                     or privacy.is_sensitive(inner)
                     or config.get("mode") == "features"
                     or (profile or "default") in (config.get("private_profiles") or []))
    if answers is None:
        state = route.state_for(route.clip_ask(inner, limit), context_tokens=record["context_tokens"],
                                private=features_only, limit=limit)
        try:
            answers = client.ask(state, route.questions(), timeout=timeout, transport=transport)["answers"]
        except client.JevError as error:
            return {**record, "source": "fail_open", "why": f"Jev unavailable ({error.code})"}
    if not all(isinstance(answers.get(name), dict) for name in ("difficulty", "kind", "costly_mistake")):
        return {**record, "source": "fail_open", "why": "routing answers incomplete"}
    try:
        judged = route.judge_answers(answers, config, risky=route.is_risky(inner), features_only=features_only)
    except (KeyError, TypeError, ValueError):
        return {**record, "source": "fail_open", "why": "routing answers incomplete"}
    jev ={key: judged[key] for key in ("tier", "specialty", "confidence", "difficulty", "stakes")}
    if judged["tier"] is None:
        return {**record, "niveau": "tiny", "source": "jev", "why": judged["reason"], "jev": jev}
    niveau = (policy.get("tier_to_niveau") or {}).get(judged["tier"], "standard")
    record.update(type=_KIND_TO_TYPE.get(judged["specialty"], "EXPLAIN"),
                  niveau=niveau if niveau in NIVEAUS else "standard",
                  repo_werk=judged["specialty"] == "coding", source="jev", why=judged["reason"], jev=jev)
    if record["niveau"] == "frontier":
        record.update(exit="ESCALATE", signals=["G8"],
                      reason=f"Jev judged this {judged['tier']} {judged['specialty']} work")
    return record


def run_agent(chosen: Dict[str, Any], prompt: str, *, policy: Dict[str, Any], session: str = "",
              runners: Optional[Dict[str, Any]] = None, transport: Optional[Callable[..., bytes]] = None,
              timeout: Optional[float] = None) -> agents.Result:
    """Hand one prompt to the agent a route named, within its own time limit or `timeout` if shorter.

    Raises agents.AgentError.
    """
    settings = (policy.get("agents") or {}).get(chosen["agent"]) or {}
    kind, model = settings.get("kind"), str(settings.get("model") or "")
    limit = float(_int(settings.get("timeout"), 600) or 600)
    timeout = min(limit, timeout) if timeout else limit
    runners = runners or {}
    if kind == "codex":
        return agents.run_codex(prompt, model=model, timeout=timeout, argv=settings.get("argv"),
                                runner=runners.get("codex"))
    if kind == "claude":
        return agents.run_claude(prompt, model=model, session=session, max_turns=_int(settings.get("max_turns"), 8) or 8,
                                 timeout=timeout, argv=settings.get("argv"), runner=runners.get("claude"))
    if kind == "openrouter":
        return agents.run_openrouter(prompt, model=model, timeout=timeout, transport=transport)
    raise agents.AgentError("failed", f"unknown agent kind {kind!r}")


# Failures that will not fix themselves by the next turn: cool the agent for its full cooldown,
# so no lane retries it on every message. A timeout cools it briefly (`timeout_cooldown`), so the
# next turn does not wait out the same slow seat again. An odd failure cools nothing.
_COOL_ON = ("quota", "auth", "missing")
_BUDGET_FLOOR = 30.0                          # below this many seconds left, no agent is started


def _rung(name: str) -> str:
    """Dispatch's cooldowns live in the shared ladder file under their own names, so a dispatch
    timeout never cools routing's `claude` rung, and routing's refusals never shut dispatch out."""
    return f"dispatch:{name}"


def dispatch_turn(text: str, messages: Sequence[Dict[str, Any]], *, profile: Optional[str] = "default",
                  context_tokens: int = 0, interactive: bool = True, run: bool = True, session: str = "",
                  policy: Optional[Dict[str, Any]] = None, config: Optional[Dict[str, Any]] = None,
                  transport: Optional[client.Transport] = None,
                  agent_transport: Optional[Callable[..., bytes]] = None,
                  runners: Optional[Dict[str, Any]] = None, cooling: Optional[Callable[[str], float]] = None,
                  refuse: Optional[Callable[..., Any]] = None, answers: Optional[Dict[str, Any]] = None,
                  clock: Optional[Callable[[], float]] = None) -> Dict[str, Any]:
    """One fresh user turn, start to finish. `agent` is "local" whenever this machine answers.

    The privacy class is read over everything a handoff would carry (the request and the recent
    history), not the newest message alone. With run=False (shadow) it decides and reports what
    it would send, and hands nothing over. `turn_budget` bounds the time spent on agents.
    """
    policy = policy or load_policy()
    cooling = cooling or ladder.cooling
    refuse = refuse or ladder.refuse
    clock = clock or time.monotonic
    started = clock()
    budget = float(_int(policy.get("turn_budget"), 900))
    handoff = policy.get("handoff") or {}
    max_messages = _int(handoff.get("max_messages"), 6)
    leaving = relay.leaving_text(messages, request=text, max_messages=max_messages)
    klass, why = privacy_class(leaving or text, profile=profile, policy=policy)
    triage = classify_with_jev(text, privacy_class=klass, policy=policy, context_tokens=context_tokens,
                               interactive=interactive, config=config, transport=transport, answers=answers,
                               profile=profile)
    out: Dict[str, Any] = {"privacy": klass, "privacy_why": why, "jev": triage.get("jev"),
                           "triage": {k: v for k, v in triage.items() if k != "jev"}, "attempts": []}
    if klass == "highly_sensitive":
        # Said before the route is chosen, so the log names the real reason, not "standard work".
        return {**out, **_local(f"highly sensitive ({why}): this machine answers", [])}
    if relay.has_images(messages):
        return {**out, **_local("the turn carries an image; a handed-off turn is text only for now", [],
                                downgraded=triage.get("niveau") == "frontier")}
    failed: Dict[str, str] = {}
    for _ in range(len(policy.get("agents") or {}) + 1):
        chosen = choose_route(triage, policy, cooling=lambda name: 1e9 if name in failed else cooling(_rung(name)))
        if chosen["agent"] == LOCAL:
            return {**out, **chosen, "downgraded": chosen["downgraded"] or bool(failed)}
        prompt = relay.build_handoff(messages, agent=chosen["agent"], request=text,
                                     reason=str(triage.get("reason") or triage.get("why") or "frontier work"),
                                     max_messages=max_messages, max_chars=_int(handoff.get("max_chars"), 12000))
        if prompt is None:
            return {**out, **_local("the conversation holds something that must not leave this machine",
                                    chosen["considered"], downgraded=True)}
        if not run:
            return {**out, **chosen, "would_send_chars": len(prompt)}
        remaining = budget - (clock() - started)
        if remaining < _BUDGET_FLOOR:
            out["attempts"].append({"agent": chosen["agent"], "error": "budget",
                                    "detail": "the turn's time budget is spent"})
            break
        try:
            result = run_agent(chosen, prompt, policy=policy, session=session, runners=runners,
                               transport=agent_transport, timeout=remaining)
        except agents.AgentError as error:
            out["attempts"].append({"agent": chosen["agent"], "error": error.code, "detail": error.detail})
            failed[chosen["agent"]] = error.code
            settings = (policy.get("agents") or {}).get(chosen["agent"]) or {}
            if error.code in _COOL_ON:
                refuse(_rung(chosen["agent"]), f"{error.code}: {error.detail}",
                       cooldown=float(_int(settings.get("cooldown"), 1800)))
            elif error.code == "timeout":
                refuse(_rung(chosen["agent"]), f"{error.code}: {error.detail}",
                       cooldown=float(_int(policy.get("timeout_cooldown"), 300)))
            continue
        except Exception as error:  # noqa: BLE001 - an adapter bug is a failed attempt, never a lost turn
            out["attempts"].append({"agent": chosen["agent"], "error": "failed", "detail": type(error).__name__})
            failed[chosen["agent"]] = "failed"
            continue
        model = result.model or chosen["model"]
        return {**out, **chosen, "model": model, "session": result.session,
                "text": relay.relay(result.text, agent=chosen["agent"], model=model)}
    return {**out, **_local("no agent answered in time, or every one that may take this turn failed; "
                            "this machine answers", [], downgraded=True)}


def check_agents(policy: Dict[str, Any], *, which: Optional[Callable[[str], Optional[str]]] = None,
                 cooling: Optional[Callable[[str], float]] = None,
                 has_key: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
    """Per agent: on or off, its model, whether its program or key is here, and any cooldown. Runs nothing."""
    which = which or shutil.which
    cooling = cooling or ladder.cooling
    has_key = has_key or (lambda: bool(keystore.resolve("openrouter")))
    rows: Dict[str, Any] = {}
    for name, settings in (policy.get("agents") or {}).items():
        kind = settings.get("kind")
        argv = settings.get("argv")
        # The program that will actually run: a custom argument list names its own.
        program = argv[0] if isinstance(argv, list) and argv else {"codex": "codex", "claude": "claude"}.get(str(kind))
        available = bool(which(program)) if program else (has_key() if kind == "openrouter" else False)
        rows[name] = {"kind": kind, "enabled": bool(settings.get("enabled")), "model": settings.get("model") or "",
                      "privacy": settings.get("privacy") or [], "available": available,
                      "cooling_s": round(cooling(_rung(name)))}
    return {"policy_mode": policy.get("mode"), "profiles": policy.get("profiles") or {}, "agents": rows,
            "policy_files": [str(path) for path in policy_paths()]}
