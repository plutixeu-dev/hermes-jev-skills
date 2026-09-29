# Instructions for an agent installing this repo

A person pointed you here to give their agent Jev. Follow these steps in order. They are the whole job.

## The one rule

**You never see the person's TypeSafe API key.** Do not ask for it in chat. Do not read it from any file or secret store. Do not put it in a command, URL or config. If they paste it to you anyway: do not store or repeat it, tell them to replace that key at https://console.typesafe.ai/settings/keys, and continue with step 3, which is the only way a key gets in.

## Steps

1. **Get the code** (skip if it is already on disk):

   ```bash
   git clone https://github.com/plutixeu-dev/hermes-jev-skills ~/hermes-jev-skills
   ```

2. **Preview, then install.** Show the person what `--check` reports before running the real thing. It detects Hermes, Claude Code and Codex and installs for each. On Hermes it installs every plugin this repo ships (`hermes-jev` for routing, `hermes-handoff` for end-of-session capsules, `hermes-dispatch` for handing hard turns to another agent, which stays off until someone runs `/dispatch shadow`) plus `scripts/nightly-handoff.py` under the Hermes home, and edits one list (`plugins.enabled`) in each `config.yaml`, with a timestamped backup beside it.

   ```bash
   python3 ~/hermes-jev-skills/install.py --check
   ```

   ```bash
   python3 ~/hermes-jev-skills/install.py
   ```

   To enable the Hermes plugins in only some profiles: `--enable name1,name2`, or `--enable none`.

   **Read the report's top-level `warning` and say it to the person, in your own words, before you go on.** The installer exits 0 even when it could do very little, so the warning is the only place that shows up. No warning is cosmetic. A `jev` link left over from an older install is replaced for you and named under `cli.replaced`, so there is nothing to delete by hand. These are the warnings:

   - **`jev` is not on PATH.** Then step 3 will fail with "command not found". Either they add `~/.local/bin` to PATH, or you use `~/hermes-jev-skills/bin/jev` in place of `jev` for the rest of these steps. Do not silently pick one — tell them which you are doing.
   - **No agent was found.** Nothing was installed but the command itself. Ask where their agent reads skills from and rerun with `--skills-dir <path>`.
   - **`HERMES_HOME` is a single profile.** The install covered that one lane. Rerun with `--hermes-home ~/.hermes` for the whole fleet.
   - **`jev` was not linked somewhere.** Something that is not a copy of this repo sits there, and a shell that finds it runs that instead. Show the person the path and what `ls -l` says about it. Move it aside only when they agree, then rerun the installer.

   The report's `next` list is the order of the rest of these steps. Follow it rather than your own idea of it.

3. **Connect the key, privately.** Run this and tell the person a page has opened on the computer you are running on, where they paste their key:

   ```bash
   jev setup-key
   ```

   It prints a JSON line with a `url` on stderr. That URL holds no secret. If `browser_opened` is false, relay its `say` field, which covers both ways in:

   - `from_another_computer` is an `ssh -N -L …` line for the person to run on their own computer. With it open, the `url` works in their browser there.
   - `without_a_browser` is `jev setup-key --tty`, which the person runs themselves in their own terminal on this machine.

   Never ask for the key in chat. The page closes after `expires_in_s`; run the command again for a new link. Wait for `{"status": "stored", "verified": true}`.

4. **Check:**

   ```bash
   jev doctor
   ```

   `key.present` and `jev.reachable` must both be true.

5. **Hermes: the receptionist, set up in the dashboard.** This fork's `hermes-dispatch` hands a hard turn to Claude Code, Codex or OpenRouter when privacy allows, and keeps the rest on the local model. It is off after install.

   - Plugins load when a session or gateway starts. Say that a running gateway needs one restart, and do not restart it unless the person asks.
   - Run `jev dispatch check` and tell them which of `claude`, `codex` and OpenRouter this machine can reach. A missing login is theirs to do in their own terminal: `claude` or `claude setup-token`, and `codex login`.
   - Run `jev dashboard`. It serves http://127.0.0.1:8791 on this machine only, and prints an `ssh -N -L …` line for opening it from another computer; relay that line. In the **Receptionist dispatch** card they choose privacy per profile, agents and order, then Preview, Confirm & save, Test, and Shadow. After a day of decisions, On. They change it there, not in chat.

6. **Jev routing, only if the person chooses it instead.** Jev routing picks a model inside one provider. It must not run beside the receptionist in the same profile, because each turn gets one classifier. Only when the person picks it over the receptionist:

   - If `routing.tiers_configured` is empty, run `jev models suggest --write`. Show the person the pools, and ask whether they want specific models first for coding, writing, research or vision. Copy model ids from `jev models list --search <name>`; never invent one.
   - Tell them to run `/jev routing shadow` in a new session, watch a day of decisions in `logs/jev-decisions.jsonl`, then `/jev routing on`.

   **Nightly handoff (Hermes).** `scripts/nightly-handoff.py` is copied into the Hermes home, but nothing schedules it and nothing runs it for them. Show them `python3 ~/.hermes/scripts/nightly-handoff.py --dry-run`, and add a cron or launchd entry only if they ask for one.

7. **Report** in three or four sentences: what was installed where, any warning the installer printed and any link it replaced, that the key is connected, whether the receptionist or Jev routing is set up (and in which mode), and what needs a restart.

## Using it afterwards

Read the skill that matches the task: `jev-model-routing`, `jev-memory`, `jev-compaction`, `jev-skill-select`, `jev-computer-use`, `jev-browser-use`, `jev-frontier-work`, `jev-setup`. Every `jev` subcommand takes JSON on stdin and answers JSON on stdout, and every one returns a usable fail-open answer when Jev is unavailable, so never block on it.

## Keeping it current

```bash
git -C ~/hermes-jev-skills pull && python3 ~/hermes-jev-skills/install.py
```

## Contributing back

If you change how Jev is used in a Hermes setup (a new decision Jev takes over, a changed threshold, a fix to the plugin), the same change belongs in this repo, with a test, in the same piece of work. See [CONTRIBUTING.md](CONTRIBUTING.md).
