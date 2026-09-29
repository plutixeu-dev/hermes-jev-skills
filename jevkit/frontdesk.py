"""The front desk's shared rules, read by both plugins and the dashboard so they cannot disagree.

* Which models are the receptionist: config.yaml's `model.default` and Hermes's fallback chain.
* When a chat pinned another model: its model is none of those (a `/model` in that chat).
* The front desk's mode: hermes-dispatch's switch, then config.yaml, then dispatch.json.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping

from . import catalog as catalog_mod

MODES = ("off", "shadow", "on")


def _prefixes(providers: Iterable[Any]) -> List[str]:
    out: List[str] = []
    for name in providers:
        if not isinstance(name, str) or not name.strip():
            continue
        name = name.strip()
        for alias in (name, catalog_mod.HERMES_ALIASES.get(name, name)):
            if alias not in out:
                out.append(alias)
    return out


def bare_model(model: Any, providers: Iterable[Any] = ()) -> str:
    """The model id without a leading "<provider>:" that names one of `providers`.

    Nothing else comes off. Splitting on the first colon read `qwen3.5:4b` as the model "4b", so an
    Ollama receptionist always looked pinned and Jev was never asked (2026-09-29, turn 20:57:09).
    An OpenRouter variant such as `x/y:free` keeps its tag the same way.
    """
    text = str(model or "").strip()
    for prefix in _prefixes(providers):
        if text.startswith(prefix + ":"):
            return text[len(prefix) + 1:]
    return text


def _fallbacks(config: Mapping[str, Any]) -> List[Dict[str, str]]:
    """Hermes's fallback chain: `fallback_providers` first, then the legacy single `fallback_model`."""
    out: List[Dict[str, str]] = []
    for raw in (config.get("fallback_providers"), config.get("fallback_model")):
        entries = [raw] if isinstance(raw, Mapping) else raw if isinstance(raw, list) else []
        for entry in entries:
            if isinstance(entry, Mapping) and entry.get("provider") and entry.get("model"):
                out.append({"provider": str(entry["provider"]), "model": str(entry["model"])})
    return out


def receptionist(config: Any) -> Dict[str, Any]:
    """The chat model a profile's config.yaml names, and the fallbacks Hermes may use for it.

    `model.default` (Hermes also reads `model.model`), or `model` itself when it is a string.
    """
    config = config if isinstance(config, Mapping) else {}
    model = config.get("model")
    if isinstance(model, Mapping):
        default, provider = model.get("default") or model.get("model") or "", model.get("provider") or ""
    else:
        default, provider = model or "", ""
    return {"model": str(default).strip(), "provider": str(provider).strip(), "fallbacks": _fallbacks(config)}


def is_pinned(model: Any, provider: Any, config: Any) -> bool:
    """True when this chat runs a model its config.yaml does not name: a `/model` in this chat.

    The receptionist and every fallback are never a pin: the dashboard saved the one, and Hermes
    picks the other after a rate limit or an outage. With no receptionist saved, or no model on
    the wire, there is nothing to compare, so it is not a pin.
    """
    desk = receptionist(config)
    if not desk["model"] or not str(model or "").strip():
        return False
    providers = [provider, desk["provider"]] + [entry["provider"] for entry in desk["fallbacks"]]
    chat = bare_model(model, providers)
    named = [desk["model"]] + [entry["model"] for entry in desk["fallbacks"]]
    return all(bare_model(name, providers) != chat for name in named)


def desk_mode(state: Any, config_value: Any, policy: Any) -> str:
    """hermes-dispatch's mode for one profile, read the way that plugin reads it: its switch
    (`<home>/jev/dispatch-state.json`), then its setting in config.yaml, then `mode` in
    dispatch.json, then off. A value that is not off, shadow or on reads as off."""
    for value in ((state if isinstance(state, Mapping) else {}).get("mode"), config_value,
                  (policy if isinstance(policy, Mapping) else {}).get("mode")):
        if value is not None:
            value = str(value).lower()
            return value if value in MODES else "off"
    return "off"
