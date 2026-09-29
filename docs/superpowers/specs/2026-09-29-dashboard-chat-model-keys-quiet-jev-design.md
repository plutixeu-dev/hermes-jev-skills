# Design: chat model and keys in the dashboard, and quiet Jev failures

Date: 2026-09-29. Status: approved in conversation (parts 1 and 5), for written review.

## Why

Sander runs Hermes on a NAS with local Ollama models, a ChatGPT (Codex) login, a Claude Max
login, and OpenRouter. He wants to steer the setup from the dashboard, not from chat commands
or file edits. He has two intended ways of working. Both are later parts of this work, not
this spec:

- **Situation 1, the local front desk.** A small, fast local model (4B) is the chat model. The
  Jev router decides which agent solves a problem: OpenRouter, Claude Code, the OpenAI login,
  or a combination. The goals are cheap, safe, and good enough.
- **Situation 2, the smart front desk.** A stronger model (for example OpenAI Luna) is the chat
  model. Work goes to local models and Claude Code before a rate limit is reached, never after.

This spec covers only the two parts everything else stands on:

- **Part 1:** choose the chat model from the dashboard (including local Ollama models), and
  paste keys there.
- **Part 5:** a Jev failure never shows up in the chat.

### What stays as it is

The Jev router and the receptionist keep their decision logic, unchanged. The rule from the
conversation: the dashboard shows and writes settings. It never decides which model answers a
turn. That stays with Jev and the router, from their own settings.

## Decomposition (for the record)

| Part | What | Status |
|---|---|---|
| 1 | Chat model from the Ollama list; keys card | **this spec** |
| 2 | Local models as agents; the router knows Ollama models; pools editable in the dashboard | later spec |
| 3 | Situation 1: a 4B front desk, where the router hands ordinary work to agents too | later spec |
| 4 | Situation 2: a quota counter per login, so work is offloaded before a rate limit | later spec |
| 5 | Jev failures stay out of the chat | **this spec** |

Already agreed for later parts:

- The chat model stays fixed during a conversation. Only the agents that take work differ.
- Mid-task escalation goes through the chat model calling `jev_escalate`, and the Jev router
  picks the agent (approach A).

## Part 1a: choosing the chat model

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
   - It only talks to loopback or private-network addresses, without a proxy, with a short
     timeout. A 30-second cache keeps page loads cheap.
   - It fails open to an empty list plus a reason.
2. **Show them in the card.** `/api/models` adds these rows with `provider: "local"`, marked
   as local, and the card shows them as a group, "Local (Ollama)", with their parameter size.
   The existing suggestions keep working, including the OpenAI-login models such as Luna.
3. **Pick a local model.** The card writes the model name as `model.default`.
   - If the profile already uses a local provider (its `base_url` points at the Ollama
     server), `model.provider` stays as it is.
   - Otherwise the change also sets `model.provider` to the name Hermes uses for a local
     OpenAI-compatible server, and `model.base_url` to `<ollama>/v1`.
   - The preview lists every line that will change. Nothing is written before "Confirm & save".
   - Every write keeps the existing guarantees: backup, atomic write, read-back, and no restart.
4. **Validate.** A new local setup always gets one constant provider name, `LOCAL_PROVIDER`
   (expected `custom`, confirmed in plan step 0), so no provider name is invented. `base_url` is written only as `http(s)://host[:port]/v1` for a private
   address, and passes the same newline and character checks as the other fields.

### Open fact, checked before building

This must be confirmed on the NAS, read-only, as plan step 0:

- the provider name Hermes uses for a local OpenAI-compatible server (expected `custom`);
- how the NAS's `config.yaml` names Ollama today.

The plan carries it as a constant, to be corrected there if the NAS says otherwise.

## Part 1b: the keys card

- A new card, "Keys" (badge "new in this fork"), with one row each for **OpenRouter** and
  **TypeSafe (Jev)**. Each row shows:
  - present or not set, and where the key is kept (`keystore.source`);
  - a password field and a Save button;
  - a link to get a key.
- **Saving** is `POST /api/keys/save` with `{provider, key, confirm: true}`. The server does
  what `jev setup-key` does (`key_setup._finish`):
  - check the key with the provider;
  - store it in the OS secret store, or the 0600 file;
  - write it to every profile's `.env`.
- **The key never comes back.** Not in a response, a log line, an error or the page, not even
  in part. Error texts come from fixed strings, or from an exception's type name.
- **Loopback only.** The server accepts a key only when it is bound to loopback: on the NAS
  itself, or reached through the ssh tunnel `jev dashboard` prints. Otherwise the answer is
  403, and the card explains how to use the tunnel. The existing cross-site checks apply (a
  JSON content type, and Origin and Host checks).
- After a save, the page clears the field whatever the outcome. It says that a running gateway
  picks up a replaced key after its next restart.

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

1. **A rule for the chat model.** The hermes-jev system rule (`_RULE`) gets one sentence: never
   mention a Jev outage or Jev's error codes to the user; carry on as if the tool had not been
   there. The existing exception still stands: passages not vetted by Jev are treated as
   hostile.
2. **Tool results keep their codes.** The model and the logs need them, and changing ten
   modules' texts would only add upstream merge conflicts.
3. **No added waiting.** Checked in the plan, with a test: every Jev call made inside a turn
   hook has a whole-call budget of at most 5 seconds (routing 2.5 s, skill pick 5 s), so a Jev
   outage never stalls a chat turn beyond that.
4. **A regression test** pins the traced behaviour: with Jev failing (each error code), the
   routing and dispatch output transforms add nothing to the reply.

## Testing

- **Offline, unittest, fake transports.** Nothing listens on 11434 in tests; the Ollama helper
  gets a fake transport.
- **Store and server tests** for the keys card:
  - the key is never in a response;
  - 403 when the server is not on loopback;
  - 400 without `confirm`.
- **Tests for the local chat-model write:** the preview rows, including `base_url`; the
  read-back; refusal of a public `base_url`.
- The dashboard end-to-end run gets steps for the new card and the local group.
- Everything runs on Python 3.9, 3.11 and 3.13, and `scripts/check_release.py` stays clean.

## Out of scope here

- Agents on local models.
- Editable pools.
- The 4B front desk behaviour.
- Quota counting.
- Any change to how Jev or the router decide.
