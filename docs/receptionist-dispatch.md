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
| `claude` | `claude -p`, in plan mode, without tools | the Claude login Claude Code already holds |
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

A value of the wrong type is ignored. `"enabled": "false"` is a string, so it is not a yes. A
broken setting in a later file makes things stricter, never looser: a broken `enabled` is a no,
and a broken privacy list allows nothing.

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
- So does a sensitive word, or an IBAN from any country. The built-in words are the Dutch
  words for a client, a patient or a file, in any form, and the words for a conversation
  report, a treatment plan, an anamnesis, medication, a criminal record, debts, a BSN and an
  IBAN. `sensitive_terms` adds your own words. It never removes the built-in ones.
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
then `routing` in config.yaml.

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
  recent user and assistant turns, text only, redacted, about 12,000 characters. It has the
  reasoning library's shape: To, Reason, Request, Constraints, Evidence, Tried, Need back. The
  Constraints line asks for a written answer only: read no files, run no commands, change
  nothing. More than 20 turns never leave, whatever the setting.
- System prompts, tool output, memory and files are never part of a handoff. A handoff that
  would carry a secret value is not sent at all.
- **The log** holds decisions only: who, why, the privacy class, and how each attempt went.
  Never prompt text, never an answer. A failure's detail never carries an agent's output.

The answer comes back as it is, under one line: `[openai · <model>]`, a blank line, then the
answer.

## Isolation

Agents run in a fresh empty directory with a minimal environment. API keys are not passed on,
so each CLI uses its own login. Claude runs with its file, shell and web tools disabled. Codex
runs in its read-only sandbox, and the handoff asks it to read no files. It can still run
read-only commands if it decides to, so the working directory is empty on purpose.

## Cooldowns

A quota, auth or missing-program failure cools that agent for its `cooldown`: 30 minutes by
default, 10 for OpenRouter. A timeout cools it for `timeout_cooldown`, five minutes. Then the
next agent or the local model answers.

Cooldowns live in the shared ladder file under `dispatch:<agent>`, separate from routing's
rungs. Every lane sees them. `jev ladder clear --rung dispatch:openai` reopens a seat early.

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
