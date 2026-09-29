# Handleiding: deze fork installeren en de receptie gebruiken

Deze fork (`plutixeu-dev/hermes-jev-skills`) is de originele hermes-jev-skills (`kerpopule/hermes-jev-skills`) plus een "receptie". Deze handleiding vertelt:

- wat er anders is dan in het origineel;
- hoe je de fork installeert;
- hoe je de modellen en logins koppelt;
- wat je moet doen voordat de receptie werkt. Het is niet plug-and-play; reken op ongeveer een halfuur, plus een dag kijken in de proefstand.

Wil je alleen weten waar de receptie zit en hoe je hem bedient? Lees dan [receptie.md](receptie.md).

## 1. Wat deze fork toevoegt aan het origineel

| Onderdeel | Origineel | Deze fork |
|---|---|---|
| Wie een vraag beantwoordt | Hermes' eigen model. Jev kan binnen één provider een ander model kiezen (routering). | **Receptie**: een moeilijke vraag die naar buiten mag, gaat naar Claude Code (je Claude-login), Codex (je ChatGPT-login) of OpenRouter. De rest blijft op je lokale model. Het antwoord komt letterlijk terug, met één regel die zegt wie het schreef. |
| Privacy | Geheimen worden gemaskeerd voordat Jev iets leest. | Per profiel een privacyklasse. Een bericht met een wachtwoord, sleutel, IBAN, telefoonnummer of gevoelig woord (cliënt, dossier, BSN…) blijft altijd op je eigen machine. |
| Profielen in één gateway | Elk profiel las als `default`. | Elk profiel is zichzelf. Dat herstelt ook `private_profiles` van de gewone routering. |
| Dashboard (`jev dashboard`) | Modellen per profiel, de Jev-routeringsschakelaar, pools en live beslissingen. | Plus het blok **Receptionist dispatch** (label "new in this fork"). Alles van de receptie stel je daar in, zonder chatcommando's. |
| Terminal | `jev …` | Plus `jev dispatch` (laat zien wie een vraag zou krijgen) en `jev dispatch check`. |

Alles wat niet in deze tabel staat, is het origineel. De technische lijst staat in [CHANGELOG.md](../CHANGELOG.md) onder "Receptionist dispatch" en "Every profile of a multiplexed gateway is itself".

## 2. Hoe het samenwerkt

```
jij ─► Hermes (je lokale model, bijvoorbeeld Qwen3.6 via Ollama)
         │  de receptie (plugin hermes-dispatch) bekijkt elk nieuw bericht:
         │   1. privacy: mag dit naar buiten?        (je instellingen + de privacyfilter)
         │   2. moeilijkheid: Jev beoordeelt het     (heeft je Jev-sleutel nodig)
         │   3. wie is vrij, in jouw volgorde        (Claude → ChatGPT → OpenRouter)
         ├─ makkelijk, privé of twijfel ─► je lokale model antwoordt, zoals altijd
         └─ moeilijk en mag naar buiten ─► claude / codex / OpenRouter antwoordt
```

Faalt iets (geen login, limiet op, time-out, Jev onbereikbaar), dan antwoordt je lokale model. De chat blijft dus altijd werken.

**Belangrijk:** de receptie kiest geen lokaal model. "Lokaal" is het hoofdmodel dat Hermes al gebruikt. Automatisch doorsturen naar een zwaarder lokaal model, zoals qwen38-deep-review of gpt-oss-120b, zit er nog niet in.

## 3. Installeren (op de machine waar Hermes draait)

Heb je het origineel al in `~/hermes-jev-skills`? Zet die map dan op de fork:

```bash
git -C ~/hermes-jev-skills remote set-url origin https://github.com/plutixeu-dev/hermes-jev-skills
git -C ~/hermes-jev-skills fetch origin
git -C ~/hermes-jev-skills checkout main && git -C ~/hermes-jev-skills pull
```

Nog niets? Dan:

```bash
git clone https://github.com/plutixeu-dev/hermes-jev-skills ~/hermes-jev-skills
```

Daarna eerst kijken, dan installeren:

```bash
python3 ~/hermes-jev-skills/install.py --check
python3 ~/hermes-jev-skills/install.py
```

Dit installeert drie Hermes-plugins: `hermes-jev`, `hermes-handoff` en `hermes-dispatch` (de receptie). De receptie staat na installatie **uit**. Herstart de Hermes-gateway één keer, zodat Hermes de plugins laadt. Na die ene keer werkt elke wijziging in het dashboard meteen, zonder herstart.

Wat de installer zelf regelt:

- **Oude `jev`-links.** Een link naar een oudere kopie van deze repo, zoals een oude clone of een cache, wordt vervangen. Wijst een link naar iets dat niet meer bestaat, dan ook. Je ziet ze in het rapport onder `cli.replaced`, en je hoeft niets met de hand te verwijderen.
- **Een eigen bestand of een link naar iets anders heet `jev`.** Dat blijft staan, en het rapport waarschuwt. Die waarschuwing is niet cosmetisch: een terminal die dat bestand vindt, draait die `jev` en niet deze.
- **De lijst `next` in het rapport** is de volgorde van de rest:
  1. `jev doctor`;
  2. zo nodig `jev setup-key`;
  3. de gateway één keer herstarten;
  4. `jev dispatch check`;
  5. het dashboard.

  `/jev routing` staat daar alleen als alternatief voor de receptie, niet als extra.

## 4. De koppelingen: wat je één keer instelt

| Wat | Waarvoor | Hoe (in je eigen terminal, op de Hermes-machine) |
|---|---|---|
| **Jev-sleutel** (TypeSafe) | Jev beoordeelt of een vraag moeilijk is. Zonder sleutel blijft alles lokaal. | `jev setup-key`: er opent een pagina waar jij de sleutel plakt. Controleer daarna met `jev doctor`. |
| **Lokaal model** | Je gewone chat | Zoals je Hermes nu al hebt ingesteld (Ollama). In het dashboard zie en wijzig je het onder "Main model". |
| **Claude Code** (Max-login) | Moeilijke vragen naar Claude | `claude` installeren en één keer inloggen met je Max-account. Voor een server zonder browser: `claude setup-token`. |
| **Codex** (ChatGPT-login, Plus) | Moeilijke vragen naar ChatGPT | `codex` installeren en `codex login`. Let op: Codex kan bestanden lezen die de Hermes-gebruiker mag lezen. Zet Codex dus alleen aan als je dat accepteert. |
| **OpenRouter** (optioneel) | Laatste redmiddel, alleen voor publieke vragen | `jev setup-key --provider openrouter` |

Sleutels plak je altijd zelf op die pagina, nooit in een chat. Het dashboard toont alleen óf er een sleutel is.

**Draait Hermes op een NAS of server zonder scherm?** De pagina van `jev setup-key` werkt alleen op die machine zelf, via 127.0.0.1. Als daar geen browser opengaat, geeft `jev setup-key` zelf twee manieren:

- **Via een SSH-tunnel.** Er staat een regel `ssh -N -L <poort>:127.0.0.1:<poort> <gebruiker>@<machine>` bij. Voer die uit op je eigen computer en laat het venster open. Daarna opent de link daar gewoon in je browser.
- **Zonder browser.** Log zelf in op de machine en voer `jev setup-key --tty` uit. Je plakt de sleutel dan in een verborgen prompt.

De link werkt tien minuten. Is hij verlopen, draai `jev setup-key` dan opnieuw: je krijgt een nieuwe poort, dus ook een nieuwe tunnelregel.

## 5. Instellen in het dashboard

Dit is de korte versie. Elke knop, de bestanden en een tabel met problemen en oplossingen staan in [receptie.md](receptie.md).

1. Start `jev dashboard` en open `http://127.0.0.1:8791` in je browser op dezelfde machine. Werk je vanaf een andere computer, bijvoorbeeld omdat Hermes op een NAS draait? Dan toont `jev dashboard` een regel `ssh -N -L 8791:127.0.0.1:8791 …`. Voer die uit op je eigen computer, laat het venster open, en open daar `http://127.0.0.1:8791`. Voor een privénetwerk zoals Tailscale: zie [router-dashboard/README.md](../router-dashboard/README.md), onderdeel "Off your own machine".
2. Kijk in het blok **Receptionist dispatch** eerst bij **Checks**:
   - "Plugin installed" moet een vinkje hebben;
   - "Enabled in this profile" ook.

   Staat er "not enabled", voer dan uit wat daar staat en herstart de gateway één keer.
3. Onder **Privacy** kies je per profiel:
   - **Only this machine**: niets gaat naar buiten. Dit is de standaard, en de veiligste keuze.
   - **May go to an agent**: moeilijke vragen mogen naar Claude of ChatGPT. Berichten met geheimen of gevoelige woorden blijven toch lokaal.
   - **Public**: mag ook naar OpenRouter.

   Heb je geen extra profielen, dan is er alleen `default`.
4. Onder **Agents**: zet Claude Code aan en kies een model (bijvoorbeeld `opus`). Wil je Claude ook voor plannen en reviews gebruiken, en niet alleen voor code? Haal dan het vinkje "Only repository work" weg. Zet ChatGPT (Codex) en OpenRouter aan zodra je die wilt gebruiken.
5. Onder **Order**: met **Swap** zet je Claude eerst, ook voor "Other hard work".
6. Klik **Preview dispatch changes**, controleer de lijst en klik **Confirm & save**. Je ziet "Saved and verified", met het pad van de back-up.
7. Klik **Test** bij Claude. Er gaat één vast testbericht uit, en je ziet "ok" of een foutmelding met de reden.
8. Zet de schakelaar op **Shadow**. De receptie beslist en noteert dan alleen, maar stuurt nog niets door. Staat er een waarschuwing dat de Jev-routering aan staat, klik dan op de knop eronder: er beslist er maar één tegelijk.
9. Chat een dag gewoon verder. Onderaan het blok zie je bij elk bericht wie het zou hebben beantwoord en waarom. De tekst van je berichten staat daar nooit.
10. Klopt het? Zet de schakelaar op **On**. Terug kan altijd met **Off**.

## 6. Veelgestelde vragen

- **Is het plug-and-play?** Nee. Je doet eenmalig de koppelingen uit hoofdstuk 4 en de instellingen uit hoofdstuk 5. Daarna gaat alles vanzelf, en wijzig je alleen nog via het dashboard.
- **Wat als Claude of ChatGPT vol zit?** Dan wordt die agent een tijd overgeslagen en is de volgende aan de beurt, of je lokale model. In het dashboard zie je "cooling N s", met een Reset-knop.
- **Wat gaat er precies naar buiten?** Alleen bij een doorgestuurde vraag, en alleen:
  - je vraag;
  - hooguit zes recente berichten, zonder geheimen;
  - samen maximaal zo'n 12.000 tekens.

  Nooit bestanden, geheugen of tool-uitvoer. Details staan in [receptionist-dispatch.md](receptionist-dispatch.md).
- **Kan ik de gewone Jev-routering nog gebruiken?** Ja, maar niet tegelijk met de receptie in hetzelfde profiel. Het dashboard waarschuwt dan.
