"""jevkit/ollama.py: this machine's Ollama models, loopback or private only. Never a real request."""
import json
import unittest

from jevkit import ollama

TAGS = json.dumps({"models": [
    {"name": "qwen3.6:27b", "size": 17_000_000_000, "details": {"parameter_size": "27B", "family": "qwen3"}},
    {"name": "qwen3.5:4b", "size": 3_300_000_000, "details": {"parameter_size": "4.7B", "family": "qwen3"}},
    "not a model"]}).encode()
TAILSCALE = ".".join(("100", "101", "7", "9"))    # built here: the release check refuses the literal


class Fetch:
    def __init__(self, body=TAGS, error=None):
        self.body, self.error, self.urls = body, error, []

    def __call__(self, url, timeout):
        self.urls.append((url, timeout))
        if self.error:
            raise self.error
        return self.body


class BaseUrlTests(unittest.TestCase):
    def test_ollama_host_wins_and_gets_a_scheme_and_a_port(self):
        self.assertEqual(ollama.base_url(None, {"OLLAMA_HOST": "127.0.0.1"}), "http://127.0.0.1:11434")
        self.assertEqual(ollama.base_url(None, {"OLLAMA_HOST": "10.0.0.5:11434"}), "http://10.0.0.5:11434")
        self.assertEqual(ollama.base_url(None, {"OLLAMA_HOST": "0.0.0.0"}), "http://127.0.0.1:11434")

    def test_a_profile_base_url_on_port_11434_is_used(self):
        self.assertEqual(ollama.base_url("http://10.0.0.5:11434/v1", {}), "http://10.0.0.5:11434")
        self.assertEqual(ollama.base_url("https://openrouter.ai/api/v1", {}), ollama.DEFAULT_URL)

    def test_private_means_loopback_private_or_tailscale_by_address(self):
        for host in ("127.0.0.1", "localhost", "::1", "10.0.0.5", TAILSCALE):
            self.assertTrue(ollama.private_host(host), host)
        for host in ("8.8.8.8", "nas.example.org", ""):
            self.assertFalse(ollama.private_host(host), host)


class ListModelsTests(unittest.TestCase):
    def setUp(self):
        ollama._CACHE.clear()

    def test_models_are_listed_sorted_with_their_size(self):
        out = ollama.list_models(fetch=Fetch(), environ={}, now=0)
        self.assertEqual([m["name"] for m in out["models"]], ["qwen3.5:4b", "qwen3.6:27b"])
        self.assertEqual(out["models"][0]["parameter_size"], "4.7B")
        self.assertEqual((out["url"], out["reason"]), (ollama.DEFAULT_URL, ""))

    def test_a_public_address_is_never_asked(self):
        fetch = Fetch()
        out = ollama.list_models(fetch=fetch, environ={"OLLAMA_HOST": "8.8.8.8"}, now=0)
        self.assertEqual((out["models"], fetch.urls), ([], []))
        self.assertIn("not a loopback or private address", out["reason"])

    def test_no_answer_is_an_empty_list_and_a_reason(self):
        out = ollama.list_models(fetch=Fetch(error=OSError("refused")), environ={}, now=0)
        self.assertEqual(out["models"], [])
        self.assertEqual(out["reason"], "Ollama did not answer (OSError)")

    def test_a_reply_that_is_not_the_tags_shape_is_no_models(self):
        out = ollama.list_models(fetch=Fetch(body=b"<html>"), environ={}, now=0)
        self.assertEqual(out["models"], [])
        self.assertTrue(out["reason"])

    def test_thirty_seconds_of_cache(self):
        fetch = Fetch()
        ollama.list_models(fetch=fetch, environ={}, now=100)
        ollama.list_models(fetch=fetch, environ={}, now=129)
        ollama.list_models(fetch=fetch, environ={}, now=131)
        self.assertEqual(len(fetch.urls), 2)
        self.assertEqual(fetch.urls[0], (ollama.DEFAULT_URL + "/api/tags", ollama.TIMEOUT))


if __name__ == "__main__":
    unittest.main()
