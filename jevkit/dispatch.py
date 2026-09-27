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
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import catalog as catalog_mod
from . import ladder, privacy

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
    if decision == "ASK" and str(record.get("question", "")).count("?") != 1:
        errors.append("ASK needs a question with exactly one question mark")
    if decision == "ASSUME" and len(str(record.get("assumption", "")).strip()) < 4:
        errors.append("ASSUME needs an assumption")
    if decision == "ESCALATE" and len(str(record.get("reason", "")).strip()) < 4:
        errors.append("ESCALATE needs a reason")
    return errors


def parse_triage(text: str) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """The one TRIAGE line in a model's output, validated: (record, []) or (None, errors)."""
    found = _TRIAGE_LINE.findall(text or "")
    if not found:
        return None, ["no TRIAGE line"]
    if len(found) > 1:
        return None, ["more than one TRIAGE line"]
    try:
        record = json.loads(found[0])
    except json.JSONDecodeError as error:
        return None, [f"TRIAGE is not valid JSON ({error.msg})"]
    errors = check_triage(record)
    return (None, errors) if errors else (record, [])


# Words that put someone else's health, money, record or file into the turn. They make a turn
# highly sensitive: only this machine may answer it. A long term counts anywhere, so plurals and
# compounds do too ("dossiers", "zorgdossier"); a short one only as a whole word. Deliberately
# not "client", "token" or "diagnose": in a coding chat those are ordinary words, and a gate that
# fires on every other coding turn gets switched off. dispatch.json can add terms
# (`sensitive_terms`); it never removes these.
DEFAULT_SENSITIVE_TERMS = (
    "cliënt", "patiënt", "dossier", "gespreksverslag", "behandelplan", "anamnese", "medicatie",
    "strafblad", "schulden", "burgerservicenummer", "bsn", "iban",
)


def _mentions(lowered: str, term: str) -> bool:
    term = term.lower()
    if len(term) >= 6:
        return term in lowered
    return re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", lowered) is not None


def privacy_class(text: str, *, profile: Optional[str], policy: Dict[str, Any]) -> Tuple[str, str]:
    """(class, why) for one turn: the stricter of what the profile is and what the text shows.

    A profile nobody classified gets `default_privacy`, highly sensitive unless the policy says
    otherwise: an unknown lane stays on this machine.
    """
    profiles = policy.get("profiles") or {}
    base = profiles.get(profile or "default") or policy.get("default_privacy") or "highly_sensitive"
    if base not in PRIVACY:
        base = "highly_sensitive"
    probe = privacy.normalize(text or "")
    if privacy.is_sensitive(probe):
        return "highly_sensitive", "looks like it holds a secret"
    lowered = probe.lower()
    for term in DEFAULT_SENSITIVE_TERMS + tuple(policy.get("sensitive_terms") or ()):
        if _mentions(lowered, str(term)):
            return "highly_sensitive", f"mentions {term}"
    if privacy.has_iban(probe):
        return "highly_sensitive", "holds an IBAN"
    if base == "public" and privacy.has_contact_details(probe):
        return "private", "holds contact details"
    return base, f"profile {profile or 'default'}"


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
    "turn_budget": 900,                       # seconds one turn may spend on agents before this machine answers
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
                if isinstance(settings, dict):
                    policy["agents"][name] = {**policy["agents"].get(name, {}), **settings}
        for key, value in layer.items():
            if key == "agents":
                continue
            # One level deep, so a file that changes one order or one profile keeps the others.
            if isinstance(value, dict) and isinstance(policy.get(key), dict):
                policy[key] = {**policy[key], **value}
            else:
                policy[key] = value
    return policy


def _int(value: Any, default: int = 0) -> int:
    """A number from a hand-edited file. Anything that is not one counts as the default."""
    try:
        return int(value)
    except (TypeError, ValueError):
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
    if last_resort and klass != "public":
        return "the last resort takes public turns only"
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
