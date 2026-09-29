# Jev routers compared with the front desk

Date: 2026-09-29. Why: Sander received an AI-generated list of "Jev routers" and asked whether any of them does something better than the front desk, and what to adopt or integrate.

Method: four research passes, reading code rather than READMEs, with file and line evidence kept in the session.
- **Projects:** about 35 repositories were cloned and read.
- **Jev docs:** docs.typesafe.ai could not be fetched directly. It was read through a verbatim mirror, the official Python SDK and the OpenRouter docs repository.
- **Licenses:** this fork is MIT. Code is reusable from MIT sources with their notice, and from Apache-2.0 sources with their LICENSE and NOTICE. AGPL and unlicensed code gives ideas only.

## The list, checked

| Claimed | What is true |
|---|---|
| vinilana/jev-gateway: "asks Jev whether a local model can handle the request or it must go to a paid model" | Real, MIT, TypeScript, 256 stars. **It never chooses a model.** Jev picks the next tool call of a coding agent (force it, hint it, build it directly, or pass through), on every agent-loop step. |
| prismhq/jev-router: "LiteLLM, local via Ollama/vLLM or paid via OpenRouter" | Real, MIT, one commit. LiteLLM plus one Jev choice over candidate models. **No Ollama or vLLM anywhere**, and no confidence gate. |
| BillionsBobby/JevRouter: "picks models, sub-agents and tools by prompt complexity" | Real, MIT. One choice over capabilities by how well each description fits. **Not by complexity**, and it only returns a decision. |
| jcm-router: "Jev decides model and reasoning effort per message" | Real (adarshmishra07/jcm-router), MIT. It serves Claude Code on the Anthropic Messages API only, not Kilo or Codex. |
| antoniolg gist "Codex Jev Router" on 127.0.0.1:4319 | Real, but unlicensed: a modified copy of 0xNatoshi/jev-codex-router (MIT) with the notice dropped. When Jev is unsure it falls back to the middle tier, not to the frontier model. |
| awesome-jev-tools (v-modal), awesome-jev (yibie, fatwang2), awesome-jev-use-cases (walidboulanouar) | All real. |

## Where the front desk is ahead

Of about 30 routers read, **none hands a whole turn from a small local receptionist to another agent and relays its answer verbatim**. They all swap a model or an effort level inside one vendor. Upstream hermes-jev-skills has no dispatch either. What else we have that they mostly lack:

- **Privacy before anything leaves.**
  - Per-profile privacy classes; secret, IBAN and sensitive-term detection.
  - Jev reads features only for private turns, and nothing for highly sensitive ones.
  - Several others send the whole conversation to Jev; jev-gateway also logs tool arguments.
- **A local default.** The others assume a paid frontier chat model.
- **One Jev call per user turn.** jev-gateway and the Codex router call Jev on every loop step; by one author's count 92% of those calls were tool steps.
- **A real shadow mode, cooldowns, an agent order and a turn budget.** jev-gateway can add 8–16 s per request while Jev is down.
- **Dashboard edits with preview, confirmation, backup and read-back.** Theirs are read-only, or environment variables and in-memory switches.
- **The same three questions the best-measured router settled on.** hyspacex/jev-router asks task kind, difficulty on a four-level rubric, and harm if wrong.

## What others do better

| Idea | Source (license) | Verdict |
|---|---|---|
| A health summary: how often Jev was called, failed or not asked, who answered, mean latency | jev-gateway dashboard (MIT) | **Adopted in this plan** (Task 9 summary, Task 10 health line) |
| Plain explanations of Jev errors, and 403 kept apart from 401 | jev-gateway setup; TypeSafe SDK | **Adopted** (Task 9b codes, Task 10 texts) |
| Log the exact Jev build that answered, and its cost | TypeSafe and OpenRouter response fields | **Adopted** (Task 9b) |
| A follow-up question that keeps the previous agent: "yes, do it" after a Claude plan must not fall back to the 4B | jcm-router (`is_followup` ≥ 0.55), hyspacex (earlier-request field), both MIT | **Next plan.** It changes the decision and has a privacy question: Jev reads only features for private turns |
| Reasoning effort per agent, asked in the same Jev request, clamped to what each CLI supports | Switchboard (Apache-2.0), gargpratyush (MIT); upstream 0.22 `jevkit/effort.py` (MIT) | **Next plan**, via the upstream merge. Pass it on as `claude --effort` and `codex exec -c model_reasoning_effort=…`. Default medium, cap high: upstream measured high effort at 1.79× the tokens of medium for no first-try gain |
| Quota before the limit: read `x-codex-*-used-percent` and `anthropic-ratelimit-unified-*` headers, pace use against the window | hyspacex `quota.py` (MIT), auto-model-router `quota.py` (MIT); jevonian's header names (AGPL, facts only) | **Next plan** (spec part 4) |
| Hand over from a ChatGPT-login receptionist: emit a valid Responses stream (`response.created` … `response.completed`) under one stable id | Switchboard `decision-stream.ts` (Apache-2.0), gargpratyush (MIT), auto-model-router `responses_api.py` (MIT) | **Next plan** (spec part 4) |
| Verify-then-escalate: the cheap model answers, Jev checks the answer, and escalates when P(wrong) > 0.7 | TypeSafe SDE cascade; OpenRouter's verified-cascade cookbook ("about 7% of the cost"); auto-model-router (MIT) | **Option for the next plan.** Costs the 4B's full answer time first, and sends the answer text to Jev |
| A circuit breaker on Jev, and a second Jev channel (TypeSafe ↔ OpenRouter) | dirien/jev-router (Apache-2.0) | Later. It saves 2.5 s per turn only while Jev is down |
| A counterfactual cost per turn, and replaying thresholds over the log | jcm-router `tune`, hyspacex replay (MIT) | Later. It needs the probabilities in the log first |
| OpenRouter's own `typesafe/jev-router` as the OpenRouter agent's model: it picks a model and effort per request | OpenRouter docs | **Usable today with no code:** set it as the OpenRouter agent's model in the Front desk card. It fails when Jev fails, and the front desk then answers locally |
| A mock Jev for end-to-end tests without a key | jev-gateway `mock-jev.mjs` (MIT) | Later |

## What the Jev docs say about our client

These are correct already:
- the argmax and exact-key-set checks;
- reading the level probabilities rather than the averaged score;
- the 0.6 and 0.85 gates, which match the docs' own example;
- failing open;
- one combined request;
- the decision cache.

Mismatches:
1. **A score answer without `confidence` counted as fully sure** (`client._check_answer` defaulted it to 1.0), so a malformed reply could buy the cheapest tier. The schema requires it. **Fixed in Task 9b:** it now counts as unsure.
2. **We call the moving alias** (`jev-latest`, `~typesafe/jev-latest`) while our thresholds are tuned to one build. The docs and OpenRouter's skill say to pin a version (`jev-1.13.0`, `typesafe/jev-1.13`). **Proposed**, not done: it changes what Sander sees on OpenRouter and needs a shadow run first.
3. **We dropped the build that answered, and the cost.** **Fixed in Task 9b.**
4. **`TYPESAFE_MODEL` overrides the model for both providers**, so a TypeSafe id sent to OpenRouter fails every turn. Proposed: one override per provider.
5. **Retries.** 408 and OpenRouter's 524 were not retried (**fixed in Task 9b**). `Retry-After` is ignored and the backoff is linear; within a 2.5 s budget that matters little.
6. **403 read as a bad key, 413 as an unknown error.** The SDK treats 403 as permission denied. **Fixed in Task 9b:** `forbidden` and `state_too_large`.
7. **The difficulty rubric mixes two things.** Level 3 folds in "high-stakes … legal or money", which `costly_mistake` already asks. The docs warn this lowers confidence. Proposed, measured in shadow before changing.
8. **Two comments said Jev charges per request.** Billing is per input token ($0.042 per million, about $0.00002 per call); combining questions saves a round trip, not money. **Fixed in Task 9b.**
9. **Jev is strongest in English.** Track routing on Dutch turns separately in the shadow log.

## Caveats measured by others

- **Switching agents or models is not free.** jcm-router lost $19.53 over 309 requests by switching the main chat, because every switch rewrote the prompt cache. suenot's Jev-routed subagents cost 69.7% more than one strong agent. A handed-over turn starts a cold session. So measure a day in Shadow before On, and keep "hard only" as the default.
- **Thresholds do not carry over.** hyspacex settled on 0.4 and jcm on 0.7, each from its own data. Ours should come from our own shadow log.
