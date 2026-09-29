#!/usr/bin/env python3
"""The keys card: which Jev and OpenRouter keys are there, saving a pasted one, and one real Jev check.

A key goes in and never comes out. No response, log line, error or page carries it, not even in
part. Errors are fixed strings or an exception's type name.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from jevkit import client, key_setup, keystore  # noqa: E402

import routing_store  # noqa: E402

VARIABLES = {"typesafe": "TYPESAFE_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
CHECK_STATE = "The build finished and all tests passed."
CHECK_TIMEOUT = 10.0
NOT_A_KEY = "that does not look like an API key"


def _env_value(home: str, variable: str) -> str:
    """A variable's value in a profile's .env, for this process only. Nothing here returns it."""
    try:
        with open(os.path.join(home, ".env"), "r", encoding="utf-8") as fh:
            for line in fh:
                name, sep, value = line.partition("=")
                if sep and name.strip() == variable:
                    return value.strip().strip("'\"")
    except OSError:
        pass
    return ""


def machine_keys() -> Dict[str, bool]:
    """Which providers this machine's own store has a key for (environment, secret store, 0600 file)."""
    return {name: bool(keystore.resolve(name)) for name in keystore.PROVIDERS}


def jev_route(home: str, machine: Optional[Dict[str, bool]] = None) -> str:
    """How a turn in this profile reaches Jev: "typesafe", "openrouter" or "absent".

    The plugin's own rule (keystore.provider): TypeSafe first, then OpenRouter. A gateway also
    reads the profile's .env, which this process has not loaded, so that counts too.
    """
    machine = machine_keys() if machine is None else machine
    for name in keystore.PROVIDERS:
        if machine.get(name) or _env_value(home, VARIABLES[name]):
            return name
    return "absent"


def _model_for(provider: str) -> str:
    return client.OPENROUTER_MODEL if provider == "openrouter" else client.DEFAULT_MODEL


def state(hermes_home: str, *, accepts_keys: bool = True) -> Dict[str, Any]:
    machine = machine_keys()
    homes = routing_store._jev_homes(hermes_home)
    providers = {}
    for name in keystore.PROVIDERS:
        label, url, _ = key_setup.PROVIDER_PAGES[name]
        providers[name] = {"label": label, "get": url, "present": machine[name], "source": keystore.source(name),
                           "lanes": {profile: bool(_env_value(home, VARIABLES[name])) for profile, home in homes}}
    route = jev_route(hermes_home, machine)
    return {"providers": providers, "accepts_keys": accepts_keys, "jev_route": route,
            "jev_model": _model_for(route) if route != "absent" else ""}


def save(hermes_home: str, provider: str, key: Any) -> Dict[str, Any]:
    """What `jev setup-key` does: check the key with the provider, store it in the OS secret store
    or the 0600 file, and write it to every profile's .env. Returns where it went, never the key."""
    if provider not in VARIABLES:
        raise ValueError("provider must be typesafe or openrouter")
    if not isinstance(key, str) or not keystore.looks_like_key(key):
        return {"ok": False, "status": "rejected", "reason": NOT_A_KEY}
    try:
        result = key_setup._finish(key.strip(), True, True, Path(hermes_home), provider)
    except ValueError:
        return {"ok": False, "status": "rejected", "reason": NOT_A_KEY}
    except Exception as error:  # noqa: BLE001 - the type name only: a message could quote the key
        return {"ok": False, "status": "rejected", "reason": f"could not store the key ({type(error).__name__})"}
    if result.get("status") != "stored":
        return {"ok": False, "status": "rejected",
                "reason": f"{key_setup.PROVIDER_PAGES[provider][0]} did not accept that key"}
    return {"ok": True, "status": "stored", "verified": result.get("verified"), "provider": provider,
            "stored_in": list(result.get("stored_in") or []), "hermes_env_files": result.get("hermes_env_files", 0)}


def check(hermes_home: str, provider: str, *, transport: Optional[client.Transport] = None) -> Dict[str, Any]:
    """One real decisions request through `provider`, the smallest there is, with no retry.
    The key comes from this machine's store, else the default profile's .env."""
    if provider not in VARIABLES:
        raise ValueError("provider must be typesafe or openrouter")
    key = keystore.resolve(provider) or _env_value(hermes_home, VARIABLES[provider])
    if not key:
        return {"ok": False, "provider": provider, "model": _model_for(provider), "error": "no_key"}
    try:
        reply = client.ask(CHECK_STATE, {"ok": client.noul("The text reports a successful outcome")},
                           api_key=key, provider=provider, timeout=CHECK_TIMEOUT, retries=0, transport=transport)
    except client.JevError as error:
        return {"ok": False, "provider": provider, "model": _model_for(provider), "error": error.code}
    return {"ok": True, "provider": provider, "model": reply.get("model") or _model_for(provider),
            "latency_ms": reply.get("latency_ms"), "answer": round(float(reply["answers"]["ok"]["noul"]), 2)}
