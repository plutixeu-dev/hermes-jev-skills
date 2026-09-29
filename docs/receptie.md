# De receptie: waar hij zit, hoe je hem instelt en gebruikt

De receptie (`hermes-dispatch`) is het onderdeel dat deze fork aan hermes-jev-skills toevoegt. Hij bekijkt elk nieuw bericht in Hermes en beslist wie antwoordt:

- **je lokale model**, zoals altijd. Dat gebeurt bij verreweg de meeste berichten;
- **of een sterkere agent.** Dat kan Claude Code zijn (je Claude-login), ChatGPT via Codex (je ChatGPT-login) of OpenRouter.

Alleen een bericht dat Jev als moeilijk beoordeelt, en dat volgens je privacy-instelling naar buiten mag, gaat naar een agent. Het antwoord van die agent komt ongewijzigd terug, met bovenaan één regel die zegt wie het schreef.

Je bedient alles vanuit het dashboard. Chatcommando's of bestanden aanpassen hoeft niet.

Hoe je de fork installeert en de logins koppelt, staat in [handleiding.md](handleiding.md). De technische details staan in [receptionist-dispatch.md](receptionist-dispatch.md).

## 1. Waar zit de receptie?

| Wat | Waar | Wat je ermee doet |
|---|---|---|
| **Het bedieningspaneel** | `jev dashboard`, blok **Receptionist dispatch** (label "new in this fork") | Alles: aan/uit, privacy, agents, volgorde, testen, meekijken |
| De plugin zelf | `~/.hermes/plugins/hermes-dispatch/` | Niets. De installer zet hem daar neer en zet hem aan in `config.yaml` |
| Aan/uit-stand per profiel | `~/.hermes/jev/dispatch-state.json` (een profiel: `~/.hermes/profiles/<naam>/jev/`) | Niets. Het dashboard schrijft dit bestand |
| Privacy, agents en volgorde | `~/.hermes/jev/dispatch.json`, met een back-up bij elke wijziging | Niets. Het dashboard schrijft dit bestand |
| Het logboek | `~/.hermes/logs/jev-decisions.jsonl`, regels met `"kind":"dispatch"` | Niets. Het dashboard toont het onderaan het blok |
| De code | `jevkit/dispatch.py`, `privacy.py`, `agents.py` en `relay.py` in deze repo | Alleen als je de fork zelf wilt aanpassen |

Het logboek bevat alleen beslissingen: wie antwoordde, waarom, en de privacyklasse. Er staat nooit de tekst van je berichten of van de antwoorden in.

## 2. Het dashboard openen

Op de machine waar Hermes draait:

```bash
jev dashboard
```

Open dan `http://127.0.0.1:8791` in een browser op die machine.

**Draait Hermes op een NAS of server zonder scherm?** Dan toont `jev dashboard` zelf een regel als deze:

```
from another computer: run `ssh -N -L 8791:127.0.0.1:8791 <gebruiker>@<nas>` there, leave it open, then open http://127.0.0.1:8791/
```

Voer die `ssh`-regel uit op je eigen computer en laat dat venster open. Open daarna `http://127.0.0.1:8791` in je eigen browser. Het dashboard blijft op de NAS alleen lokaal bereikbaar; de tunnel is versleuteld. Het dashboard stopt als je `jev dashboard` afsluit (Ctrl+C).

## 3. Het blok, onderdeel voor onderdeel

Kies bovenaan het dashboard het profiel. Heb je geen extra profielen, dan is dat `default`.

### De schakelaar: Off, Shadow, On

| Stand | Wat er gebeurt |
|---|---|
| **Off** | De receptie doet niets. Alles gaat zoals zonder deze fork. |
| **Shadow** | De receptie beslist en schrijft op wie het had moeten beantwoorden, maar stuurt niets door. Je lokale model beantwoordt alles. Dit is de proefstand. |
| **On** | De receptie stuurt moeilijke berichten echt door. |

Een wijziging werkt vanaf je volgende bericht, zonder herstart.

Het vinkje **"Say so when a hard turn was answered here"** laat Hermes één regel toevoegen als een moeilijk bericht toch lokaal is beantwoord, bijvoorbeeld omdat het privé was of Claude vol zat. Die regel begint met `[dispatch]`. Dit werkt alleen in de stand On.

Staat er een rode melding over **Jev-routering**? Dan staat in dit profiel ook de gewone Jev-routering (`/jev routing`) aan. Er mag er maar één tegelijk beslissen. Klik op de knop onder de melding: die zet de Jev-routering voor je uit.

### Privacy

Per profiel kies je waar moeilijke berichten heen mogen:

| Keuze | Betekenis |
|---|---|
| **Only this machine** | Niets gaat naar buiten. Dit is de standaard, en de veiligste keuze. |
| **May go to an agent** | Mag naar Claude Code of ChatGPT (Codex), nooit naar OpenRouter. Jev krijgt alleen grove kenmerken te zien, zoals lengte en of er code in staat, niet je tekst. |
| **Public (OpenRouter too)** | Mag ook naar OpenRouter. Jev leest de tekst, met geheimen eruit gefilterd. |

Wat je ook kiest, een bericht met een van deze dingen blijft **altijd** op je eigen machine:

- een wachtwoord, sleutel of token;
- een IBAN;
- woorden als cliënt, patiënt, dossier, behandelplan, medicatie, BSN of schulden.

Een telefoonnummer of adres maakt een publiek bericht privé. Het kijkt daarbij ook naar de laatste berichten die zouden meegaan, niet alleen het nieuwste.

### Agents

Eén regel per agent:

| Kolom | Wat het is |
|---|---|
| **Use** | Aan of uit. |
| **Model** | Welk model de agent gebruikt. Bij Claude: `opus`, `sonnet` of `haiku`. Leeg betekent het standaardmodel van dat programma. OpenRouter heeft altijd een model nodig. |
| **Only repository work** | Alleen bij Claude, en standaard aangevinkt: Claude krijgt dan alleen programmeerwerk. Haal het vinkje weg als Claude ook plannen, reviews en ander moeilijk werk mag doen. |
| **Status** | `found` als het programma op deze machine staat, `not found` als het ontbreekt. Bij OpenRouter: `key present`, of het commando om de sleutel te koppelen. `cooling N s` betekent dat de agent even wordt overgeslagen, bijvoorbeeld omdat je limiet op was; **Reset** heft dat op. |
| **Test** | Stuurt één vast testbericht ("Reply with only the word: ok") via die agent, met de **opgeslagen** instellingen. Je ziet of hij antwoordde, met welk model, en het begin van het antwoord. Of je ziet de reden waarom het mislukte. |

### Order

Hier staat wie het eerst wordt gevraagd, apart voor **Repository work** (programmeerwerk) en **Other hard work** (al het andere). Met **Swap** draai je de twee om. OpenRouter komt altijd als laatste, en alleen voor publieke berichten.

Is de eerste agent uit, vol of niet bereikbaar, dan gaat het bericht naar de volgende. Lukt geen enkele, dan antwoordt je lokale model. Je chat blijft dus altijd werken.

### Opslaan: Preview, Confirm & save

Wijzigingen bij Privacy, Agents en Order worden niet meteen bewaard:

1. Klik **Preview dispatch changes**. Je ziet een lijst van wat verandert.
2. Klik **Confirm & save**. Het dashboard maakt een back-up, schrijft `dispatch.json` en leest het terug ter controle. Je ziet **"Saved and verified"** met het pad van de back-up.

Wil je het toch niet, klik dan **Discard**. De schakelaar en het notice-vinkje worden wel meteen bewaard.

### Checks

| Regel | Moet zijn | Zo niet |
|---|---|---|
| Plugin installed | ✓ | Draai `python3 ~/hermes-jev-skills/install.py`. |
| Enabled in this profile | ✓ | Draai het commando dat erbij staat en herstart de gateway één keer. |
| llm_execution | yes | Je Hermes is te oud voor de receptie. Werk Hermes bij. |
| Settings file | readable, of "not there yet" | Het bestand is kapot. Zet de laatste back-up terug (die staat ernaast) of los het met de hand op. Tot dan staan alle agents uit. |

### Recent dispatch decisions

Onderaan staat per bericht:

- de tijd, het profiel en de stand;
- **Answered by**: `local` of de agent. In Shadow is dat wie het zou hebben beantwoord;
- de privacyklasse en het niveau dat Jev gaf;
- de reden;
- de pogingen.

Zo zie je in de proefstand of de keuzes kloppen.

## 4. De eerste keer instellen

Voor de situatie waarin je lokaal begint, Claude (Max) als eerste hulp neemt en ChatGPT voorlopig niet gebruikt.

Doe eerst dit:

- `jev doctor` geeft `key.present` en `jev.reachable` allebei `true`;
- de gateway is na de installatie één keer herstart;
- `claude` is geïnstalleerd en ingelogd, als **dezelfde gebruiker** die Hermes draait. Log in door `claude` in je eigen terminal te starten; op een machine zonder browser toont het een link die je op een ander apparaat opent.

Dan in het dashboard:

1. **Checks**: alles heeft een vinkje.
2. **Privacy**: zet `default` op **May go to an agent**.
3. **Agents**: vink **Use** aan bij Claude Code en kies model `opus`. Haal het vinkje **Only repository work** weg als Claude ook plannen en reviews mag doen. Laat ChatGPT en OpenRouter uit.
4. **Order**: klik **Swap** bij "Other hard work", zodat Claude ook daar eerst staat.
5. Klik **Preview dispatch changes** en daarna **Confirm & save**.
6. Klik **Test** bij Claude. Je moet "ok" zien. Zie je een fout, kijk dan bij hoofdstuk 6.
7. Zet de schakelaar op **Shadow**.
8. Chat een dag gewoon verder en kijk af en toe naar **Recent dispatch decisions**.
9. Klopt het? Zet de schakelaar op **On**.

ChatGPT later toevoegen: draai `codex login` in je eigen terminal. Vink daarna ChatGPT aan bij Agents, sla op en test.

## 5. Dagelijks gebruik

- **Een doorgestuurd antwoord herken je** aan de eerste regel, bijvoorbeeld `[claude · opus]`. Daaronder staat het antwoord van die agent, ongewijzigd. Zonder zo'n regel komt het antwoord van je lokale model.
- **Een gesprek met Claude loopt door.** Het volgende bericht in dezelfde Hermes-sessie gaat verder in hetzelfde Claude-gesprek.
- **Limiet op?** Dan wordt die agent een tijd overgeslagen: je ziet `cooling` in het dashboard. De volgende agent of je lokale model neemt het over. **Reset** probeert het meteen weer.
- **Even niets naar buiten?** Zet de schakelaar op **Off** of **Shadow**. Dat werkt meteen, en je instellingen blijven bewaard.
- **Nooit doorgestuurd** worden:
  - berichten van geplande taken (cron);
  - berichten van subagents;
  - de stappen binnen één beurt, zoals tool-aanroepen. Alleen het begin van je bericht telt.

## 6. Problemen oplossen

| Je ziet | Oorzaak | Oplossing |
|---|---|---|
| Het blok **Receptionist dispatch** ontbreekt | Je dashboard komt uit een oude installatie | `git -C ~/hermes-jev-skills pull && python3 ~/hermes-jev-skills/install.py`, dan `jev dashboard` opnieuw |
| Claude staat op `not found` | `claude` is niet geïnstalleerd, of niet te vinden voor deze gebruiker | Installeer Claude Code als de gebruiker die Hermes draait en test opnieuw |
| **Test** geeft een login- of auth-fout | Claude is niet ingelogd voor deze gebruiker | `claude` starten in je eigen terminal en inloggen, dan opnieuw **Test** |
| Elke beslissing is `local`, met reden "highly sensitive" | Het profiel staat op **Only this machine** | Privacy op **May go to an agent**, dan Preview, dan Confirm & save |
| Elke beslissing is `local`, met reden "Jev unavailable" | De Jev-sleutel ontbreekt of Jev is onbereikbaar | `jev doctor`, en zo nodig `jev setup-key` |
| Beslissingen met "stood aside" | De Jev-routering staat ook aan | Klik op de knop onder de rode melding |
| Claude krijgt alleen programmeervragen | **Only repository work** staat aangevinkt | Vinkje weg, dan Preview, dan Confirm & save |
| Een moeilijke vraag bleef toch lokaal | Er stond een wachtwoord, IBAN of gevoelig woord in, of in de berichten ervoor | Zo bedoeld. De reden staat in de lijst onderaan |
| **Enabled in this profile** heeft een ✗ | De plugin staat niet aan in `config.yaml` | Draai het commando dat erbij staat en herstart de gateway één keer |
| Settings file "cannot be read" | `dispatch.json` is met de hand kapotgemaakt | Zet de back-up ernaast terug. Tot dan staan alle agents veilig uit |

## 7. Liever via de chat?

Het dashboard en deze commando's schrijven dezelfde bestanden. Je mag ze door elkaar gebruiken:

| Commando in Hermes | Doet |
|---|---|
| `/dispatch` | Toont de stand, de privacyklasse en de agents |
| `/dispatch shadow`, `/dispatch on`, `/dispatch off` | Zelfde als de schakelaar |
| `/dispatch notice on`, `/dispatch notice off` | Zelfde als het notice-vinkje |

In een terminal toont `jev dispatch check` welke agents deze machine kan bereiken.
