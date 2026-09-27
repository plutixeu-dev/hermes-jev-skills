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
