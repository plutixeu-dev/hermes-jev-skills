"""keys_store: which keys are there, saving one, and one real Jev check. A key never comes back."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import keys_store as ks  # noqa: E402
from jevkit import client, keystore  # noqa: E402

KEY = "k" * 32                                   # not key-shaped on purpose


class KeysStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = self.tmp.name
        os.makedirs(os.path.join(self.home, "profiles", "wiki"))
        for patch in (mock.patch.object(keystore, "resolve", lambda provider=None: None),
                      mock.patch.object(keystore, "source", lambda provider=None: "absent"),
                      mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": os.path.join(self.home, "xdg")})):
            patch.start()
            self.addCleanup(patch.stop)
        os.environ.pop("TYPESAFE_MODEL", None)          # restored by the patch.dict above

    def env(self, home, text):
        with open(os.path.join(home, ".env"), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_state_names_where_each_key_is_and_never_carries_it(self):
        self.env(self.home, "OPENROUTER_API_KEY=%s\n" % KEY)
        out = ks.state(self.home)
        row = out["providers"]["openrouter"]
        self.assertEqual((row["present"], row["lanes"]), (False, {"default": True, "wiki": False}))
        self.assertEqual((out["jev_route"], out["jev_model"]), ("openrouter", client.OPENROUTER_MODEL))
        self.assertNotIn(KEY, json.dumps(out))

    def test_save_does_what_setup_key_does_and_returns_no_key(self):
        stored = {"status": "stored", "verified": True, "stored_in": ["os-secret-store"], "hermes_env_files": 2,
                  "provider": "openrouter", "length": 32}
        with mock.patch.object(ks.key_setup, "_finish", return_value=stored) as finish:
            out = ks.save(self.home, "openrouter", KEY)
        self.assertEqual(finish.call_args[0][0], KEY)
        self.assertEqual((out["ok"], out["verified"], out["hermes_env_files"]), (True, True, 2))
        self.assertNotIn(KEY, json.dumps(out))
        self.assertNotIn("length", out)

    def test_a_failure_is_a_fixed_text_or_a_type_name_never_the_message(self):
        with mock.patch.object(ks.key_setup, "_finish", side_effect=RuntimeError("boom " + KEY)):
            out = ks.save(self.home, "typesafe", KEY)
        self.assertEqual(out["reason"], "could not store the key (RuntimeError)")
        self.assertEqual(ks.save(self.home, "typesafe", "short")["reason"], "that does not look like an API key")
        with self.assertRaises(ValueError):
            ks.save(self.home, "anthropic", KEY)

    def test_check_makes_exactly_one_request_through_the_provider_asked_for(self):
        seen = []

        def wire(body, headers, timeout):
            seen.append((json.loads(body)["model"], headers["Authorization"]))
            return json.dumps({"answers": {"ok": {"type": "noul", "noul": 0.97}}}).encode()
        self.env(self.home, "OPENROUTER_API_KEY=%s\n" % KEY)       # only in the gateway's .env
        out = ks.check(self.home, "openrouter", transport=wire)
        self.assertEqual(seen, [(client.OPENROUTER_MODEL, "Bearer " + KEY)])
        self.assertEqual((out["ok"], out["model"], out["answer"]), (True, client.OPENROUTER_MODEL, 0.97))
        self.assertIsInstance(out["latency_ms"], int)
        self.assertNotIn(KEY, json.dumps(out))

    def test_check_reports_the_code_and_does_not_retry(self):
        calls = []

        def limited(body, headers, timeout):
            calls.append(1)
            raise client.JevError("rate_limited")
        self.env(self.home, "TYPESAFE_API_KEY=%s\n" % KEY)
        self.assertEqual(ks.check(self.home, "typesafe", transport=limited)["error"], "rate_limited")
        self.assertEqual(len(calls), 1)

    def test_check_without_a_key_sends_nothing(self):
        def never(*_):
            raise AssertionError("a request went out with no key")
        self.assertEqual(ks.check(self.home, "openrouter", transport=never)["error"], "no_key")


if __name__ == "__main__":
    unittest.main()
