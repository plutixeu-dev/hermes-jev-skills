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
