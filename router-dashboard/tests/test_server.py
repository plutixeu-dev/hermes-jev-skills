"""Tests for the dashboard HTTP surface (real server on an ephemeral port)."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dispatch_store as ds  # noqa: E402
import routing_store as rs  # noqa: E402
import server as srv  # noqa: E402
from jevkit import dispatch, ladder  # noqa: E402

CFG = """\
model:
  default: deepseek/deepseek-v4.1-flash
  provider: openrouter
auxiliary:
  compression:
    provider: openrouter
    model: deepseek/deepseek-v4-flash-0731
"""


class ServerTestCase(unittest.TestCase):
    token = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.home = cls.tmp.name
        with open(os.path.join(cls.home, "config.yaml"), "w", encoding="utf-8") as fh:
            fh.write(CFG)
        os.makedirs(os.path.join(cls.home, "profiles", "wiki"), exist_ok=True)
        with open(os.path.join(cls.home, "profiles", "wiki", "config.yaml"), "w", encoding="utf-8") as fh:
            fh.write(CFG)
        cfg = srv.Config(cls.home, cls.token)
        cls.httpd = srv.make_server("127.0.0.1", 0, cfg)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def call(self, path, body=None, token=None):
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET")
        if data:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("X-Dashboard-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def test_health_and_auth_mode(self):
        code, body = self.call("/api/health")
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertFalse(body["auth_required"])

    def test_ui_is_served(self):
        with urllib.request.urlopen("http://127.0.0.1:%d/" % self.port, timeout=10) as resp:
            html = resp.read().decode()
        self.assertEqual(resp.status, 200)
        self.assertIn("Hermes Model Routing", html)
        self.assertIn("Apply changes", html)

    def test_state_lists_profiles_and_use_cases(self):
        code, body = self.call("/api/state")
        self.assertEqual(code, 200)
        self.assertEqual([p["name"] for p in body["profiles"]], ["default", "wiki"])
        self.assertEqual(body["use_cases"][0]["key"], "__main__")
        self.assertIn("intent", body["jev_mode"])

    def test_plan_previews_without_writing(self):
        before = open(os.path.join(self.home, "config.yaml"), encoding="utf-8").read()
        code, body = self.call("/api/plan", {"profile": "default",
                                             "changes": {"compression": {"model": "google/gemini-2.5-flash"}}})
        self.assertEqual(code, 200)
        self.assertEqual(body["rows"][0]["after"], "google/gemini-2.5-flash")
        self.assertEqual(open(os.path.join(self.home, "config.yaml"), encoding="utf-8").read(), before)

    def test_apply_requires_confirm(self):
        code, body = self.call("/api/apply", {"profile": "default",
                                              "changes": {"compression": {"model": "x/y"}}})
        self.assertEqual(code, 400)
        self.assertIn("confirm", body["error"])

    def test_apply_writes_and_verifies(self):
        code, body = self.call("/api/apply", {"profile": "wiki", "confirm": True,
                                              "changes": {"__main__": {"model": "z-ai/glm-5.3"},
                                                          "compression": {"model": "google/gemini-2.5-flash"}}})
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"], body)
        self.assertTrue(body["verified"])
        self.assertTrue(body["reload_required"])
        self.assertTrue(os.path.isfile(body["backup"]))
        cfg = rs.read_config(os.path.join(self.home, "profiles", "wiki", "config.yaml"))
        self.assertEqual(cfg["main"]["model"], "z-ai/glm-5.3")
        self.assertEqual(cfg["slots"]["compression"]["model"], "google/gemini-2.5-flash")

    def test_unknown_profile_and_slot_are_rejected(self):
        code, body = self.call("/api/apply", {"profile": "nope", "confirm": True, "changes": {}})
        self.assertEqual(code, 404)
        code, body = self.call("/api/plan", {"profile": "default", "changes": {"bogus": {"model": "m"}}})
        self.assertEqual(code, 400)
        self.assertIn("unknown use case", body["error"])

    def test_injection_is_rejected(self):
        code, body = self.call("/api/plan", {"profile": "default",
                                             "changes": {"compression": {"model": "a\ninjected: true"}}})
        self.assertEqual(code, 400)


class PoolsEndpointTestCase(unittest.TestCase):
    """The grid the page draws comes from the server, so an empty specialty pool has to
    arrive as a cell, not be missing from the payload."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.home = cls.tmp.name
        with open(os.path.join(cls.home, "config.yaml"), "w", encoding="utf-8") as fh:
            fh.write(CFG)
        os.makedirs(os.path.join(cls.home, "jev"))
        with open(os.path.join(cls.home, "jev", "routing.json"), "w", encoding="utf-8") as fh:
            json.dump({"tiers": {t: {"general": ["openrouter:a/b"], "vision": ["openrouter:c/d"]}
                                 for t in ("simple", "medium", "hard")}}, fh)
        # A routing.json in the tester's own ~/.config would otherwise layer into this.
        cls.env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": os.path.join(cls.home, "xdg")})
        cls.env.start()
        os.environ.pop("JEV_ROUTING_CONFIG", None)
        cls.httpd = srv.make_server("127.0.0.1", 0, srv.Config(cls.home, None))
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.env.stop()
        cls.tmp.cleanup()

    def get(self, path):
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (self.port, path), timeout=10) as resp:
            return json.loads(resp.read().decode())

    def test_pools_endpoint_returns_a_full_grid_per_profile(self):
        body = self.get("/api/jev/pools")
        self.assertEqual(body["tiers"], ["simple", "medium", "hard"])
        grid = body["profiles"][0]["tiers"]
        self.assertEqual(grid["medium"]["coding"]["listed"], 0)
        self.assertEqual(grid["medium"]["general"]["models"][0]["ref"], "openrouter:a/b")

    def test_pools_endpoint_names_the_dead_axis(self):
        profile = self.get("/api/jev/pools")["profiles"][0]
        self.assertEqual(profile["dead_tiers"], ["simple", "medium", "hard"])
        self.assertIn("discarded", " ".join(w["text"] for w in profile["warnings"]))

    def test_page_offers_the_grid_and_the_pool_column(self):
        with urllib.request.urlopen("http://127.0.0.1:%d/" % self.port, timeout=10) as resp:
            html = resp.read().decode()
        self.assertIn("Routing pools", html)
        self.assertIn("From pool", html)


class NonLoopbackTestCase(unittest.TestCase):
    def test_refuses_exposed_bind_without_token(self):
        cfg = srv.Config(tempfile.gettempdir(), token=None)
        with self.assertRaises(SystemExit):
            srv.make_server("0.0.0.0", 0, cfg)

    def test_token_required_when_set(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            with open(os.path.join(tmp.name, "config.yaml"), "w", encoding="utf-8") as fh:
                fh.write(CFG)
            httpd = srv.make_server("127.0.0.1", 0, srv.Config(tmp.name, "s3cret"))
            port = httpd.server_address[1]
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            try:
                req = urllib.request.Request("http://127.0.0.1:%d/api/state" % port)
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(req, timeout=10)
                self.assertEqual(ctx.exception.code, 401)
                req = urllib.request.Request("http://127.0.0.1:%d/api/state" % port)
                req.add_header("X-Dashboard-Token", "s3cret")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    self.assertEqual(resp.status, 200)
            finally:
                httpd.shutdown()
                httpd.server_close()
        finally:
            tmp.cleanup()


class AuthFlowTestCase(unittest.TestCase):
    """The ?token= link must work in a browser: exchange once, then use a cookie."""

    TOKEN = "s3cret"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        with open(os.path.join(cls.tmp.name, "config.yaml"), "w", encoding="utf-8") as fh:
            fh.write(CFG)
        cls.httpd = srv.make_server("127.0.0.1", 0, srv.Config(cls.tmp.name, cls.TOKEN))
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def get(self, path, cookie=None, follow=True):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path))
        if cookie:
            req.add_header("Cookie", cookie)
        opener = None if follow else urllib.request.build_opener(_NoRedirect)
        if follow:
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return resp.status, dict(resp.headers), resp.read().decode()
            except urllib.error.HTTPError as exc:
                return exc.code, dict(exc.headers), exc.read().decode()
        try:
            with opener.open(req, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read().decode()

    def test_token_link_redirects_and_sets_cookie(self):
        code, headers, _ = self.get("/?token=%s" % self.TOKEN, follow=False)
        self.assertEqual(code, 302)
        self.assertEqual(headers.get("Location"), "/")
        cookie = headers.get("Set-Cookie") or ""
        self.assertIn("hermes_dash_token=%s" % self.TOKEN, cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)

    def test_wrong_token_link_sets_no_cookie(self):
        code, headers, body = self.get("/?token=nope", follow=False)
        self.assertEqual(code, 200)
        self.assertNotIn("Set-Cookie", headers)
        self.assertIn("Hermes Model Routing", body)  # page still loads; API stays locked

    def test_cookie_authorizes_api(self):
        code, _, body = self.get("/api/state", cookie="hermes_dash_token=%s" % self.TOKEN)
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["profiles"][0]["name"], "default")

    def test_query_token_authorizes_api(self):
        code, _, body = self.get("/api/state?token=%s" % self.TOKEN)
        self.assertEqual(code, 200)
        self.assertIn("profiles", json.loads(body))

    def test_unauthenticated_api_is_locked(self):
        code, _, body = self.get("/api/state")
        self.assertEqual(code, 401)
        self.assertIn("unauthorized", json.loads(body)["error"])


_DEFAULT_TOKEN = object()  # "use self.token", so an explicit token=None in a call means "send none"


class DispatchApiTestCase(unittest.TestCase):
    """/api/dispatch/*: the same real-server fixture as ServerTestCase, with JEV_LADDER_STATE and
    XDG_CONFIG_HOME pointed into the temp home and jevkit.keystore.resolve patched to None, so no
    real key store or ladder is ever touched.

    `token` is a class attribute a subclass overrides to require auth (see DispatchApiAuthTestCase
    below, the token subclass pattern): `call()` sends it by default, so every test here still
    passes once a token is required, and a test that wants to check the unauthenticated case passes
    `token=None` explicitly to override that default.
    """

    token = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.home = cls.tmp.name
        with open(os.path.join(cls.home, "config.yaml"), "w", encoding="utf-8") as fh:
            fh.write(CFG)
        os.makedirs(os.path.join(cls.home, "profiles", "wiki"), exist_ok=True)
        with open(os.path.join(cls.home, "profiles", "wiki", "config.yaml"), "w", encoding="utf-8") as fh:
            fh.write(CFG)

        cls.env = mock.patch.dict(os.environ, {
            "XDG_CONFIG_HOME": os.path.join(cls.home, "xdg"),
            "JEV_LADDER_STATE": os.path.join(cls.home, "jev", "ladder.json"),
        })
        cls.env.start()
        for name in ("JEV_DISPATCH_POLICY", "OPENROUTER_API_KEY", "TYPESAFE_API_KEY"):
            os.environ.pop(name, None)

        # This sandbox's own `claude`/`codex` on PATH must never leak into a check_agents() result.
        cls.which_patch = mock.patch("shutil.which", return_value=None)
        cls.which_patch.start()
        # state() calls check_agents with its default has_key, which calls keystore.resolve():
        # on macOS that would shell out to the real Keychain. Never real, whatever the machine.
        cls.keystore_patch = mock.patch.object(dispatch.keystore, "resolve", return_value=None)
        cls.keystore_patch.start()

        cfg = srv.Config(cls.home, cls.token)
        cls.httpd = srv.make_server("127.0.0.1", 0, cfg)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.keystore_patch.stop()
        cls.which_patch.stop()
        cls.env.stop()
        cls.tmp.cleanup()

    def call(self, path, body=None, token=_DEFAULT_TOKEN):
        if token is _DEFAULT_TOKEN:
            token = self.token
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET")
        if data:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("X-Dashboard-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    # -- GET /api/dispatch/state --------------------------------------------------------
    def test_dispatch_state_returns_both_profiles(self):
        code, body = self.call("/api/dispatch/state")
        self.assertEqual(code, 200)
        self.assertEqual(set(body["profiles"]), {"default", "wiki"})

    # -- POST /api/dispatch/switch -------------------------------------------------------
    def test_dispatch_switch_sets_one_profile_from_dashboard(self):
        code, body = self.call("/api/dispatch/switch", {"scope": "wiki", "switch": "mode", "value": "shadow"})
        self.assertEqual(code, 200)
        self.assertEqual(body["profiles"]["wiki"]["mode"], {"value": "shadow", "source": "dashboard"})

    def test_dispatch_switch_all_without_confirm_is_rejected(self):
        code, body = self.call("/api/dispatch/switch", {"scope": "__all__", "switch": "mode", "value": "on"})
        self.assertEqual(code, 400)
        self.assertIn("confirm", body["error"])

    def test_dispatch_switch_bad_value_is_rejected(self):
        code, body = self.call("/api/dispatch/switch", {"scope": "wiki", "switch": "mode", "value": "bogus"})
        self.assertEqual(code, 400)

    # -- POST /api/dispatch/plan ---------------------------------------------------------
    def _read_if_exists(self, path):
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def test_dispatch_plan_previews_without_writing(self):
        # Read the fleet file's own before/after, rather than asserting it is absent: another
        # test in this class may have already created it (independent tests share one home).
        fleet = os.path.join(self.home, "jev", "dispatch.json")
        before = self._read_if_exists(fleet)
        code, body = self.call("/api/dispatch/plan", {"changes": {"agents": {"claude": {"enabled": True}}}})
        self.assertEqual(code, 200)
        self.assertIn({"setting": "agents.claude.enabled", "before": False, "after": True}, body["rows"])
        self.assertEqual(self._read_if_exists(fleet), before)

    # -- POST /api/dispatch/apply --------------------------------------------------------
    def test_dispatch_apply_requires_confirm(self):
        code, body = self.call("/api/dispatch/apply", {"changes": {"agents": {"openai": {"enabled": True}}}})
        self.assertEqual(code, 400)
        self.assertIn("confirm", body["error"])

    def test_dispatch_apply_writes_and_verifies(self):
        code, body = self.call("/api/dispatch/apply", {"confirm": True,
                                                        "changes": {"agents": {"openai": {"enabled": True}}}})
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"], body)
        self.assertTrue(body["verified"])
        fleet = os.path.join(self.home, "jev", "dispatch.json")
        with open(fleet, encoding="utf-8") as fh:
            self.assertTrue(json.load(fh)["agents"]["openai"]["enabled"])

    # -- GET /api/dispatch/live ----------------------------------------------------------
    def test_dispatch_live_returns_only_dispatch_rows(self):
        log = os.path.join(self.home, "logs", "jev-decisions.jsonl")
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with open(log, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": 1, "kind": "route", "tier": "medium", "model": "m"}) + "\n")
            fh.write(json.dumps({"ts": 2, "kind": "dispatch", "agent": "openai", "model": "gpt-6"}) + "\n")
        code, body = self.call("/api/dispatch/live?since=0")
        self.assertEqual(code, 200)
        self.assertEqual(len(body["events"]), 1)
        self.assertEqual(body["events"][0]["agent"], "openai")
        self.assertEqual(body["events"][0]["model"], "gpt-6")

    # -- POST /api/dispatch/cooldown ------------------------------------------------------
    def test_dispatch_cooldown_resets_and_rejects_unknown_agent(self):
        ladder.refuse("dispatch:claude", "quota", cooldown=900)
        self.assertGreater(ladder.cooling("dispatch:claude"), 0)
        code, body = self.call("/api/dispatch/cooldown", {"agent": "claude"})
        self.assertEqual(code, 200)
        self.assertEqual(ladder.cooling("dispatch:claude"), 0)
        self.assertEqual(body["agents"]["claude"]["cooling_s"], 0)

        code, body = self.call("/api/dispatch/cooldown", {"agent": "bogus"})
        self.assertEqual(code, 400)

    # -- POST /api/dispatch/test ----------------------------------------------------------
    def test_dispatch_test_requires_confirm_then_returns_the_fake_result(self):
        code, body = self.call("/api/dispatch/test", {"agent": "claude"})
        self.assertEqual(code, 400)
        self.assertIn("confirm", body["error"])

        fake = {"ok": True, "agent": "claude", "model": "opus", "answer": "ok"}
        with mock.patch.object(ds, "test_agent", return_value=fake):
            code, body = self.call("/api/dispatch/test", {"agent": "claude", "confirm": True})
        self.assertEqual(code, 200)
        self.assertEqual(body, fake)


class DispatchApiAuthTestCase(DispatchApiTestCase):
    """The token subclass pattern: same fixture and every test above (now sent WITH the token
    by call()'s default), plus one test that every /api/dispatch/* route refuses without it."""

    token = "s3cret-dispatch"

    def test_dispatch_routes_require_the_token(self):
        get_paths = ["/api/dispatch/state", "/api/dispatch/live?since=0"]
        post_calls = [
            ("/api/dispatch/switch", {"scope": "wiki", "switch": "mode", "value": "shadow"}),
            ("/api/dispatch/plan", {"changes": {}}),
            ("/api/dispatch/apply", {"changes": {}, "confirm": True}),
            ("/api/dispatch/cooldown", {"agent": "claude"}),
            ("/api/dispatch/test", {"agent": "claude", "confirm": True}),
        ]
        for path in get_paths:
            with self.subTest(path=path):
                code, _ = self.call(path, token=None)
                self.assertEqual(code, 401)
        for path, payload in post_calls:
            with self.subTest(path=path):
                code, _ = self.call(path, payload, token=None)
                self.assertEqual(code, 401)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


if __name__ == "__main__":
    unittest.main(verbosity=2)
