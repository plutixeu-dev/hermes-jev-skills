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
import types
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
        for text in ("Vat het gespreksverslag samen", "Wat vindt mijn cliënt ervan?"):
            self.assertEqual(self.klass(text, profile="default"), "highly_sensitive", text)

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

    def test_ibans_from_the_whole_registry(self):
        for text in ("TR33 0006 1005 1978 6457 8413 26", "RS35 2600 0560 1001 6113 79",
                     "UA21 3223 1300 0002 6007 2335 6600 1", "NL91-ABNA-0417-1643-00"):
            self.assertTrue(dispatch.privacy.has_iban(text), text)

    def test_a_version_string_followed_by_words_is_not_an_iban(self):
        self.assertFalse(dispatch.privacy.has_iban(
            "Set the target to es2023 so that optional chaining works in older browsers"))

    def test_your_own_terms_are_normalised_like_the_text(self):
        policy = {**POLICY, "sensitive_terms": ["reïntegratie"]}    # i + combining diaeresis
        self.assertEqual(dispatch.privacy_class("Het reïntegratietraject loopt", profile="coding",
                                                policy=policy)[0], "highly_sensitive")


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

    def test_openrouter_takes_public_turns_only_under_any_name(self):
        pol = policy(openrouter={"enabled": True, "model": "x/y", "privacy": ["public", "private"]})
        pol["last_resort"] = ""
        pol["frontier_order"] = {"repo": ["openrouter"], "default": ["openrouter"]}
        self.assertEqual(self.route(triage(privacy="private"), pol)["agent"], "local")
        self.assertEqual(self.route(triage(privacy="public"), pol)["agent"], "openrouter")

    def test_an_endless_window_is_no_window(self):
        pol = policy(openai={"enabled": True, "context_tokens": float("inf")})
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

    def load(self, layer):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dispatch.json"
            path.write_text(json.dumps(layer))
            return dispatch.load_policy(path)

    def test_enabled_written_as_a_string_is_not_a_yes(self):
        loaded = self.load({"profiles": {"default": "private"}, "agents": {"openai": {"enabled": "false"}}})
        self.assertIs(loaded["agents"]["openai"]["enabled"], False)
        self.assertEqual(dispatch.choose_route(triage(), loaded, cooling=NOT_COOLING)["agent"], "local")

    def test_a_setting_of_the_wrong_type_keeps_its_default(self):
        loaded = self.load({"frontier_order": ["openai"], "tier_to_niveau": [], "handoff": 5, "turn_budget": "lang",
                            "agents": {"claude": {"argv": "claude -p", "timeout": "600", "model": "opus"}}})
        defaults = dispatch.load_policy(NOWHERE)
        for key in ("frontier_order", "tier_to_niveau", "handoff", "turn_budget"):
            self.assertEqual(loaded[key], defaults[key], key)
        self.assertEqual(loaded["agents"]["claude"]["argv"], ["claude", "-p"])
        self.assertEqual((loaded["agents"]["claude"]["timeout"], loaded["agents"]["claude"]["model"]), (600, "opus"))

    def test_one_term_given_as_a_string_is_kept_not_dropped(self):
        loaded = self.load({"sensitive_terms": "salaris", "profiles": {"coding": "public"}})
        self.assertEqual(loaded["sensitive_terms"], ["salaris"])
        self.assertEqual(dispatch.privacy_class("Wat is mijn salaris?", profile="coding", policy=loaded)[0],
                         "highly_sensitive")

    def test_an_argument_list_written_as_one_string_is_split_like_a_command_line(self):
        loaded = self.load({"agents": {"openai": {"argv": "codex exec --json -"}}})
        self.assertEqual(loaded["agents"]["openai"]["argv"], ["codex", "exec", "--json", "-"])

    def test_one_agent_named_in_an_order_is_an_order_of_one(self):
        loaded = self.load({"frontier_order": {"default": "openai"}})
        self.assertEqual(loaded["frontier_order"], {"repo": ["claude", "openai"], "default": ["openai"]})


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

    def test_a_turn_with_words_about_secrets_sends_features_as_routing_does(self):
        wire = Wire()
        self.classify(text="Why is my API key rejected by the proxy?", transport=wire)
        sent = json.dumps(wire.bodies)
        self.assertNotIn("rejected by the proxy", sent)
        self.assertIn("turn_features", sent)

    def test_routing_set_to_features_only_is_honoured(self):
        wire = Wire()
        config = {**dispatch.route.load_config(NOWHERE), "mode": "features"}
        self.classify(text="Rewrite the scheduler loop", transport=wire, config=config)
        self.assertNotIn("Rewrite the scheduler loop", json.dumps(wire.bodies))

    def test_a_private_profile_in_routing_sends_features(self):
        wire = Wire()
        config = {**dispatch.route.load_config(NOWHERE), "private_profiles": ["werk"]}
        self.classify(text="Rewrite the scheduler loop", transport=wire, config=config, profile="werk")
        self.assertNotIn("Rewrite the scheduler loop", json.dumps(wire.bodies))

    def test_missing_inner_answers_fail_open(self):
        record = self.classify(answers={"difficulty": {"score": 1}, "kind": {}, "costly_mistake": {}})
        self.assertEqual((record["niveau"], record["source"]), ("standard", "fail_open"))

    def test_no_token_count_is_zero(self):
        self.assertEqual(self.classify(klass="highly_sensitive", context_tokens=None)["context_tokens"], 0)


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


class ReviewFixTests(unittest.TestCase):
    def klass(self, text, profile="coding", policy=POLICY):
        return dispatch.privacy_class(text, profile=profile, policy=policy)

    def test_a_null_reason_or_a_bare_question_mark_is_not_one(self):
        _, errors = dispatch.parse_triage(line(exit="ESCALATE", signals=["G8"], niveau="frontier", reason=None))
        self.assertTrue(any("reason" in e for e in errors))
        _, errors = dispatch.parse_triage(line(exit="ASK", niveau=None, signals=["G4"], question="?"))
        self.assertTrue(any("question" in e for e in errors))

    def test_input_that_breaks_the_json_parser_is_an_error_not_a_crash(self):
        self.assertIsNone(dispatch.parse_triage(b"TRIAGE {}")[0])
        self.assertIsNone(dispatch.parse_triage("TRIAGE {\"a\": " + "[" * 100000)[0])
        self.assertIsNone(dispatch.parse_triage('TRIAGE {"context_tokens": ' + "9" * 5000 + "}")[0])

    def test_a_coding_question_about_secrets_is_not_a_secret(self):
        for text in ("How do I hash a password in Python?", "Why is my API key rejected?",
                     "id = Column(Integer, primary_key=True)", "cache_key = f(x)", "page_token=next_token",
                     "export OPENAI_API_KEY=$OPENAI_API_KEY"):
            self.assertEqual(self.klass(text)[0], "public", text)

    def test_a_secret_value_is_still_highly_sensitive(self):
        for text in ("my password is hunter22", "wachtwoord: Welkom01!", "Authorization: Bearer abcdefghijklmnop123",
                     "-----BEGIN RSA PRIVATE KEY-----",
                     "secret: " + "wJalrXUtnFEMI" + "/K7MDENG/bPxRfiCY" + "EXAMPLEKEY"):
            self.assertEqual(self.klass(text)[0], "highly_sensitive", text)

    def test_code_is_not_contact_details(self):
        for text in ("git clone git@github.com:owner/repo.git", "ts=1727452076 max=2147483647",
                     "Date.now() gave 1727452076123", "commit 73a8c72552aa0b1f", "2024-09-27 release"):
            self.assertEqual(self.klass(text)[0], "public", text)

    def test_a_phone_number_as_people_write_one_is(self):
        for text in ("Bel me op 06-12345678", "+31 6 1234 5678", "(555) 123-4567", "020-7946099"):
            self.assertEqual(self.klass(text)[0], "private", text)

    def test_terms_without_the_trema_count(self):
        self.assertEqual(self.klass("Hoeveel clienten heb je vandaag?")[0], "highly_sensitive")

    def test_a_single_term_given_as_a_string_is_one_term(self):
        policy = {**POLICY, "sensitive_terms": "salaris"}
        self.assertEqual(self.klass("Wat is mijn salaris?", policy=policy),
                         ("highly_sensitive", "mentions a term from sensitive_terms"))
        self.assertEqual(self.klass("Leg uit wat een bind mount is.", policy=policy)[0], "public")

    def test_a_built_in_term_is_named_and_a_persons_own_term_never_is(self):
        """The reason goes into the decision log. A word the person added is their own secret."""
        policy = {**POLICY, "sensitive_terms": ["salaris", "Project Merel"]}
        self.assertEqual(self.klass("Wat staat er in het dossier?", policy=policy), ("highly_sensitive", "mentions dossier"))
        for text in ("Wat is mijn salaris?", "Hoe staat project merel ervoor?"):
            self.assertEqual(self.klass(text, policy=policy), ("highly_sensitive", "mentions a term from sensitive_terms"))
        out = dispatch.dispatch_turn("Wat is mijn salaris?", [{"role": "user", "content": "Wat is mijn salaris?"}],
                                     profile="coding", policy={**dispatch.load_policy(NOWHERE), **policy},
                                     config=dispatch.route.load_config(NOWHERE), cooling=NOT_COOLING,
                                     refuse=lambda *a, **k: None)
        self.assertEqual(out["agent"], "local")
        self.assertNotIn("salaris", json.dumps(out))

    def test_profiles_that_are_not_a_mapping_count_as_unclassified(self):
        self.assertEqual(self.klass("hoi", policy={**POLICY, "profiles": ["coding"]})[0], "highly_sensitive")

    def test_the_reason_says_when_a_profile_is_not_classified(self):
        self.assertIn("not classified", self.klass("hoi", profile="onbekend")[1])


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
        self.assertEqual(self.refused, [("dispatch:openai", "quota", 1800.0)])
        self.assertEqual(out["attempts"][0]["error"], "quota")
        self.assertEqual(out["session"], "s-9")

    def test_a_timeout_cools_the_seat_briefly(self):
        pol = live_policy(openai=ON)

        def slow(argv, stdin_text, timeout):
            raise subprocess.TimeoutExpired("codex", timeout)

        out = self.turn(pol, runners={"codex": slow})
        self.assertEqual((out["agent"], out["downgraded"]), ("local", True))
        self.assertEqual(self.refused, [("dispatch:openai", "timeout", 300.0)])

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
        self.assertIn("highly sensitive", out["reason"])
        self.assertEqual(self.calls, [])

    def test_a_bad_max_turns_does_not_crash_the_turn(self):
        pol = live_policy(claude={"enabled": True, "only_repo": False})
        pol["agents"]["claude"]["max_turns"] = "veel"
        out = self.turn(pol, runners={"claude": self.runner("claude")})
        self.assertEqual(out["agent"], "claude")

    def test_an_image_stays_here_for_now(self):
        chat = [{"role": "user", "content": [{"type": "text", "text": "Wat staat hier?"},
                                             {"type": "image_url", "image_url": {"url": "data:..."}}]}]
        out = self.turn(live_policy(openai=ON), chat=chat, text="Wat staat hier?", runners={"codex": self.runner("codex")})
        self.assertEqual(out["agent"], "local")
        self.assertEqual(self.calls, [])

    def test_shadow_honours_the_time_budget_as_live_does(self):
        pol = live_policy(openai=ON)
        pol["turn_budget"] = 10
        out = self.turn(pol, run=False)
        self.assertEqual((out["agent"], out["attempts"][-1]["error"]), ("local", "budget"))

    def test_a_negative_agent_timeout_means_the_default(self):
        pol = live_policy(openai={"enabled": True, "timeout": -1})
        seen = []

        def run(argv, stdin_text, timeout):
            seen.append(timeout)
            Path(argv[argv.index("--output-last-message") + 1]).write_text("ok", encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, "", "")

        self.turn(pol, runners={"codex": run})
        self.assertGreater(seen[0], 30)

    def test_a_timeout_the_budget_caused_does_not_cool_the_seat(self):
        pol = live_policy(openai=ON)
        pol["turn_budget"] = 100

        def slow(argv, stdin_text, timeout):
            raise subprocess.TimeoutExpired("codex", timeout)

        self.turn(pol, runners={"codex": slow}, clock=lambda: 0.0)
        self.assertEqual(self.refused, [])


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

    def test_a_custom_argument_list_is_checked_by_its_own_program(self):
        pol = policy(openai={"enabled": True, "argv": ["/opt/codex/bin/codex", "exec", "-"]})
        seen = []
        dispatch.check_agents(pol, which=lambda program: seen.append(program), cooling=NOT_COOLING,
                              has_key=lambda: False)
        self.assertIn("/opt/codex/bin/codex", seen)


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

    def run_stdin(self, payload, *argv, policy=None):
        buffer = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"JEV_LADDER_STATE": str(Path(tmp) / "ladder.json")}), \
                mock.patch.object(cli.dispatch, "load_policy", return_value=policy or dispatch.load_policy(NOWHERE)), \
                mock.patch("sys.stdin", io.StringIO(json.dumps(payload))), contextlib.redirect_stdout(buffer):
            code = cli.main(["dispatch", *argv])
        return code, json.loads(buffer.getvalue())

    def test_without_a_prompt_the_turn_is_the_newest_user_message(self):
        code, out = self.run_stdin({"messages": [{"role": "user", "content": "Vat het dossier samen"}]},
                                   "--privacy", "private")
        self.assertEqual((code, out["privacy"], out["agent"]), (0, "highly_sensitive", "local"))

    def test_privacy_on_the_command_line_only_ever_tightens(self):
        strict = dispatch.load_policy(NOWHERE)
        strict["profiles"] = {"default": "highly_sensitive"}
        with mock.patch.object(cli.dispatch, "load_policy", return_value=strict), \
                contextlib.redirect_stdout(io.StringIO()) as buffer:
            cli.main(["dispatch", "--prompt", "Leg uit wat een bind mount is", "--privacy", "public"])
        self.assertEqual(json.loads(buffer.getvalue())["privacy"], "highly_sensitive")

    def test_input_that_is_not_an_object_is_an_error_answer(self):
        code, out = self.run_stdin([1, 2])
        self.assertEqual((code, out["error"]), (2, "invalid_request"))

    def test_an_empty_history_makes_the_prompt_the_only_turn(self):
        seen = []

        def capture(text, messages, **kwargs):
            seen.append(messages)
            return {"agent": "local"}

        with mock.patch.object(cli.dispatch, "dispatch_turn", side_effect=capture):
            code, out = self.run_stdin({"prompt": "Leg uit wat een bind mount is", "messages": []})
        self.assertEqual((code, out["agent"]), (0, "local"))
        self.assertEqual(seen, [[{"role": "user", "content": "Leg uit wat een bind mount is"}]])
        code, out = self.run_stdin({"messages": []})
        self.assertEqual((code, out["error"]), (2, "invalid_request"))

    def test_the_privacy_flag_says_it_only_ever_tightens(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), self.assertRaises(SystemExit):
            cli.main(["dispatch", "--help"])
        self.assertIn("make the profile at least this strict for this call; it never loosens one",
                      " ".join(buffer.getvalue().split()))

    def test_a_history_ending_in_a_reply_gets_the_prompt_as_its_new_turn(self):
        pol = dispatch.load_policy(NOWHERE)
        pol["profiles"] = {"default": "private"}
        pol["agents"]["openai"]["enabled"] = True
        frontier = {"type": "CHANGE", "exit": "ESCALATE", "signals": ["G8"], "niveau": "frontier",
                    "privacy": "private", "context_tokens": 0, "repo_werk": False, "interactief": True,
                    "reason": "hard work", "source": "jev"}
        with mock.patch.object(cli.dispatch, "classify_with_jev", return_value=frontier):
            code, out = self.run_stdin({"prompt": "Find the race", "messages": [
                {"role": "user", "content": "Kijk naar de scheduler"}, {"role": "assistant", "content": "Welke?"}]},
                policy=pol)
        self.assertEqual((out["agent"], out["would_send_chars"] > 0), ("openai", True))


AWS_LIKE = "wJalrXUtnFEMI" + "/K7MDENG/bPxRfiCY" + "EXAMPLEKEY"    # built at runtime: no key shape in the source

SECRET_FORMS = [
    '{"password": "Welkom01!"}', "'password' => 'Welkom01!'", '"api_key": "abcd1234efgh"',
    "https://admin:Welkom01!@db.example.org/app", "redis://:pw1234@host:6379", "password is: Welkom01",
    "wachtwoord van mijn bank is Zonnebloem", "my password is sunshine", "Authorization: Basic dXNlcjpwYXNz",
    "mysql -u root -pWelkom01 db", "run it with --password Welkom01", "curl -u admin:Welkom01 https://x.y",
    "DB_PASS=Welkom01", "MYSQL_PWD=Welkom01", "pwd=Welkom01", "pincode: 4829", "mijn pincode is 4829",
    "mijn OPENAI_API_KEY=nietecht123", "GITHUB_TOKEN=nietecht123", "my password is hunter22", "wachtwoord: Welkom01!",
    "Authorization: Bearer abcdefghijklmnop123", "-----BEGIN RSA PRIVATE KEY-----", "secret: " + AWS_LIKE,
    "password: 'hunter2'", "my password is hunter2, store it", "sshpass -p Welkom01 ssh host",
]
NOT_SECRETS = [
    "How do I hash a password in Python?", "Why is my API key rejected?", "id = Column(Integer, primary_key=True)",
    "cache_key = f(x)", "page_token=next_token", "the password is incorrect", "export OPENAI_API_KEY=$OPENAI_API_KEY",
    "My password is too short, what is the minimum?", "The password is hashed with bcrypt", "the token is expired",
    "API key is invalid, how do I rotate it?", "sort_key=lambda x: x", "Which password manager do you recommend?",
    "Mijn wachtwoord is vergeten, hoe reset ik het?", "wachtwoord is niet sterk genoeg", "gcc -pthread main.c",
    "git log -p", "password: required", "The API key is stored in the keychain",
    "token = request.headers['X-Token']", "key = config.get('key')", "set PASSWORD in your .env file",
    "curl -u $USER:$TOKEN https://api", "Use --password-stdin with docker login", "mysql -u root -p mydb",
    "for key, value in items.items():", "headers = {'Authorization': f'Bearer {token}'}",
]


class SecretValueTests(unittest.TestCase):
    def test_every_common_form_of_a_secret_value_is_caught(self):
        for text in SECRET_FORMS:
            self.assertTrue(dispatch.privacy.has_secret_value(text), text)

    def test_questions_and_code_about_secrets_are_not_secrets(self):
        for text in NOT_SECRETS:
            self.assertFalse(dispatch.privacy.has_secret_value(text), text)

    def test_dutch_mobile_numbers_in_every_usual_spelling(self):
        for text in ("06 1234 5678", "06-1234 5678", "+31 (0)6 12345678", "0612345678"):
            self.assertTrue(dispatch.privacy.has_contact_details(text), text)
            self.assertNotIn("5678", dispatch.privacy.redact(f"bel {text} morgen"), text)


class IsolationOfCooldownsTests(unittest.TestCase):
    def test_dispatch_cools_its_own_names_not_routings_rungs(self):
        seen = []
        pol = live_policy(openai=ON)

        def fail(argv, stdin_text, timeout):
            return subprocess.CompletedProcess(argv, 1, "", "usage limit")

        dispatch.dispatch_turn("Find the race", CHAT, policy=pol, config=dispatch.route.load_config(NOWHERE),
                               answers=HARD_GENERAL, runners={"codex": fail}, cooling=lambda name: seen.append(name) or 0.0,
                               refuse=lambda name, reason, cooldown=0: seen.append(("refuse", name)))
        self.assertIn(("refuse", "dispatch:openai"), seen)
        self.assertTrue(all(isinstance(n, tuple) or n.startswith("dispatch:") for n in seen))

    def test_an_adapter_bug_is_a_failed_attempt_not_a_lost_turn(self):
        pol = live_policy(openai=ON)
        with mock.patch.object(dispatch, "run_agent", side_effect=KeyError("bug")):
            out = dispatch.dispatch_turn("Find the race", CHAT, policy=pol, config=dispatch.route.load_config(NOWHERE),
                                         answers=HARD_GENERAL, cooling=NOT_COOLING, refuse=lambda *a, **k: None)
        self.assertEqual((out["agent"], out["attempts"][0]["error"]), ("local", "failed"))

    def test_the_default_budget_stays_under_hermes_idle_warning(self):
        self.assertLess(dispatch.DEFAULT_POLICY["turn_budget"], 900)

    def test_check_reports_the_mode_in_the_file_as_policy_mode(self):
        report = dispatch.check_agents(policy(), which=lambda p: None, cooling=NOT_COOLING, has_key=lambda: False)
        self.assertIn("policy_mode", report)
        self.assertNotIn("mode", report)


class LayeringTests(unittest.TestCase):
    def layers(self, shared, own):
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for index, layer in enumerate((shared, own)):
                path = Path(tmp) / f"{index}.json"
                path.write_text(json.dumps(layer))
                paths.append(path)
            with mock.patch.object(dispatch, "policy_paths", return_value=paths):
                return dispatch.load_policy()

    def test_a_broken_no_in_the_profile_file_is_still_a_no(self):
        for broken in ("false", 0, None, []):
            loaded = self.layers({"agents": {"openai": {"enabled": True}}}, {"agents": {"openai": {"enabled": broken}}})
            self.assertIs(loaded["agents"]["openai"]["enabled"], False, broken)

    def test_a_broken_privacy_list_allows_nothing(self):
        for broken in (None, ["public", None], "private,public"):
            loaded = self.layers({}, {"agents": {"openai": {"privacy": broken}}})
            expected = ["private,public"] if isinstance(broken, str) else []
            self.assertEqual(loaded["agents"]["openai"]["privacy"], expected, broken)

    def test_broken_privacy_settings_reset_to_the_strictest(self):
        loaded = self.layers({"profiles": {"default": "public"}, "jev_text_for": ["public", "private"]},
                             {"profiles": 5, "default_privacy": 7, "jev_text_for": {"x": 1}})
        self.assertEqual((loaded["profiles"], loaded["default_privacy"], loaded["jev_text_for"]),
                         ({}, "highly_sensitive", []))


class ProfileHomeTests(unittest.TestCase):
    """A gateway that serves several profiles binds each turn's profile with a context-local
    override (hermes_constants) and leaves HERMES_HOME at the root."""

    def test_the_policy_files_are_those_of_the_profile_the_gateway_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "profiles" / "secondbrain"
            fake = types.ModuleType("hermes_constants")
            fake.get_hermes_home_override = lambda: str(home)
            with mock.patch.dict(os.environ, {"HERMES_HOME": tmp, "XDG_CONFIG_HOME": str(root / "config")}), \
                    mock.patch.dict(sys.modules, {"hermes_constants": fake}):
                os.environ.pop("JEV_DISPATCH_POLICY", None)
                self.assertEqual((dispatch.catalog_mod.hermes_home(), dispatch.catalog_mod.hermes_root()), (home, root))
                paths = dispatch.policy_paths()
        self.assertEqual(paths[-2:], [root / "jev" / "dispatch.json", home / "jev" / "dispatch.json"])


class BrokenSettingsTests(unittest.TestCase):
    """A broken entry or file in a later layer turns agents off and privacy to the strictest."""

    SHARED = {"mode": "on", "profiles": {"default": "public"}, "default_privacy": "public",
              "jev_text_for": ["public", "private"],
              "agents": {"openai": {"enabled": True}, "claude": {"enabled": True},
                         "openrouter": {"enabled": True, "model": "x/y"}}}

    def load(self, own):
        """The shared layer above, then `own`: an object is written as JSON, bytes as they are."""
        with tempfile.TemporaryDirectory() as tmp:
            shared, mine = Path(tmp) / "shared.json", Path(tmp) / "own.json"
            shared.write_text(json.dumps(self.SHARED))
            if isinstance(own, bytes):
                mine.write_bytes(own)
            elif own == "a directory":
                mine.mkdir()
            else:
                mine.write_text(json.dumps(own))
            with mock.patch.object(dispatch, "policy_paths", return_value=[shared, mine]):
                return dispatch.load_policy(), str(mine)

    def assert_off(self, loaded, names=("openai", "claude", "openrouter"), why=None):
        for name in names:
            self.assertEqual((loaded["agents"][name]["enabled"], loaded["agents"][name]["privacy"]), (False, []),
                             f"{name} {why!r}")

    def test_an_agent_entry_that_is_not_an_object_turns_that_agent_off(self):
        for broken in (False, None, "off", 0, []):
            loaded, _ = self.load({"agents": {"openai": broken}})
            self.assert_off(loaded, ["openai"], broken)
            self.assertEqual(loaded["agents"]["openai"]["kind"], "codex", broken)
            self.assertIs(loaded["agents"]["claude"]["enabled"], True, broken)

    def test_agents_that_is_not_an_object_turns_every_agent_off(self):
        for broken in (None, [], "none", False):
            loaded, _ = self.load({"agents": broken})
            self.assert_off(loaded, why=broken)

    def test_a_file_that_is_there_but_unreadable_turns_everything_off_and_is_named(self):
        for raw in ('{"mode": "on", "profiles": {"default": "public"}}'.encode("utf-16"), b'{"mode": "on",',
                    b"[1, 2]", b'"on"', b"[" * 100000, "a directory"):
            loaded, path = self.load(raw)
            self.assertEqual(loaded["broken_files"], [path], raw[:20])
            self.assertEqual((loaded["mode"], loaded["profiles"], loaded["default_privacy"], loaded["jev_text_for"]),
                             ("off", {}, "highly_sensitive", []), raw[:20])
            self.assert_off(loaded, why=raw[:20])

    def test_no_file_is_just_no_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "a-file"
            blocker.write_text("x")
            paths = [Path(tmp) / "missing.json", blocker / "dispatch.json"]   # not found; not a directory
            with mock.patch.object(dispatch, "policy_paths", return_value=paths):
                loaded = dispatch.load_policy()
        self.assertEqual((loaded["broken_files"], loaded["mode"]), ([], "off"))
        self.assertEqual(dispatch.load_policy(NOWHERE)["broken_files"], [])

    def test_a_broken_mode_is_off(self):
        loaded, _ = self.load({"mode": ["on"]})
        self.assertEqual(loaded["mode"], "off")

    def test_a_file_cannot_claim_broken_files_of_its_own(self):
        loaded, _ = self.load({"broken_files": ["elsewhere"]})
        self.assertEqual(loaded["broken_files"], [])

    def test_check_names_the_broken_files(self):
        loaded, path = self.load(b"{not json")
        report = dispatch.check_agents(loaded, which=lambda p: None, cooling=NOT_COOLING, has_key=lambda: False)
        self.assertEqual(report["broken_files"], [path])


class DispatchDocsTests(unittest.TestCase):
    """What the docs say about dispatch, held to the code that keeps it."""

    ROOT = Path(__file__).resolve().parents[1]

    def read(self, name):
        import unicodedata
        return unicodedata.normalize("NFC", (self.ROOT / name).read_text(encoding="utf-8"))

    def test_the_built_in_terms_are_named_as_they_are(self):
        import unicodedata
        for name in ("README.md", "docs/receptionist-dispatch.md"):
            text = self.read(name)
            for term in dispatch.DEFAULT_SENSITIVE_TERMS:
                self.assertTrue(f"`{unicodedata.normalize('NFC', term)}`" in text, f"{name}: {term}")
            self.assertFalse("a conversation report, a treatment plan" in text, name)   # English words do not match

    def test_the_readme_links_the_dispatch_doc_from_the_table_and_the_bullet(self):
        readme = self.read("README.md")
        self.assertTrue("| [receptionist-dispatch.md](docs/receptionist-dispatch.md) |" in readme)
        bullet = readme.split("- **Dispatch**", 1)[1].split("\n- **Memory**", 1)[0]
        self.assertIn("(docs/receptionist-dispatch.md)", bullet)
        self.assertIn("\n  - Jev reads", bullet)            # a fourth sub-bullet, not a paragraph inside the list

    def test_the_dispatch_doc_says_the_limits_plainly(self):
        doc = " ".join(self.read("docs/receptionist-dispatch.md").split())
        for said in ("Stop does not interrupt a handed-off turn", "`~/.hermes` included", "keep Codex off",
                     "`jev ladder status` does not list dispatch cooldowns", "jev ladder clear --rung dispatch:openai",
                     "its own `dispatch.json` and its own `/dispatch` switch"):
            self.assertTrue(said in doc, said)

    def test_the_changelog_says_what_a_broken_setting_does(self):
        unreleased = self.read("CHANGELOG.md").split("\n## ", 2)[1]
        self.assertFalse("values of the wrong type are ignored" in unreleased)
        self.assertTrue("turns agents off and privacy to the strictest" in unreleased)
