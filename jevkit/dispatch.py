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

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from . import privacy

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
# highly sensitive: only this machine may answer it. Deliberately not "client" or "token": in
# a coding chat those are ordinary words, and a gate that fires on every other coding turn
# gets switched off. dispatch.json can add terms (`sensitive_terms`); it never removes these.
DEFAULT_SENSITIVE_TERMS = (
    "cliënt", "cliënten", "patiënt", "patiënten", "dossier", "bsn", "burgerservicenummer", "iban",
    "diagnose", "medicatie", "strafblad", "schulden", "gespreksverslag",
)
_IBAN = re.compile(r"\bNL\d{2}\s?[A-Z]{4}(?:\s?\d){10}\b")


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
        if re.search(r"(?<!\w)" + re.escape(str(term).lower()) + r"(?!\w)", lowered):
            return "highly_sensitive", f"mentions {term}"
    if _IBAN.search(probe):
        return "highly_sensitive", "holds an IBAN"
    if base == "public" and privacy.has_contact_details(probe):
        return "private", "holds contact details"
    return base, f"profile {profile or 'default'}"
