"""Hermetic tests for the dispatch plugin's llm_execution middleware. No Hermes, no agent, no Jev."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types
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


def gateway_profile(home):
    """Hermes's hermes_constants as a multiplexed gateway leaves it: the turn's profile bound
    context-locally, HERMES_HOME left at the root. Patched into sys.modules, so it goes again."""
    fake = types.ModuleType("hermes_constants")
    fake.get_hermes_home_override = lambda: None if home is None else str(home)
    return mock.patch.dict(sys.modules, {"hermes_constants": fake})


class Next:
    def __init__(self):
        self.calls = 0

    def __call__(self, request=None):
        self.calls += 1
        return "LOCAL-RESPONSE"


class FakeCtx:
    """Hermes's plugin context, as far as this plugin asks it anything."""

    def __init__(self, loaded=(), probe=True):
        self.loaded = set(loaded)
        if probe is not True:
            self.has_plugin = probe

    def has_plugin(self, plugin_id):
        return plugin_id in self.loaded

    def get_config(self, name, default=None):
        return default


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
            self.dispatched.append({**kwargs, "text": text, "messages": messages})
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

    def test_a_stale_jev_switch_stands_nothing_aside_once_hermes_jev_is_gone(self):
        self.mode("on")
        state = Path(self.home.name) / "jev" / "state.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"routing": "on"}))
        with mock.patch.object(plugin, "_CTX", FakeCtx(loaded=["hermes-dispatch"])):
            self.assertEqual(self.call(session="gone")[1].calls, 0)
        self.assertEqual(len(self.dispatched), 1)

    def test_while_hermes_jev_is_loaded_or_cannot_be_asked_dispatch_stands_aside(self):
        self.mode("on")
        state = Path(self.home.name) / "jev" / "state.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"routing": "shadow"}))

        def broken(plugin_id):
            raise RuntimeError("no registry")

        for index, ctx in enumerate((FakeCtx(loaded=["hermes-jev"]), FakeCtx(probe=None), FakeCtx(probe=broken))):
            with mock.patch.object(plugin, "_CTX", ctx):
                self.assertEqual(self.call(session=f"s{index}")[1].calls, 1)
        self.assertEqual(self.dispatched, [])

    def test_a_switch_file_that_is_not_utf8_is_no_switch(self):
        self.mode("on")
        for name in ("dispatch-state.json", "state.json"):
            path = Path(self.home.name) / "jev" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"\xff\xfe{")
        self.assertEqual(self.call()[1].calls, 0)
        self.assertEqual(len(self.dispatched), 1)

    def test_a_policy_that_cannot_be_loaded_is_a_local_answer(self):
        with mock.patch.object(plugin.dispatch, "load_policy", side_effect=RuntimeError("boom")):
            result, following = self.call()
        self.assertEqual((result, following.calls, self.dispatched), ("LOCAL-RESPONSE", 1, []))

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

    def test_a_multiplexed_gateway_turn_is_dispatched_as_its_own_profile(self):
        self.mode("on")
        with gateway_profile(Path(self.home.name) / "profiles" / "secondbrain"):
            self.call()
        self.assertEqual(self.dispatched[0]["profile"], "secondbrain")

    def test_updating_a_tracked_session_evicts_no_other(self):
        for index in range(plugin._MAX_SESSIONS):
            plugin._on_pre_llm_call(session_id=f"s{index}", turn_id="t1", user_message="hoi")
        plugin._on_pre_llm_call(session_id="s5", turn_id="t2", user_message="hoi")
        self.assertEqual(len(plugin._TURNS), plugin._MAX_SESSIONS)
        self.assertIn("s0", plugin._TURNS)
        plugin._on_pre_llm_call(session_id="new", turn_id="t1", user_message="hoi")    # the oldest goes
        self.assertEqual(("s0" in plugin._TURNS, "s1" in plugin._TURNS, "s5" in plugin._TURNS), (False, True, True))

    def test_a_session_compression_rotated_still_dispatches_its_turn_once(self):
        """Preflight compression can rotate session_id between pre_llm_call and the first provider
        call. Hermes's turn id holds a uuid, so the turn is found by it."""
        self.mode("on")
        turn = "s1:task-9:0f3a9c2e"
        plugin._on_pre_llm_call(session_id="s1", turn_id=turn, user_message=REQUEST["messages"][-1]["content"],
                                platform="telegram")
        following = Next()
        result = plugin._on_llm_execution(request=dict(REQUEST), next_call=following, session_id="s1-child",
                                          turn_id=turn, api_mode="chat_completions")
        self.assertEqual((following.calls, len(self.dispatched)), (0, 1))
        self.assertEqual(result.choices[0].message.content, self.answer["text"])
        again = Next()
        plugin._on_llm_execution(request=dict(REQUEST), next_call=again, session_id="s1-child", turn_id=turn,
                                 api_mode="chat_completions")
        self.assertEqual((again.calls, len(self.dispatched)), (1, 1))

    DOWNGRADED = {"agent": "local", "reason": "no frontier agent may take this turn; this machine answers",
                  "downgraded": True, "privacy": "private", "triage": {}, "attempts": []}

    def test_the_notice_prefixes_a_downgraded_local_answer_once(self):
        self.mode("on")
        self.policy["notice"] = "on"
        self.answer = dict(self.DOWNGRADED)
        self.call()
        first = plugin._on_transform_output(response_text="Lokaal antwoord.", session_id="s1", turn_id="t1")
        self.assertEqual(first, f"[dispatch] {self.DOWNGRADED['reason']}\n\nLokaal antwoord.")
        self.assertIsNone(plugin._on_transform_output(response_text="Lokaal antwoord.", session_id="s1", turn_id="t1"))

    def test_no_notice_when_it_is_off_or_nothing_was_downgraded(self):
        self.mode("on")
        self.answer = dict(self.DOWNGRADED)
        self.call()
        self.assertIsNone(plugin._on_transform_output(response_text="x", session_id="s1", turn_id="t1"))
        self.policy["notice"] = "on"
        self.answer = {**self.DOWNGRADED, "downgraded": False}
        self.call(turn="t2")
        self.assertIsNone(plugin._on_transform_output(response_text="x", session_id="s1", turn_id="t2"))

    def test_the_notice_finds_a_turn_whose_session_compression_rotated(self):
        self.mode("on")
        self.policy["notice"] = "on"
        self.answer = dict(self.DOWNGRADED)
        plugin._on_pre_llm_call(session_id="s1", turn_id="s1:task:9e", user_message="Find the race")
        plugin._on_llm_execution(request=dict(REQUEST), next_call=Next(), session_id="s1-child",
                                 turn_id="s1:task:9e", api_mode="chat_completions")
        noticed = plugin._on_transform_output(response_text="x", session_id="s1-child", turn_id="s1:task:9e")
        self.assertEqual(noticed, f"[dispatch] {self.DOWNGRADED['reason']}\n\nx")

    def test_another_turn_id_is_never_taken_for_this_one(self):
        self.mode("on")
        plugin._on_pre_llm_call(session_id="s1", turn_id="s1:a:1", user_message="Find the race")
        following = Next()
        plugin._on_llm_execution(request=dict(REQUEST), next_call=following, session_id="s2", turn_id="s2:b:2",
                                 api_mode="chat_completions")
        self.assertEqual((following.calls, self.dispatched), (1, []))

    def history_turn(self, history, wire):
        """One turn as Hermes runs it: pre_llm_call with the history, then the wire request."""
        plugin._on_pre_llm_call(session_id="s1", turn_id="t1", user_message="Find the race in the scheduler",
                                conversation_history=history, platform="telegram")
        plugin._on_llm_execution(request={"model": "qwen36", "messages": wire}, next_call=Next(), session_id="s1",
                                 turn_id="t1", api_mode="chat_completions")
        return self.dispatched[-1]

    def test_the_handoff_history_is_the_clean_conversation_not_the_wire_copy(self):
        self.mode("on")
        recalled = "Kijk naar de scheduler\n<memory-context>Sander woont in Utrecht</memory-context>"
        history = [{"role": "user", "content": "[CONTEXT COMPACTION] tool output of earlier work",
                    "_compressed_summary": True},
                   "not a row",
                   {"role": "user", "content": "Kijk naar de scheduler", "api_content": recalled},
                   {"role": "assistant", "content": "Welke scheduler?"},
                   {"role": "user", "content": "Find the race in the scheduler"}]
        wire = [{"role": "user", "content": recalled}, {"role": "assistant", "content": "Welke scheduler?"},
                {"role": "user", "content": "Find the race in the scheduler\n\n[Jev skill suggestion] x"}]
        sent = self.history_turn(history, wire)
        self.assertIn("Utrecht", json.dumps(wire))
        for leaked in ("memory-context", "Utrecht", "CONTEXT COMPACTION", "skill suggestion", "not a row"):
            self.assertNotIn(leaked, json.dumps(sent["messages"]), leaked)
        self.assertEqual(sent["messages"][-1], {"role": "user", "content": "Find the race in the scheduler"})
        self.assertIn({"role": "user", "content": "Kijk naar de scheduler"}, sent["messages"])
        self.assertEqual(sent["context_tokens"], len(json.dumps(wire)) // 4)        # still the wire's size

    def test_without_a_history_the_wire_is_all_there_is_less_what_hermes_injected(self):
        self.mode("on")
        self.call()
        self.assertEqual(self.dispatched[0]["messages"], REQUEST["messages"])
        wire = [{"role": "system", "content": "You are Hermes."},
                {"role": "user", "content": "Kijk naar de scheduler\n\n<memory-context>Utrecht</memory-context>"},
                {"role": "assistant", "content": "Welke?  "}, {"role": "user", "content": "Find the race"}]
        plugin._on_pre_llm_call(session_id="s2", turn_id="t1", user_message="Find the race")
        plugin._on_llm_execution(request={"messages": wire}, next_call=Next(), session_id="s2", turn_id="t1",
                                 api_mode="chat_completions")
        self.assertEqual(self.dispatched[1]["messages"], [
            {"role": "user", "content": "Kijk naar de scheduler"}, {"role": "assistant", "content": "Welke?  "},
            {"role": "user", "content": "Find the race"}])

    def test_a_history_that_compaction_emptied_is_the_turn_alone(self):
        self.mode("on")
        merged = {"role": "user", "content": "[CONTEXT COMPACTION] summary\n\nFind the race in the scheduler",
                  "_compressed_summary": True}
        sent = self.history_turn([merged], [{"role": "user", "content": merged["content"]}])
        self.assertEqual(sent["messages"], [{"role": "user", "content": "Find the race in the scheduler"}])

    def test_context_hermes_adds_to_a_multimodal_row_stays_behind(self):
        """A turn made of parts keeps its injected context as a text part of its own (Hermes #71998),
        in the stored row, and Hermes appends it to the live row after pre_llm_call."""
        self.mode("on")
        earlier = {"role": "user", "content": [
            {"type": "text", "text": "Wat staat hier?"}, {"type": "image_url", "image_url": {"url": "data:..."}},
            {"type": "text", "text": "<memory-context>Sander woont in Utrecht</memory-context>\n\n[plugin]"}]}
        current = {"role": "user", "content": [{"type": "text", "text": "Find the race in the scheduler"}]}
        plugin._on_pre_llm_call(session_id="s1", turn_id="t1", user_message="Find the race in the scheduler",
                                conversation_history=[earlier, {"role": "assistant", "content": "Een grafiek."},
                                                      current], platform="telegram")
        current["content"].append({"type": "text", "text": "[Jev skill suggestion] added later"})
        plugin._on_llm_execution(request=dict(REQUEST), next_call=Next(), session_id="s1", turn_id="t1",
                                 api_mode="chat_completions")
        sent = json.dumps(self.dispatched[0]["messages"])
        for leaked in ("Utrecht", "[plugin]", "added later"):
            self.assertNotIn(leaked, sent, leaked)
        self.assertIn("Wat staat hier?", sent)

    def test_a_multimodal_row_read_back_as_text_loses_its_context_too(self):
        """Stored, a turn made of parts is its text projection, injected part included."""
        self.mode("on")
        stored = {"role": "user", "content": "Wat staat hier?\n[screenshot]\n<memory-context>\n[System note]\n\n"
                                             "Sander woont in Utrecht\n</memory-context>\n\n[Handoff from the "
                                             "previous session]\n\nHet plan voor Merel"}
        sent = self.history_turn([stored, {"role": "assistant", "content": "Een grafiek."},
                                  {"role": "user", "content": "Find the race in the scheduler"}], [])
        self.assertEqual(sent["messages"][0], {"role": "user", "content": "Wat staat hier?\n[screenshot]"})
        self.assertNotIn("Merel", json.dumps(sent["messages"]))


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

    def test_a_multiplexed_gateway_turn_is_its_own_profile(self):
        home = Path(self.home.name) / "profiles" / "secondbrain"
        with gateway_profile(home):
            self.assertEqual(plugin._profile(), "secondbrain")
            self.assertIn("for secondbrain", plugin._dispatch_command("on"))
        self.assertEqual(json.loads((home / "jev" / "dispatch-state.json").read_text())["mode"], "on")
        self.assertFalse((Path(self.home.name) / "jev" / "dispatch-state.json").exists())

    def test_without_an_override_hermes_home_is_the_profile(self):
        with mock.patch.dict(os.environ, {"HERMES_HOME": str(Path(self.home.name) / "profiles" / "coding")}):
            with gateway_profile(None):
                self.assertEqual(plugin._profile(), "coding")
            with mock.patch.dict(sys.modules, {"hermes_constants": types.ModuleType("hermes_constants")}):
                self.assertEqual(plugin._profile(), "coding")          # an older Hermes: no override at all

    def test_status_names_every_agent(self):
        report = {"mode": "off", "profiles": {}, "policy_files": [],
                  "agents": {"openai": {"kind": "codex", "enabled": False, "model": "", "privacy": [],
                                        "available": True, "cooling_s": 0}}}
        with mock.patch.object(plugin.dispatch, "check_agents", return_value=report), \
                mock.patch.object(plugin.dispatch, "load_policy", return_value=plugin.dispatch.load_policy(Path("/x"))):
            text = plugin._dispatch_command("")
        self.assertIn("openai", text)
        self.assertIn("usage: /dispatch", text)

    def test_status_names_a_settings_file_it_could_not_read(self):
        with open(Path(self.home.name) / "dispatch.json", "wb") as handle:
            handle.write(b"\xff\xfe{")
        with mock.patch.dict(os.environ, {"JEV_DISPATCH_POLICY": str(Path(self.home.name) / "dispatch.json"),
                                          "JEV_LADDER_STATE": str(Path(self.home.name) / "ladder.json")}), \
                mock.patch.object(plugin.dispatch.shutil, "which", return_value=None), \
                mock.patch.object(plugin.dispatch.keystore, "resolve", return_value=None):
            text = plugin._dispatch_command("")
        broken = [line for line in text.splitlines() if "dispatch.json" in line]
        self.assertEqual(len(broken), 1, text)
        self.assertIn("every agent off", broken[0])
