# Model routing dashboard

One page for every Hermes profile's models, with Jev on top.

```bash
jev dashboard
```

Then open http://127.0.0.1:8791/.

- **Jev routing switch**: Off, Shadow (decide and log, do not switch) or On. It follows the profile picker: one profile, or **All profiles** with a confirmation. Takes effect on the next message; no restart. In a profile whose front desk is Shadow or On, routing stands aside by itself, and the card names those profiles.
- **Routing pools**: the pools in `jev/routing.json` as a grid — tiers down the side, kinds of work across the top — because that is the shape routing actually has. A missing pool is drawn as a hatched gap rather than left out, and a tier that has nothing but `general` and `vision` gets said out loud: Jev is asked what kind of work the turn is, that tier has no specialist pool, so the answer is bought and discarded. Models struck through are removed by an `exclude` pattern; a pool with nothing left is called empty in practice. Read-only — `jev models suggest --write` and your editor own that file.
- **Live**: every Jev decision as it happens, across all profiles: tier, kind of work, the model it went to, **which pool it came from**, confidence, Jev's latency. `medium / coding` means the specialty answer chose the model; `medium / general (fallback)` means it did not, which is the same money for no effect. Decisions only; the text of a turn is never logged or shown.
- **Models**: the main model and each auxiliary slot (compression, vision, title generation and the rest) per profile, with a searchable list of every model you hold a key or login for. **All profiles** sets a slot for everyone at once, after a confirmation that names how many agents it touches.
- **Front desk** (new in this fork): the card switches `hermes-dispatch` Off, Shadow or On, sets the notice, each profile's privacy class, each agent's on/off, model and (for claude) `only_repo`, and the order they're tried.
  - **Where it writes.** Mode and notice write to `<profile home>/jev/dispatch-state.json`, the file `/dispatch` writes. Privacy, agents and order write to `<hermes root>/jev/dispatch.json`, previewed and saved with a backup and a verified read-back. Both take effect on the next message; nothing here restarts a gateway.
  - **The receptionist.** Each profile's chat model in `config.yaml`, shown above the switch with a Change button. It is not a pin: only a `/model` in one chat is.
  - **Warnings.** Each says why a Shadow or On profile would still answer every turn itself: no receptionist, privacy "Only this machine", no agent on, no Jev key, or a receptionist on the ChatGPT login, which cannot hand a turn over yet.
  - **One row per turn.** Whether Jev was called (and how long it took, and which way), fail-open with its code, or not called. Then the tier and kind of work, and who answered. A turn the receptionist answered after a Jev failure is a warning row. A health line above the table sums up the rows and names the Jev build that answered.
- **Keys** (new in this fork): TypeSafe and OpenRouter, where each key is (secret store, `~/.config/jev`, which profiles' `.env`), and which one turns reach Jev through.
  - **Save.** Paste a key and press Save: it is checked with the provider, stored the way `jev setup-key` stores it, and never sent back to the page. The field empties before the request goes. It works only on a dashboard bound to loopback, which includes the ssh tunnel.
  - **Check.** One real decisions request to Jev, the way a turn makes it. It shows the model asked for, the latency and the build that answered, or what went wrong in words: the key was refused, may not use this model, no credit left, rate limited, Jev not reachable.
- **Local (Ollama)** (new in this fork): under Main model, the models this machine's Ollama serves (loopback or a private address only). Picking one writes the model with its provider and `base_url`. Moving a profile off a local server clears the local `base_url`, because Hermes honours `model.base_url` for other providers too.
- **Backups.** Model changes and `dispatch.json` are previewed, backed up beside the file, and read back to verify. The Jev routing switch and the front desk's mode and notice switches are written straight away, with no backup. Keys go to the secret store and the profiles' `.env` files, as `jev setup-key` writes them. None of it restarts a gateway.

The grid is layered the way `jevkit/route.py` layers it: `~/.config/jev/routing.json`, then the shared Hermes one, then the profile's own. A later file replaces a whole tier it mentions and inherits the tiers it does not, so a profile that pins only `hard` keeps everyone else's `simple` and `medium`. The page names every file it read.

Needs PyYAML, which Hermes' own Python already has; `jev dashboard` uses that interpreter when it finds it.

**From another computer** (Hermes on a NAS or server without a browser): `jev dashboard` prints an `ssh -N -L 8791:127.0.0.1:8791 <user>@<host>` line. Run it on your own computer and leave it open, then open http://127.0.0.1:8791 there. Nothing changes on the server side: it stays on loopback, and the tunnel is encrypted.

**Off your own machine** (Tailscale, VPN): `jev dashboard --host <private-ip>`. It refuses to start without a token off loopback, prints a one-time link carrying it, and swaps it for an HttpOnly cookie. The traffic is plain HTTP, so only do this on a network you trust end to end, and never on a public address.

Tests: `python -m unittest discover -s router-dashboard/tests` (with PyYAML installed).
