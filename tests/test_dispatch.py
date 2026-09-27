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
