# Dispatch in the Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Everything the receptionist-dispatch plugin reads can be seen and set from `jev dashboard`, so nobody has to type `/dispatch …` or `/jev routing …` in a chat or edit `dispatch.json` by hand.

**Architecture:** One new store module, `router-dashboard/dispatch_store.py`, reads and writes the same files the `hermes-dispatch` plugin reads:
- mode and notice live in each profile's `jev/dispatch-state.json`, the file `/dispatch` writes;
- everything else lives in the fleet file `<hermes root>/jev/dispatch.json`.

It computes each profile's effective settings with `jevkit.dispatch.load_policy`, the code the plugin runs. `server.py` exposes it under `/api/dispatch/*`, and `static/index.html` gets one new card.

The card is marked as new in this fork, and so are the docs.

**Tech stack:**
- jevkit: Python 3.9+, standard library.
- Dashboard: standard library plus PyYAML, as it already is.
- Tests: `unittest` with offline fakes, and a real server on an ephemeral port as in `router-dashboard/tests/test_server.py`.

**Spec:** the person's request. This is where the rulings trace back to.
- "Build it into the dashboard."
- "I don't want to switch things on and off in the chat or by hand, only via the dashboard."
- "Make clear what is a feature we added compared to what already existed."

---

## Global constraints

- **The dashboard controls everything the plugin reads.** For every profile:
  - mode (off, shadow, on);
  - notice (off, on);
  - privacy class;
  - per agent: on or off, model, and `only_repo` for claude;
  - the frontier order;
  - clearing a cooldown;
  - a test call per agent;
  - the one-classifier conflict with Jev routing, and its fix.

  The chat commands keep working. The dashboard writes the same files they write.
- **Where each value lives**:
  - Mode and notice: `<profile home>/jev/dispatch-state.json`, keys `mode` and `notice`. The default profile's home is the Hermes root. For "All profiles", write every profile's file.
  - Privacy, agents and order: `<hermes root>/jev/dispatch.json`, under the keys `profiles`, `agents.<name>.enabled|model|only_repo` and `frontier_order.repo|default`.
- **Effective values are the plugin's.**
  - Mode and notice: the state file, then `plugins.entries.hermes-dispatch.settings.<name>` in the profile's `config.yaml` (or legacy `.config`), then `mode`/`notice` in the effective policy, then `off`.
  - The rest: `jevkit.dispatch.load_policy(paths=[<XDG>/jev/dispatch.json, <root>/jev/dispatch.json, <profile home>/jev/dispatch.json])`, where the third path applies only to a named profile.
- **Safe writes**, as `routing_store.apply_changes` does them:
  - a preview first; POST `apply` requires `confirm: true`;
  - a backup under `<hermes root>/backups/model-routing-dashboard/<UTC stamp>/` if the file existed;
  - a temporary file and `os.replace`;
  - a read-back, verified against what was planned.

  A `dispatch.json` that exists but is not a JSON object is never overwritten. The plan and apply calls refuse, naming the file. Writing never restarts a gateway.
- **Values accepted:**
  - privacy classes: `public`, `private`, `highly_sensitive`;
  - agents: `openai`, `claude`, `openrouter`;
  - order entries: `claude` and `openai` only, each once;
  - model: empty, or matching `jevkit.agents._MODEL_NAME`;
  - booleans must be JSON booleans.

  Anything else is refused with a 400 naming the field.
- **No secrets and no text.**
  - Keys are never read, shown or stored; the page shows only whether an OpenRouter key is present.
  - Dispatch log rows are reduced to decision fields. Turn text and answers never appear. A test call returns only `ok`, the model, the error code, and the first 80 characters of the answer to its fixed prompt.
- **Marked as new.**
  - The card's title badge reads `new in this fork`.
  - Its first line says: "Added in this fork: the upstream hermes-jev-skills has no dispatch."
  - README gets a section "What this fork adds".
- **Commands every task must pass**, on `python3` and on a Python 3.9, both with PyYAML:
  - `env -u TYPESAFE_API_KEY -u OPENROUTER_API_KEY python3 -m unittest discover -s tests`
  - `python3 -m unittest discover -s router-dashboard/tests`
  - `python3 scripts/check_release.py`
- **Commits:** the task's message line, a blank line, then exactly these two trailers:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Sa4DrKFGyrJYKnfEULQLcz
  ```
  Stage only the task's files. Never write key-shaped literals (`sk-…`, `ghp_…`, `AKIA…`, `AIza…`) or `/home/<name>/` paths.

## File structure

| File | Responsibility |
|---|---|
| `jevkit/dispatch.py` | `policy_paths(root, home)`, `load_policy(paths=…)`, `check_agents(…, paths=…)`: one profile's view, asked for explicitly |
| `router-dashboard/dispatch_store.py` (new) | read and write dispatch settings; state, switches, plan and apply, live, cooldown, test call |
| `router-dashboard/routing_store.py` | `jev_live` leaves `kind: "dispatch"` rows to the dispatch panel |
| `router-dashboard/server.py` | `/api/dispatch/*` routes; `HERMES_HOME` defaults to `--hermes-home` |
| `router-dashboard/static/index.html` | the "Receptionist dispatch" card |
| `router-dashboard/tests/test_dispatch_store.py` (new), `router-dashboard/tests/test_server.py` | tests |
| `README.md`, `router-dashboard/README.md`, `docs/receptionist-dispatch.md`, `CHANGELOG.md` | what this fork adds; rollout from the dashboard |

---

### Task 1: The dispatch store

**Files:**
- Modify: `jevkit/dispatch.py`, `tests/test_dispatch.py`, `router-dashboard/routing_store.py`
- Create: `router-dashboard/dispatch_store.py`, `router-dashboard/tests/test_dispatch_store.py`

- [ ] **Step 1: jevkit, explicit paths (test first).**
  - `policy_paths(root: Optional[Path] = None, home: Optional[Path] = None) -> List[Path]`. `None` means `catalog_mod.hermes_root()` / `hermes_home()`, as today. `JEV_DISPATCH_POLICY` still replaces everything.
  - `load_policy(path: Optional[Path] = None, *, paths: Optional[Sequence[Path]] = None)`. With `paths`, it layers exactly those, least specific first.
  - `check_agents(policy, *, which=None, cooling=None, has_key=None, paths=None)`. With `paths`, `policy_files` lists those.
  - Tests in `tests/test_dispatch.py`:
    - explicit root and home give the XDG file, `<root>/jev/dispatch.json` and `<home>/jev/dispatch.json`, in that order;
    - home equal to root gives two paths;
    - `load_policy(paths=[a, b])` lets b's `agents.openai.enabled` win over a's;
    - `check_agents(policy, paths=[x])["policy_files"] == [str(x)]`;
    - the no-argument behaviour is unchanged.

- [ ] **Step 2: `routing_store.jev_live` skips dispatch rows (test first).** A `jev-decisions.jsonl` holding a `route` row and a `dispatch` row gives `events` with only the route row. The dispatch rows belong to the dispatch panel.

- [ ] **Step 3: write `router-dashboard/tests/test_dispatch_store.py`, then `dispatch_store.py`.**

The module imports jevkit from the repo root: `sys.path.insert(0, <repo root>)` where the repo root is `router-dashboard/..`. It reuses `routing_store._jev_homes`, `_tail_lines` and `read_config` where they fit.

Public functions, with the exact shapes the server and page rely on:

```python
AGENTS = ("claude", "openai", "openrouter")          # the order the page lists them
FRONTIER = ("claude", "openai")                       # what frontier_order may hold
SWITCHES = {"mode": ("off", "shadow", "on"), "notice": ("off", "on")}
CLAUDE_MODELS = ("opus", "sonnet", "haiku")           # offered in the page; any valid id is accepted
TEST_PROMPT = "Reply with only the word: ok"
TEST_TIMEOUT = 120

def state(hermes_home: str) -> dict: ...
def set_switch(hermes_home: str, scope: str, name: str, value: str) -> dict: ...
def plan(hermes_home: str, changes: dict) -> dict: ...
def apply(hermes_home: str, changes: dict, backup_root: Optional[str] = None) -> dict: ...
def live(hermes_home: str, since: float = 0.0, limit: int = 100) -> dict: ...
def reset_cooldown(hermes_home: str, agent: str) -> dict: ...
def test_agent(hermes_home: str, agent: str, *, runners=None, transport=None) -> dict: ...
```

`state()` returns:

```json
{
  "fleet_file": "<root>/jev/dispatch.json",
  "fleet_file_state": "missing | ok | broken",
  "plugin": {"installed": true, "llm_execution": true},
  "default_privacy": "highly_sensitive",
  "order": {"repo": ["claude", "openai"], "default": ["openai", "claude"]},
  "agents": {"claude": {"kind": "claude", "enabled": false, "model": "", "only_repo": true,
                        "available": true, "cooling_s": 0}, "...": {}},
  "openrouter_key": false,
  "profiles": {"default": {"home": "...", "mode": {"value": "off", "source": "default"},
                           "notice": {"value": "off", "source": "default"},
                           "privacy": {"value": "highly_sensitive", "source": "default"},
                           "jev_routing": "off", "conflict": false, "plugin_enabled": true,
                           "policy_files": ["..."], "broken_files": []}}
}
```

What each part means:
- **Sources:**
  - mode and notice come from `dashboard` (the state file), `config.yaml`, `dispatch.json` or `default`;
  - privacy is `dispatch.json` when the profile is listed in the effective `profiles`, else `default`.
- **`plugin.installed`:** `<root>/plugins/hermes-dispatch` is a directory.
- **`plugin.llm_execution`:** importing `LLM_EXECUTION_MIDDLEWARE` from `hermes_cli.middleware` succeeds (`true`), raises ImportError (`null`, meaning unknown in this interpreter), or raises anything else (`false`).
- **`plugin_enabled`:** `hermes-dispatch` is in that profile's `config.yaml` `plugins.enabled`.
- **`jev_routing`:** the profile's own `jev/state.json` `routing`, else the root's, else `plugins.entries.hermes-jev.settings.routing` (or `.config.routing`), else `off`.
- **`conflict`:** `jev_routing` is shadow or on, and `hermes-jev` is in that profile's `plugins.enabled`.
- **`agents`:** from `check_agents` over the root's view, plus `only_repo` for claude.
- **`openrouter_key`:** that row's `available`, a boolean.

`set_switch`:
- `name` must be in `SWITCHES` and `value` in its tuple.
- `scope` is a profile name or `"__all__"`, which means every profile's file, default included.
- It writes atomically and keeps other keys, then returns `{"ok": true, "scope", "switch", "value", **state(hermes_home)}`.

`plan` and `apply`:
- **Input:** `changes` is `{"profiles": {name: class}, "agents": {name: {"enabled": bool, "model": str, "only_repo": bool}}, "order": {"repo": [...], "default": [...]}}`. Every part is optional. `only_repo` is claude's only, and profile names must be known profiles.
- **`plan` output:** `{"rows": [{"setting": "agents.claude.model", "before": "", "after": "opus"}, ...], "text": "<the new file, indent 2, sorted keys>"}`. The file is never written, and a change to the same value gives no row.
- **`apply` output:** `{"ok", "changed", "rows", "backup", "verified", "mismatches", "overridden", "message"}`.
  - The write keeps every key it does not change.
  - `overridden` lists `{"profile", "setting", "file"}` where a profile's own `dispatch.json` sets that same value differently. It is information, not a failure.
  - With no rows, it writes nothing: `changed: 0`, `message: "nothing to change"`.
- **Refusals:** both raise `ValueError` for bad input and for a broken existing fleet file.

`live`:
- Reads `kind == "dispatch"` rows from every profile's `logs/jev-decisions.jsonl`, newest first.
- Each row is reduced to `ts, profile, mode, live, agent, model, reason, downgraded, privacy, privacy_why, would_send_chars, niveau` (from `triage.niveau`) and `attempts` as `[{agent, error}]`.
- Returns `{"now", "events"}`.

`reset_cooldown`:
- Checks `agent` against `AGENTS`.
- Calls `jevkit.ladder.clear("dispatch:" + agent)` and returns `state()`.

`test_agent`:
- Runs `dispatch.run_agent(chosen, TEST_PROMPT, policy=root policy, timeout=TEST_TIMEOUT, runners=runners, transport=transport)`, where `chosen = {"agent": agent, "model": <its model>}`. It runs whether or not the agent is enabled, because it is a test.
- Success: `{"ok": true, "agent", "model", "answer": answer[:80]}`.
- Failure: `{"ok": false, "agent", "error": code, "detail": detail}`, where the failure is an `AgentError` with its code and detail. Any other exception gives `ok: false`, error `failed`, and the exception's type name as the detail.

Tests use a temporary Hermes home: a root `config.yaml` and `profiles/wiki/config.yaml`, with `JEV_LADDER_STATE` and `XDG_CONFIG_HOME` pointed into the temp dir. They cover:
- the state shape on an empty home: every default, every source `default`, `fleet_file_state` `missing`;
- mode sources: the state file beats `config.yaml`, which beats `dispatch.json`;
- `set_switch`:
  - for one profile it writes only that file;
  - `__all__` writes both files and keeps other keys;
  - bad names, values and scopes raise;
- the conflict: routing `on` with `hermes-jev` enabled, then with `hermes-jev` not enabled;
- plan and apply:
  - `plan` writes nothing;
  - `apply` writes, backs up an existing file, verifies, keeps unknown keys, and gives `nothing to change` when there is nothing to change;
  - the refusals: a broken fleet file, class `geheim`, model `-x`, `"enabled": "true"`, order `["claude", "claude"]`, an unknown profile, and `only_repo` on openai;
- `overridden`: a profile file that sets `agents.claude.enabled: false` after the fleet enables it;
- `live`:
  - it returns only dispatch rows;
  - a row carrying extra fields such as `text` or `prompt` comes back without them;
- `reset_cooldown` clears a refused rung, and an unknown agent raises;
- `test_agent`, with fake runners: success, a quota failure giving code `quota`, and a runner raising `RuntimeError` giving `failed` / `RuntimeError`;
- `jev_live` ignores dispatch rows.

- [ ] **Step 4: run until green**, then run all three commands on both interpreters.

- [ ] **Step 5: commit.** Stage `jevkit/dispatch.py tests/test_dispatch.py router-dashboard/routing_store.py router-dashboard/dispatch_store.py router-dashboard/tests/test_dispatch_store.py` with the message `dashboard: a dispatch store that reads and writes what the plugin reads`.

---

### Task 2: The dispatch API

**Files:**
- Modify: `router-dashboard/server.py`, `router-dashboard/tests/test_server.py`

- [ ] **Step 1: write the failing tests** in `test_server.py`. Use the same real-server fixture, with `JEV_LADDER_STATE` pointed into the temp home. Cover:
  - `GET /api/dispatch/state` returns 200 and both profiles.
  - `POST /api/dispatch/switch`:
    - `{scope: "wiki", switch: "mode", value: "shadow"}` returns 200, and the state shows `wiki` shadow from `dashboard`;
    - `__all__` without `confirm: true` returns 400;
    - a bad value returns 400.
  - `POST /api/dispatch/plan` `{changes}` returns rows and writes nothing.
  - `POST /api/dispatch/apply` returns 400 without `confirm: true`; with it, it writes and is verified.
  - `GET /api/dispatch/live?since=0` returns only dispatch rows.
  - `POST /api/dispatch/cooldown` `{agent: "claude"}` returns 200; an unknown agent returns 400.
  - `POST /api/dispatch/test` returns 400 without `confirm: true`. With it (`dispatch_store.test_agent` patched to a fake), it returns the fake result.
  - With a token set, every `/api/dispatch/*` route answers 401 without the token.

- [ ] **Step 2: implement.**
  - In `do_GET`: `/api/dispatch/state`, and `/api/dispatch/live` with `since` parsed as in `/api/jev/live`.
  - In `do_POST`, before the profile-based `/api/plan` and `/api/apply` handling: `/api/dispatch/switch`, `/api/dispatch/plan`, `/api/dispatch/apply`, `/api/dispatch/cooldown` and `/api/dispatch/test`.
  - A `ValueError` gives 400 with its message; any other exception gives 500 with `Type: message`.
  - In `main()`: `os.environ.setdefault("HERMES_HOME", args.hermes_home)`, so jevkit's ladder and keystore see the same home as the page.

- [ ] **Step 3: run until green**, then run all three commands on both interpreters.

- [ ] **Step 4: commit.** Stage `router-dashboard/server.py router-dashboard/tests/test_server.py` with the message `dashboard: the dispatch API`.

---

### Task 3: The dispatch card

**Files:**
- Modify: `router-dashboard/static/index.html`, `router-dashboard/tests/test_server.py` (the page test)

Match the page's existing style and code. Reuse, don't copy:
- the `.card`, `.seg`, `.badge`, `.note`, table and modal classes;
- `esc`, `HDR` and `askAll` for "All profiles" confirmations;
- the profile picker (`CURRENT`, `ALL`).

- [ ] **Step 1: write the failing page test** in `test_server.py`. `GET /` must contain the ids `dispatchCard`, `dispatchNew`, `dispSeg`, `dispConflict`, `dispNotice`, `dispPrivacy`, `dispAgents`, `dispOrder`, `dispChecks`, `dispPreview`, `dispReceipt` and `dispLive`, and the text `new in this fork`.

- [ ] **Step 2: build the card**, placed right after `#jevCard`:
  1. **Title and intro.**
     - Title: "Receptionist dispatch", with the badge `<span class="badge new" id="dispatchNew">new in this fork</span>`. Style `.badge.new` with the accent colour so it stands out.
     - First line: "Added in this fork: the upstream hermes-jev-skills has no dispatch."
     - Second line: "A turn Jev judges hard goes to Claude Code, ChatGPT (Codex) or OpenRouter when the profile's privacy allows it; everything else is answered on this machine. The answer comes back unchanged, with one line naming who wrote it."
  2. **Mode.** `#dispSeg` has Off, Shadow and On, and follows the profile picker exactly as `#jevSeg` does. "All profiles" asks through `askAll`, then POSTs `/api/dispatch/switch` with `confirm: true`. Next to it, the scope line gives the source of the value: "set here", "from config.yaml", "from dispatch.json" or "default".
  3. **Conflict.** `#dispConflict` is visible when any profile in scope has `conflict`. It reads: "Jev routing is <value> for <profile>. Only one of them decides a turn, so dispatch stands aside there." Its button, "Turn Jev routing off for <profile | all profiles>", POSTs `/api/jev/switch` with `switch: "routing"` and `value: "off"` (plus `confirm` for all), then reloads both states.
  4. **Notice.** `#dispNotice` is a checkbox: "Say so when a hard turn was answered here." It POSTs `switch: "notice"` for the scope.
  5. **Privacy.** `#dispPrivacy` is a table with one row per profile. Each row has a select:
     - `highly_sensitive`: "Only this machine"
     - `private`: "May go to an agent"
     - `public`: "Public (OpenRouter too)"

     Under the table: "Profiles not listed in dispatch.json: only this machine. Turns holding a password, an IBAN, a phone number or words like cliënt, dossier or BSN stay here whatever the class."
  6. **Agents.** `#dispAgents` has rows for Claude Code, ChatGPT (Codex) and OpenRouter. Each row has:
     - an On checkbox;
     - a model `<input list=…>` with a datalist: claude gets `CLAUDE_MODELS`; openai gets `/api/models` entries whose provider is `openai-codex`; openrouter gets those whose provider is `openrouter`. Empty means the CLI's own default, and OpenRouter needs one;
     - for claude only, an "Only repository work" checkbox;
     - status: "found" or "not found" (for OpenRouter: "key present" or "no key: run `jev setup-key --provider openrouter`"), plus "cooling N s" with a Reset button that POSTs `/api/dispatch/cooldown`;
     - a Test button. It asks "Send one short test message to <agent>? It uses your login.", then POSTs `/api/dispatch/test` with `confirm: true` and shows ok, or the error code, inline.
  7. **Order.** `#dispOrder` shows "Repository work: A → B [Swap]" and "Other hard work: A → B [Swap]", plus the note "OpenRouter: last resort, public turns only."
  8. **Save.** Privacy, agent and order edits are staged. They are shown as a count on a "Preview dispatch changes" button, which POSTs `/api/dispatch/plan` and shows the rows in `#dispPreview`. "Confirm & save" POSTs `/api/dispatch/apply` with `confirm: true`. `#dispReceipt` then shows:
     - saved and verified, or not;
     - the backup path;
     - any `overridden` rows;
     - "Takes effect on the next message. No restart."
  9. **Checks.** `#dispChecks` shows:
     - plugin installed;
     - enabled in this profile;
     - `llm_execution`: yes, no, or "unknown here, check `jev dispatch check`";
     - the settings file path and state;
     - broken files, in the warning style;
     - once: "After installing, restart the gateway one time so Hermes loads the plugin."
  10. **Live.** `#dispLive` is a table of recent dispatch decisions:
      - columns: time, profile, shadow or on, who answered (agent · model, or "this machine"), privacy, level, reason, attempts;
      - it loads with the page, and polls with the header's Live button (`pollLive` also calls `/api/dispatch/live`);
      - empty text: "No dispatch decisions yet. Set Shadow for a profile and send it a message."
  - Errors from any call go to `alert` or an inline note, as the page already does. Nothing is logged to the console with values.

- [ ] **Step 3: run the page test, then all three commands on both interpreters.**

- [ ] **Step 4: commit.** Stage `router-dashboard/static/index.html router-dashboard/tests/test_server.py` with the message `dashboard: the receptionist dispatch card, marked new in this fork`.

---

### Task 4: Say what this fork adds, and roll out from the dashboard

**Files:**
- Modify: `README.md`, `router-dashboard/README.md`, `docs/receptionist-dispatch.md`, `CHANGELOG.md`

- [ ] **Step 1: `README.md`.** Add a section `## What this fork adds` near the top, after the opening paragraph. It lists, each with one line and a link:
  - receptionist dispatch (the `hermes-dispatch` plugin, `jev dispatch`, and the dashboard card);
  - the privacy work it brought (secret values in every common form, IBAN from the whole registry, Dutch mobile numbers, sensitive Dutch terms, per-profile privacy classes);
  - each profile of a multiplexed gateway seen as itself (this fixes routing's `private_profiles` too);
  - the dashboard card.

  End with: "Everything else is upstream hermes-jev-skills."
- [ ] **Step 2: `router-dashboard/README.md`.** Add a bullet: "**Receptionist dispatch** (new in this fork) …". It covers what the card sets, the files it writes, and that it takes effect on the next message with no restart.
- [ ] **Step 3: `docs/receptionist-dispatch.md`.** "Rolling it out" becomes dashboard-first:
  1. open `jev dashboard`;
  2. in the dispatch card, set privacy per profile and switch agents on;
  3. use the Test button;
  4. set Shadow and watch the dispatch table for a day;
  5. set On.

  The chat commands stay as the alternative.
- [ ] **Step 4: `CHANGELOG.md`** under `## Unreleased`: one bullet for the dashboard card.
- [ ] **Step 5: run all three commands on both interpreters. Then commit.** Stage the four files with the message `docs: what this fork adds, and dispatch rolled out from the dashboard`.
