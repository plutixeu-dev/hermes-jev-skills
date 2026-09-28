#!/usr/bin/env python3
"""Read and write everything the hermes-dispatch plugin reads: mode, notice, privacy classes,
per-agent settings, the frontier order, cooldowns and a one-off test call per agent.

This is the data layer only; the HTTP routes live in server.py and the page in
static/index.html. Design rules, matching routing_store:

  * Safe writes: a preview first (`plan`), a backup of an existing file before `apply` changes
    it, a temporary file plus `os.replace`, and a verified read-back.
  * No secrets, no turn text: an OpenRouter key is reported present or absent, never read back;
    a dispatch log row is reduced to its decision fields (see `live`).
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import yaml

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from jevkit import dispatch, ladder  # noqa: E402
from jevkit import agents as agents_mod  # noqa: E402

import routing_store  # noqa: E402

AGENTS = ("claude", "openai", "openrouter")          # the order the page lists them
FRONTIER = ("claude", "openai")                       # what frontier_order may hold
SWITCHES = {"mode": ("off", "shadow", "on"), "notice": ("off", "on")}
CLAUDE_MODELS = ("opus", "sonnet", "haiku")           # offered in the page; any valid id is accepted
TEST_PROMPT = "Reply with only the word: ok"
TEST_TIMEOUT = 120

# Shared with routing_store: one dashboard, one backups tree, disambiguated by file name.
_BACKUP_DIRNAME = "model-routing-dashboard"
_CHANGE_SECTIONS = ("profiles", "agents", "order")
_AGENT_FIELDS = ("enabled", "model", "only_repo")


# ------------------------------------------------------------------ small file readers


def _read_json(path: Path) -> Dict[str, Any]:
    """A JSON object from disk, or {} for anything missing, unreadable or not an object."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_yaml(path: Path) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _yaml_plugin_setting(config_path: Path, plugin: str, key: str) -> Any:
    """`plugins.entries.<plugin>.settings.<key>`, or the legacy `.config.<key>`, or None."""
    entry = ((_read_yaml(config_path).get("plugins") or {}).get("entries") or {}).get(plugin)
    if not isinstance(entry, dict):
        return None
    for section in ("settings", "config"):
        value = (entry.get(section) or {}).get(key)
        if value is not None:
            return value
    return None


def _plugin_enabled(config_path: Path, plugin: str) -> bool:
    enabled = (_read_yaml(config_path).get("plugins") or {}).get("enabled")
    return isinstance(enabled, list) and plugin in enabled


def _scalar_from_files(paths: Sequence[Path], key: str) -> Optional[str]:
    """The value the layered dispatch.json files set for a plain string key, most specific wins.

    Used only to label WHERE mode/notice came from; the effective value the plugin actually
    uses still comes from dispatch.load_policy.
    """
    value = None
    for path in paths:
        candidate = _read_json(Path(path)).get(key)
        if isinstance(candidate, str):
            value = candidate
    return value


def _fleet_path(hermes_home: str) -> Path:
    return Path(hermes_home) / "jev" / "dispatch.json"


def _read_fleet(path: Path) -> Dict[str, Any]:
    """The fleet file's own content, verbatim. {} when missing; raises ValueError when broken."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as error:
        raise ValueError(f"{path} is not valid JSON; fix it by hand before changing it here") from error
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object; fix it by hand before changing it here")
    return data


def _fleet_file_state(path: Path) -> str:
    if not path.is_file():
        return "missing"
    try:
        _read_fleet(path)
    except ValueError:
        return "broken"
    return "ok"


def _llm_execution_available() -> Optional[bool]:
    """True: this Hermes has the middleware. None: unknown here (not installed). False: broken."""
    try:
        from hermes_cli.middleware import LLM_EXECUTION_MIDDLEWARE  # type: ignore  # noqa: F401
    except ImportError:
        return None
    except Exception:  # noqa: BLE001 - anything else means "no", not "unknown"
        return False
    return True


def _jev_routing_for(root: Path, home: Path, config_path: Path) -> str:
    """hermes-jev's routing switch, read the way that plugin reads it (see hermes-dispatch's
    own `_jev_routing_active`): the profile's own state, else the root's, else config.yaml."""
    root_state = _read_json(root / "jev" / "state.json")
    home_state = _read_json(home / "jev" / "state.json") if home != root else {}
    value = {**root_state, **home_state}.get("routing")
    if value is None:
        value = _yaml_plugin_setting(config_path, "hermes-jev", "routing")
    return str(value or "off").lower()


def _scoped_setting(state_path: Path, config_path: Path, paths: Sequence[Path], key: str) -> Dict[str, str]:
    """mode/notice's value and where it came from: the state file, config.yaml, dispatch.json
    or the code's own default - in that order, matching hermes-dispatch's own `_setting`."""
    state = _read_json(state_path)
    if key in state:
        return {"value": str(state[key]).lower(), "source": "dashboard"}
    cfg_value = _yaml_plugin_setting(config_path, "hermes-dispatch", key)
    if cfg_value is not None:
        return {"value": str(cfg_value).lower(), "source": "config.yaml"}
    file_value = _scalar_from_files(paths, key)
    if file_value is not None:
        return {"value": file_value.lower(), "source": "dispatch.json"}
    return {"value": "off", "source": "default"}


def _dig(data: Any, dotted: str) -> Any:
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


# ------------------------------------------------------------------ state


def _agent_rows(root_policy: Dict[str, Any], root_paths: Sequence[Path]) -> Dict[str, Any]:
    report = dispatch.check_agents(root_policy, paths=root_paths)
    agents: Dict[str, Any] = {}
    for name in AGENTS:
        row = report["agents"].get(name) or {}
        entry = {"kind": row.get("kind") or "", "enabled": bool(row.get("enabled")),
                 "model": row.get("model") or "", "available": bool(row.get("available")),
                 "cooling_s": row.get("cooling_s", 0)}
        if name == "claude":
            entry["only_repo"] = bool((root_policy.get("agents") or {}).get("claude", {}).get("only_repo"))
        agents[name] = entry
    return agents


def state(hermes_home: str) -> Dict[str, Any]:
    """Everything the dashboard shows: the fleet file, the agents, and every profile's own view."""
    root = Path(hermes_home)
    fleet_path = _fleet_path(hermes_home)
    root_paths = dispatch.policy_paths(root=root, home=root)
    root_policy = dispatch.load_policy(paths=root_paths)
    agents = _agent_rows(root_policy, root_paths)

    profiles: Dict[str, Any] = {}
    for name, home in routing_store._jev_homes(hermes_home):
        home_path = Path(home)
        paths = dispatch.policy_paths(root=root, home=home_path)
        policy = dispatch.load_policy(paths=paths)
        config_path = home_path / "config.yaml"
        state_path = home_path / "jev" / "dispatch-state.json"
        profiles_map = policy.get("profiles") if isinstance(policy.get("profiles"), dict) else {}
        privacy_value = dispatch.privacy_class("", profile=name, policy=policy)[0]
        jev_routing = _jev_routing_for(root, home_path, config_path)
        profiles[name] = {
            "home": str(home_path),
            "mode": _scoped_setting(state_path, config_path, paths, "mode"),
            "notice": _scoped_setting(state_path, config_path, paths, "notice"),
            "privacy": {"value": privacy_value, "source": "dispatch.json" if name in profiles_map else "default"},
            "jev_routing": jev_routing,
            "conflict": jev_routing in ("shadow", "on") and _plugin_enabled(config_path, "hermes-jev"),
            "plugin_enabled": _plugin_enabled(config_path, "hermes-dispatch"),
            "policy_files": [str(p) for p in paths],
            "broken_files": list(policy.get("broken_files") or []),
        }

    return {
        "fleet_file": str(fleet_path),
        "fleet_file_state": _fleet_file_state(fleet_path),
        "plugin": {"installed": (root / "plugins" / "hermes-dispatch").is_dir(),
                   "llm_execution": _llm_execution_available()},
        "default_privacy": root_policy.get("default_privacy") or "highly_sensitive",
        "order": root_policy.get("frontier_order") or {},
        "agents": agents,
        "openrouter_key": bool(agents.get("openrouter", {}).get("available")),
        "profiles": profiles,
    }


# ------------------------------------------------------------------ switches (mode, notice)


def set_switch(hermes_home: str, scope: str, name: str, value: str) -> Dict[str, Any]:
    """mode/notice for one profile, or `__all__` for every one of them, default included."""
    if name not in SWITCHES or value not in SWITCHES[name]:
        raise ValueError(f"{name} must be one of {SWITCHES.get(name)}")
    homes = dict(routing_store._jev_homes(hermes_home))
    if scope != "__all__" and scope not in homes:
        raise ValueError(f"unknown profile: {scope!r}")

    def write(home: str) -> None:
        path = os.path.join(home, "jev", "dispatch-state.json")
        data = _read_json(Path(path))
        if data.get(name) == value:
            return
        data[name] = value
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".dashboard-tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, path)

    for home in (homes.values() if scope == "__all__" else [homes[scope]]):
        write(home)
    return {"ok": True, "scope": scope, "switch": name, "value": value, **state(hermes_home)}


# ------------------------------------------------------------------ plan / apply


def plan(hermes_home: str, changes: Dict[str, Any]) -> Dict[str, Any]:
    """Before/after rows for the fleet file (`<hermes root>/jev/dispatch.json`); writes nothing."""
    if not isinstance(changes, dict):
        raise ValueError("changes must be an object")
    unknown = set(changes) - set(_CHANGE_SECTIONS)
    if unknown:
        raise ValueError(f"unknown change: {sorted(unknown)[0]!r}")

    path = _fleet_path(hermes_home)
    before = _read_fleet(path)                       # raises ValueError when the file is broken
    known_profiles = {name for name, _ in routing_store._jev_homes(hermes_home)}
    after = copy.deepcopy(before)
    rows: List[Dict[str, Any]] = []

    def note(setting: str, old: Any, new: Any) -> None:
        if old != new:
            rows.append({"setting": setting, "before": old, "after": new})

    if "profiles" in changes:
        spec = changes["profiles"]
        if not isinstance(spec, dict):
            raise ValueError("profiles must be an object")
        current = before.get("profiles") if isinstance(before.get("profiles"), dict) else {}
        updated = dict(current)
        for name, klass in spec.items():
            if name not in known_profiles:
                raise ValueError(f"unknown profile: {name!r}")
            if klass not in dispatch.PRIVACY:
                raise ValueError(f"profiles.{name} must be one of {dispatch.PRIVACY}")
            note(f"profiles.{name}", current.get(name, ""), klass)
            updated[name] = klass
        after["profiles"] = updated

    if "agents" in changes:
        spec = changes["agents"]
        if not isinstance(spec, dict):
            raise ValueError("agents must be an object")
        current_agents = before.get("agents") if isinstance(before.get("agents"), dict) else {}
        updated_agents = copy.deepcopy(current_agents)
        for name, fields in spec.items():
            if name not in AGENTS:
                raise ValueError(f"unknown agent: {name!r}")
            if not isinstance(fields, dict):
                raise ValueError(f"agents.{name} must be an object")
            bad = set(fields) - set(_AGENT_FIELDS)
            if bad:
                raise ValueError(f"agents.{name}.{sorted(bad)[0]} is not a field the dashboard writes")
            current = dict(current_agents.get(name) or {})
            updated = dict(current)
            defaults = dispatch.DEFAULT_POLICY["agents"].get(name, {})
            if "enabled" in fields:
                value = fields["enabled"]
                if not isinstance(value, bool):
                    raise ValueError(f"agents.{name}.enabled must be true or false")
                note(f"agents.{name}.enabled", current.get("enabled", defaults.get("enabled", False)), value)
                updated["enabled"] = value
            if "model" in fields:
                model = fields["model"]
                if not isinstance(model, str) or (model and not agents_mod._MODEL_NAME.fullmatch(model)):
                    raise ValueError(f"agents.{name}.model is not a usable model id")
                note(f"agents.{name}.model", current.get("model", defaults.get("model", "")), model)
                updated["model"] = model
            if "only_repo" in fields:
                if name != "claude":
                    raise ValueError(f"only_repo applies to claude only, not {name}")
                value = fields["only_repo"]
                if not isinstance(value, bool):
                    raise ValueError(f"agents.{name}.only_repo must be true or false")
                note(f"agents.{name}.only_repo", current.get("only_repo", defaults.get("only_repo", True)), value)
                updated["only_repo"] = value
            updated_agents[name] = updated
        after["agents"] = updated_agents

    if "order" in changes:
        spec = changes["order"]
        if not isinstance(spec, dict):
            raise ValueError("order must be an object")
        bad_kinds = set(spec) - {"repo", "default"}
        if bad_kinds:
            raise ValueError(f"unknown order: {sorted(bad_kinds)[0]!r}")
        current_order = before.get("frontier_order") if isinstance(before.get("frontier_order"), dict) else {}
        updated_order = dict(current_order)
        for kind, sequence in spec.items():
            if (not isinstance(sequence, list) or len(sequence) != len(FRONTIER)
                    or set(sequence) != set(FRONTIER) or len(set(sequence)) != len(sequence)):
                raise ValueError(f"order.{kind} must list {list(FRONTIER)} once each")
            default_order = list(dispatch.DEFAULT_POLICY["frontier_order"][kind])
            note(f"frontier_order.{kind}", current_order.get(kind, default_order), sequence)
            updated_order[kind] = sequence
        after["frontier_order"] = updated_order

    return {"rows": rows, "text": json.dumps(after, indent=2, sort_keys=True)}


def _overrides(hermes_home: str, rows: Sequence[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Where a named profile's own dispatch.json disagrees with what the fleet now says."""
    root = Path(hermes_home)
    out: List[Dict[str, str]] = []
    for name, home in routing_store._jev_homes(hermes_home):
        home_path = Path(home)
        if home_path == root:
            continue                              # the default profile's own file IS the fleet file
        own_path = home_path / "jev" / "dispatch.json"
        own = _read_json(own_path)
        for row in rows:
            value = _dig(own, row["setting"])
            if value is not None and value != row["after"]:
                out.append({"profile": name, "setting": row["setting"], "file": str(own_path)})
    return out


def apply(hermes_home: str, changes: Dict[str, Any], backup_root: Optional[str] = None) -> Dict[str, Any]:
    """Write `changes` to the fleet file, with a backup, a temp file + replace, and a read-back."""
    path = _fleet_path(hermes_home)
    planned = plan(hermes_home, changes)               # raises ValueError for bad input or a broken file
    rows, text = planned["rows"], planned["text"]
    if not rows:
        return {"ok": True, "changed": 0, "rows": [], "backup": None, "verified": True,
                "mismatches": [], "overridden": [], "message": "nothing to change"}

    backup = None
    if path.is_file():
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        bdir = Path(backup_root or os.path.join(hermes_home, "backups", _BACKUP_DIRNAME)) / stamp
        bdir.mkdir(parents=True, exist_ok=True)
        backup = str(bdir / path.name)
        shutil.copy2(path, backup)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".dashboard-tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)

    after = json.loads(path.read_text(encoding="utf-8"))
    mismatches = []
    for row in rows:
        got = _dig(after, row["setting"])
        if got != row["after"]:
            mismatches.append({**row, "read_back": got})
    verified = not mismatches

    return {"ok": verified, "changed": len(rows), "rows": rows, "backup": backup, "verified": verified,
            "mismatches": mismatches, "overridden": _overrides(hermes_home, rows),
            "message": ("applied and verified; takes effect on the next message, no restart" if verified
                        else "write did not verify; restore from backup")}


# ------------------------------------------------------------------ live


_EVENT_FIELDS = ("ts", "profile", "mode", "live", "agent", "model", "reason", "downgraded",
                 "privacy", "privacy_why", "would_send_chars")


def live(hermes_home: str, since: float = 0.0, limit: int = 100) -> Dict[str, Any]:
    """Recent dispatch decisions across every profile, newest first, decision fields only."""
    events: List[Dict[str, Any]] = []
    for name, home in routing_store._jev_homes(hermes_home):
        for line in routing_store._tail_lines(os.path.join(home, "logs", "jev-decisions.jsonl")):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or row.get("kind") != "dispatch":
                continue
            if float(row.get("ts") or 0) <= since:
                continue
            row.setdefault("profile", name)
            reduced = {key: row.get(key) for key in _EVENT_FIELDS}
            reduced["niveau"] = (row.get("triage") or {}).get("niveau")
            reduced["attempts"] = [{"agent": a.get("agent"), "error": a.get("error")}
                                   for a in (row.get("attempts") or []) if isinstance(a, dict)]
            events.append(reduced)
    events.sort(key=lambda r: r.get("ts") or 0, reverse=True)
    return {"now": time.time(), "events": events[:limit]}


# ------------------------------------------------------------------ cooldowns and a test call


def reset_cooldown(hermes_home: str, agent: str) -> Dict[str, Any]:
    if agent not in AGENTS:
        raise ValueError(f"unknown agent: {agent!r}")
    ladder.clear(f"dispatch:{agent}")
    return state(hermes_home)


def test_agent(hermes_home: str, agent: str, *, runners: Optional[Dict[str, Any]] = None,
               transport: Optional[Any] = None) -> Dict[str, Any]:
    """One fixed prompt to `agent`, whether or not it is enabled: this is a test call, not a route."""
    if agent not in AGENTS:
        raise ValueError(f"unknown agent: {agent!r}")
    root = Path(hermes_home)
    policy = dispatch.load_policy(paths=dispatch.policy_paths(root=root, home=root))
    model = (policy.get("agents") or {}).get(agent, {}).get("model") or ""
    chosen = {"agent": agent, "model": model}
    try:
        result = dispatch.run_agent(chosen, TEST_PROMPT, policy=policy, timeout=TEST_TIMEOUT,
                                    runners=runners, transport=transport)
    except agents_mod.AgentError as error:
        return {"ok": False, "agent": agent, "error": error.code, "detail": error.detail}
    except Exception as error:  # noqa: BLE001 - a broken adapter is a failed test, not a lost call
        return {"ok": False, "agent": agent, "error": "failed", "detail": type(error).__name__}
    return {"ok": True, "agent": agent, "model": result.model or model, "answer": result.text[:80]}
