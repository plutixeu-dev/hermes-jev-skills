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
