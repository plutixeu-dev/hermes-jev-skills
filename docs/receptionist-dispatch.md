# Receptionist dispatch

A front desk for a Hermes chat whose own model is local. Most turns stay on the local model.
A turn that Jev judges hard can go to another agent: Codex on a ChatGPT login, Claude Code on
a Claude login, or OpenRouter. That agent's answer comes back unchanged, with one line on top
that names who wrote it.

It is off by default. No agent is enabled and no model is filled in. A profile you did not
classify stays fully local.

## What it does

Three parts, kept apart on purpose.

1. **Classify.** Jev answers three routing questions about the turn: how hard it is, what kind
   of work it is, and what a mistake would cost. The answers become one TRIAGE record in the
   reasoning library's routing-contract schema. Routing and dispatch share one judgement, with
   the same thresholds.
2. **Policy.** Code and a JSON file decide, never a model. The hurdles come in a fixed order:
   privacy, level, context window, cooldowns, then the order you set. Only a turn Jev judges
   hard (level `frontier`) can leave. If Jev is down or slow, the turn counts as standard work
   and stays here.
3. **Run and relay.** The chosen agent gets a redacted handoff on stdin. Its answer comes back
   as it is. The local model does not retell it, so no number, warning or decision changes on
   the way.

The `hermes-dispatch` plugin runs these parts on the first provider call of each new user
turn. It uses Hermes's `llm_execution` middleware, which may replace the provider call. When
the policy picks this machine, the call goes ahead untouched. When it picks an agent, that
agent's answer becomes the assistant message.

Anything unsure, failed or blocked is answered locally. Dispatch never loses a turn.

| Agent | What runs | Login |
|---|---|---|
| `openai` | `codex exec`, in Codex's read-only sandbox | the ChatGPT login Codex already holds |
| `claude` | `claude -p`, in plan mode, without tools or MCP servers | the Claude login Claude Code already holds |
| `openrouter` | one chat completion | the key stored with `jev setup-key --provider openrouter` |

The OpenRouter key goes in the way the Jev key does: the person pastes it into the page that
command opens. It never passes through the chat.

## An example `dispatch.json`

```json
{
  "profiles": {"default": "private", "coding": "public", "secondbrain": "highly_sensitive"},
  "agents": {
    "openai": {"enabled": true, "model": "<a model id your Codex login lists>"},
    "claude": {"enabled": true, "model": "opus"},
    "openrouter": {"enabled": false, "model": "<an OpenRouter model id>"}
  }
}
```

This file says:

- The default profile is private, `coding` is public, and `secondbrain` is highly sensitive.
  Any other profile gets `default_privacy`, which is highly sensitive.
- Codex and Claude Code are on. OpenRouter is off.

Fill in a model id your own login lists. Never invent one. An empty model means the CLI's own
default. OpenRouter needs a model.

The file can live in three places. Each one is laid over the one before, setting by setting,
so the last one wins:

1. `~/.config/jev/dispatch.json`, for every Hermes on this machine;
2. `~/.hermes/jev/dispatch.json`, for the whole fleet;
3. `~/.hermes/profiles/<name>/jev/dispatch.json`, for one profile.

`JEV_DISPATCH_POLICY` names one file and replaces all three.

A gateway that serves several profiles from one process (`multiplex_profiles`) binds each
turn's profile without changing `HERMES_HOME`. Dispatch follows the turn's profile, so each
profile of a multiplexed gateway reads its own `dispatch.json` and its own `/dispatch` switch,
has its own class, and logs under its own home.

Other settings, all optional:

| Setting | Default | What it does |
|---|---|---|
| `default_privacy` | `highly_sensitive` | the class of a profile not listed in `profiles` |
| `jev_text_for` | `["public"]` | the classes whose redacted text Jev may read; the rest send coarse features |
| `frontier_order` | repository work: claude, then openai; other work: openai, then claude | who is asked first |
| `last_resort` | `openrouter` | tried after the frontier order, for public turns only |
| `sensitive_terms` | none | your own words that keep a turn here, added to the built-in list |
| `handoff` | 6 turns, 12,000 characters | how much conversation a handoff carries |
| `turn_budget` | 600 | seconds one turn may spend on agents |
| `timeout_cooldown` | 300 | seconds an agent that timed out is skipped |
| `skip_prefixes` | `[kanban]`, `[SESSION HANDOFF` | turns starting with these are never dispatched |
| `skip_platforms` | `cron` | turns from these platforms are never dispatched |

Each agent also takes `privacy` (the classes it may take), `timeout`, `cooldown`,
`context_tokens` (its context window; 0 means no check) and `only_repo` (repository work only;
on for claude by default). Claude also takes `max_turns`. If a flag differs in your installed
CLI, put the working argument list under `agents.<name>.argv`. No code change is needed.

A broken value or file in a later layer turns agents off and privacy to the strictest, never
looser than what came before:

- `"enabled": "false"` is a string, so it is not a yes. A broken `enabled` is a no, and a
  broken privacy list allows nothing.
- An agent entry that is not an object (`"openai": false`, `null`, `"off"`) turns that agent
  off. An `agents` that is not an object turns every agent off.
- A broken `profiles`, `default_privacy` or `jev_text_for` becomes the strictest value, and a
  broken `mode` is `off`.
- A file that is there but cannot be read (not UTF-8, not JSON, not an object) does all of
  that at once: every agent off, privacy at its strictest, mode `off`. `jev dispatch check`
  lists it under `broken_files`, and `/dispatch` names it. A file that is not there is simply
  skipped.

## Privacy classes

Every turn has one of three classes.

| Class | What Jev reads | Where the turn may go |
|---|---|---|
| `public` | the turn, redacted | Codex, Claude Code or OpenRouter |
| `private` | coarse features only: length, whether code is present, whether risk words appear | Codex or Claude Code, never OpenRouter |
| `highly_sensitive` | nothing: Jev is not asked | nowhere: this machine answers |

A profile gets its class from `profiles`. A profile you did not list gets `default_privacy`.
So nothing leaves a profile until you classify it.

Routing's own privacy settings count too. With routing's `mode: "features"`, or a profile in
its `private_profiles`, Jev reads coarse features only, whatever the class.

The class of a turn is the stricter of two things: the profile's class, and what the text
shows. The check reads the message and every recent turn a handoff would carry, not only the
newest message.

- A secret value makes a turn highly sensitive.
- So does a sensitive word, or an IBAN from any country. The built-in words are Dutch, and
  only these count: `cliënt`, `patiënt`, `clienten`, `patienten`, `dossier`,
  `gespreksverslag`, `behandelplan`, `anamnese`, `medicatie`, `strafblad`, `schulden`,
  `burgerservicenummer`, `bsn` and `iban`. The longer ones count in any form, plurals and
  compounds too (`zorgdossier`); `bsn` and `iban` count as whole words. English words do not
  count: "client file" or "treatment plan" on its own keeps no turn here. `sensitive_terms` adds
  your own words, in any language. It never removes the built-in ones.
- Contact details make a public turn private.

**A secret.** A secret is a value (a key, a token, a password), not a word about one. A
question about passwords is not a secret. A turn that holds a password is. The forms
recognised are listed in `privacy.has_secret_value`. A turn with words about passwords, keys
or tokens, but no value, may still leave by its class. Jev then reads it as coarse features
only.

OpenRouter takes public turns only. That rule follows its kind, not the name you give it.

## One classifier per turn

hermes-jev routing (`/jev routing`) asks Jev about every turn as well. Two classifiers on one
turn cost two calls, and they can disagree. The reasoning library's rule is one classifier per
message.

So while routing is on or in shadow, dispatch stands aside. It logs `stood aside`, and the
local call goes ahead. It reads routing the way hermes-jev does: a `/jev routing` switch first,
then `routing` in config.yaml. That counts only while hermes-jev is loaded: when Hermes says
the plugin is gone, a switch left behind no longer silences dispatch.

Turn routing off before you turn dispatch on.

## Modes

| Mode | Decides | Logs | Hands a turn over |
|---|---|---|---|
| `off` | no | no | no |
| `shadow` | yes | yes | no |
| `on` | yes | yes | yes |

`off` is exactly the old behaviour: every call goes ahead untouched.

The mode in force is the first of these that is set:

1. a `/dispatch` switch for this profile, kept in its `jev/dispatch-state.json`;
2. `mode` in config.yaml, under `plugins.entries.hermes-dispatch.settings`;
3. `mode` in `dispatch.json`;
4. `off`.

In config.yaml, quote the value: `mode: "on"`. Many YAML readers take a bare `on` or `off` as
true or false, and the plugin reads that as off.

`/dispatch` without arguments shows the mode in force, the profile's class, and every agent.
It also says so when this Hermes has no `llm_execution` middleware. Dispatch cannot act there.

`/dispatch notice on` adds one line to a reply when a turn that deserved another agent was
answered here. The line starts with `[dispatch]` and says why. It works in mode `on` only.

The plugin loads when a session or gateway starts. After that, a switch takes effect on the
next turn.

Some turns are never dispatched:

- a subagent's turn;
- a turn from a platform in `skip_platforms`, such as cron;
- a turn that starts with a prefix in `skip_prefixes`;
- every provider call after the first one of a turn, so the tool loop is left alone;
- a turn on a provider that is not `chat_completions`. Dispatch decides and logs, and the local
  call goes ahead.

A Claude Code answer continues in the same Claude session on the next turn of that Hermes
session. After a failed claude attempt, the next turn starts a new Claude session.

## Rolling it out

1. `jev dispatch check`. It shows `policy_mode`, the mode written in dispatch.json.
   `/dispatch` without arguments shows the mode in force, since a switch or config.yaml can
   override the file.
2. `/jev routing off` (one classifier per turn).
3. `/dispatch shadow`, then a day of
   `grep '"kind":"dispatch"' ~/.hermes/logs/jev-decisions.jsonl`. A named profile keeps its
   log under its own home: `~/.hermes/profiles/<name>/logs/`.
4. `/dispatch on` with one agent enabled. With only claude enabled, only coding turns leave,
   because claude takes repository work only unless `only_repo` is false.
5. The others, one at a time.

Rollback is `/dispatch off`.

To see one decision by hand, run `jev dispatch --prompt "..." --profile coding`. It asks Jev
and shows who would answer, and it hands nothing to an agent. `--run` hands the turn over.

## What leaves this machine

- **To Jev**, to classify the turn. A public turn goes as redacted text, capped at routing's
  `ask_chars` (2,500 characters). A private turn, or one with words about passwords, keys or
  tokens, goes as coarse features. So does a turn that routing's own settings keep to
  features. A highly sensitive turn sends nothing.
- **To the chosen agent's provider**, one handoff. It holds the person's message and up to six
  recent user and assistant turns as they were said, text only, redacted, about 12,000
  characters. It has the reasoning library's shape: To, Reason, Request, Constraints, Evidence,
  Tried, Need back. The Constraints line asks for a written answer only: read no files, run no
  commands, change nothing. More than 20 turns never leave, whatever the setting.
- System prompts, tool output, memory and files are never part of a handoff. The history is
  the conversation as it was said, not the copy Hermes sends its own model: the recalled
  `<memory-context>`, every plugin's context and compaction summaries stay behind. One
  exception: a turn that carried an image keeps the context Hermes added inside the turn
  itself. Dispatch cuts it from the recalled memory on, but plugin context added without
  recalled memory carries no mark, so it goes along, redacted. A handoff that would carry a
  secret value is not sent at all.
- **The log** holds decisions only: who, why, the privacy class, and how each attempt went.
  Never prompt text, never an answer. A failure's detail never carries an agent's output. A
  word you added to `sensitive_terms` is never named: the reason says only that one of them
  matched.

The answer comes back as it is, under one line: `[openai · <model>]`, a blank line, then the
answer.

## Isolation

Agents run in an empty directory with a minimal environment. API keys are not passed on, so
each CLI uses its own login. The Claude login token from `claude setup-token`
(`CLAUDE_CODE_OAUTH_TOKEN`) does pass, because it is the subscription login itself, and so do
the proxy settings.

Codex gets a fresh empty directory for every turn. Claude gets one of its own,
`jev-claude-<uid>/work`, so the next turn resumes its session there. It lives in your runtime
directory (`XDG_RUNTIME_DIR`, normally `/run/user/<uid>`) when that is yours alone, and in
the temp directory otherwise. The directory is used only while it is a real directory, yours,
closed to other users' writes, and empty. Otherwise claude runs in a fresh one and starts a
new session: isolation beats continuity.

Where the directory lives matters because Claude Code reads every `CLAUDE.md` from its
working directory up to `/`. Under your home it would read yours. Under /tmp it would read
one that another user of the machine left there. The runtime directory avoids both.

Claude runs with no tools at all (`--tools ""`) and no MCP servers (`--strict-mcp-config`).
`--disallowedTools` stays as the fallback for a CLI without `--tools`.

Codex runs in its read-only sandbox, and the handoff asks it to read no files. Read-only is
not no access. Codex can still read any file the Hermes user can read, `~/.hermes` included,
and a turn that asks it to can send such a file to OpenAI. The empty working directory does
not fence that in. The remedies are to keep Codex off, or to run Hermes as a user that cannot
read what must stay here.

## Cooldowns

A quota, auth or missing-program failure cools that agent for its `cooldown`: 30 minutes by
default, 10 for OpenRouter. A timeout cools it for `timeout_cooldown`, five minutes. Then the
next agent or the local model answers.

Cooldowns live in the shared ladder file under `dispatch:<agent>`, separate from routing's
rungs. Every lane sees them. `jev ladder status` does not list dispatch cooldowns: it shows
routing's rungs only. `jev dispatch check` and `/dispatch` do, and
`jev ladder clear --rung dispatch:openai` clears one, which reopens that seat early.

## Time

A turn spends at most `turn_budget` (600 s) on agents. That keeps it under Hermes's 900 s idle
warning (`agent.gateway_timeout_warning`). With less than 30 s of the budget left, no agent is
started and this machine answers. Keep Hermes's own idle limits above the budget.

## Subscription logins and their terms

Codex runs on your ChatGPT login, and Claude Code on your Claude login. Dispatch runs them as
programs on your behalf. The terms of those plans still apply, and this repo cannot tell you
whether your use fits them. Read them before you enable an agent, and more so when other
people talk to this Hermes.

Plans also have usage limits. When a seat is full, dispatch cools that agent, and the next
agent or the local model answers.

OpenRouter is metered: every call is paid for with your own key.

Never add `--bare` to claude's argument list. That mode ignores the subscription login and
needs an API key.

## Limits

- **Not streamed.** The answer arrives whole, when the agent is done.
- **Text only.** A turn that carries an image stays on this machine.
- **Read-only agents.** They answer in writing, and change no files and no systems.
- **Synchronous.** The chat waits while the agent works, up to `turn_budget`.
- **Stop does not stop it.** Stop does not interrupt a handed-off turn. The agent runs until it
  answers or `turn_budget` ends.
