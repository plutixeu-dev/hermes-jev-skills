"""The front desk's shared rules: which model is the receptionist, and when a chat pinned another.

Offline, no Hermes. These rules decide whether Jev is asked at all, so every case here is one a
real turn has hit or will hit.
"""
import unittest

from jevkit import frontdesk

OLLAMA = {"model": {"provider": "custom", "default": "qwen3.5:4b", "base_url": "http://127.0.0.1:11434/v1"}}


class BareModelTests(unittest.TestCase):
    def test_an_ollama_tag_is_part_of_the_name(self):
        self.assertEqual(frontdesk.bare_model("qwen3.5:4b", ["custom"]), "qwen3.5:4b")

    def test_only_a_named_provider_comes_off(self):
        self.assertEqual(frontdesk.bare_model("openrouter:deepseek/deepseek-v4:free", ["openrouter"]),
                         "deepseek/deepseek-v4:free")
        self.assertEqual(frontdesk.bare_model("deepseek/deepseek-v4:free", ["openrouter"]),
                         "deepseek/deepseek-v4:free")

    def test_the_hermes_name_and_the_catalog_name_of_a_provider_both_count(self):
        self.assertEqual(frontdesk.bare_model("openai:gpt-5.5", ["openai-codex"]), "gpt-5.5")
        self.assertEqual(frontdesk.bare_model("openai-codex:gpt-5.5", ["openai-codex"]), "gpt-5.5")


class PinTests(unittest.TestCase):
    def test_the_receptionist_is_not_a_pin_whatever_its_provider_is_called(self):
        """2026-09-29 20:57:09: qwen3.5:4b was read as the model "4b", so every turn was pinned."""
        for provider in ("custom", "local-ollama-cpu", "custom:local-ollama-cpu", ""):
            with self.subTest(provider=provider):
                self.assertFalse(frontdesk.is_pinned("qwen3.5:4b", provider, OLLAMA))

    def test_a_model_chosen_in_this_chat_is_a_pin(self):
        self.assertTrue(frontdesk.is_pinned("gpt-5.5", "openai-codex", OLLAMA))
        self.assertTrue(frontdesk.is_pinned("qwen3.6:27b", "custom", OLLAMA))

    def test_a_fallback_is_hermes_choice_not_a_pin(self):
        chain = [{"provider": "openrouter", "model": "deepseek/deepseek-v4"}]
        for config in ({**OLLAMA, "fallback_providers": chain}, {**OLLAMA, "fallback_model": chain[0]}):
            with self.subTest(config=config):
                self.assertFalse(frontdesk.is_pinned("deepseek/deepseek-v4", "openrouter", config))

    def test_a_prefixed_default_matches_the_bare_model_on_the_wire(self):
        config = {"model": {"provider": "openrouter", "default": "openrouter:deepseek/deepseek-v4:free"}}
        self.assertFalse(frontdesk.is_pinned("deepseek/deepseek-v4:free", "openrouter", config))

    def test_hermes_reads_model_model_and_a_bare_string_as_well(self):
        self.assertFalse(frontdesk.is_pinned("qwen3.5:4b", "custom", {"model": {"model": "qwen3.5:4b"}}))
        self.assertFalse(frontdesk.is_pinned("qwen3.5:4b", "custom", {"model": "qwen3.5:4b"}))

    def test_with_no_receptionist_saved_nothing_is_a_pin(self):
        for config in (None, {}, {"model": {}}, {"model": None}, "not a mapping"):
            with self.subTest(config=config):
                self.assertFalse(frontdesk.is_pinned("anything", "custom", config))

    def test_a_request_without_a_model_cannot_be_told_apart_so_it_is_not_a_pin(self):
        self.assertFalse(frontdesk.is_pinned("", "custom", OLLAMA))


class DeskModeTests(unittest.TestCase):
    def test_the_switch_file_wins_then_config_yaml_then_dispatch_json(self):
        self.assertEqual(frontdesk.desk_mode({"mode": "shadow"}, "on", {"mode": "off"}), "shadow")
        self.assertEqual(frontdesk.desk_mode({}, "on", {"mode": "off"}), "on")
        self.assertEqual(frontdesk.desk_mode({}, None, {"mode": "shadow"}), "shadow")
        self.assertEqual(frontdesk.desk_mode(None, None, None), "off")

    def test_anything_else_is_off(self):
        """A bare YAML `on` arrives as True; the plugin has always read that as off."""
        self.assertEqual(frontdesk.desk_mode({}, True, {}), "off")
        self.assertEqual(frontdesk.desk_mode({"mode": "aan"}, None, {}), "off")


if __name__ == "__main__":
    unittest.main()
