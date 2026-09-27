# Receptionist Dispatch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A front desk for a Hermes chat whose own model is local. A turn Jev judges hard is handed to the agent that should answer it (Codex on a ChatGPT login, Claude Code on a Claude login, or OpenRouter), and that agent's answer comes back unchanged. Everything else stays on the local model.

**Architecture:** Three parts that never mix.
1. **Classify** (`jevkit/dispatch.py`). Jev's three routing answers become one TRIAGE record in the reasoning library's routing-contract schema.
2. **Policy** (`jevkit/dispatch.py`, config `dispatch.json`). Deterministic code applies hurdles in order: privacy, level, context, cooldowns, then order.
3. **Run and relay** (`jevkit/agents.py`, `jevkit/relay.py`). The chosen CLI or endpoint gets a redacted handoff on stdin, and its answer returns under one line naming the author.

A Hermes plugin (`hermes/plugin/hermes-dispatch`) calls all three from `llm_execution` middleware, which is allowed to replace the provider call. Shadow mode decides and logs only. Every unsure, failed or blocked path answers locally.

**Tech stack:** Python 3.9+ standard library only. `unittest` and offline fakes. Hermes plugin seams: `pre_llm_call`, `transform_llm_output`, `llm_execution` middleware, and a command.

---

## Samenvatting voor Sander

Dit plan bouwt de "receptie" uit het gesprek van 24 t/m 27 september.

- **Standaard lokaal.** Je lokale model blijft de chat voeren. Alleen een beurt die Jev "moeilijk" vindt, en die volgens je privacyregels naar buiten mag, gaat naar Codex (ChatGPT-login), Claude Code (Max-login) of OpenRouter.
- **Antwoord komt letterlijk terug.** Er staat één regel boven die zegt wie het schreef.
- **Veilige start.** Alles staat standaard uit, er zijn geen modelnamen ingevuld, en een profiel dat je niet hebt ingedeeld blijft volledig lokaal.
- **Rol van de reasoning library.**
  - Het TRIAGE-schema van de library is het contract tussen classificeren en beleid.
  - De `<handoff>`-vorm en de relay-regels (letterlijk doorgeven, bron noemen) zijn in code afgedwongen.
  - In fase 2 kan een lokaal receptionist-model met de library zelf de TRIAGE-regel schrijven, in plaats van Jev.
  - Regel uit de library die de code afdwingt: **één classifier per bericht, nooit twee.**

## What exists and what this adds

| Need | Where it already is | What this plan adds |
|---|---|---|
| Difficulty, kind and stakes per turn | `route.questions()` / `route.decide` | `route.judge_answers()`, extracted so dispatch uses the same thresholds |
| Redaction, secret detection | `jevkit/privacy.py` | `has_contact_details()`, privacy classes per profile |
| A full seat, remembered by every lane | `jevkit/ladder.py` (`cooling`, `refuse`) | agents cooled on quota, auth and missing-program failures |
| Decision log and dashboard | `logs/jev-decisions.jsonl` | `kind: "dispatch"` rows (no prompt text) |
| Replacing the provider call | Hermes `llm_execution` middleware | `hermes-dispatch` plugin |

## Changes after the review of Tasks 0-2

The first batch was implemented and reviewed. These plan changes came out of it, and later tasks already include them:
- **Task 1:** the TRIAGE line may lack its closing brace, so a broken line is reported as invalid JSON rather than as missing. This is the only deviation from the plan's code.
- **Task 2b** (new): sensitive terms count in any form (plurals, compounds), an IBAN from any common country is found by its check digits, and the English verb "diagnose" no longer counts.
- **Task 3:** dict settings merge one level deep, a bad number in `dispatch.json` counts as the default, and `turn_budget` and `timeout_cooldown` are added.
- **Task 8:** `leaving_text` gives the privacy check everything a handoff would carry.
- **Task 9:** the privacy class covers the history too, a turn has a time budget, and a timeout cools the agent for five minutes.
- **Task 11:** the plugin reads its own settings from config.yaml, sees hermes-jev routing switched on in config.yaml as well (one classifier per turn), bounds its session map, and still loads on a Hermes without `llm_execution`.

## Before production (D0, on the machine that runs Hermes; not part of the code tasks)

Run these and keep the output. Every one is read-only apart from one short prompt per login.

```bash
# Hermes has the execution middleware this plugin needs
python3 -c "import hermes_cli.middleware as m; print(m.LLM_EXECUTION_MIDDLEWARE)"    # expect: llm_execution
# the chat provider is chat_completions (a local llama.cpp or Ollama endpoint normally is)
hermes config show | grep -i -E "provider|api_mode|base_url"
# Codex: logged in, flags present, prompt read from stdin
codex --version && codex login status
codex exec --help | grep -E -- "--json|--sandbox|--output-last-message|--skip-git-repo-check"
echo "Antwoord met alleen het woord: hallo" | codex exec --skip-git-repo-check --sandbox read-only -
# Claude Code: logged in with the Max plan (never --bare: that ignores the subscription login)
claude --version
echo "Antwoord met alleen het woord: hallo" | claude -p --output-format json --permission-mode plan
# OpenRouter key for the last resort, stored by jev (the person pastes it into the page)
jev setup-key --provider openrouter
jev dispatch check
```

If a flag differs in the installed version, put the working argument list under `agents.<name>.argv` in `dispatch.json`. No code change is needed.

---

### Task 0: Make the baseline green (hermetic key)

`tests/test_turn.py` and `tests/test_question_shape.py` fake the wire, but `client.ask` resolves a key before it uses the transport. On a machine with no key they fail with `no_key` (3 failures, 5 errors). Every later task needs a green baseline.

**Files:**
- Modify: `tests/test_turn.py` (imports and a module-level setup)
- Modify: `tests/test_question_shape.py` (the same)

- [ ] **Step 1: Confirm the failure**

Run: `env -u TYPESAFE_API_KEY -u OPENROUTER_API_KEY python3 -m unittest tests.test_turn tests.test_question_shape`
Expected: `FAILED (failures=3, errors=5)`, with `Jev unavailable (no_key)` in the output.

- [ ] **Step 2: Add the setup to both modules**

In each file, add `import os` and `from unittest import mock` to the imports, and this below the imports:

```python
def setUpModule():
    """client.ask resolves a key before it touches the fake wire.

    Without one it raises no_key, so these tests passed only where a real key happened to be
    installed. An obviously fake key in the environment is read first, so the keychain is
    never consulted either.
    """
    patcher = mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": "test-key-not-real"})
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)
```

- [ ] **Step 3: Run the whole suite**

Run: `env -u TYPESAFE_API_KEY -u OPENROUTER_API_KEY python3 -m unittest discover -s tests`
Expected: `OK` (4 skipped).

- [ ] **Step 4: Commit**

```bash
git add tests/test_turn.py tests/test_question_shape.py
git commit -m "tests: give the merged-request and question-shape suites a fake key"
```

---

### Task 1: The TRIAGE record

**Files:**
- Create: `jevkit/dispatch.py`
- Create: `tests/test_dispatch.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_dispatch.py`:

```python
"""The front desk (jevkit/dispatch.py). Offline: no key, no network, and no agent is ever run.

Fake secrets are spelled so they trip `privacy.is_sensitive` but not scripts/check_release.py:
a literal key shape in a test is a key shape in the release.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jevkit import cli, dispatch  # noqa: E402


def line(**fields):
    record = {"type": "EXPLAIN", "exit": "PROCEED", "signals": [], "niveau": "tiny", "privacy": "public",
              "context_tokens": 300, "repo_werk": False, "interactief": True}
    record.update(fields)
    return "TRIAGE " + json.dumps(record)


class TriageTests(unittest.TestCase):
    def test_a_valid_line_is_read(self):
        record, errors = dispatch.parse_triage(line() + "\nEen bind mount koppelt een map.")
        self.assertEqual(errors, [])
        self.assertEqual(record["niveau"], "tiny")

    def test_no_line_is_an_error_not_a_guess(self):
        self.assertEqual(dispatch.parse_triage("Gewoon een antwoord."), (None, ["no TRIAGE line"]))

    def test_two_lines_are_refused(self):
        self.assertEqual(dispatch.parse_triage(line() + "\n" + line()), (None, ["more than one TRIAGE line"]))

    def test_broken_json_is_refused(self):
        record, errors = dispatch.parse_triage('TRIAGE {"type": "EXPLAIN",')
        self.assertIsNone(record)
        self.assertTrue(errors[0].startswith("TRIAGE is not valid JSON"))

    def test_an_unknown_level_is_refused(self):
        _, errors = dispatch.parse_triage(line(niveau="cloud"))
        self.assertTrue(any("niveau" in e for e in errors))

    def test_ask_may_leave_the_level_empty_but_needs_exactly_one_question(self):
        _, errors = dispatch.parse_triage(line(exit="ASK", niveau=None, signals=["G4"], question="Welke datum?"))
        self.assertEqual(errors, [])
        _, errors = dispatch.parse_triage(line(exit="ASK", niveau=None, signals=["G4"], question="Wat? En wanneer?"))
        self.assertTrue(any("exactly one question" in e for e in errors))

    def test_escalate_needs_a_reason(self):
        _, errors = dispatch.parse_triage(line(exit="ESCALATE", signals=["G8"], niveau="frontier"))
        self.assertTrue(any("reason" in e for e in errors))

    def test_a_list_of_types_is_allowed(self):
        self.assertEqual(dispatch.parse_triage(line(type=["CHANGE", "REVIEW"]))[1], [])

    def test_a_boolean_is_not_a_token_count(self):
        _, errors = dispatch.parse_triage(line(context_tokens=True))
        self.assertTrue(any("context_tokens" in e for e in errors))
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 -m unittest tests.test_dispatch -v`
Expected: `ImportError`, because `jevkit.dispatch` does not exist yet.

- [ ] **Step 3: Write the module**

`jevkit/dispatch.py`:

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest tests.test_dispatch -v`
Expected: all 9 pass.

- [ ] **Step 5: Commit**

```bash
git add jevkit/dispatch.py tests/test_dispatch.py
git commit -m "dispatch: the TRIAGE record from the reasoning library's routing contract"
```

---

### Task 2: The privacy class of a turn

**Files:**
- Modify: `jevkit/privacy.py` (add `has_contact_details` after `is_sensitive`)
- Modify: `jevkit/dispatch.py` (add `DEFAULT_SENSITIVE_TERMS`, `_IBAN`, `privacy_class`)
- Test: `tests/test_dispatch.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_dispatch.py`)

```python
POLICY = {"profiles": {"default": "private", "coding": "public"}, "default_privacy": "highly_sensitive"}


class PrivacyClassTests(unittest.TestCase):
    def klass(self, text, profile="coding", policy=POLICY):
        return dispatch.privacy_class(text, profile=profile, policy=policy)[0]

    def test_a_profile_nobody_classified_stays_on_this_machine(self):
        self.assertEqual(self.klass("hoi", profile="onbekend"), "highly_sensitive")

    def test_the_profile_class_applies_to_an_ordinary_turn(self):
        self.assertEqual(self.klass("Leg uit wat een bind mount is."), "public")
        self.assertEqual(self.klass("Leg uit wat een bind mount is.", profile="default"), "private")

    def test_a_secret_makes_any_turn_highly_sensitive(self):
        self.assertEqual(self.klass("mijn OPENAI_API_KEY=nietecht123"), "highly_sensitive")

    def test_words_about_someone_elses_file_make_it_highly_sensitive(self):
        self.assertEqual(self.klass("Vat het gespreksverslag van mijn cliënt samen", profile="default"),
                         "highly_sensitive")

    def test_client_in_code_is_not_a_person(self):
        self.assertEqual(self.klass("Why does my HTTP client time out?"), "public")

    def test_contact_details_lift_a_public_turn_to_private(self):
        self.assertEqual(self.klass("Mail jan@example.org de planning"), "private")

    def test_an_iban_is_highly_sensitive(self):
        self.assertEqual(self.klass("Maak over naar NL91 ABNA 0417 1643 00"), "highly_sensitive")

    def test_the_policy_adds_terms_and_keeps_the_defaults(self):
        policy = {**POLICY, "sensitive_terms": ["salaris"]}
        self.assertEqual(self.klass("Wat is mijn salaris?", policy=policy), "highly_sensitive")
        self.assertEqual(self.klass("Wat staat er in het dossier?", policy=policy), "highly_sensitive")
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 -m unittest tests.test_dispatch.PrivacyClassTests -v`
Expected: `AttributeError: module 'jevkit.dispatch' has no attribute 'privacy_class'`.

- [ ] **Step 3: Add `has_contact_details` to `jevkit/privacy.py`** (directly after `is_sensitive`)

```python
def has_contact_details(text: str) -> bool:
    """An email address or a phone number: data about a person, even when it is your own."""
    probe = normalize(text)
    return bool(_EMAIL.search(probe) or _PHONE.search(probe) or _INTL_PHONE.search(probe))
```

- [ ] **Step 4: Add the privacy class to `jevkit/dispatch.py`**

Add `from . import privacy` to the imports, and below `parse_triage`:

```python
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
```

- [ ] **Step 5: Run the tests**

Run: `python3 -m unittest tests.test_dispatch -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add jevkit/privacy.py jevkit/dispatch.py tests/test_dispatch.py
git commit -m "dispatch: privacy class per turn, the stricter of profile and text"
```

---

### Task 2b: Terms in any form, and any IBAN (review finding on Task 2)

Task 2 matched whole words only, so "gespreksverslagen", "dossiers", "patiëntendossier" and "zorgdossier" all counted as public. Its IBAN rule only knew Dutch IBANs in capitals. And "diagnose" is the English verb of every debugging chat: a gate that fires on it gets switched off, so it leaves the default list. The Dutch clinical words stay.

**Files:**
- Modify: `jevkit/privacy.py` (add `_IBAN_LENGTHS`, `_IBAN_START`, `has_iban` after `has_contact_details`)
- Modify: `jevkit/dispatch.py` (new `DEFAULT_SENSITIVE_TERMS`, `_mentions`; `privacy_class` uses both and `privacy.has_iban`; remove `_IBAN`)
- Test: `tests/test_dispatch.py`

- [ ] **Step 1: Write the failing tests** (append)

```python
class PrivacyTermTests(unittest.TestCase):
    def klass(self, text):
        return dispatch.privacy_class(text, profile="coding", policy=POLICY)[0]

    def test_plurals_and_compounds_count(self):
        for text in ("Vat de gespreksverslagen samen", "Sorteer de dossiers", "Open het patiëntendossier",
                     "Wat staat er in het zorgdossier?", "Maak een behandelplan"):
            self.assertEqual(self.klass(text), "highly_sensitive", text)

    def test_the_english_verb_diagnose_is_an_ordinary_debugging_word(self):
        self.assertEqual(self.klass("Help me diagnose why the build fails"), "public")

    def test_a_short_term_counts_as_a_whole_word_only(self):
        self.assertEqual(self.klass("Zet het BSN-nummer in het formulier"), "highly_sensitive")
        self.assertEqual(self.klass("Rename the absnt flag"), "public")

    def test_an_iban_from_any_country_in_any_case(self):
        for text in ("Maak over naar BE71 0961 2345 6769", "rekening nl91 abna 0417 1643 00 graag",
                     "DE89370400440532013000", "GB82 WEST 1234 5698 7654 32"):
            self.assertEqual(self.klass(text), "highly_sensitive", text)

    def test_a_code_that_only_looks_like_an_iban_is_not_one(self):
        self.assertFalse(dispatch.privacy.has_iban("NL12 ABNA 0417 1643 00"))
        self.assertFalse(dispatch.privacy.has_iban("Libanon, AB12 CDEF, DE12 3456"))
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 -m unittest tests.test_dispatch.PrivacyTermTests -v`
Expected: failures on plurals, "diagnose" and the IBANs, and `AttributeError` for `has_iban`.

- [ ] **Step 3: Add `has_iban` to `jevkit/privacy.py`** (after `has_contact_details`)

```python
# IBAN lengths per country, for the countries a Dutch household meets. The length is what keeps
# the next word out of the number when an IBAN is written in groups of four.
_IBAN_LENGTHS = {"AD": 24, "AT": 20, "BE": 16, "BG": 22, "CH": 21, "CY": 28, "CZ": 24, "DE": 22, "DK": 18,
                 "EE": 20, "ES": 24, "FI": 18, "FR": 27, "GB": 22, "GI": 23, "GR": 27, "HR": 21, "HU": 28,
                 "IE": 22, "IS": 26, "IT": 27, "LI": 21, "LT": 20, "LU": 20, "LV": 21, "MC": 27, "MT": 31,
                 "NL": 18, "NO": 15, "PL": 28, "PT": 25, "RO": 24, "SE": 24, "SI": 19, "SK": 24, "SM": 27}
_IBAN_START = re.compile(r"\b([A-Z]{2})(\d{2})")


def has_iban(text: str) -> bool:
    """An IBAN from one of those countries, grouped or not, in any case.

    The check digits decide, as Luhn does for cards: a code that merely looks like an IBAN
    almost never passes mod 97.
    """
    probe = normalize(text).upper()
    for match in _IBAN_START.finditer(probe):
        length = _IBAN_LENGTHS.get(match.group(1))
        if not length:
            continue
        compact = probe[match.start():match.start() + length + length // 4 + 2].replace(" ", "")[:length]
        if len(compact) != length or not (compact.isascii() and compact.isalnum()):
            continue
        rearranged = compact[4:] + compact[:4]
        if int("".join(str(int(char, 36)) for char in rearranged)) % 97 == 1:
            return True
    return False
```

- [ ] **Step 4: Change `jevkit/dispatch.py`**

Replace `DEFAULT_SENSITIVE_TERMS` and `_IBAN` with:

```python
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
```

In `privacy_class`, make the term loop `if _mentions(lowered, str(term)):` and replace `if _IBAN.search(probe):` with `if privacy.has_iban(probe):`.

- [ ] **Step 5: Run the tests**

Run: `python3 -m unittest tests.test_dispatch -v`
Expected: all pass. The Task 2 tests still pass.

- [ ] **Step 6: Commit**

```bash
git add jevkit/privacy.py jevkit/dispatch.py tests/test_dispatch.py
git commit -m "dispatch: sensitive terms in any form, any IBAN by its check digits"
```

---

### Task 3: The policy file and the route choice

**Files:**
- Modify: `jevkit/dispatch.py` (add `LOCAL`, `DEFAULT_POLICY`, `policy_paths`, `load_policy`, `_local`, `_skip`, `choose_route`)
- Test: `tests/test_dispatch.py`

- [ ] **Step 1: Write the failing tests** (append)

```python
NOWHERE = Path("/nonexistent/dispatch.json")
NOT_COOLING = lambda name: 0.0  # noqa: E731
ON = {"enabled": True}


def triage(**fields):
    record = {"type": "CHANGE", "exit": "ESCALATE", "signals": ["G8"], "niveau": "frontier", "privacy": "private",
              "context_tokens": 2000, "repo_werk": False, "interactief": True, "reason": "hard work"}
    record.update(fields)
    return record


def policy(**agents):
    base = dispatch.load_policy(NOWHERE)
    for name, settings in agents.items():
        base["agents"][name] = {**base["agents"][name], **settings}
    return base


class ChooseRouteTests(unittest.TestCase):
    def route(self, record, pol, cooling=NOT_COOLING):
        return dispatch.choose_route(record, pol, cooling=cooling)

    def test_everything_below_frontier_stays_here(self):
        for niveau in ("tiny", "fast", "standard"):
            self.assertEqual(self.route(triage(niveau=niveau, exit="PROCEED", signals=[]), policy(openai=ON))["agent"],
                             "local")

    def test_a_missing_level_means_standard_never_the_cloud(self):
        self.assertEqual(self.route(triage(niveau=None), policy(openai=ON))["agent"], "local")

    def test_a_question_goes_to_the_person(self):
        record = triage(exit="ASK", niveau=None, question="Welke?")
        self.assertEqual(self.route(record, policy(openai=ON))["agent"], "local")

    def test_highly_sensitive_never_leaves_and_says_so(self):
        pol = policy(openai=ON, claude=ON, openrouter={"enabled": True, "model": "x/y"})
        chosen = self.route(triage(privacy="highly_sensitive"), pol)
        self.assertEqual((chosen["agent"], chosen["downgraded"]), ("local", True))

    def test_frontier_work_goes_to_openai_first(self):
        pol = policy(openai=ON, claude={"enabled": True, "only_repo": False})
        self.assertEqual(self.route(triage(), pol)["agent"], "openai")

    def test_repository_work_goes_to_claude_first(self):
        self.assertEqual(self.route(triage(repo_werk=True), policy(openai=ON, claude=ON))["agent"], "claude")

    def test_claude_takes_only_repository_work_by_default(self):
        chosen = self.route(triage(), policy(claude=ON))
        self.assertEqual(chosen["agent"], "local")
        self.assertIn({"agent": "claude", "skipped": "only for repository work"}, chosen["considered"])

    def test_a_cooling_agent_is_skipped_for_the_next(self):
        pol = policy(openai=ON, claude={"enabled": True, "only_repo": False})
        chosen = self.route(triage(), pol, cooling=lambda name: 900.0 if name == "openai" else 0.0)
        self.assertEqual(chosen["agent"], "claude")

    def test_the_last_resort_takes_public_turns_only_whatever_its_settings_say(self):
        pol = policy(openrouter={"enabled": True, "model": "x/y", "privacy": ["public", "private"]})
        self.assertEqual(self.route(triage(privacy="private"), pol)["agent"], "local")
        self.assertEqual(self.route(triage(privacy="public"), pol)["agent"], "openrouter")

    def test_nothing_enabled_is_a_visible_downgrade(self):
        chosen = self.route(triage(), policy())
        self.assertEqual((chosen["agent"], chosen["downgraded"]), ("local", True))
        self.assertEqual([c["agent"] for c in chosen["considered"]], ["openai", "claude", "openrouter"])

    def test_a_conversation_too_long_for_the_agent_is_not_sent(self):
        pol = policy(openai={"enabled": True, "context_tokens": 64_000})
        self.assertEqual(self.route(triage(context_tokens=100_000), pol)["agent"], "local")

    def test_max_lokaal_in_the_chat_is_answered_here_now(self):
        self.assertEqual(self.route(triage(niveau="max_lokaal"), policy(openai=ON))["agent"], "local")

    def test_a_window_that_is_not_a_number_is_ignored(self):
        pol = policy(openai={"enabled": True, "context_tokens": "veel"})
        self.assertEqual(self.route(triage(), pol)["agent"], "openai")


class PolicyFileTests(unittest.TestCase):
    def test_a_file_overrides_one_agent_setting_and_keeps_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dispatch.json"
            path.write_text(json.dumps({"profiles": {"default": "private"},
                                        "agents": {"openai": {"enabled": True, "model": "gpt-6-sol"}}}))
            loaded = dispatch.load_policy(path)
        self.assertEqual(loaded["profiles"], {"default": "private"})
        self.assertEqual((loaded["agents"]["openai"]["model"], loaded["agents"]["openai"]["kind"]), ("gpt-6-sol", "codex"))
        self.assertIn("claude", loaded["agents"])

    def test_changing_one_order_keeps_the_other(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dispatch.json"
            path.write_text(json.dumps({"frontier_order": {"default": ["claude"]}}))
            loaded = dispatch.load_policy(path)
        self.assertEqual(loaded["frontier_order"], {"repo": ["claude", "openai"], "default": ["claude"]})

    def test_a_broken_file_leaves_the_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dispatch.json"
            path.write_text("{not json")
            self.assertEqual(dispatch.load_policy(path)["mode"], "off")

    def test_loading_twice_shares_nothing(self):
        first = dispatch.load_policy(NOWHERE)
        first["agents"]["openai"]["enabled"] = True
        self.assertFalse(dispatch.load_policy(NOWHERE)["agents"]["openai"]["enabled"])

    def test_the_environment_names_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mine.json"
            path.write_text(json.dumps({"mode": "shadow"}))
            with mock.patch.dict(os.environ, {"JEV_DISPATCH_POLICY": str(path)}):
                self.assertEqual(dispatch.load_policy()["mode"], "shadow")
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 -m unittest tests.test_dispatch.ChooseRouteTests tests.test_dispatch.PolicyFileTests -v`
Expected: `AttributeError` for `load_policy`.

- [ ] **Step 3: Add the policy and the route choice to `jevkit/dispatch.py`**

Extend the imports to:

```python
import copy
import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import catalog as catalog_mod
from . import ladder, privacy
```

Then append:

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest tests.test_dispatch -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add jevkit/dispatch.py tests/test_dispatch.py
git commit -m "dispatch: policy file and route choice, hurdles before preferences"
```

---

### Task 4: Jev as the classifier (one shared judgement)

`route.decide` holds the tier logic inline. This task extracts it into pure functions that behave exactly as before, so dispatch uses the same calibrated thresholds rather than a copy.

**Files:**
- Modify: `jevkit/route.py` (add `clip_ask`, `is_risky`, `judge_answers`; make `decide` use them)
- Modify: `jevkit/dispatch.py` (add `classify_with_jev`)
- Test: `tests/test_dispatch.py` (new tests); the existing route tests guard the refactor

- [ ] **Step 1: Write the failing tests** (append)

```python
def answers(p_hard=0.0, p_simple=0.0, confidence=0.9, kind="general", stakes=0.1):
    rest = max(0.0, 1.0 - p_hard - p_simple)
    return {"difficulty": {"score": 2.0 if p_hard >= 0.6 else 0.1 if p_simple >= 0.7 else 1.0,
                           "confidence": confidence,
                           "probabilities": {0: p_simple, 1: rest, 2: p_hard, 3: 0.0}},
            "kind": {"choice": kind, "confidence": 0.9},
            "costly_mistake": {"noul": stakes}}


class Wire:
    """A Jev that judges every turn hard coding work, and remembers what it was sent."""

    def __init__(self):
        self.bodies = []

    def __call__(self, body, headers, timeout):
        request = json.loads(body)
        self.bodies.append(request)
        out = {}
        for name, question in request["questions"].items():
            if question["type"] == "score":
                out[name] = {"type": "score", "score": 2.05, "confidence": 0.9,
                             "probabilities": {"0": 0.1, "1": 0.1, "2": 0.45, "3": 0.35}}
            elif question["type"] == "choice":
                keys = list(question["criteria"])
                best = "coding" if "coding" in keys else keys[0]
                share = 0.1 / max(1, len(keys) - 1)
                out[name] = {"type": "choice", "choice": best, "confidence": 0.9,
                             "probabilities": {key: (0.9 if key == best else share) for key in keys}}
            else:
                out[name] = {"type": "noul", "noul": 0.2}
        return json.dumps({"model": "jev-test", "answers": out, "usage": {"input_tokens": 1}}).encode()


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": "test-key-not-real"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.policy = dispatch.load_policy(NOWHERE)

    def classify(self, text="Find the race in the scheduler", klass="public", **kwargs):
        kwargs.setdefault("config", dispatch.route.load_config(NOWHERE))   # never this machine's routing.json
        return dispatch.classify_with_jev(text, privacy_class=klass, policy=self.policy, **kwargs)

    def test_hard_coding_work_is_frontier_repository_work(self):
        record = self.classify(answers=answers(p_hard=0.8, kind="coding"))
        self.assertEqual((record["niveau"], record["exit"], record["signals"]), ("frontier", "ESCALATE", ["G8"]))
        self.assertTrue(record["repo_werk"])
        self.assertEqual(dispatch.check_triage(record), [])

    def test_simple_work_is_tiny(self):
        record = self.classify(answers=answers(p_simple=0.9, confidence=0.95))
        self.assertEqual((record["niveau"], record["exit"]), ("tiny", "PROCEED"))

    def test_an_unsure_answer_about_a_harmless_turn_stays_small(self):
        record = self.classify(answers=answers(p_hard=0.8, confidence=0.3))
        self.assertEqual(record["niveau"], "tiny")
        self.assertIn("low confidence", record["why"])

    def test_jev_is_never_asked_about_a_highly_sensitive_turn(self):
        def refuse(*_):
            raise AssertionError("Jev was asked about a highly sensitive turn")
        record = self.classify(klass="highly_sensitive", transport=refuse)
        self.assertEqual((record["niveau"], record["source"]), ("standard", "policy"))

    def test_a_private_turn_sends_features_not_text(self):
        wire = Wire()
        record = self.classify(text="SECRETPLAN: rewrite the scheduler", klass="private", transport=wire)
        self.assertEqual(record["niveau"], "frontier")
        self.assertNotIn("SECRETPLAN", json.dumps(wire.bodies))
        self.assertIn("turn_features", json.dumps(wire.bodies))

    def test_a_public_turn_sends_redacted_text(self):
        wire = Wire()
        self.classify(text="Rewrite the scheduler for jan@example.org", transport=wire)
        sent = json.dumps(wire.bodies)
        self.assertIn("Rewrite the scheduler", sent)
        self.assertNotIn("jan@example.org", sent)

    def test_jev_down_means_standard_which_stays_here(self):
        def down(*_):
            raise dispatch.client.JevError("auth_failed")
        record = self.classify(transport=down)
        self.assertEqual((record["niveau"], record["source"]), ("standard", "fail_open"))

    def test_incomplete_answers_are_not_a_judgement(self):
        record = self.classify(answers={"difficulty": {"score": 1.0}})
        self.assertEqual((record["niveau"], record["source"]), ("standard", "fail_open"))


class JudgeAnswersTests(unittest.TestCase):
    def test_features_only_never_buys_the_cheapest_tier(self):
        config = dispatch.route.load_config(NOWHERE)
        judged = dispatch.route.judge_answers(answers(p_simple=0.9, confidence=0.95), config,
                                              risky=False, features_only=True)
        self.assertEqual(judged["tier"], "medium")

    def test_hard_needs_real_probability_mass(self):
        config = dispatch.route.load_config(NOWHERE)
        self.assertEqual(dispatch.route.judge_answers(answers(p_hard=0.8), config, risky=False,
                                                      features_only=False)["tier"], "hard")
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 -m unittest tests.test_dispatch.ClassifyTests tests.test_dispatch.JudgeAnswersTests -v`
Expected: `AttributeError` for `classify_with_jev` and `judge_answers`.

- [ ] **Step 3: Extract the judgement in `jevkit/route.py`**

Add these three functions directly above `def decide(`:

```python
def clip_ask(inner: str, limit: int) -> str:
    """What Jev reads of a long turn: its opening and, mostly, its end."""
    return inner if len(inner) <= limit else inner[:limit // 4] + "\n[…]\n" + inner[-(limit - limit // 4):]


def is_risky(text: str) -> bool:
    """Risk words anywhere in the whole turn. Read on the whole text, never the clipped copy."""
    return bool(_HARD_RISK.search(privacy.normalize(text)))


def judge_answers(answers: Mapping[str, Any], config: Mapping[str, Any], *, risky: bool,
                  features_only: bool) -> Dict[str, Any]:
    """Tier and specialty from Jev's three answers: the policy `decide` has always applied.

    `tier` is None when Jev is unsure about a harmless turn: nothing earned a change.
    """
    difficulty, confidence = answers["difficulty"]["score"], answers["difficulty"]["confidence"]
    stakes = answers["costly_mistake"]["noul"]
    spread = answers["difficulty"].get("probabilities") or {}
    if spread:
        p_simple, p_hard = spread.get(0, 0.0), spread.get(2, 0.0) + spread.get(3, 0.0)
    else:                      # no spread returned: fall back to the averaged score, conservatively
        p_simple, p_hard = float(difficulty < 0.5), float(difficulty >= 2.25)
    judged: Dict[str, Any] = {"difficulty": difficulty, "confidence": confidence, "stakes": stakes}

    # An unsure answer is not evidence of a hard turn. Its averaged score lands mid-rubric by arithmetic,
    # so it must never buy the expensive tier: a harmless unsure turn stays put, a risky one gets medium.
    unsure = confidence < config["min_confidence"]
    if unsure and not (risky or stakes > 0.6):
        return {**judged, "tier": None, "specialty": "general", "reason": f"low confidence {confidence:.2f}"}

    if unsure:
        tier = "medium"
    elif p_hard >= config["hard_needs_probability"]:
        tier = "hard"
    elif p_simple >= config["simple_needs_probability"] and confidence >= config["simple_needs_confidence"]:
        tier = "simple"
    else:
        tier = "medium"
    if tier == "simple" and (risky or stakes > 0.4 or features_only):
        tier = "medium"        # risk words and costly mistakes set a floor of medium; they do not buy hard
    if not unsure and stakes > 0.85 and p_hard >= 0.35:
        tier = "hard"          # a costly mistake tips a turn that is already leaning hard
    kind = answers["kind"]
    specialty = kind["choice"] if kind["confidence"] >= 0.5 else "general"
    return {**judged, "tier": tier, "specialty": specialty, "reason": f"{tier} {specialty}"}
```

In `decide`:
1. Replace `ask = inner if len(inner) <= limit else inner[:limit // 4] + "\n[…]\n" + inner[-(limit - limit // 4):]` with `ask = clip_ask(inner, limit)`.
2. Replace `risky = bool(_HARD_RISK.search(privacy.normalize(inner)))` with `risky = is_risky(inner)`.
3. Replace the block that starts at `difficulty, confidence = answers["difficulty"]["score"], ...` and ends at `specialty = kind["choice"] if kind["confidence"] >= 0.5 else "general"` (including both comment blocks inside it) with:

```python
    judged = judge_answers(answers, config, risky=risky, features_only=(mode == "features"))
    if judged["tier"] is None:
        return _keep(current, judged["reason"], private=private)
    tier, specialty = judged["tier"], judged["specialty"]
    difficulty, confidence, stakes = judged["difficulty"], judged["confidence"], judged["stakes"]
```

- [ ] **Step 4: Prove the refactor changed nothing**

Run: `python3 -m unittest tests.test_jevkit tests.test_route_health tests.test_turn tests.test_plugin_middleware tests.test_reported_bugs`
Expected: `OK`, the same count as before the change.

- [ ] **Step 5: Add `classify_with_jev` to `jevkit/dispatch.py`**

Change the jevkit import line to `from . import client, ladder, privacy, route`, then append:

```python
_KIND_TO_TYPE = {"coding": "CHANGE", "writing": "CREATE", "research": "RESEARCH", "general": "EXPLAIN"}


def classify_with_jev(text: str, *, privacy_class: str, policy: Dict[str, Any], context_tokens: int = 0,
                      interactive: bool = True, config: Optional[Dict[str, Any]] = None,
                      transport: Optional[client.Transport] = None, timeout: float = 2.5,
                      answers: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One TRIAGE record from Jev's three routing answers, judged by the router's own rules.

    Jev is never asked about a highly sensitive turn. For a class not in `jev_text_for` Jev reads
    coarse features only, never the text, as a private profile does in routing. Jev down, slow,
    or answering in a shape we cannot read: niveau `standard`, which the policy keeps here.
    """
    record: Dict[str, Any] = {"type": "EXPLAIN", "exit": "PROCEED", "signals": [], "niveau": "standard",
                              "privacy": privacy_class, "context_tokens": max(0, int(context_tokens)),
                              "repo_werk": False, "interactief": bool(interactive)}
    if privacy_class not in PRIVACY or privacy_class == "highly_sensitive":
        return {**record, "privacy": "highly_sensitive", "source": "policy",
                "why": "highly sensitive: Jev is not asked"}
    config = config or route.load_config()
    inner = route.unwrap(text, config)
    limit = int(config.get("ask_chars", 2500))
    features_only = privacy_class not in (policy.get("jev_text_for") or [])
    if answers is None:
        state = route.state_for(route.clip_ask(inner, limit), context_tokens=context_tokens,
                                private=features_only, limit=limit)
        try:
            answers = client.ask(state, route.questions(), timeout=timeout, transport=transport)["answers"]
        except client.JevError as error:
            return {**record, "source": "fail_open", "why": f"Jev unavailable ({error.code})"}
    if not all(isinstance(answers.get(name), dict) for name in ("difficulty", "kind", "costly_mistake")):
        return {**record, "source": "fail_open", "why": "routing answers incomplete"}
    judged = route.judge_answers(answers, config, risky=route.is_risky(inner), features_only=features_only)
    jev = {key: judged[key] for key in ("tier", "specialty", "confidence", "difficulty", "stakes")}
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
```

- [ ] **Step 6: Run the tests**

Run: `python3 -m unittest tests.test_dispatch -v && python3 -m unittest discover -s tests`
Expected: both `OK`.

- [ ] **Step 7: Commit**

```bash
git add jevkit/route.py jevkit/dispatch.py tests/test_dispatch.py
git commit -m "route: one judgement shared by routing and dispatch; dispatch: Jev as classifier"
```

---

### Task 5: The Codex agent

**Files:**
- Create: `jevkit/agents.py`
- Create: `tests/test_agents.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_agents.py`:

```python
"""The agent adapters. Offline: fake runners and a fake transport; no CLI or endpoint is ever called."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jevkit import agents, client  # noqa: E402

PROMPT = "<handoff>\nTo: openai\nRequest: geheim plan voor de scheduler\n</handoff>"


class FakeRun:
    """Stands in for subprocess.run: records the call and answers as the CLI would."""

    def __init__(self, returncode=0, stdout="", stderr="", write=None, error=None):
        self.returncode, self.stdout, self.stderr, self.write, self.error = returncode, stdout, stderr, write, error
        self.argv, self.stdin, self.timeout = None, None, None

    def __call__(self, argv, stdin_text, timeout):
        self.argv, self.stdin, self.timeout = list(argv), stdin_text, timeout
        if self.error is not None:
            raise self.error
        if self.write is not None:
            Path(self.argv[self.argv.index("--output-last-message") + 1]).write_text(self.write, encoding="utf-8")
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, self.stderr)


class FillTests(unittest.TestCase):
    def test_an_empty_value_drops_its_flag(self):
        self.assertEqual(agents.fill(["codex", "--model", "{model}", "-"], model=""), ["codex", "-"])

    def test_a_value_takes_its_place(self):
        self.assertEqual(agents.fill(["codex", "--model", "{model}", "-"], model="gpt-6-sol"),
                         ["codex", "--model", "gpt-6-sol", "-"])

    def test_an_unknown_placeholder_is_left_alone(self):
        self.assertEqual(agents.fill(["x", "{other}"], model="m"), ["x", "{other}"])


class CodexTests(unittest.TestCase):
    def test_the_prompt_goes_in_on_stdin_never_on_the_command_line(self):
        run = FakeRun(write="Het antwoord.")
        result = agents.run_codex(PROMPT, model="gpt-6-sol", runner=run)
        self.assertEqual(result.text, "Het antwoord.")
        self.assertEqual(run.stdin, PROMPT)
        self.assertNotIn("geheim", " ".join(run.argv))
        self.assertEqual(run.argv[run.argv.index("--model") + 1], "gpt-6-sol")
        self.assertEqual(run.argv[run.argv.index("--sandbox") + 1], "read-only")

    def test_no_model_means_the_cli_default(self):
        run = FakeRun(write="ok")
        agents.run_codex(PROMPT, runner=run)
        self.assertNotIn("--model", run.argv)

    def test_the_json_events_are_read_when_no_file_was_written(self):
        events = "\n".join([json.dumps({"type": "turn.started"}),
                            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Uit JSON."}})])
        self.assertEqual(agents.run_codex(PROMPT, runner=FakeRun(stdout=events)).text, "Uit JSON.")

    def test_a_usage_limit_is_quota(self):
        run = FakeRun(returncode=1, stderr="ERROR: You've hit your usage limit. Try again later.")
        with self.assertRaises(agents.AgentError) as caught:
            agents.run_codex(PROMPT, runner=run)
        self.assertEqual(caught.exception.code, "quota")

    def test_not_logged_in_is_auth(self):
        with self.assertRaises(agents.AgentError) as caught:
            agents.run_codex(PROMPT, runner=FakeRun(returncode=1, stderr="Not logged in. Run codex login."))
        self.assertEqual(caught.exception.code, "auth")

    def test_a_missing_program_is_missing(self):
        with self.assertRaises(agents.AgentError) as caught:
            agents.run_codex(PROMPT, runner=FakeRun(error=FileNotFoundError("codex")))
        self.assertEqual(caught.exception.code, "missing")

    def test_a_slow_run_is_a_timeout(self):
        with self.assertRaises(agents.AgentError) as caught:
            agents.run_codex(PROMPT, timeout=1, runner=FakeRun(error=subprocess.TimeoutExpired("codex", 1)))
        self.assertEqual(caught.exception.code, "timeout")

    def test_an_answer_that_discusses_rate_limits_is_still_an_answer(self):
        run = FakeRun(write="Je raakt de rate limit door te veel parallelle calls.")
        self.assertIn("rate limit", agents.run_codex(PROMPT, runner=run).text)

    def test_nothing_back_is_a_failure(self):
        with self.assertRaises(agents.AgentError) as caught:
            agents.run_codex(PROMPT, runner=FakeRun())
        self.assertEqual(caught.exception.code, "failed")
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 -m unittest tests.test_agents -v`
Expected: `ImportError` for `jevkit.agents`.

- [ ] **Step 3: Write `jevkit/agents.py`**

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest tests.test_agents -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add jevkit/agents.py tests/test_agents.py
git commit -m "agents: codex exec on the ChatGPT login, prompt on stdin, failures as codes"
```

---

### Task 6: The Claude Code agent

**Files:**
- Modify: `jevkit/agents.py` (add `CLAUDE_ARGV`, `run_claude`)
- Test: `tests/test_agents.py`

- [ ] **Step 1: Write the failing tests** (append)

```python
def claude_json(**fields):
    body = {"type": "result", "subtype": "success", "is_error": False, "result": "Het antwoord.",
            "session_id": "sess-1"}
    body.update(fields)
    return json.dumps(body)


class ClaudeTests(unittest.TestCase):
    def test_an_answer_and_its_session_come_back(self):
        run = FakeRun(stdout=claude_json())
        result = agents.run_claude(PROMPT, model="opus", runner=run)
        self.assertEqual((result.text, result.session), ("Het antwoord.", "sess-1"))
        self.assertEqual(run.stdin, PROMPT)
        self.assertNotIn("geheim", " ".join(run.argv))

    def test_it_plans_and_edits_nothing_by_default(self):
        run = FakeRun(stdout=claude_json())
        agents.run_claude(PROMPT, runner=run)
        self.assertEqual(run.argv[run.argv.index("--permission-mode") + 1], "plan")
        self.assertNotIn("--bare", run.argv)

    def test_a_session_is_resumed_only_when_there_is_one(self):
        run = FakeRun(stdout=claude_json())
        agents.run_claude(PROMPT, runner=run)
        self.assertNotIn("--resume", run.argv)
        agents.run_claude(PROMPT, session="sess-1", runner=run)
        self.assertEqual(run.argv[run.argv.index("--resume") + 1], "sess-1")

    def test_a_usage_limit_is_quota(self):
        run = FakeRun(returncode=1, stdout=claude_json(is_error=True, result="Claude AI usage limit reached|1760000000"))
        with self.assertRaises(agents.AgentError) as caught:
            agents.run_claude(PROMPT, runner=run)
        self.assertEqual(caught.exception.code, "quota")

    def test_an_error_result_with_exit_zero_is_still_an_error(self):
        run = FakeRun(stdout=claude_json(is_error=True, subtype="error_max_turns", result=""))
        with self.assertRaises(agents.AgentError) as caught:
            agents.run_claude(PROMPT, runner=run)
        self.assertEqual(caught.exception.code, "failed")

    def test_output_that_is_not_json_is_a_failure(self):
        with self.assertRaises(agents.AgentError):
            agents.run_claude(PROMPT, runner=FakeRun(stdout="Welcome to Claude Code"))
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 -m unittest tests.test_agents.ClaudeTests -v`
Expected: `AttributeError: ... 'run_claude'`.

- [ ] **Step 3: Add to `jevkit/agents.py`** (the template next to `CODEX_ARGV`, the function at the end)

```python
CLAUDE_ARGV = ["claude", "-p", "--output-format", "json", "--model", "{model}", "--max-turns", "{max_turns}",
               "--permission-mode", "plan", "--resume", "{session}"]
```

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest tests.test_agents -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add jevkit/agents.py tests/test_agents.py
git commit -m "agents: claude -p on the Claude Code login, plan mode, session resume"
```

---

### Task 7: The OpenRouter agent

**Files:**
- Modify: `jevkit/agents.py` (add `OPENROUTER_URL`, `run_openrouter`, and `from . import client, keystore`)
- Test: `tests/test_agents.py`

- [ ] **Step 1: Write the failing tests** (append)

```python
class OpenRouterTests(unittest.TestCase):
    def reply(self, text="Het antwoord.", model="vendor/model-1"):
        return json.dumps({"model": model, "choices": [{"message": {"role": "assistant", "content": text}}]}).encode()

    def test_one_chat_completion_with_the_given_key(self):
        seen = {}

        def transport(url, body, headers, timeout):
            seen.update(url=url, body=json.loads(body), auth=headers["Authorization"])
            return self.reply()

        result = agents.run_openrouter(PROMPT, model="vendor/model-1", key="or-test", transport=transport)
        self.assertEqual((result.text, result.model), ("Het antwoord.", "vendor/model-1"))
        self.assertEqual(seen["url"], agents.OPENROUTER_URL)
        self.assertEqual(seen["auth"], "Bearer or-test")
        self.assertEqual(seen["body"]["messages"], [{"role": "user", "content": PROMPT}])

    def test_429_is_quota(self):
        def transport(*_):
            raise client.JevError("rate_limited")
        with self.assertRaises(agents.AgentError) as caught:
            agents.run_openrouter(PROMPT, model="m", key="k", transport=transport)
        self.assertEqual(caught.exception.code, "quota")

    def test_no_key_is_auth_and_nothing_is_sent(self):
        def transport(*_):
            raise AssertionError("sent without a key")
        with mock.patch.object(agents.keystore, "resolve", return_value=None):
            with self.assertRaises(agents.AgentError) as caught:
                agents.run_openrouter(PROMPT, model="m", transport=transport)
        self.assertEqual(caught.exception.code, "auth")

    def test_no_model_is_refused_before_anything_is_sent(self):
        with self.assertRaises(agents.AgentError):
            agents.run_openrouter(PROMPT, model="", key="k", transport=lambda *_: self.reply())

    def test_a_reply_without_an_answer_is_a_failure(self):
        with self.assertRaises(agents.AgentError):
            agents.run_openrouter(PROMPT, model="m", key="k", transport=lambda *_: b'{"choices": []}')
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 -m unittest tests.test_agents.OpenRouterTests -v`
Expected: `AttributeError: ... 'run_openrouter'`.

- [ ] **Step 3: Add to `jevkit/agents.py`**

Add `from . import client, keystore` after the typing import, `OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"` next to the templates, and at the end:

```python
def run_openrouter(prompt: str, *, model: str, timeout: float = 120.0,
                   transport: Optional[Callable[..., bytes]] = None, key: Optional[str] = None) -> Result:
    """One chat completion on OpenRouter, with the key `jev setup-key --provider openrouter` stored."""
    if not model:
        raise AgentError("failed", "no OpenRouter model configured")
    key = key or keystore.resolve("openrouter")
    if not key:
        raise AgentError("auth", "no OpenRouter key: run `jev setup-key --provider openrouter`")
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "HTTP-Referer": "https://github.com/kerpopule/hermes-jev-skills", "X-Title": "Hermes Jev Skills"}
    try:
        raw = (transport or client.post)(OPENROUTER_URL, body, headers, timeout)
    except client.JevError as error:
        code = {"rate_limited": "quota", "credits_exhausted": "quota", "auth_failed": "auth"}.get(error.code, "failed")
        raise AgentError(code, f"openrouter {error.code}") from None
    try:
        data = json.loads(raw)
        text = str(data["choices"][0]["message"]["content"] or "").strip()
    except (ValueError, KeyError, IndexError, TypeError):
        raise AgentError("failed", "openrouter replied without an answer") from None
    if not text:
        raise AgentError("failed", "openrouter replied with an empty answer")
    return Result(text=text, model=str(data.get("model") or model))
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest tests.test_agents -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add jevkit/agents.py tests/test_agents.py
git commit -m "agents: OpenRouter chat completion as the metered last resort"
```

---

### Task 8: The handoff out and the relay back

**Files:**
- Create: `jevkit/relay.py`
- Create: `tests/test_relay.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_relay.py`:

```python
"""What leaves for an agent, and how its answer comes back. Offline."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jevkit import relay  # noqa: E402

CHAT = [
    {"role": "system", "content": "SYSTEMPROMPT with memory and paths"},
    {"role": "user", "content": "Mijn scheduler loopt vast."},
    {"role": "assistant", "content": "Welke versie draai je?"},
    {"role": "tool", "content": "TOOLOUTPUT ~/.env"},
    {"role": "user", "content": "Versie 2. Mail jan@example.org als je iets vindt."},
]


class HandoffTests(unittest.TestCase):
    def test_system_prompts_and_tool_output_never_leave(self):
        text = relay.build_handoff(CHAT, agent="openai", reason="hard coding work")
        self.assertNotIn("SYSTEMPROMPT", text)
        self.assertNotIn("TOOLOUTPUT", text)
        self.assertIn("Mijn scheduler loopt vast.", text)

    def test_the_library_shape(self):
        text = relay.build_handoff(CHAT, agent="openai", reason="hard coding work")
        for field in ("<handoff>", "To: openai", "Reason: hard coding work", "Request:", "Constraints:",
                      "Evidence:", "Tried:", "Need back:", "</handoff>"):
            self.assertIn(field, text)

    def test_everything_leaving_is_redacted(self):
        text = relay.build_handoff(CHAT, agent="openai", reason="r")
        self.assertNotIn("jan@example.org", text)
        self.assertIn("[email]", text)

    def test_a_secret_anywhere_in_what_would_leave_stops_the_handoff(self):
        chat = CHAT[:-1] + [{"role": "user", "content": "gebruik GITHUB_TOKEN=nietecht123"}]
        self.assertIsNone(relay.build_handoff(chat, agent="openai", reason="r"))

    def test_a_handoff_to_this_machine_is_not_redacted(self):
        text = relay.build_handoff(CHAT, agent="local", reason="r", external=False)
        self.assertIn("jan@example.org", text)

    def test_no_user_message_last_means_nothing_to_hand_over(self):
        self.assertIsNone(relay.build_handoff(CHAT[:3], agent="openai", reason="r"))

    def test_history_is_bounded(self):
        chat = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"bericht {i}"} for i in range(21)]
        text = relay.build_handoff(chat, agent="openai", reason="r", max_messages=2)
        self.assertNotIn("bericht 17", text)
        self.assertIn("bericht 18", text)
        self.assertIn("bericht 19", text)

    def test_the_original_request_replaces_injected_context(self):
        chat = CHAT[:-1] + [{"role": "user", "content": "Versie 2.\n\n[Jev skill suggestion] load x"}]
        text = relay.build_handoff(chat, agent="openai", reason="r", request="Versie 2.")
        self.assertNotIn("skill suggestion", text)

    def test_what_would_leave_is_the_request_and_the_bounded_history(self):
        text = relay.leaving_text(CHAT, max_messages=2)
        self.assertIn("Welke versie draai je?", text)
        self.assertIn("Mijn scheduler loopt vast.", text)
        self.assertNotIn("SYSTEMPROMPT", text)
        self.assertNotIn("TOOLOUTPUT", text)
        self.assertNotIn("Mijn scheduler", relay.leaving_text(CHAT, max_messages=1))

    def test_images_are_seen(self):
        chat = [{"role": "user", "content": [{"type": "text", "text": "Wat staat hier?"},
                                             {"type": "image_url", "image_url": {"url": "data:..."}}]}]
        self.assertTrue(relay.has_images(chat))
        self.assertFalse(relay.has_images(CHAT))


class RelayTests(unittest.TestCase):
    def test_the_answer_comes_back_unchanged_under_its_author(self):
        answer = "Niet opnieuw starten voordat de schijf is vervangen. Kosten: € 82.500."
        self.assertEqual(relay.relay(answer, agent="openai", model="gpt-6-sol"),
                         "[openai · gpt-6-sol]\n\n" + answer)

    def test_without_a_model_the_agent_is_named(self):
        self.assertEqual(relay.relay("ok", agent="claude"), "[claude]\n\nok")
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 -m unittest tests.test_relay -v`
Expected: `ImportError` for `jevkit.relay`.

- [ ] **Step 3: Write `jevkit/relay.py`**

```python
"""What an agent gets from the conversation, and how its answer comes back.

Out: a handoff in the reasoning library's shape (To, Reason, Request, Constraints, Evidence,
Tried, Need back) and the last few turns, text only. System prompts and tool output never
leave: that is where memory, files and credentials live. For an agent off this machine every
line is redacted, and a turn that looks like it holds a secret stops the handoff: None.

Back: the agent's answer unchanged, under one line that says who wrote it. The chat model does
not retell it, so no number, warning or decision can change on the way.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from . import privacy

_IMAGE_PARTS = ("image_url", "input_image", "image")


def text_of(content: Any) -> str:
    """The text of one message: a string, or the text parts of a list of parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
            elif isinstance(part, dict) and part.get("type") in _IMAGE_PARTS:
                parts.append("[image]")
        return "\n".join(part for part in parts if part)
    return ""


def has_images(messages: Sequence[Dict[str, Any]]) -> bool:
    """Does the newest user message carry an image. A handed-off turn is text only today."""
    for message in reversed(list(messages)):
        if isinstance(message, dict) and message.get("role") == "user":
            content = message.get("content")
            return isinstance(content, list) and any(
                isinstance(part, dict) and part.get("type") in _IMAGE_PARTS for part in content)
    return False


def _turns(messages: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The user and assistant messages that have text: all a handoff can ever carry."""
    return [m for m in messages if isinstance(m, dict) and m.get("role") in ("user", "assistant")
            and text_of(m.get("content")).strip()]


def leaving_text(messages: Sequence[Dict[str, Any]], *, request: Optional[str] = None,
                 max_messages: int = 6) -> str:
    """Everything a handoff of these messages would carry, as one text: what the privacy check reads.

    Checking only the newest message let an earlier one about a client file leave as history.
    """
    turns = _turns(messages)
    if not turns or turns[-1].get("role") != "user":
        return request or ""
    asked = request if request is not None else text_of(turns[-1]["content"])
    earlier = turns[-(max_messages + 1):-1] if max_messages > 0 else []
    return "\n".join([asked] + [text_of(m.get("content")) for m in earlier])


def build_handoff(messages: Sequence[Dict[str, Any]], *, agent: str, reason: str, request: Optional[str] = None,
                  external: bool = True, max_messages: int = 6, max_chars: int = 12000) -> Optional[str]:
    """The task text for one agent, or None when nothing may be sent.

    `request`, when given, is the person's own words for this turn: what plugins appended to the
    user message (a skill suggestion, a handoff capsule) is then left behind.
    """
    turns = _turns(messages)
    if not turns or turns[-1].get("role") != "user":
        return None
    asked = (request if request is not None else text_of(turns[-1]["content"])).strip()
    earlier = turns[-(max_messages + 1):-1] if max_messages > 0 else []
    if external and (privacy.is_sensitive(asked)
                     or any(privacy.is_sensitive(text_of(m.get("content"))) for m in [turns[-1], *earlier])):
        return None
    budget = max(200, max_chars // 2)
    each = max(200, (max_chars - budget) // len(earlier)) if earlier else 0

    def clean(text: str, limit: int) -> str:
        if external:
            return privacy.redact(text, limit)
        return text if len(text) <= limit else text[:limit] + " […]"

    lines = ["<handoff>", f"To: {agent}", f"Reason: {reason}", f"Request: {clean(asked, budget)}",
             "Constraints: answer in writing only; change no files and no systems; "
             "answer in the language of the request",
             "Evidence: " + ("the recent conversation below" if earlier else "nothing beyond the request"),
             "Tried: nothing yet",
             "Need back: a complete answer the person can read as it is",
             "</handoff>"]
    if earlier:
        lines += ["", "Recent conversation, oldest first:"]
        lines += [f"[{m['role']}] {clean(text_of(m.get('content')).strip(), each)}" for m in earlier]
    return "\n".join(lines)


def relay(answer: str, *, agent: str, model: str = "") -> str:
    """The agent's answer as the person sees it: unchanged, with who wrote it on the first line."""
    who = f"{agent} · {model}" if model else agent
    return f"[{who}]\n\n{answer.strip()}"
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest tests.test_relay -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add jevkit/relay.py tests/test_relay.py
git commit -m "relay: a redacted handoff out, the answer back unchanged under its author"
```

---

### Task 9: One turn, start to finish

**Files:**
- Modify: `jevkit/dispatch.py` (add `run_agent`, `_COOL_ON`, `dispatch_turn`, `check_agents`; import `shutil`, `agents`, `keystore`, `relay`)
- Test: `tests/test_dispatch.py`

- [ ] **Step 1: Write the failing tests** (append)

```python
HARD_CODING = answers(p_hard=0.8, kind="coding")
HARD_GENERAL = answers(p_hard=0.8, kind="general")
CHAT = [{"role": "user", "content": "Find the race in the scheduler"}]


def live_policy(**agents):
    pol = policy(**agents)
    pol["profiles"] = {"default": "private"}
    return pol


class TurnTests(unittest.TestCase):
    def setUp(self):
        self.refused = []
        self.calls = []

    def refuse(self, name, reason, cooldown=0):
        self.refused.append((name, reason.split(":")[0], cooldown))

    def runner(self, name, error=None, text="Antwoord."):
        """A fake codex or claude: an answer, or a failure with `error` on stderr."""
        def run(argv, stdin_text, timeout):
            self.calls.append(name)
            if error is not None:
                return subprocess.CompletedProcess(argv, 1, "", error)
            if name == "codex":
                Path(argv[argv.index("--output-last-message") + 1]).write_text(text, encoding="utf-8")
                return subprocess.CompletedProcess(argv, 0, "", "")
            body = json.dumps({"type": "result", "is_error": False, "result": text, "session_id": "s-9"})
            return subprocess.CompletedProcess(argv, 0, body, "")
        return run

    def turn(self, pol, answers_=HARD_GENERAL, chat=CHAT, text=None, run=True, runners=None, clock=None):
        return dispatch.dispatch_turn(text or chat[-1]["content"], chat, profile="default", run=run, policy=pol,
                                      config=dispatch.route.load_config(NOWHERE), answers=answers_,
                                      runners=runners or {}, cooling=NOT_COOLING, refuse=self.refuse,
                                      clock=clock)

    def test_shadow_decides_and_hands_nothing_over(self):
        out = self.turn(live_policy(openai=ON), run=False, runners={"codex": self.runner("codex")})
        self.assertEqual(out["agent"], "openai")
        self.assertGreater(out["would_send_chars"], 0)
        self.assertEqual(self.calls, [])
        self.assertNotIn("text", out)

    def test_an_answer_comes_back_named(self):
        pol = live_policy(openai={"enabled": True, "model": "gpt-6-sol"})
        out = self.turn(pol, runners={"codex": self.runner("codex", text="De race zit in de lock.")})
        self.assertEqual(out["agent"], "openai")
        self.assertEqual(out["text"], "[openai · gpt-6-sol]\n\nDe race zit in de lock.")

    def test_a_full_seat_cools_and_the_next_agent_answers(self):
        pol = live_policy(openai=ON, claude={"enabled": True, "only_repo": False, "cooldown": 900})
        out = self.turn(pol, runners={"codex": self.runner("codex", error="You've hit your usage limit"),
                                      "claude": self.runner("claude")})
        self.assertEqual(out["agent"], "claude")
        self.assertEqual(self.refused, [("openai", "quota", 1800.0)])
        self.assertEqual(out["attempts"][0]["error"], "quota")
        self.assertEqual(out["session"], "s-9")

    def test_a_timeout_cools_the_seat_briefly(self):
        pol = live_policy(openai=ON)

        def slow(argv, stdin_text, timeout):
            raise subprocess.TimeoutExpired("codex", timeout)

        out = self.turn(pol, runners={"codex": slow})
        self.assertEqual((out["agent"], out["downgraded"]), ("local", True))
        self.assertEqual(self.refused, [("openai", "timeout", 300.0)])

    def test_the_time_budget_stops_the_next_attempt(self):
        pol = live_policy(openai=ON, claude={"enabled": True, "only_repo": False})
        pol["turn_budget"] = 100
        ticks = iter([0.0, 0.0, 95.0, 95.0, 95.0])        # start, before openai, before claude, spare
        out = self.turn(pol, runners={"codex": self.runner("codex", error="usage limit"),
                                      "claude": self.runner("claude")}, clock=lambda: next(ticks))
        self.assertEqual(self.calls, ["codex"])
        self.assertEqual((out["agent"], out["attempts"][-1]["error"]), ("local", "budget"))

    def test_an_agent_gets_no_more_time_than_the_turn_has_left(self):
        pol = live_policy(openai=ON)
        pol["turn_budget"] = 100
        seen = []

        def run(argv, stdin_text, timeout):
            seen.append(timeout)
            Path(argv[argv.index("--output-last-message") + 1]).write_text("ok", encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, "", "")

        ticks = iter([0.0, 40.0, 40.0])
        self.turn(pol, runners={"codex": run}, clock=lambda: next(ticks))
        self.assertEqual(seen, [60.0])

    def test_an_earlier_turn_about_a_client_file_keeps_the_turn_here(self):
        chat = [{"role": "user", "content": "Hier is het dossier van mijn cliënt."},
                {"role": "assistant", "content": "Ik heb het gelezen."},
                {"role": "user", "content": "Find the race in the scheduler"}]
        out = self.turn(live_policy(openai=ON), chat=chat, runners={"codex": self.runner("codex")})
        self.assertEqual((out["agent"], out["privacy"]), ("local", "highly_sensitive"))
        self.assertEqual(self.calls, [])

    def test_every_agent_failing_answers_here(self):
        pol = live_policy(openai=ON, claude={"enabled": True, "only_repo": False})
        out = self.turn(pol, runners={"codex": self.runner("codex", error="usage limit"),
                                      "claude": self.runner("claude", error="usage limit reached")})
        self.assertEqual((out["agent"], out["downgraded"]), ("local", True))
        self.assertEqual([a["agent"] for a in out["attempts"]], ["openai", "claude"])

    def test_a_secret_in_the_conversation_keeps_the_turn_here(self):
        chat = [{"role": "user", "content": "token: GITHUB_TOKEN=nietecht123"},
                {"role": "assistant", "content": "Genoteerd."},
                {"role": "user", "content": "Find the race in the scheduler"}]
        out = self.turn(live_policy(openai=ON), chat=chat, runners={"codex": self.runner("codex")})
        self.assertEqual(out["agent"], "local")
        self.assertEqual(self.calls, [])

    def test_highly_sensitive_asks_nobody(self):
        chat = [{"role": "user", "content": "Vat het dossier van mijn cliënt samen"}]
        out = self.turn(live_policy(openai=ON), chat=chat, answers_=None, runners={"codex": self.runner("codex")})
        self.assertEqual((out["agent"], out["privacy"]), ("local", "highly_sensitive"))
        self.assertEqual(self.calls, [])

    def test_an_image_stays_here_for_now(self):
        chat = [{"role": "user", "content": [{"type": "text", "text": "Wat staat hier?"},
                                             {"type": "image_url", "image_url": {"url": "data:..."}}]}]
        out = self.turn(live_policy(openai=ON), chat=chat, text="Wat staat hier?", runners={"codex": self.runner("codex")})
        self.assertEqual(out["agent"], "local")
        self.assertEqual(self.calls, [])


class CheckAgentsTests(unittest.TestCase):
    def test_it_reports_without_running_anything(self):
        pol = policy(openai={"enabled": True, "model": "gpt-6-sol"})
        report = dispatch.check_agents(pol, which=lambda program: "/usr/bin/codex" if program == "codex" else None,
                                       cooling=NOT_COOLING, has_key=lambda: False)
        self.assertEqual(report["agents"]["openai"],
                         {"kind": "codex", "enabled": True, "model": "gpt-6-sol", "privacy": ["public", "private"],
                          "available": True, "cooling_s": 0})
        self.assertFalse(report["agents"]["claude"]["available"])
        self.assertFalse(report["agents"]["openrouter"]["available"])
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 -m unittest tests.test_dispatch.TurnTests tests.test_dispatch.CheckAgentsTests -v`
Expected: `AttributeError: ... 'dispatch_turn'`.

- [ ] **Step 3: Add to `jevkit/dispatch.py`**

Add `import shutil` and `import time`, and change the jevkit import to `from . import agents, client, keystore, ladder, privacy, relay, route`. Then append:

```python
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
        return agents.run_claude(prompt, model=model, session=session, max_turns=int(settings.get("max_turns") or 8),
                                 timeout=timeout, argv=settings.get("argv"), runner=runners.get("claude"))
    if kind == "openrouter":
        return agents.run_openrouter(prompt, model=model, timeout=timeout, transport=transport)
    raise agents.AgentError("failed", f"unknown agent kind {kind!r}")


# Failures that will not fix themselves by the next turn: cool the agent for its full cooldown,
# so no lane retries it on every message. A timeout cools it briefly (`timeout_cooldown`), so the
# next turn does not wait out the same slow seat again. An odd failure cools nothing.
_COOL_ON = ("quota", "auth", "missing")
_BUDGET_FLOOR = 30.0                          # below this many seconds left, no agent is started


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
                               interactive=interactive, config=config, transport=transport, answers=answers)
    out: Dict[str, Any] = {"privacy": klass, "privacy_why": why, "jev": triage.get("jev"),
                           "triage": {k: v for k, v in triage.items() if k != "jev"}, "attempts": []}
    if relay.has_images(messages):
        return {**out, **_local("the turn carries an image; a handed-off turn is text only for now", [],
                                downgraded=triage.get("niveau") == "frontier")}
    failed: Dict[str, str] = {}
    for _ in range(len(policy.get("agents") or {}) + 1):
        chosen = choose_route(triage, policy, cooling=lambda name: 1e9 if name in failed else cooling(name))
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
                refuse(chosen["agent"], f"{error.code}: {error.detail}",
                       cooldown=float(_int(settings.get("cooldown"), 1800)))
            elif error.code == "timeout":
                refuse(chosen["agent"], f"{error.code}: {error.detail}",
                       cooldown=float(_int(policy.get("timeout_cooldown"), 300)))
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
        program = {"codex": "codex", "claude": "claude"}.get(str(kind))
        available = bool(which(program)) if program else (has_key() if kind == "openrouter" else False)
        rows[name] = {"kind": kind, "enabled": bool(settings.get("enabled")), "model": settings.get("model") or "",
                      "privacy": settings.get("privacy") or [], "available": available,
                      "cooling_s": round(cooling(name))}
    return {"mode": policy.get("mode"), "profiles": policy.get("profiles") or {}, "agents": rows,
            "policy_files": [str(path) for path in policy_paths()]}
```

Also add `Sequence` to the typing import.

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest tests.test_dispatch -v`
Expected: all pass. The refuse recorder in `test_a_full_seat_cools_and_the_next_agent_answers` expects the default openai cooldown (1800).

- [ ] **Step 5: Commit**

```bash
git add jevkit/dispatch.py tests/test_dispatch.py
git commit -m "dispatch: one turn end to end, cooling full seats, local on every failure"
```

---

### Task 10: `jev dispatch` on the command line

**Files:**
- Modify: `jevkit/cli.py` (import `dispatch`, add `cmd_dispatch` and its parser)
- Test: `tests/test_dispatch.py`

- [ ] **Step 1: Write the failing tests** (append)

```python
class CliTests(unittest.TestCase):
    def run_cli(self, *argv):
        buffer = io.StringIO()
        with mock.patch.object(cli.dispatch, "load_policy", return_value=dispatch.load_policy(NOWHERE)), \
                contextlib.redirect_stdout(buffer):
            code = cli.main(list(argv))
        return code, json.loads(buffer.getvalue())

    def test_a_highly_sensitive_turn_is_answered_here_without_asking_jev(self):
        code, out = self.run_cli("dispatch", "--prompt", "hoi", "--privacy", "highly_sensitive")
        self.assertEqual((code, out["agent"]), (0, "local"))

    def test_check_runs_nothing_and_names_every_agent(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"JEV_LADDER_STATE": str(Path(tmp) / "ladder.json")}), \
                mock.patch.object(dispatch.keystore, "resolve", return_value=None):
            code, out = self.run_cli("dispatch", "check")
        self.assertEqual(code, 0)
        self.assertEqual(set(out["agents"]), {"openai", "claude", "openrouter"})
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 -m unittest tests.test_dispatch.CliTests -v`
Expected: `AttributeError: module 'jevkit.cli' has no attribute 'dispatch'`, or argparse `invalid choice: 'dispatch'`.

- [ ] **Step 3: Add the command to `jevkit/cli.py`**

Add `dispatch` to the `from . import ...` line in alphabetical order. Add the handler after `cmd_route`:

```python
def cmd_dispatch(args: argparse.Namespace) -> int:
    """Which agent would answer a turn, and with --run, its answer. `check` runs nothing."""
    policy = dispatch.load_policy()
    if args.action == "check":
        return _out(dispatch.check_agents(policy))
    request = _stdin_json() if args.prompt is None else {"prompt": args.prompt}
    prompt = str(request.get("prompt") or "")
    profile = args.profile or "default"
    if args.privacy:
        policy = {**policy, "profiles": {**(policy.get("profiles") or {}), profile: args.privacy}}
    messages = request.get("messages") or [{"role": "user", "content": prompt}]
    return _out(dispatch.dispatch_turn(prompt, messages, profile=profile,
                                       context_tokens=int(request.get("context_tokens") or 0),
                                       interactive=not args.background, run=args.run, policy=policy))
```

In `build_parser`, after the `route` parser:

```python
    p = sub.add_parser("dispatch", help="which agent answers a turn: local, openai, claude or openrouter; "
                                        "--run hands it over, `check` shows what is set up")
    p.add_argument("action", nargs="?", choices=["route", "check"], default="route")
    p.add_argument("--prompt")
    p.add_argument("--profile")
    p.add_argument("--privacy", choices=list(dispatch.PRIVACY), help="treat the profile as this class for this call")
    p.add_argument("--background", action="store_true", help="not an interactive chat turn")
    p.add_argument("--run", action="store_true", help="actually hand the turn to the chosen agent")
    p.set_defaults(func=cmd_dispatch)
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest tests.test_dispatch tests.test_cli_help_examples -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add jevkit/cli.py tests/test_dispatch.py
git commit -m "cli: jev dispatch shows who would answer a turn, and check shows the agents"
```

---

### Task 11: The Hermes plugin

**Files:**
- Create: `hermes/plugin/hermes-dispatch/plugin.yaml`
- Create: `hermes/plugin/hermes-dispatch/__init__.py`
- Create: `tests/test_dispatch_plugin.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_dispatch_plugin.py`:

```python
"""Hermetic tests for the dispatch plugin's llm_execution middleware. No Hermes, no agent, no Jev."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PLUGIN = Path(__file__).resolve().parents[1] / "hermes" / "plugin" / "hermes-dispatch"
REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "hermes_dispatch_under_test", PLUGIN / "__init__.py", submodule_search_locations=[str(PLUGIN), str(REPO)])
plugin = importlib.util.module_from_spec(spec)
sys.modules["hermes_dispatch_under_test"] = plugin
spec.loader.exec_module(plugin)

REQUEST = {"model": "qwen36", "messages": [{"role": "user", "content": "Find the race in the scheduler"}]}


class Next:
    def __init__(self):
        self.calls = 0

    def __call__(self, request=None):
        self.calls += 1
        return "LOCAL-RESPONSE"


class MiddlewareTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        self.logs, self.dispatched = [], []
        self.policy = plugin.dispatch.load_policy(Path("/nonexistent"))
        self.answer = {"agent": "openai", "model": "gpt-6-sol", "text": "[openai · gpt-6-sol]\n\nDe lock.",
                       "reason": "frontier work for openai", "downgraded": False, "privacy": "private",
                       "triage": {"niveau": "frontier"}, "attempts": []}
        plugin._TURNS.clear()
        plugin._SESSIONS.clear()

        def fake_turn(text, messages, **kwargs):
            self.dispatched.append(kwargs)
            return dict(self.answer)

        for patch in (mock.patch.dict(os.environ, {"HERMES_HOME": self.home.name}),
                      mock.patch.object(plugin, "_log", self.logs.append),
                      mock.patch.object(plugin, "_hermes_jev_routing", return_value=None),   # never a real config.yaml
                      mock.patch.object(plugin.dispatch, "load_policy", lambda *a, **k: self.policy),
                      mock.patch.object(plugin.dispatch, "dispatch_turn", side_effect=fake_turn)):
            patch.start()
            self.addCleanup(patch.stop)

    def mode(self, value):
        self.policy["mode"] = value

    def call(self, session="s1", turn="t1", api_mode="chat_completions", parent="", text=None, platform="telegram"):
        plugin._on_pre_llm_call(session_id=session, turn_id=turn, user_message=text or REQUEST["messages"][-1]["content"],
                                parent_session_id=parent, platform=platform)
        following = Next()
        result = plugin._on_llm_execution(request=dict(REQUEST), next_call=following, session_id=session,
                                          turn_id=turn, api_mode=api_mode)
        return result, following

    def test_off_is_exactly_the_old_behaviour(self):
        self.mode("off")
        result, following = self.call()
        self.assertEqual((result, following.calls, self.dispatched), ("LOCAL-RESPONSE", 1, []))

    def test_shadow_decides_logs_and_answers_locally(self):
        self.mode("shadow")
        result, following = self.call()
        self.assertEqual((result, following.calls), ("LOCAL-RESPONSE", 1))
        self.assertFalse(self.dispatched[0]["run"])
        self.assertEqual(self.logs[-1]["agent"], "openai")
        self.assertNotIn("text", self.logs[-1])

    def test_on_hands_the_turn_over_and_returns_a_chat_completion(self):
        self.mode("on")
        result, following = self.call()
        self.assertEqual(following.calls, 0)
        self.assertTrue(self.dispatched[0]["run"])
        choice = result.choices[0]
        self.assertEqual(choice.message.content, "[openai · gpt-6-sol]\n\nDe lock.")
        self.assertEqual((choice.finish_reason, choice.message.tool_calls, result.usage), ("stop", None, None))

    def test_only_a_chat_completions_provider_is_ever_short_circuited(self):
        self.mode("on")
        result, following = self.call(api_mode="codex_responses")
        self.assertEqual((result, following.calls), ("LOCAL-RESPONSE", 1))
        self.assertFalse(self.dispatched[0]["run"])

    def test_the_tool_loop_after_the_first_call_is_left_alone(self):
        self.mode("on")
        self.call()
        following = Next()
        plugin._on_llm_execution(request=dict(REQUEST), next_call=following, session_id="s1", turn_id="t1",
                                 api_mode="chat_completions")
        self.assertEqual((following.calls, len(self.dispatched)), (1, 1))

    def test_a_subagent_is_never_dispatched(self):
        self.mode("on")
        result, following = self.call(parent="parent-session")
        self.assertEqual((result, following.calls, self.dispatched), ("LOCAL-RESPONSE", 1, []))

    def test_template_turns_and_cron_are_left_alone(self):
        self.mode("on")
        self.assertEqual(self.call(text="[kanban] move card 3")[1].calls, 1)
        self.assertEqual(self.call(session="s2", platform="cron")[1].calls, 1)
        self.assertEqual(self.dispatched, [])

    def test_one_classifier_per_turn_while_jev_routing_is_on(self):
        self.mode("on")
        state = Path(self.home.name) / "jev" / "state.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"routing": "shadow"}))
        result, following = self.call()
        self.assertEqual((result, following.calls, self.dispatched), ("LOCAL-RESPONSE", 1, []))
        self.assertIn("one classifier", self.logs[-1]["reason"])

    def test_a_failure_inside_is_a_local_answer(self):
        self.mode("on")
        with mock.patch.object(plugin.dispatch, "dispatch_turn", side_effect=RuntimeError("boom")):
            result, following = self.call()
        self.assertEqual((result, following.calls), ("LOCAL-RESPONSE", 1))
        self.assertIn("dispatch failed", self.logs[-1]["reason"])

    def test_a_local_decision_lets_the_call_go_ahead(self):
        self.mode("on")
        self.answer = {"agent": "local", "reason": "standard work stays on this machine", "downgraded": False,
                       "privacy": "private", "triage": {}, "attempts": []}
        result, following = self.call()
        self.assertEqual((result, following.calls), ("LOCAL-RESPONSE", 1))

    def test_a_claude_session_continues_on_the_next_turn(self):
        self.mode("on")
        self.answer = {**self.answer, "agent": "claude", "session": "sess-7"}
        self.call(turn="t1")
        self.call(turn="t2")
        self.assertEqual(self.dispatched[1]["session"], "sess-7")

    def test_routing_switched_on_in_config_yaml_also_counts(self):
        self.mode("on")
        with mock.patch.object(plugin, "_hermes_jev_routing", return_value="on"):
            result, following = self.call()
        self.assertEqual((result, following.calls, self.dispatched), ("LOCAL-RESPONSE", 1, []))

    def test_config_yaml_sets_the_mode_when_no_switch_was_used(self):
        self.mode("off")
        with mock.patch.object(plugin, "_plugin_setting", lambda name: "on" if name == "mode" else None):
            result, following = self.call()
        self.assertEqual(following.calls, 0)

    def test_agent_sessions_are_bounded(self):
        self.mode("on")
        self.answer = {**self.answer, "agent": "claude", "session": "sess"}
        with mock.patch.object(plugin, "_MAX_SESSIONS", 2):
            for index in range(3):
                self.call(session=f"s{index}")
        self.assertEqual(len(plugin._SESSIONS), 2)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        patch = mock.patch.dict(os.environ, {"HERMES_HOME": self.home.name})
        patch.start()
        self.addCleanup(patch.stop)

    def test_switches_are_written_for_this_profile(self):
        self.assertIn("mode = shadow", plugin._dispatch_command("shadow"))
        saved = json.loads((Path(self.home.name) / "jev" / "dispatch-state.json").read_text())
        self.assertEqual(saved["mode"], "shadow")

    def test_status_names_every_agent(self):
        report = {"mode": "off", "profiles": {}, "policy_files": [],
                  "agents": {"openai": {"kind": "codex", "enabled": False, "model": "", "privacy": [],
                                        "available": True, "cooling_s": 0}}}
        with mock.patch.object(plugin.dispatch, "check_agents", return_value=report), \
                mock.patch.object(plugin.dispatch, "load_policy", return_value=plugin.dispatch.load_policy(Path("/x"))):
            text = plugin._dispatch_command("")
        self.assertIn("openai", text)
        self.assertIn("usage: /dispatch", text)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 -m unittest tests.test_dispatch_plugin -v`
Expected: `FileNotFoundError` for the plugin's `__init__.py`.

- [ ] **Step 3: Write `hermes/plugin/hermes-dispatch/plugin.yaml`**

```yaml
name: hermes-dispatch
version: 0.1.0
description: Front desk - hands a turn Jev judges hard to the agent that should answer it (Codex on a ChatGPT login, Claude Code, OpenRouter) and relays the answer unchanged; everything else stays on the local model. Off by default.
author: hermes-jev-skills contributors
license: MIT
homepage: https://github.com/kerpopule/hermes-jev-skills
manifest_version: 2
provides_hooks:
  - pre_llm_call
  - transform_llm_output
tags: [jev, routing, dispatch, receptionist]
config_schema:
  mode: {type: str, description: "off | shadow | on"}
  notice: {type: str, description: "off | on"}
```

- [ ] **Step 4: Write `hermes/plugin/hermes-dispatch/__init__.py`**

```python
"""Hermes dispatch plugin: the front desk that hands a turn to the agent that should answer it.

The chat model stays the Hermes model. On the first provider call of each fresh user turn,
`llm_execution` middleware asks jevkit.dispatch who should answer:

* this machine: the call goes ahead untouched;
* an agent (Codex on a ChatGPT login, Claude Code, OpenRouter): the turn is handed to it and its
  answer comes back as the assistant message, with who wrote it on the first line.

Shadow decides and logs, and always lets the local call go ahead. Only a `chat_completions`
provider is ever short-circuited, because that is the response shape this plugin builds.
Everything fails open: any error in here is a local answer, never a lost turn.

One classifier per turn: while `/jev routing` is on or in shadow, this plugin stands aside.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

from .jevkit import dispatch

_LOCK = threading.Lock()
_TURNS: Dict[str, Dict[str, Any]] = {}      # session -> this turn's text and decision
_SESSIONS: Dict[str, str] = {}              # Hermes session -> the agent session that continues it
_MAX_SESSIONS = 256
_CTX: Any = None


def _home() -> Path:
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
    except (OSError, json.JSONDecodeError):
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


def _jev_routing_active() -> bool:
    """hermes-jev's routing, read the way that plugin reads it: a `/jev` switch, then config.yaml."""
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


def _on_pre_llm_call(session_id: str = "", turn_id: Any = None, user_message: Any = "",
                     parent_session_id: str = "", platform: str = "", **_: Any) -> Any:
    text = user_message if isinstance(user_message, str) else json.dumps(user_message, default=str)[:6000]
    with _LOCK:
        if len(_TURNS) >= _MAX_SESSIONS:
            _TURNS.pop(next(iter(_TURNS)))
        _TURNS[session_id or "-"] = {"turn_id": turn_id, "text": text, "child": bool(parent_session_id),
                                     "platform": str(platform or ""), "claimed": False, "decision": None}
    return None


def _on_llm_execution(request: Any = None, next_call: Any = None, session_id: str = "", turn_id: Any = None,
                      api_mode: str = "", **_: Any) -> Any:
    policy = dispatch.load_policy()
    mode = _setting("mode", policy, "off")
    if mode not in ("shadow", "on") or not isinstance(request, dict):
        return next_call(request)
    key = session_id or "-"
    with _LOCK:
        turn = _TURNS.get(key)
        first = bool(turn) and turn["turn_id"] == turn_id and not turn["claimed"]
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
        messages = request.get("messages") or []
        decision = dispatch.dispatch_turn(
            text, messages, profile=_profile(), context_tokens=len(json.dumps(messages, default=str)) // 4,
            interactive=True, run=live, session=_SESSIONS.get(key, ""), policy=policy)
    except Exception as error:  # noqa: BLE001 - the turn goes ahead locally, and the log says why
        _log({"mode": mode, "agent": dispatch.LOCAL, "reason": f"dispatch failed ({type(error).__name__})"})
        return next_call(request)
    turn["decision"] = decision
    _log({"mode": mode, "live": live, **_summary(decision)})
    if live and decision.get("agent") != dispatch.LOCAL and decision.get("text"):
        if decision.get("session"):
            with _LOCK:
                if key not in _SESSIONS and len(_SESSIONS) >= _MAX_SESSIONS:
                    _SESSIONS.pop(next(iter(_SESSIONS)))
                _SESSIONS[key] = str(decision["session"])
        return _completion(str(decision["text"]), str(decision.get("model") or decision["agent"]))
    return next_call(request)


def _on_transform_output(response_text: str = "", session_id: str = "", **_: Any) -> Any:
    """Say it when a turn that deserved another agent was answered here. Once per turn."""
    policy = dispatch.load_policy()
    if _setting("notice", policy, "off") != "on" or _setting("mode", policy, "off") != "on":
        return None
    with _LOCK:
        turn = _TURNS.get(session_id or "-") or {}
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
```

- [ ] **Step 5: Run the tests and the installer tests**

Run: `python3 -m unittest tests.test_dispatch_plugin tests.test_install -v`
Expected: all pass. The installer discovers the plugin by its `plugin.yaml`, so `install.PLUGINS` now holds three names.

- [ ] **Step 6: Commit**

```bash
git add hermes/plugin/hermes-dispatch tests/test_dispatch_plugin.py
git commit -m "hermes-dispatch: llm_execution front desk, shadow first, off by default"
```

---

### Task 12: Say what leaves, and ship it

**Files:**
- Create: `docs/receptionist-dispatch.md`
- Modify: `README.md` (section "What leaves your machine": one bullet)
- Modify: `CHANGELOG.md` (under `## Unreleased`)
- Modify: `AGENTS.md` (step 2: the plugin list)

- [ ] **Step 1: Write `docs/receptionist-dispatch.md`**

Cover: what it does (the three parts), the example `dispatch.json` below, the privacy classes, the one-classifier rule, the modes and rollout, what leaves the machine, the terms caveat for subscription logins, and the limits (not streamed, text only, read-only agents, synchronous). Use this example file:

```json
{
  "profiles": {"default": "private", "coding": "public", "secondbrain": "highly_sensitive"},
  "agents": {
    "openai": {"enabled": true, "model": "<a model id your Codex login lists>"},
    "claude": {"enabled": true, "model": "opus"},
    "openrouter": {"enabled": false, "model": "<an OpenRouter model id>"}
  }
}
```

Include these rollout steps:
1. `jev dispatch check`
2. `/jev routing off` (one classifier per turn)
3. `/dispatch shadow` and a day of `grep '"kind":"dispatch"' ~/.hermes/logs/jev-decisions.jsonl`
4. `/dispatch on` with one agent enabled
5. The others, one at a time

Rollback is `/dispatch off`.

- [ ] **Step 2: Add the README bullet** (in "What leaves your machine", after the Routing bullet)

```markdown
- **Dispatch** (`hermes-dispatch`, off by default): when a turn Jev judged hard is handed to another agent, that agent's provider receives a handoff: the person's message and up to six recent user and assistant turns, text only, redacted, about 12,000 characters. System prompts, tool output, memory and files are never part of it. It stays on your machine, sent to no one, when the profile is highly sensitive (a profile you did not classify counts as one), or when the message or any turn the handoff would carry names a client or patient file, a conversation report, a treatment plan, medication, a criminal record, debts, a BSN or an IBAN, or looks like it holds a secret. The words are Dutch and extendable in `dispatch.json`. Jev itself reads the turn under the same rules as routing: redacted text for public profiles, coarse features for the rest.
```

- [ ] **Step 3: Add the CHANGELOG entry** (at the top of `## Unreleased`)

```markdown
**Receptionist dispatch: a hard turn goes to the agent that should answer it**

- New `hermes-dispatch` plugin (off by default) and `jev dispatch`. On the first provider call of a turn, `llm_execution` middleware classifies the turn with Jev, applies a deterministic policy (privacy class, level, context window, cooldowns, order), and either lets the local call go ahead or hands the turn to Codex on a ChatGPT login, Claude Code, or OpenRouter. The answer comes back unchanged under one line naming its author. Shadow mode decides and logs only. A quota, auth or missing-program failure cools that agent for every lane through the ladder, a timeout cools it for five minutes, and the next agent or the local model answers. A turn spends at most `turn_budget` (15 minutes) on agents. The privacy class covers every turn a handoff would carry, not only the newest.
- `route.judge_answers`, `route.clip_ask` and `route.is_risky` are extracted from `route.decide` with no change in behaviour, so routing and dispatch share one calibrated judgement.
- The TRIAGE record follows the reasoning library's routing contract, so a local receptionist can later take Jev's place as the classifier (one classifier per turn).
- `tests/test_turn.py` and `tests/test_question_shape.py` no longer depend on a real key being installed.
```

- [ ] **Step 4: Update `AGENTS.md` step 2**

Change `(hermes-jev for routing, hermes-handoff for end-of-session capsules)` to `(hermes-jev for routing, hermes-handoff for end-of-session capsules, hermes-dispatch for handing hard turns to another agent, which stays off until someone runs /dispatch shadow)`.

- [ ] **Step 5: Run everything CI runs**

Run: `env -u TYPESAFE_API_KEY -u OPENROUTER_API_KEY python3 -m unittest discover -s tests && python3 scripts/check_release.py`
Expected: `OK` and `clean`.

- [ ] **Step 6: Commit**

```bash
git add docs/receptionist-dispatch.md README.md CHANGELOG.md AGENTS.md
git commit -m "docs: what dispatch sends, how to roll it out, and how to turn it off"
```

---

## Phase 2 (not in this plan's tasks; a separate plan when phase 1 has run in shadow)

1. **The receptionist as classifier.** An auxiliary, non-streamed call to a local model. The system prompt is the reasoning library's SKILL.md sections 1, 2, 3 and 5 plus receptionist.md sections 1, 3, 4 and 6. Validate the result with `parse_triage`. On errors, send the library checker's feedback once, then fail open to `standard`. Set `classifier: receptionist` per privacy class, so private turns are classified locally with full text instead of Jev's coarse features. This also brings ASK, ASSUME and G9 ("one level up after two failures").
2. **Receptionist evals.** `scripts/eval_receptionist.py --library <path> --endpoint <url> --model <id>` runs the library's 22 `evals.json` messages. It reports false PROCEED (the dangerous one), over-asking and wrong ESCALATE per model, so the benchmark pack's D2 phase can pick the receptionist model on evidence.
3. **Asynchronous handoff.** Long agent runs go to a background job (Hermes delegation or kanban) with delivery back into the chat. The receptionist stays available meanwhile (library step 5).
4. **Images** through OpenRouter's image parts.
5. **max_lokaal** (the heavy slot) for non-interactive turns.
6. **A dashboard view** for `kind: dispatch` rows.
