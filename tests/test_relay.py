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

    def test_a_question_about_passwords_may_leave(self):
        chat = [{"role": "user", "content": "How do I hash a password in Python?"}]
        self.assertIsNotNone(relay.build_handoff(chat, agent="openai", reason="r"))

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
