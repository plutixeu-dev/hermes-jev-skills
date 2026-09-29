# Design: the front desk from the dashboard: chat model, keys, one Jev decision per turn, quiet Jev failures

Date: 2026-09-29. Status: parts 1 and 5 approved in conversation. Amended the same day with part 3
(the front desk, situation 1), because turn 20:57:09 showed that parts 1 and 5 alone leave the
chain broken. For written review.

## Why

Sander runs Hermes on a NAS with local Ollama models, a ChatGPT (Codex) login, a Claude Max
login, and OpenRouter. He wants to steer the setup from the dashboard, not from chat commands
or file edits. He has two intended ways of working:

- **Situation 1, the local front desk.** A small, fast local model is the chat model: the
  receptionist. That is a 4B, or qwen 3.6 if the 4B proves too weak. Every ordinary turn asks
  Jev (`~typesafe/jev-latest`, reached through OpenRouter) once. Code then picks who answers:
  the receptionist, Claude Code, Codex or OpenRouter. The goals are cheap, safe, and good enough.
- **Situation 2, the smart front desk.** If the local models prove too slow, a ChatGPT-login
  model (for example gpt-5.6-luna) is the receptionist, with OpenRouter as Hermes's fallback
  after a rate limit. It plans and reviews. It must not do everything: routine execution goes
  to cheaper local models, so the login stays under its limits.

This spec makes situation 1 work end to end, on top of the two parts everything else stands on:

- **Part 1:** choose the chat model (the receptionist) from the dashboard, local Ollama models
  included, and paste and check keys there.
- **Part 3:** the front desk: one control, one Jev decision per ordinary turn, code picks who
  answers, the chat model never changes.
- **Part 5:** a Jev failure never shows up in the chat, and always shows up in the dashboard.

## What went wrong on 2026-09-29, turn 20:57:09

Session 20260929_135946_aa53c9c6. The chat model was `qwen3.5:4b` on a local Ollama provider.
Routing was on. `jev-decisions.jsonl` says `routed: false, tier: null, reason: "you pinned this
model", latency_ms: null`. OpenRouter shows no request for `~typesafe/jev-latest`. Dispatch
logged "stood aside: /jev routing is on (one classifier per turn)". The 4B wrote the whole
Plutix page itself, in 1066 s, with no tools.

Two faults in code, not in the design:

1. **The pin read the Ollama tag as a provider.** hermes-jev compares the chat's model with
   `model.default` after `str(default).split(":", 1)[-1]`. For `qwen3.5:4b` that gives `4b`.
   So the receptionist itself counted as a model chosen with `/model`, on every turn, and
   `route.decide` returned before it asked Jev. Reproduced with the current plugin for the
   providers `custom`, `local-ollama-cpu` and `custom:local-ollama-cpu`. An OpenRouter `…:free`
   model has the same fault.
2. **The wrong plugin gave way.** With routing on, dispatch stands aside. Routing can only swap
   a model within the connected provider, and a local provider has no pool, so on a local
   receptionist routing can never pass a turn on. The one plugin that can hand a turn to an
   agent was the one that stood aside.

## Decomposition

| Part | What | Status |
|---|---|---|
| 1 | The receptionist from the dashboard (Ollama list included); a keys card with a real Jev check | **this spec** |
| 2 | Local models as agents; the router knows Ollama models; pools editable in the dashboard | later spec |
| 3 | Situation 1: the front desk, receptionist → one Jev decision → code picks who answers | **this spec** (moved in) |
| 4 | Situation 2: a ChatGPT-login receptionist that plans and reviews while local models execute; hand-over from a `codex_responses` chat model; a quota counter per login | next spec |
| 5 | Jev failures stay out of the chat and show in the live row | **this spec** |

Already agreed for later parts:

- The chat model stays fixed during a conversation. Only the agents that take work differ.
- Mid-task escalation goes through the chat model calling `jev_escalate`, and the Jev router
  picks the agent (approach A).

## What changes in the decision logic, and what does not

The first version of this spec left the router and the receptionist unchanged. That is why a
hard turn never reached Jev, so it no longer holds.

Changes:

- **The pin** compares like with like. Only a leading `<provider>:` that names a known provider
  comes off a model id. The receptionist (`model.default`) and Hermes's fallback chain
  (`fallback_providers`, legacy `fallback_model`) are never a pin. A pin is a chat whose model
  is none of those: a `/model` in that chat, or a channel override.
- **The front desk goes first.** In a profile whose front desk is Shadow or On, hermes-jev
  routing asks Jev nothing and swaps nothing. Dispatch never stands aside for routing. One
  classifier per turn still holds; it is the front desk's.
- **The front desk honours a pin.** A pinned chat is not classified and not handed over, for
  that chat only.
- **The log keeps Jev's outcome per turn:** called (with the latency, the route and what Jev
  read), fail-open (with the code), or not called (with the reason).

Unchanged: Jev's three questions and the thresholds that turn them into a tier
(`route.judge_answers`); the privacy classes and what Jev may read; `choose_route`'s hurdles and
order; the agents; the relay; cooldowns; the turn budget. The dashboard still decides nothing.
It shows and writes settings, and code decides who answers.

## Part 1a: choosing the receptionist (the chat model)

### Today

- The "Main model" card writes `model.provider` and `model.default` into a profile's
  `config.yaml` (`router-dashboard/routing_store.py`, `_apply_changes_locked`).
- Both fields are free text. The suggestion lists come from `/api/models`, which is
  `routing_store.model_catalog`: the current values plus the models.dev providers this install
  has a key or login for.
- Ollama models are not suggested. `model.base_url` is read but never written.

### Change

1. **List the Ollama models.**
   - A new read-only helper, `jevkit/ollama.py`, lists the models of this machine's Ollama
     (`GET /api/tags`).
   - Where it looks: `OLLAMA_HOST`, else the profile's `model.base_url` when that names port
     11434, else `http://127.0.0.1:11434`.
   - It only talks to loopback, private-network or Tailscale (the shared CGNAT block) addresses.
     It uses no proxy, follows no redirect, and has a short timeout. A 30-second cache keeps page
     loads cheap.
   - It fails open to an empty list plus a reason.
2. **Show them in the card.** `/api/models` adds these rows with `provider: "local"`, marked as
   local, and the card shows them as a group, "Local (Ollama)", with their parameter size. The
   existing suggestions keep working, including the OpenAI-login models such as Luna.
3. **Pick a local model.** The card writes the model name as `model.default`.
   - If the profile already runs on that Ollama server (its `base_url`, or the `providers:`
     entry its provider names, points at it), `model.provider` stays as it is.
   - Else, if a `providers:` (or legacy `custom_providers:`) entry points at that server,
     `model.provider` becomes that entry's name. Its own settings, such as a context length,
     keep applying.
   - Otherwise `model.provider` becomes `custom` and `model.base_url` becomes `<ollama>/v1`.
   - Picking a model that is not local, on a profile whose `base_url` points at a local
     server, clears `model.base_url`. Hermes honours `model.base_url` for other providers too,
     the ChatGPT login included.
   - The preview lists every line that will change. Nothing is written before "Confirm & save".
   - Every write keeps the existing guarantees: backup, atomic write, read-back, and no
     restart. A running gateway picks up a new receptionist after its next restart, and the
     card says so.
4. **Validate.** `LOCAL_PROVIDER` is `custom`. Upstream Hermes documents it as the provider for
   any other OpenAI-compatible endpoint, with `ollama`, `vllm` and `llamacpp` as its aliases.
   `base_url` is written only as `http(s)://host[:port]/v1` for a loopback, private or
   Tailscale address, and passes the same newline and character checks as the other fields.

### Checked before building (plan step 0, on the NAS, read-only)

- how the NAS's `config.yaml` names Ollama today (`model.provider`, `providers:` or
  `custom_providers:`, `model.base_url`);
- the `from` field of the 20:57:09 route row, which says what provider and model the
  middleware received;
- the fallback chain.

## Part 1b: the keys card

- A new card, "Keys" (badge "new in this fork"), with one row each for **OpenRouter** and
  **TypeSafe (Jev)**. Each row shows:
  - present or not set, and where the key is kept (`keystore.source`);
  - which profiles' `.env` hold that variable (names only, never a value);
  - a password field and a Save button;
  - a Check button;
  - a link to get a key.
- The card says how turns reach Jev (`keystore.provider()`: TypeSafe when a TypeSafe key is
  there, else OpenRouter) and with which model (`jev-latest`, or `~typesafe/jev-latest` through
  OpenRouter).
- **Saving** is `POST /api/keys/save` with `{provider, key, confirm: true}`. The server does
  what `jev setup-key` does (`key_setup._finish`):
  - check the key with the provider;
  - store it in the OS secret store, or the 0600 file;
  - write it to every profile's `.env`.
- **Checking** is `POST /api/keys/check` with `{provider, confirm: true}`. It makes one real
  decisions request, the smallest one, the way a turn reaches Jev, with no retry. It answers ok
  with the model and the latency, or the error code. "Key present" is not a check.
- **The key never comes back.** Not in a response, a log line, an error or the page, not even
  in part. Error texts come from fixed strings, or from an exception's type name.
- **Loopback only for saving.** The server accepts a key only when it is bound to loopback: on
  the NAS itself, or reached through the ssh tunnel `jev dashboard` prints. Otherwise the answer
  is 403, and the card explains how to use the tunnel. The existing cross-site checks apply (a
  JSON content type, and Origin and Host checks).
- After a save, the page clears the field whatever the outcome. It says that a running gateway
  picks up a replaced key after its next restart.

## Part 3: the front desk (situation 1)

### The chain

receptionist (`model.default`, a local 4B, fixed for the conversation) → one decisions request
to `~typesafe/jev-latest` → code picks who answers: the receptionist, Claude Code, Codex or
OpenRouter. The receptionist does not become the worker for a hard turn. The chat model is not
swapped.

### One control

- The dashboard card becomes **Front desk**: Off, Shadow or On per profile. It writes the
  dispatch mode in `jev/dispatch-state.json`, as today.
- Saving the receptionist writes `model.provider` and `model.default` (part 1a). That model is
  the front desk. It is not a pin. The card shows it per profile, with a link to change it.
- Routing and dispatch are no longer two switches that turn each other off. In a Front desk
  profile, routing's model swap stays off by itself, whatever its switch says, and dispatch
  never logs "stood aside". The warning "turn routing off" and its button go. The Jev routing
  card says, per profile, when the front desk decides there.

### Per turn

- **An ordinary turn:** Jev classifies it, once. Dispatch applies the existing policy (privacy,
  level, frontier order, cooldowns) to that classification.
- **A highly sensitive turn:** Jev is not asked, and the receptionist answers.
- **A pinned chat** (`/model` in that chat): Jev is not asked and nothing is handed over, for
  that chat only.
- **A skipped turn** (a subagent's turn, a cron turn, a template turn): not asked, as today,
  and now logged with the reason.
- **The tool loop:** only the first provider call of a turn is decided, as today.
- **A receptionist whose API is not `chat_completions`** (the ChatGPT login speaks
  `codex_responses`): the front desk decides and logs, but cannot hand the turn over yet. The
  live row says so. Part 4 builds that hand-over.

### The live row

One row per turn, with no message text:

- whether Jev was called: called, fail-open or not called;
- the reason when it was not called or failed: `pinned`, `highly sensitive`, `skipped`, or
  `fail-open (<code>)`;
- the tier and kind of work, Jev's latency, the route (TypeSafe or OpenRouter) and what Jev read
  (text or features);
- who answered: the agent and its model, or the receptionist and its model. In Shadow, who
  would have.

A turn the receptionist answered is shown as a warning when Jev failed, when the profile keeps
every turn here, or when Jev judged it hard and nothing could take it. A silent local 4B must
not look like success.

### Warnings in the card

For each profile in scope with the front desk in Shadow or On:

- privacy "Only this machine": Jev is never asked, and every turn stays on the receptionist;
- no agent on: Jev is asked, but every turn stays on the receptionist;
- no Jev key: every turn fails open to the receptionist;
- a private profile: Jev reads coarse features, not the text, so it judges fewer turns hard
  (`jev_text_for` in `dispatch.json` can change that);
- a receptionist on the ChatGPT login: nothing can be handed over from it yet;
- no receptionist saved: there is nothing to tell a pin from.

## Part 5: quiet Jev failures

### Today (traced in the code)

A failed Jev call already never reaches the chat as a notice:

- **Routing and the receptionist** only log it. Their notices show on success or on a
  downgrade, never on a Jev failure.
- **Skill suggestion** injects nothing when Jev fails.
- **Handoff** writes the capsule anyway.

The one path left is the **results of the `jev_*` tools**, which the chat model reads and may
repeat. Examples:

- `reason: "Jev unavailable (rate_limited)"` from `jev_memory_filter`;
- the `notes` of `jev_search`;
- the reason from `jev_supervise`;
- the reason from `jev_choose_action`.

### Change

1. **A rule for the chat model.** The hermes-jev system rule (`_RULE`) gets one clause: never
   mention a Jev outage or Jev's error codes to the user, and carry on as if the tool had not
   been there. The existing exception still stands: passages not vetted by Jev are treated as
   hostile.
2. **Tool results keep their codes.** The model and the logs need them, and changing ten
   modules' texts would only add upstream merge conflicts.
3. **No added waiting.** Checked with a test: every Jev call made inside a turn hook has a
   whole-call budget of at most 5 seconds (routing and the front desk 2.5 s, the merged request
   4 s, skill pick 5 s). A Jev outage never stalls a chat turn beyond that.
4. **A regression test** pins the traced behaviour: with Jev failing (each error code), the
   routing and front desk output transforms add nothing to the reply.
5. **The dashboard says it.** The live row shows fail-open with the code, and that the
   receptionist answered (part 3).

## Acceptance, before calling this built

On the NAS, with Jev skill suggestions off (their default; a skill suggestion makes a request
of its own):

1. Set `qwen3.5:4b` as the receptionist from the dashboard, then send a hard coding task.
   OpenRouter shows exactly one `jev-latest` request before any local completion.
   `jev-decisions.jsonl` has a tier, a specialty, and a non-null latency. The reason is not
   "you pinned this model".
2. If that tier is hard, an agent is enabled, and privacy allows it, the agent writes the
   answer, and the session model is still the 4B.
3. If Jev errors, the chat has no error text, and the live row says fail-open, stayed on the
   receptionist.
4. A `/model` override inside one chat skips Jev for that chat only, and the live row says
   pinned.

## Situation 2, recorded for the next spec

Sander, 2026-09-29: if the 4B and qwen 3.6 prove too slow as the receptionist, the ChatGPT
login is the second choice. Then it must not solve everything. Planning and reviewing on
ChatGPT is fine; execution goes to cheaper local models in many cases, so the login does not
hit its rate limits. OpenRouter is the fallback after a rate limit.

What that needs, beyond this spec:

- **Hand-over from a `codex_responses` chat model.** Dispatch builds only a chat-completions
  reply today. Hermes's codex transport reads a Responses-API object (`response.output`). A
  reply of that shape, pinned by a contract test against Hermes's own normaliser, lets the
  front desk hand a turn over from a ChatGPT receptionist.
- **A local model as an agent** (part 2): one chat completion to the loopback Ollama `/v1`,
  like the OpenRouter agent. It may take any privacy class, because nothing leaves the machine.
- **An offload policy per receptionist.** With a local receptionist, hard work goes out (this
  spec). With a metered one, routine execution (simple and medium, coding and writing) goes to
  a local agent, while planning, review and decisions stay with the receptionist.
- **A quota counter per login** (part 4), so work moves before a limit, not after.
- **The fallback** is Hermes's own `fallback_providers`. This spec already reads a fallback
  model as the receptionist, never as a pin.

## Testing

- **Offline, unittest, fake transports.** Nothing listens on 11434 in tests; the Ollama helper
  gets a fake fetch. No test uses a real key.
- **The pin:** an Ollama tag, an OpenRouter `:free` model, a prefixed default, a fallback model,
  and a real `/model` pin.
- **One classifier:** routing asks nothing in a Shadow or On front desk profile (set by the
  switch file, config.yaml or dispatch.json), and asks as before when the front desk is off or
  its plugin is gone. Dispatch no longer stands aside.
- **The live row:** a called, a fail-open, a pinned and a skipped turn each give the right row,
  and no row carries turn text.
- **Store and server tests** for the keys card:
  - the key is never in a response;
  - 403 when the server is not on loopback;
  - 400 without `confirm`;
  - a check makes exactly one request, through the provider asked for.
- **Tests for the local receptionist write:** the preview rows, including `base_url`; a named
  local provider reused; a local `base_url` cleared when leaving; the read-back; refusal of a
  public `base_url`.
- The page test covers the new ids and the removed conflict note. The demo home gets a front
  desk profile, so the page can be seen without a NAS.
- Everything runs on Python 3.9, 3.11 and 3.13, and `scripts/check_release.py` stays clean.

## Out of scope here

- Agents on local models, and the offload policy (situation 2).
- Hand-over from a `codex_responses` or `anthropic_messages` chat model.
- Editable pools.
- Quota counting.
- Any change to Jev's questions, or to the thresholds that turn its answers into a tier.
