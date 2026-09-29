"""Tests for dispatch_store: reads and writes everything the hermes-dispatch plugin reads.

Hermetic: XDG_CONFIG_HOME, JEV_LADDER_STATE and HERMES_HOME all point into a temporary
directory, no real key is ever findable, and no agent is ever really run (fake runners only).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dispatch_store as ds  # noqa: E402
import routing_store  # noqa: E402
from jevkit import dispatch, ladder  # noqa: E402


class LlmExecutionAvailabilityTests(unittest.TestCase):
    """`plugin.llm_execution`: true, unknown (null) or false, never a crash either way."""

    def test_true_when_the_middleware_import_succeeds(self):
        middleware = types.ModuleType("hermes_cli.middleware")
        middleware.LLM_EXECUTION_MIDDLEWARE = object()
        package = types.ModuleType("hermes_cli")
        with mock.patch.dict(sys.modules, {"hermes_cli": package, "hermes_cli.middleware": middleware}):
            self.assertIs(ds._llm_execution_available(), True)

    def test_false_when_the_import_raises_something_else(self):
        def raiser(name):
            raise RuntimeError("boom")

        middleware = types.ModuleType("hermes_cli.middleware")
        middleware.__getattr__ = raiser  # a module-level __getattr__ (PEP 562), just for this test
        package = types.ModuleType("hermes_cli")
        with mock.patch.dict(sys.modules, {"hermes_cli": package, "hermes_cli.middleware": middleware}):
            self.assertIs(ds._llm_execution_available(), False)

    def test_none_when_hermes_is_not_installed(self):
        with mock.patch.dict(sys.modules, {}, clear=False):
            sys.modules.pop("hermes_cli", None)
            sys.modules.pop("hermes_cli.middleware", None)
            self.assertIsNone(ds._llm_execution_available())


class DispatchStoreTests(unittest.TestCase):
    """A temporary Hermes home: a root config.yaml and profiles/wiki/config.yaml."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = self.tmp.name
        os.makedirs(os.path.join(self.home, "profiles", "wiki"), exist_ok=True)
        self.write_config("default")
        self.write_config("wiki")

        env = mock.patch.dict(os.environ, {
            "XDG_CONFIG_HOME": os.path.join(self.home, "xdg"),
            "JEV_LADDER_STATE": os.path.join(self.home, "jev", "ladder.json"),
            "HERMES_HOME": self.home,
        })
        env.start()
        self.addCleanup(env.stop)
        for name in ("JEV_DISPATCH_POLICY", "OPENROUTER_API_KEY", "TYPESAFE_API_KEY"):
            os.environ.pop(name, None)

        # This sandbox's own `claude` on PATH must never leak into a check_agents() result.
        which_patcher = mock.patch("shutil.which", return_value=None)
        which_patcher.start()
        self.addCleanup(which_patcher.stop)

        # state() calls check_agents with its default has_key, which calls keystore.resolve():
        # on macOS that would shell out to the real Keychain. Never real, whatever the machine.
        keystore_patcher = mock.patch.object(dispatch.keystore, "resolve", return_value=None)
        keystore_patcher.start()
        self.addCleanup(keystore_patcher.stop)

    # -- fixture helpers ---------------------------------------------------
    def _profile_home(self, name):
        return self.home if name == "default" else os.path.join(self.home, "profiles", name)

    def write_config(self, name, *, enabled=(), settings=None, legacy=None):
        """A profile's config.yaml: plugins.enabled and plugins.entries.<plugin>.settings,
        or the legacy `.config` section - the shapes hermes-dispatch and hermes-jev read."""
        data = {"model": {"provider": "openrouter", "default": "x"}}
        if enabled or settings or legacy:
            entries = {}
            for plugin, values in (settings or {}).items():
                entries.setdefault(plugin, {})["settings"] = values
            for plugin, values in (legacy or {}).items():
                entries.setdefault(plugin, {})["config"] = values
            data["plugins"] = {"enabled": list(enabled)}
            if entries:
                data["plugins"]["entries"] = entries
        home = self._profile_home(name)
        os.makedirs(home, exist_ok=True)
        with open(os.path.join(home, "config.yaml"), "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh)

    def write_fleet(self, data):
        path = os.path.join(self.home, "jev", "dispatch.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return path

    def write_profile_dispatch(self, name, data):
        path = os.path.join(self._profile_home(name), "jev", "dispatch.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return path

    def write_state(self, name, data):
        path = os.path.join(self._profile_home(name), "jev", "dispatch-state.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return path

    def write_jev_state(self, name, data):
        path = os.path.join(self._profile_home(name), "jev", "state.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return path

    def log(self, name, *rows):
        path = os.path.join(self._profile_home(name), "logs", "jev-decisions.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    def runner(self, name, error=None, text="ok"):
        """A fake codex or claude: an answer, or a failure with `error` on stderr."""
        def run(argv, stdin_text, timeout):
            if error is not None:
                return subprocess.CompletedProcess(argv, 1, "", error)
            if name == "codex":
                Path(argv[argv.index("--output-last-message") + 1]).write_text(text, encoding="utf-8")
                return subprocess.CompletedProcess(argv, 0, "", "")
            body = json.dumps({"type": "result", "is_error": False, "result": text, "session_id": "s-1"})
            return subprocess.CompletedProcess(argv, 0, body, "")
        return run

    # -- state(): the empty-home shape --------------------------------------
    def test_state_shape_on_an_empty_home(self):
        out = ds.state(self.home)
        self.assertEqual(out["fleet_file"], os.path.join(self.home, "jev", "dispatch.json"))
        self.assertEqual(out["fleet_file_state"], "missing")
        self.assertEqual(out["plugin"], {"installed": False, "llm_execution": None})
        self.assertEqual(out["default_privacy"], "highly_sensitive")
        self.assertEqual(out["order"], {"repo": ["claude", "openai"], "default": ["openai", "claude"]})
        self.assertFalse(out["openrouter_key"])
        self.assertEqual(set(out["agents"]), {"claude", "openai", "openrouter"})
        for name in ds.AGENTS:
            row = out["agents"][name]
            self.assertEqual((row["enabled"], row["model"], row["available"], row["cooling_s"]),
                             (False, "", False, 0))
        self.assertIs(out["agents"]["claude"]["only_repo"], True)
        self.assertNotIn("only_repo", out["agents"]["openai"])
        self.assertNotIn("only_repo", out["agents"]["openrouter"])

        self.assertEqual(set(out["profiles"]), {"default", "wiki"})
        default_source = {"value": "off", "source": "default"}
        for name in ("default", "wiki"):
            profile = out["profiles"][name]
            self.assertEqual(profile["mode"], default_source)
            self.assertEqual(profile["notice"], default_source)
            self.assertEqual(profile["privacy"], {"value": "highly_sensitive", "source": "default"})
            self.assertEqual(profile["jev_routing"], "off")
            self.assertFalse(profile["routing_stands_aside"])
            self.assertEqual((profile["warnings"], profile["jev_route"]), ([], "absent"))
            self.assertEqual(profile["receptionist"]["model"], "x")
            self.assertFalse(profile["plugin_enabled"])
            self.assertEqual(profile["broken_files"], [])
        self.assertEqual(out["profiles"]["default"]["home"], self.home)
        self.assertEqual(out["profiles"]["wiki"]["home"], os.path.join(self.home, "profiles", "wiki"))
        self.assertEqual(len(out["profiles"]["default"]["policy_files"]), 2)
        self.assertEqual(len(out["profiles"]["wiki"]["policy_files"]), 3)

    def test_plugin_installed_reports_the_directory(self):
        self.assertFalse(ds.state(self.home)["plugin"]["installed"])
        os.makedirs(os.path.join(self.home, "plugins", "hermes-dispatch"))
        self.assertTrue(ds.state(self.home)["plugin"]["installed"])

    def test_state_reports_llm_execution_true_when_available(self):
        middleware = types.ModuleType("hermes_cli.middleware")
        middleware.LLM_EXECUTION_MIDDLEWARE = object()
        package = types.ModuleType("hermes_cli")
        with mock.patch.dict(sys.modules, {"hermes_cli": package, "hermes_cli.middleware": middleware}):
            self.assertIs(ds.state(self.home)["plugin"]["llm_execution"], True)

    def test_openrouter_key_present_when_keystore_resolves_one(self):
        with mock.patch.object(dispatch.keystore, "resolve", return_value="a-fake-value-not-a-real-key"):
            out = ds.state(self.home)
        self.assertTrue(out["openrouter_key"])
        self.assertTrue(out["agents"]["openrouter"]["available"])

    def test_keystore_is_patched_so_state_never_queries_a_real_secret_store(self):
        """Without this, state()'s default has_key would shell out to the real macOS Keychain."""
        self.assertIsNone(dispatch.keystore.resolve("openrouter"))
        self.assertFalse(ds.state(self.home)["openrouter_key"])

    # -- mode/notice sources: dashboard beats config.yaml beats dispatch.json -----
    def test_mode_source_climbs_from_dispatch_json_to_config_yaml_to_the_state_file(self):
        self.write_fleet({"mode": "shadow"})
        self.assertEqual(ds.state(self.home)["profiles"]["default"]["mode"],
                         {"value": "shadow", "source": "dispatch.json"})

        self.write_config("default", enabled=["hermes-dispatch"], settings={"hermes-dispatch": {"mode": "on"}})
        self.assertEqual(ds.state(self.home)["profiles"]["default"]["mode"],
                         {"value": "on", "source": "config.yaml"})

        self.write_state("default", {"mode": "off"})
        self.assertEqual(ds.state(self.home)["profiles"]["default"]["mode"],
                         {"value": "off", "source": "dashboard"})

    def test_notice_source_dispatch_json_is_a_top_level_key(self):
        self.write_fleet({"notice": "on"})
        self.assertEqual(ds.state(self.home)["profiles"]["default"]["notice"],
                         {"value": "on", "source": "dispatch.json"})

    def test_legacy_config_section_is_read_when_settings_is_absent(self):
        self.write_config("default", enabled=["hermes-dispatch"], legacy={"hermes-dispatch": {"mode": "shadow"}})
        self.assertEqual(ds.state(self.home)["profiles"]["default"]["mode"],
                         {"value": "shadow", "source": "config.yaml"})

    def test_mode_value_reflects_a_broken_later_layer_resetting_it(self):
        """load_policy resets `mode` to "off" when a later layer cannot be read at all. The value
        shown must match that reset, even if a label of "dispatch.json" is still fair (something
        in that chain did try to set it)."""
        self.write_fleet({"mode": "on"})
        broken = os.path.join(self.home, "profiles", "wiki", "jev", "dispatch.json")
        os.makedirs(os.path.dirname(broken), exist_ok=True)
        Path(broken).write_text("{not json", encoding="utf-8")

        wiki_paths = dispatch.policy_paths(root=Path(self.home), home=Path(self.home) / "profiles" / "wiki")
        self.assertEqual(dispatch.load_policy(paths=wiki_paths)["mode"], "off")  # what the plugin itself would use
        self.assertEqual(ds.state(self.home)["profiles"]["wiki"]["mode"]["value"], "off")

    def test_a_null_mode_in_the_state_file_falls_through(self):
        """{"mode": null} is not a value the dashboard set; it must fall through like a missing key,
        the way the plugin's own `_setting` does (`value = ...get(name); if value is None: ...`)."""
        self.write_state("default", {"mode": None})
        self.assertEqual(ds.state(self.home)["profiles"]["default"]["mode"], {"value": "off", "source": "default"})

    def test_privacy_source_is_dispatch_json_only_when_the_profile_is_listed(self):
        self.write_fleet({"profiles": {"wiki": "public"}})
        out = ds.state(self.home)
        self.assertEqual(out["profiles"]["wiki"]["privacy"], {"value": "public", "source": "dispatch.json"})
        self.assertEqual(out["profiles"]["default"]["privacy"], {"value": "highly_sensitive", "source": "default"})

    def test_plugin_enabled_reflects_that_profiles_own_config_yaml(self):
        self.assertFalse(ds.state(self.home)["profiles"]["wiki"]["plugin_enabled"])
        self.write_config("wiki", enabled=["hermes-dispatch"])
        self.assertTrue(ds.state(self.home)["profiles"]["wiki"]["plugin_enabled"])
        self.assertFalse(ds.state(self.home)["profiles"]["default"]["plugin_enabled"])  # its own file, untouched

    def test_jev_routing_falls_back_to_the_root_state_file(self):
        self.write_jev_state("default", {"routing": "shadow"})
        self.assertEqual(ds.state(self.home)["profiles"]["wiki"]["jev_routing"], "shadow")

    def test_jev_routing_falls_back_to_config_yaml_when_no_state_file_sets_it(self):
        self.write_config("wiki", enabled=["hermes-jev"], settings={"hermes-jev": {"routing": "on"}})
        self.assertEqual(ds.state(self.home)["profiles"]["wiki"]["jev_routing"], "on")

    # -- the front desk goes first ------------------------------------------
    def test_routing_stands_aside_where_the_front_desk_decides(self):
        self.write_jev_state("wiki", {"routing": "on"})
        self.write_config("wiki", enabled=["hermes-jev", "hermes-dispatch"])
        self.assertFalse(ds.state(self.home)["profiles"]["wiki"]["routing_stands_aside"])     # front desk off
        self.write_state("wiki", {"mode": "shadow"})
        profile = ds.state(self.home)["profiles"]["wiki"]
        self.assertTrue(profile["routing_stands_aside"])
        self.assertNotIn("conflict", profile)

    def test_a_front_desk_that_would_leave_every_turn_here_says_why(self):
        self.write_state("wiki", {"mode": "on"})
        self.assertEqual(ds.state(self.home)["profiles"]["wiki"]["warnings"],
                         ["privacy_only_here", "no_agent", "no_jev_key"])
        self.write_fleet({"profiles": {"wiki": "private"}, "agents": {"claude": {"enabled": True}}})
        with open(os.path.join(self._profile_home("wiki"), ".env"), "w", encoding="utf-8") as fh:
            fh.write("OPENROUTER_API_KEY=" + "k" * 32 + "\n")
        self.assertEqual(ds.state(self.home)["profiles"]["wiki"]["warnings"], ["features_only"])

    def test_a_chatgpt_receptionist_cannot_hand_over_yet(self):
        self.write_state("wiki", {"mode": "shadow"})
        with open(os.path.join(self._profile_home("wiki"), "config.yaml"), "w", encoding="utf-8") as fh:
            fh.write("model:\n  provider: openai-codex\n  default: gpt-5.5\n")
        self.assertIn("no_handover", ds.state(self.home)["profiles"]["wiki"]["warnings"])

    # -- set_switch ------------------------------------------------------------
    def test_set_switch_for_one_profile_writes_only_that_file(self):
        result = ds.set_switch(self.home, "wiki", "mode", "shadow")
        self.assertEqual((result["ok"], result["scope"], result["switch"], result["value"]),
                         (True, "wiki", "mode", "shadow"))
        self.assertEqual(result["profiles"]["wiki"]["mode"], {"value": "shadow", "source": "dashboard"})
        self.assertEqual(result["profiles"]["default"]["mode"], {"value": "off", "source": "default"})
        self.assertFalse(os.path.exists(os.path.join(self.home, "jev", "dispatch-state.json")))

    def test_set_switch_all_writes_every_profile_and_keeps_other_keys(self):
        self.write_state("default", {"notice": "on"})
        result = ds.set_switch(self.home, "__all__", "mode", "on")
        self.assertEqual(result["profiles"]["default"]["mode"], {"value": "on", "source": "dashboard"})
        self.assertEqual(result["profiles"]["wiki"]["mode"], {"value": "on", "source": "dashboard"})
        with open(os.path.join(self.home, "jev", "dispatch-state.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"notice": "on", "mode": "on"})

    def test_set_switch_rejects_bad_names_values_and_scopes(self):
        with self.assertRaises(ValueError):
            ds.set_switch(self.home, "default", "bogus", "on")
        with self.assertRaises(ValueError):
            ds.set_switch(self.home, "default", "mode", "maybe")
        with self.assertRaises(ValueError):
            ds.set_switch(self.home, "ghost", "mode", "on")

    def test_set_switch_rejects_non_string_name_and_scope_instead_of_crashing(self):
        with self.assertRaises(ValueError):
            ds.set_switch(self.home, "default", ["mode"], "on")
        with self.assertRaises(ValueError):
            ds.set_switch(self.home, ["default"], "mode", "on")

    def test_set_switch_bad_name_message_names_the_allowed_switches(self):
        with self.assertRaises(ValueError) as cm:
            ds.set_switch(self.home, "default", "bogus", "on")
        self.assertIn("mode", str(cm.exception))
        self.assertIn("notice", str(cm.exception))

    # -- plan / apply ------------------------------------------------------------
    def test_plan_writes_nothing(self):
        result = ds.plan(self.home, {"agents": {"claude": {"enabled": True, "model": "opus"}}})
        self.assertFalse(os.path.exists(os.path.join(self.home, "jev", "dispatch.json")))
        self.assertIn({"setting": "agents.claude.enabled", "before": False, "after": True}, result["rows"])
        self.assertIn({"setting": "agents.claude.model", "before": "", "after": "opus"}, result["rows"])
        parsed = json.loads(result["text"])
        self.assertTrue(parsed["agents"]["claude"]["enabled"])
        self.assertEqual(result["text"], json.dumps(parsed, indent=2, sort_keys=True))

    def test_plan_gives_no_row_for_a_change_to_the_same_value(self):
        self.write_fleet({"agents": {"claude": {"enabled": True}}})
        result = ds.plan(self.home, {"agents": {"claude": {"enabled": True}}})
        self.assertEqual(result["rows"], [])

    def write_xdg_fleet(self, data):
        """A dispatch.json at the shared, machine-wide XDG layer: the fleet file never mentions
        these keys at all, but they are still part of what the dashboard shows as `state()`."""
        path = os.path.join(self.home, "xdg", "jev", "dispatch.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return path

    def test_plan_agent_before_reflects_the_effective_root_view_not_just_the_fleet_file(self):
        self.write_xdg_fleet({"agents": {"openai": {"enabled": True}}})
        self.assertTrue(ds.state(self.home)["agents"]["openai"]["enabled"])  # what the dashboard already shows

        result = ds.plan(self.home, {"agents": {"openai": {"enabled": False}}})
        self.assertIn({"setting": "agents.openai.enabled", "before": True, "after": False}, result["rows"])

        receipt = ds.apply(self.home, {"agents": {"openai": {"enabled": False}}})
        self.assertEqual(receipt["changed"], 1)
        self.assertFalse(ds.state(self.home)["agents"]["openai"]["enabled"])

    def test_plan_profile_before_reflects_the_effective_root_view(self):
        self.write_xdg_fleet({"profiles": {"wiki": "public"}})
        # Asking for the value it already effectively has must be a no-op, not a spurious row.
        self.assertEqual(ds.plan(self.home, {"profiles": {"wiki": "public"}})["rows"], [])
        result = ds.plan(self.home, {"profiles": {"wiki": "private"}})
        self.assertIn({"setting": "profiles.wiki", "before": "public", "after": "private"}, result["rows"])

    def test_plan_order_before_reflects_the_effective_root_view(self):
        self.write_xdg_fleet({"frontier_order": {"repo": ["openai", "claude"]}})
        self.assertEqual(ds.plan(self.home, {"order": {"repo": ["openai", "claude"]}})["rows"], [])
        result = ds.plan(self.home, {"order": {"repo": ["claude", "openai"]}})
        self.assertIn({"setting": "frontier_order.repo", "before": ["openai", "claude"],
                       "after": ["claude", "openai"]}, result["rows"])

    def test_apply_writes_backs_up_verifies_and_keeps_unknown_keys(self):
        path = self.write_fleet({"turn_budget": 111, "sensitive_terms": ["x"],
                                 "agents": {"claude": {"enabled": False, "argv": ["claude", "-p"]}}})
        before_text = Path(path).read_text(encoding="utf-8")
        receipt = ds.apply(self.home, {"agents": {"claude": {"enabled": True}}})
        self.assertTrue(receipt["ok"])
        self.assertTrue(receipt["verified"])
        self.assertEqual(receipt["changed"], 1)
        self.assertEqual(receipt["mismatches"], [])
        self.assertEqual(receipt["overridden"], [])
        self.assertEqual(receipt["message"], "applied and verified; takes effect on the next message, no restart")
        self.assertTrue(os.path.isfile(receipt["backup"]))
        self.assertIn("backups", receipt["backup"])
        self.assertIn("model-routing-dashboard", receipt["backup"])
        self.assertEqual(Path(receipt["backup"]).read_text(encoding="utf-8"), before_text)
        after = json.loads(Path(path).read_text(encoding="utf-8"))
        self.assertEqual(after["turn_budget"], 111)
        self.assertEqual(after["sensitive_terms"], ["x"])
        self.assertEqual(after["agents"]["claude"]["argv"], ["claude", "-p"])
        self.assertTrue(after["agents"]["claude"]["enabled"])

    def test_apply_with_nothing_to_change(self):
        self.write_fleet({"agents": {"claude": {"enabled": True}}})
        receipt = ds.apply(self.home, {"agents": {"claude": {"enabled": True}}})
        self.assertEqual((receipt["ok"], receipt["changed"], receipt["backup"], receipt["message"]),
                         (True, 0, None, "nothing to change"))

    def test_refusals(self):
        cases = {
            "broken fleet file": {"agents": {"claude": {"enabled": True}}},
            "class geheim": {"profiles": {"default": "geheim"}},
            "model -x": {"agents": {"openai": {"model": "-x"}}},
            "enabled as a string": {"agents": {"openai": {"enabled": "true"}}},
            "order with a repeat": {"order": {"repo": ["claude", "claude"]}},
            "order with an unhashable entry": {"order": {"repo": [["claude"], "openai"]}},
            "an unknown profile": {"profiles": {"ghost": "public"}},
            "only_repo on openai": {"agents": {"openai": {"only_repo": True}}},
        }
        for label, changes in cases.items():
            with self.subTest(label=label):
                fleet_path = os.path.join(self.home, "jev", "dispatch.json")
                if label == "broken fleet file":
                    os.makedirs(os.path.dirname(fleet_path), exist_ok=True)
                    with open(fleet_path, "w", encoding="utf-8") as fh:
                        fh.write("{not json")
                elif os.path.exists(fleet_path):
                    os.remove(fleet_path)
                with self.assertRaises(ValueError):
                    ds.plan(self.home, changes)
                with self.assertRaises(ValueError):
                    ds.apply(self.home, changes)

    def test_broken_fleet_file_is_never_overwritten(self):
        path = self.write_fleet({})
        Path(path).write_text("{not json", encoding="utf-8")
        with self.assertRaises(ValueError) as cm:
            ds.apply(self.home, {"agents": {"claude": {"enabled": True}}})
        self.assertIn(path, str(cm.exception))

    def test_plan_tolerates_a_malformed_agent_entry_in_the_fleet_file(self):
        """`{"agents": {"claude": true}}` is a shape load_policy already tolerates (as "off");
        plan() must not crash trying to treat that value as a dict of settings."""
        self.write_fleet({"agents": {"claude": True}})
        result = ds.plan(self.home, {"agents": {"claude": {"enabled": True}}})
        self.assertIn({"setting": "agents.claude.enabled", "before": False, "after": True}, result["rows"])

    def test_overridden_reports_a_profile_that_disagrees_with_the_fleet(self):
        wiki_path = self.write_profile_dispatch("wiki", {"agents": {"claude": {"enabled": False}}})
        receipt = ds.apply(self.home, {"agents": {"claude": {"enabled": True}}})
        self.assertEqual(receipt["overridden"],
                         [{"profile": "wiki", "setting": "agents.claude.enabled", "file": wiki_path}])

    # -- live --------------------------------------------------------------------
    def test_live_returns_only_dispatch_rows_reduced_to_decision_fields(self):
        self.log("default",
                 {"ts": 1, "kind": "route", "tier": "medium", "model": "m"},
                 {"ts": 2, "kind": "dispatch", "mode": "on", "live": True, "agent": "openai", "model": "gpt-6",
                  "reason": "frontier work", "downgraded": False, "privacy": "private", "privacy_why": "profile x",
                  "would_send_chars": 42, "triage": {"niveau": "frontier", "type": "CHANGE"},
                  "attempts": [{"agent": "openai", "error": "quota", "extra": "nope"}],
                  "chat_model": "qwen3.5:4b", "api_mode": "chat_completions", "handed_over": True,
                  "jev": {"call": "called", "latency_ms": 412, "via": "openrouter", "read": "text", "tier": "hard",
                          "specialty": "coding", "confidence": 0.9, "prompt": "must never appear"},
                  "text": "must never appear", "session": "must never appear", "prompt": "must never appear"})
        out = ds.live(self.home)
        self.assertEqual(len(out["events"]), 1)
        event = out["events"][0]
        self.assertEqual(event, {"ts": 2, "profile": "default", "mode": "on", "live": True, "agent": "openai",
                                 "model": "gpt-6", "reason": "frontier work", "downgraded": False,
                                 "privacy": "private", "privacy_why": "profile x", "would_send_chars": 42,
                                 "chat_model": "qwen3.5:4b", "api_mode": "chat_completions", "handed_over": True,
                                 "niveau": "frontier", "source": None,
                                 "jev": {"call": "called", "latency_ms": 412, "via": "openrouter", "read": "text",
                                         "tier": "hard", "specialty": "coding", "confidence": 0.9},
                                 "outcome": "agent", "attempts": [{"agent": "openai", "error": "quota"}]})
        blob = json.dumps(out)
        for leaked in ("must never appear", "type", "CHANGE", "extra", "nope"):
            self.assertNotIn(leaked, blob)

    def test_live_names_who_answered_and_flags_a_silent_receptionist(self):
        rows = [
            {"ts": 1, "kind": "dispatch", "mode": "on", "live": True, "handed_over": True, "agent": "claude",
             "jev": {"call": "called", "tier": "hard"}},
            {"ts": 2, "kind": "dispatch", "mode": "shadow", "agent": "claude", "jev": {"call": "called"}},
            {"ts": 3, "kind": "dispatch", "mode": "on", "live": False, "agent": "openai", "api_mode": "codex_responses",
             "jev": {"call": "called"}},
            {"ts": 4, "kind": "dispatch", "mode": "on", "agent": "local", "jev": {"call": "fail_open", "error": "rate_limited"}},
            {"ts": 5, "kind": "dispatch", "mode": "on", "agent": "local", "downgraded": True, "jev": {"call": "called"}},
            {"ts": 6, "kind": "dispatch", "mode": "on", "agent": "local", "privacy": "highly_sensitive",
             "privacy_why": "profile default is not classified, so highly_sensitive", "jev": {"call": "not_called"}},
            {"ts": 7, "kind": "dispatch", "mode": "on", "agent": "local", "reason": "pinned: …", "jev": {"call": "not_called"}},
            {"ts": 8, "kind": "dispatch", "mode": "on", "agent": "local", "jev": {"call": "called", "tier": "medium"}},
            {"ts": 9, "kind": "dispatch", "mode": "on", "agent": "local", "reason": "stood aside: /jev routing is on"},
        ]
        self.log("default", *rows)
        out = {e["ts"]: e for e in ds.live(self.home)["events"]}
        self.assertEqual([out[ts]["outcome"] for ts in range(1, 10)],
                         ["agent", "would_be_agent", "not_handed_over", "receptionist_warning", "receptionist_warning",
                          "receptionist_warning", "receptionist", "receptionist", "receptionist"])
        self.assertEqual(out[9]["jev"], {"call": "not_called"})                 # a row from before this change

    def test_live_sums_up_the_window_so_a_broken_chain_shows_at_a_glance(self):
        self.log("default",
                 {"ts": 1, "kind": "dispatch", "mode": "on", "agent": "local", "jev": {"call": "called", "latency_ms": 400}},
                 {"ts": 2, "kind": "dispatch", "mode": "on", "agent": "local", "jev": {"call": "called", "latency_ms": 600}},
                 {"ts": 3, "kind": "dispatch", "mode": "on", "agent": "local", "jev": {"call": "fail_open", "error": "timeout"}},
                 {"ts": 4, "kind": "dispatch", "mode": "on", "live": True, "handed_over": True, "agent": "claude",
                  "jev": {"call": "called", "latency_ms": 500}})
        summary = ds.live(self.home)["summary"]
        self.assertEqual(summary, {"turns": 4, "jev": {"called": 3, "fail_open": 1},
                                   "outcomes": {"receptionist": 2, "receptionist_warning": 1, "agent": 1},
                                   "latency_ms": 500})

    def test_live_honours_since_and_sorts_newest_first(self):
        self.log("default", {"ts": 1, "kind": "dispatch", "agent": "local"},
                 {"ts": 2, "kind": "dispatch", "agent": "openai"})
        out = ds.live(self.home)
        self.assertIsInstance(out["now"], float)
        self.assertEqual([e["ts"] for e in out["events"]], [2, 1])
        self.assertEqual([e["ts"] for e in ds.live(self.home, since=1)["events"]], [2])

    def test_live_covers_every_profile(self):
        self.log("default", {"ts": 1, "kind": "dispatch", "agent": "local"})
        self.log("wiki", {"ts": 2, "kind": "dispatch", "agent": "openai"})
        out = ds.live(self.home)
        self.assertEqual({(e["profile"], e["agent"]) for e in out["events"]},
                         {("default", "local"), ("wiki", "openai")})

    def test_jev_live_ignores_dispatch_rows_from_the_same_log(self):
        self.log("default", {"ts": 1, "kind": "route", "tier": "medium", "model": "m"},
                 {"ts": 2, "kind": "dispatch", "agent": "openai"})
        self.assertEqual(len(ds.live(self.home)["events"]), 1)
        jev_events = routing_store.jev_live(self.home)["events"]
        self.assertEqual([e["kind"] for e in jev_events], ["route"])

    # -- reset_cooldown ------------------------------------------------------------
    def test_reset_cooldown_clears_a_refused_rung(self):
        ladder.refuse("dispatch:claude", "quota", cooldown=900)
        self.assertGreater(ladder.cooling("dispatch:claude"), 0)
        result = ds.reset_cooldown(self.home, "claude")
        self.assertEqual(ladder.cooling("dispatch:claude"), 0)
        self.assertEqual(result, ds.state(self.home))
        self.assertEqual(result["agents"]["claude"]["cooling_s"], 0)

    def test_reset_cooldown_rejects_an_unknown_agent(self):
        with self.assertRaises(ValueError):
            ds.reset_cooldown(self.home, "bogus")

    # -- test_agent ------------------------------------------------------------------
    def test_test_agent_succeeds_even_though_the_agent_is_not_enabled(self):
        self.write_fleet({"agents": {"claude": {"model": "opus"}}})
        pol = dispatch.load_policy(paths=dispatch.policy_paths(root=Path(self.home), home=Path(self.home)))
        self.assertFalse(pol["agents"]["claude"]["enabled"])

        seen = []

        def runner(argv, stdin_text, timeout):
            seen.append((stdin_text, timeout))
            return self.runner("claude", text="Reply: ok")(argv, stdin_text, timeout)

        result = ds.test_agent(self.home, "claude", runners={"claude": runner})
        self.assertEqual(result, {"ok": True, "agent": "claude", "model": "opus", "answer": "Reply: ok"})
        self.assertEqual(seen, [(ds.TEST_PROMPT, ds.TEST_TIMEOUT)])

    def test_test_agent_quota_failure(self):
        self.write_fleet({"agents": {"openai": {"model": "gpt-6"}}})
        result = ds.test_agent(self.home, "openai",
                               runners={"codex": self.runner("codex", error="You have hit your usage limit")})
        self.assertFalse(result["ok"])
        self.assertEqual((result["agent"], result["error"]), ("openai", "quota"))
        self.assertIn("detail", result)

    def test_test_agent_generic_failure(self):
        def boom(argv, stdin_text, timeout):
            raise RuntimeError("kapot")

        self.write_fleet({"agents": {"claude": {"model": "opus"}}})
        result = ds.test_agent(self.home, "claude", runners={"claude": boom})
        self.assertEqual(result, {"ok": False, "agent": "claude", "error": "failed", "detail": "RuntimeError"})

    def test_test_agent_rejects_an_unknown_agent(self):
        with self.assertRaises(ValueError):
            ds.test_agent(self.home, "bogus")

    # -- concurrency: ThreadingHTTPServer runs requests side by side (I2) ---------------
    def test_concurrent_switch_writes_leave_valid_json_with_both_keys(self):
        """20 threads alternately setting mode and notice on one profile: each write is a
        read-modify-write, so without a lock and a unique temp file the file ends up invalid
        JSON or missing a key."""
        import threading
        for _ in range(5):
            errors = []
            barrier = threading.Barrier(20)

            def work(i):
                try:
                    barrier.wait()
                    if i % 2:
                        ds.set_switch(self.home, "wiki", "mode", ("off", "shadow", "on")[i % 3])
                    else:
                        ds.set_switch(self.home, "wiki", "notice", ("off", "on")[(i // 2) % 2])
                except Exception as error:  # noqa: BLE001 - collected and reported below
                    errors.append(repr(error))

            threads = [threading.Thread(target=work, args=(i,)) for i in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [])
            path = os.path.join(self._profile_home("wiki"), "jev", "dispatch-state.json")
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(set(data), {"mode", "notice"})
            leftovers = [n for n in os.listdir(os.path.dirname(path)) if n != "dispatch-state.json"]
            self.assertEqual(leftovers, [])

    def test_parallel_applies_keep_the_file_valid_and_each_backup_distinct(self):
        import threading
        self.write_fleet({"turn_budget": 7})
        receipts, errors = [], []
        barrier = threading.Barrier(10)

        def work(i):
            try:
                barrier.wait()
                receipts.append(ds.apply(self.home, {"agents": {"claude": {"model": "model-%d" % i}}}))
            except Exception as error:  # noqa: BLE001
                errors.append(repr(error))

        threads = [threading.Thread(target=work, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(receipts), 10)
        self.assertTrue(all(r["verified"] for r in receipts))
        backups = [r["backup"] for r in receipts]
        self.assertEqual(len(set(backups)), 10, "two applies shared one backup folder")
        self.assertTrue(all(os.path.isfile(b) for b in backups))
        after = json.loads(Path(self.home, "jev", "dispatch.json").read_text(encoding="utf-8"))
        self.assertEqual(after["turn_budget"], 7)
        self.assertRegex(after["agents"]["claude"]["model"], r"^model-\d$")
        self.assertEqual(sorted(os.listdir(os.path.join(self.home, "jev"))), ["dispatch.json"])

    # -- live: a malformed row is skipped, not a crash ----------------------------------
    def test_live_skips_rows_with_a_non_numeric_ts_or_a_non_dict_triage(self):
        self.log("default",
                 {"ts": "yesterday", "kind": "dispatch", "agent": "openai"},
                 {"ts": [1], "kind": "dispatch", "agent": "openai"},
                 {"ts": True, "kind": "dispatch", "agent": "openai"},
                 {"ts": 3, "kind": "dispatch", "agent": "openai", "triage": "frontier"},
                 {"ts": 4, "kind": "dispatch", "agent": "openai", "triage": ["frontier"]},
                 {"ts": 5, "kind": "dispatch", "agent": "claude", "triage": {"niveau": "frontier"}},
                 {"ts": 6.5, "kind": "dispatch", "agent": "local"})
        out = ds.live(self.home)
        self.assertEqual([(e["ts"], e["agent"], e["niveau"]) for e in out["events"]],
                         [(6.5, "local", None), (5, "claude", "frontier")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
