"""The front desk's acceptance, offline: both plugins on one turn, the way Hermes calls them.

Plan Task 12 checks four things on the machine that runs Hermes. These are the same four, with
the two plugins loaded from this repo and registered with a stand-in for Hermes's plugin API:
- a config.yaml whose receptionist is an Ollama model, `qwen3.5:4b`;
- routing switched on, as it was on 2026-09-29;
- a fake Jev reached through OpenRouter, a fake Claude Code, and a fake local completion.

Hermes's order per turn: the `pre_llm_call` hooks, then around each provider call the
`llm_request` middleware and the `llm_execution` middleware, then `transform_llm_output`.
Nothing here reaches the network, a real key or a real login.
"""
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
RECEPTIONIST = "qwen3.5:4b"
HARD = "The scheduler deadlocks under load. Find the race and propose a fix."
BUILD = "typesafe/jev-1.13-20260917"


def load(name, folder):
    """A plugin as Hermes loads it: a package whose jevkit is its own copy."""
    where = REPO / "hermes" / "plugin" / folder
    spec = importlib.util.spec_from_file_location(name, where / "__init__.py",
                                                  submodule_search_locations=[str(where), str(REPO)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


routing = load("hermes_jev_acceptance", "hermes-jev")
desk = load("hermes_dispatch_acceptance", "hermes-dispatch")


class Hermes:
    """Hermes's plugin API as far as these two plugins use it, and one turn in Hermes's order."""

    def __init__(self):
        self.hooks, self.middleware = {}, {}

    def register_hook(self, name, fn):
        self.hooks.setdefault(name, []).append(fn)

    def register_middleware(self, name, fn):
        self.middleware.setdefault(name, []).append(fn)

    def register_tool(self, **_):
        pass

    def register_command(self, *_, **__):
        pass

    def register_system_prompt_section(self, *_, **__):
        pass

    def has_plugin(self, name):
        return name in ("hermes-jev", "hermes-dispatch")

    def get_config(self, name, default=None):
        return default

    def turn(self, session, text, events, *, model=RECEPTIONIST, provider="custom", api_mode="chat_completions"):
        """One user turn with one provider call. Returns the reply and the model each layer saw."""
        turn_id = f"{session}-turn"
        for hook in self.hooks["pre_llm_call"]:
            hook(session_id=session, turn_id=turn_id, user_message=text, platform="telegram",
                 conversation_history=[{"role": "user", "content": text}])
        request = {"model": model, "messages": [{"role": "user", "content": text}]}
        for middleware in self.middleware.get("llm_request", []):
            changed = middleware(request=request, original_request=request, session_id=session, turn_id=turn_id,
                                 model=model, provider=provider, api_mode=api_mode)
            if isinstance(changed, dict) and isinstance(changed.get("request"), dict):
                request = changed["request"]
        seen = {"requested": request["model"], "provider_called_with": None}

        def provider_call(sent):
            events.append("local completion")
            seen["provider_called_with"] = sent["model"]
            return types.SimpleNamespace(choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(role="assistant", content="LOCAL ANSWER"))], model=sent["model"])

        call = provider_call
        for middleware in reversed(self.middleware.get("llm_execution", [])):
            call = (lambda layer, inner: lambda sent: layer(
                request=sent, next_call=inner, session_id=session, turn_id=turn_id, api_mode=api_mode,
                model=model, provider=provider))(middleware, call)
        reply = call(request).choices[0].message.content
        for hook in self.hooks["transform_llm_output"]:
            changed = hook(response_text=reply, session_id=session, turn_id=turn_id)
            if isinstance(changed, str):
                reply = changed
        return reply, seen


def jev_wire(events, kit, *, fail=None):
    """A Jev that judges every turn hard coding work and remembers each request, or fails with `fail`.
    `kit` is the plugin's own jevkit client: each plugin catches its own copy's JevError."""
    def send(body, headers, timeout):
        request = json.loads(body)
        events.append("jev " + request["model"])
        if fail:
            raise kit.JevError(fail)
        answers = {}
        for name, question in request["questions"].items():
            if question["type"] == "score":
                answers[name] = {"type": "score", "score": 2.05, "confidence": 0.9,
                                 "probabilities": {"0": 0.1, "1": 0.1, "2": 0.45, "3": 0.35}}
            elif question["type"] == "choice":
                keys = list(question["criteria"])
                best = "coding" if "coding" in keys else keys[0]
                share = 0.1 / max(1, len(keys) - 1)
                answers[name] = {"type": "choice", "choice": best, "confidence": 0.9,
                                 "probabilities": {key: (0.9 if key == best else share) for key in keys}}
            else:
                answers[name] = {"type": "noul", "noul": 0.2}
        return json.dumps({"model": BUILD, "answers": answers, "usage": {"cost": 0.00002}}).encode()
    return send


class FrontDeskAcceptanceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        (self.home / "jev").mkdir()
        # The incident's conditions: routing on, and the receptionist an Ollama tag with a colon.
        (self.home / "jev" / "state.json").write_text(json.dumps({"routing": "on", "skills": "off"}))
        (self.home / "jev" / "dispatch.json").write_text(json.dumps({
            "profiles": {"default": "private"},
            "agents": {"claude": {"enabled": True, "model": "opus", "only_repo": False}}}))
        config = {"model": {"provider": "custom", "default": RECEPTIONIST, "base_url": "http://127.0.0.1:11434/v1"},
                  "plugins": {"enabled": ["hermes-jev", "hermes-dispatch"]}}
        hermes_cli = types.ModuleType("hermes_cli")
        hermes_cli_config = types.ModuleType("hermes_cli.config")
        hermes_cli_config.load_config_readonly = lambda: config
        hermes_cli.config = hermes_cli_config
        env = {"HERMES_HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / "xdg"),
               "JEV_DISPATCH_POLICY": str(self.home / "jev" / "dispatch.json"),
               "JEV_ROUTING_CONFIG": str(self.home / "jev" / "routing.json"),
               "JEV_LADDER_STATE": str(self.home / "jev" / "ladder.json"),
               "OPENROUTER_API_KEY": "k" * 40}                   # not key-shaped on purpose
        patches = [mock.patch.dict(os.environ, env),
                   mock.patch.dict(sys.modules, {"hermes_cli": hermes_cli, "hermes_cli.config": hermes_cli_config})]
        self.events, self.claude_prompts = [], []

        def claude(prompt, **kwargs):
            self.events.append("claude " + kwargs.get("model", ""))
            self.claude_prompts.append(prompt)
            return desk.dispatch.agents.Result(text="The lock order in the scheduler is the race.", model="opus")

        for plugin in (routing, desk):
            kit = sys.modules[plugin.__name__ + ".jevkit.client"]
            keystore = sys.modules[plugin.__name__ + ".jevkit.keystore"]
            patches += [mock.patch.object(kit, "_openrouter_transport", jev_wire(self.events, kit)),
                        mock.patch.object(kit, "_http_transport", jev_wire(self.events, kit)),
                        mock.patch.object(keystore, "_from_keychain", lambda provider="typesafe": None),
                        mock.patch.object(keystore, "_from_file", lambda provider="typesafe": None)]
        patches.append(mock.patch.object(desk.dispatch.agents, "run_claude", claude))
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        for name in ("TYPESAFE_API_KEY", "TYPESAFE_MODEL"):
            os.environ.pop(name, None)                           # restored by the patch.dict above
        routing._TURNS.clear()
        desk._TURNS.clear()
        desk._SESSIONS.clear()
        self.hermes = Hermes()
        routing.register(self.hermes)
        desk.register(self.hermes)

    def front_desk(self, mode):
        (self.home / "jev" / "dispatch-state.json").write_text(json.dumps({"mode": mode}))

    def rows(self):
        path = self.home / "logs" / "jev-decisions.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def fail_jev(self, code):
        for plugin in (routing, desk):
            kit = sys.modules[plugin.__name__ + ".jevkit.client"]
            patch = mock.patch.object(kit, "_openrouter_transport", jev_wire(self.events, kit, fail=code))
            patch.start()
            self.addCleanup(patch.stop)

    def test_1_a_hard_turn_asks_jev_exactly_once_before_the_local_completion(self):
        self.front_desk("shadow")
        reply, seen = self.hermes.turn("chat", HARD, self.events)
        self.assertEqual(self.events, ["jev ~typesafe/jev-latest", "local completion"])
        self.assertEqual((reply, seen["requested"], seen["provider_called_with"]), ("LOCAL ANSWER", RECEPTIONIST, RECEPTIONIST))
        rows = self.rows()
        self.assertEqual([row.get("kind") for row in rows], ["dispatch"])     # no routing row for this turn
        jev = rows[0]["jev"]
        self.assertEqual((jev["call"], jev["via"], jev["model"], jev["build"], jev["tier"], jev["specialty"]),
                         ("called", "openrouter", "~typesafe/jev-latest", BUILD, "hard", "coding"))
        self.assertIsInstance(jev["latency_ms"], int)
        self.assertEqual((rows[0]["agent"], rows[0]["chat_model"]), ("claude", RECEPTIONIST))     # who would answer
        self.assertNotIn("pinned", json.dumps(rows))

    def test_2_a_hard_turn_is_answered_by_the_agent_and_the_chat_model_stays(self):
        self.front_desk("on")
        reply, seen = self.hermes.turn("chat", HARD, self.events)
        self.assertEqual(self.events, ["jev ~typesafe/jev-latest", "claude opus"])
        self.assertTrue(reply.startswith("[claude · opus]\n\n"), reply)
        self.assertEqual((seen["requested"], seen["provider_called_with"]), (RECEPTIONIST, None))
        self.assertIn(HARD, self.claude_prompts[0])
        row = self.rows()[-1]
        self.assertEqual((row["agent"], row["handed_over"], row["chat_model"]), ("claude", True, RECEPTIONIST))
        # The next turn in the same chat still asks the receptionist's model first.
        _, seen = self.hermes.turn("chat", "And add a test for it.", self.events)
        self.assertEqual(seen["requested"], RECEPTIONIST)

    def test_3_a_jev_failure_leaves_no_trace_in_the_chat_and_the_row_says_fail_open(self):
        self.front_desk("on")
        self.fail_jev("http_404")
        reply, seen = self.hermes.turn("chat", HARD, self.events)
        self.assertEqual(reply, "LOCAL ANSWER")                      # no error text, no notice
        self.assertEqual(seen["provider_called_with"], RECEPTIONIST)
        row = self.rows()[-1]
        self.assertEqual((row["jev"], row["agent"]), ({"call": "fail_open", "error": "http_404", "read": "features"},
                                                      "local"))

    def test_4_a_model_chosen_with_slash_model_skips_jev_for_that_chat_only(self):
        self.front_desk("on")
        reply, seen = self.hermes.turn("pinned", HARD, self.events, model="gpt-5.5", provider="openai-codex",
                                       api_mode="codex_responses")
        self.assertEqual((self.events, reply, seen["requested"]), (["local completion"], "LOCAL ANSWER", "gpt-5.5"))
        row = self.rows()[-1]
        self.assertEqual((row["jev"], row["chat_model"]), ({"call": "not_called"}, "gpt-5.5"))
        self.assertTrue(row["reason"].startswith("pinned"), row["reason"])
        self.hermes.turn("receptionist", HARD, self.events)         # another chat is asked as always
        self.assertEqual(self.events[1:], ["jev ~typesafe/jev-latest", "claude opus"])

    def test_with_the_front_desk_off_routing_decides_as_it_did(self):
        """The alternative still works: in a profile without a front desk, routing takes the turn.
        The Ollama tag reaches it whole, and the receptionist is not read as a pin."""
        self.front_desk("off")
        reply, _ = self.hermes.turn("chat", HARD, self.events)
        route = next(row for row in self.rows() if row.get("kind") == "route")
        self.assertEqual(route["from"], "custom:" + RECEPTIONIST)
        self.assertNotEqual(route["reason"], "you pinned this model")
        self.assertNotIn("dispatch", [row.get("kind") for row in self.rows()])
        self.assertEqual(reply, "LOCAL ANSWER")


if __name__ == "__main__":
    unittest.main()
