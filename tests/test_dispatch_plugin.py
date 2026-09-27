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

    def test_a_session_that_failed_is_not_resumed(self):
        self.mode("on")
        self.answer = {**self.answer, "agent": "claude", "session": "sess-7"}
        self.call(turn="t1")
        self.answer = {"agent": "local", "reason": "every agent failed", "downgraded": True, "privacy": "private",
                       "triage": {}, "attempts": [{"agent": "claude", "error": "failed"}]}
        self.call(turn="t2")
        self.call(turn="t3")
        self.assertEqual(self.dispatched[2]["session"], "")

    def test_a_message_made_of_parts_is_handed_over_as_text(self):
        self.mode("shadow")
        plugin._on_pre_llm_call(session_id="s9", turn_id="t1", user_message=[
            {"type": "text", "text": "Vind de race"}, {"type": "image_url", "image_url": {"url": "data:..."}}])
        plugin._on_llm_execution(request=dict(REQUEST), next_call=Next(), session_id="s9", turn_id="t1",
                                 api_mode="chat_completions")
        self.assertEqual(plugin._TURNS["s9"]["text"], "Vind de race\n[image]")

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
