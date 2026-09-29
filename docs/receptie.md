# De receptie: waar hij zit, hoe je hem instelt en gebruikt

De receptie (`hermes-dispatch`) is het onderdeel dat deze fork aan hermes-jev-skills toevoegt. In het dashboard heet hij **Front desk**. Hij bekijkt elk nieuw bericht in Hermes en beslist wie antwoordt:

- **de receptie zelf**: het chatmodel van je profiel, bijvoorbeeld een lokaal model via Ollama. Dat gebeurt bij verreweg de meeste berichten;
- **of een sterkere agent.** Dat kan Claude Code zijn (je Claude-login), ChatGPT via Codex (je ChatGPT-login) of OpenRouter.

Per bericht vraagt de receptie Jev één keer hoe moeilijk het is. Alleen een bericht dat Jev als moeilijk beoordeelt, en dat volgens je privacy-instelling naar buiten mag, gaat naar een agent. Het antwoord van die agent komt ongewijzigd terug, met bovenaan één regel die zegt wie het schreef. Je chatmodel verandert nooit.

Je bedient alles vanuit het dashboard. Chatcommando's of bestanden aanpassen hoeft niet.

Hoe je de fork installeert en de logins koppelt, staat in [handleiding.md](handleiding.md). De technische details staan in [receptionist-dispatch.md](receptionist-dispatch.md).

## 1. Waar zit de receptie?

| Wat | Waar | Wat je ermee doet |
|---|---|---|
| **Het bedieningspaneel** | `jev dashboard`, blok **Front desk** (label "new in this fork") | Alles: aan/uit, privacy, agents, volgorde, testen, meekijken |
| **De sleutels** | `jev dashboard`, blok **Keys** | Een sleutel plakken, en controleren of Jev antwoordt |
| **Het receptiemodel** | `jev dashboard`, blok **Main model**, met de lijst **Local (Ollama)** | Kiezen welk model de chat voert |
| De plugin zelf | `~/.hermes/plugins/hermes-dispatch/` | Niets. De installer zet hem daar neer en zet hem aan in `config.yaml` |
| Aan/uit-stand per profiel | `~/.hermes/jev/dispatch-state.json` (een profiel: `~/.hermes/profiles/<naam>/jev/`) | Niets. Het dashboard schrijft dit bestand |
| Privacy, agents en volgorde | `~/.hermes/jev/dispatch.json`, met een back-up bij elke wijziging | Niets. Het dashboard schrijft dit bestand |
| Het logboek | `~/.hermes/logs/jev-decisions.jsonl`, regels met `"kind":"dispatch"` | Niets. Het dashboard toont het onderaan het blok |
| De code | `jevkit/dispatch.py`, `frontdesk.py`, `privacy.py`, `agents.py` en `relay.py` in deze repo | Alleen als je de fork zelf wilt aanpassen |

Het logboek bevat alleen beslissingen: of Jev gevraagd is, wat Jev vond, wie antwoordde, waarom, en de privacyklasse. Er staat nooit de tekst van je berichten of van de antwoorden in.

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

## 3. De receptie kiezen

De receptie is het chatmodel dat in `config.yaml` van je profiel staat. In het blok **Front desk** zie je het bovenaan, bijvoorbeeld "Receptionist: **qwen3.5:4b** · local". Met **Change** spring je naar de plek waar je het kiest.

Een lokaal model via Ollama:

1. Ga naar het blok **Main model**. Daaronder staat de lijst **Local (Ollama)** met de modellen die Ollama op deze machine heeft.
2. Kies er een, bijvoorbeeld `qwen3.5:4b`.
3. Klik rechtsboven **Apply changes**. De preview toont elke regel die verandert: het model, de provider (`custom`) en het adres van Ollama (`base_url`).
4. Klik **Confirm & apply**. Het dashboard maakt een back-up en leest het bestand terug ter controle.
5. Herstart de gateway één keer. Pas daarna praat een lopende gateway met de nieuwe receptie.

Staat er "Local (Ollama): none found"? Dan antwoordt Ollama niet op deze machine, of heeft het nog geen modellen. De reden en het adres staan eronder.

Ga je van een lokaal model terug naar een model in de cloud, dan haalt het dashboard het lokale adres (`base_url`) weg. Anders zou Hermes het cloudmodel op je eigen machine zoeken.

De receptie is geen vastgezet model. Alleen een `/model` in één chat zet een model vast, en dan alleen voor die chat (zie hoofdstuk 6).

## 4. Sleutels

In het blok **Keys** staan twee sleutels: **TypeSafe** en **OpenRouter**. Jev is via allebei bereikbaar. Bovenaan staat via welke je berichten Jev bereiken, bijvoorbeeld "Turns reach Jev through **OpenRouter** · `~typesafe/jev-latest`".

- **Plakken:** plak een sleutel in het veld en klik **Save**. Het veld wordt meteen leeggemaakt. De sleutel gaat naar de kluis van deze computer en naar de `.env` van elk profiel. Hij komt nooit meer terug op de pagina, ook niet gedeeltelijk. Plak een sleutel nooit in een chat.
- **Controleren:** klik **Check**. Het dashboard stuurt dan één klein verzoek naar Jev, zoals een bericht dat ook doet. Het kost een fractie van een cent.

Wat je daarna ziet:

| Je ziet | Betekenis |
|---|---|
| `ok · ~typesafe/jev-latest · 412 ms` | Jev antwoordde, via dat model, in 412 milliseconden. Eronder staat welke versie van Jev antwoordde |
| the key was refused | De sleutel klopt niet. Maak een nieuwe en plak die |
| the key may not use this model | De sleutel werkt, maar mag Jev niet gebruiken. Kijk bij de rechten van de sleutel |
| no credit left | Het tegoed is op. Waardeer op bij TypeSafe of OpenRouter |
| rate limited, try again in a minute | Te veel verzoeken tegelijk. Probeer het over een minuut opnieuw |
| Jev could not be reached from this machine | Deze machine komt niet bij Jev. Kijk naar het netwerk |
| this model id is not served (jev-latest moved?) | De aanbieder kent de modelnaam van Jev niet meer. Werk de repo bij: `git -C ~/hermes-jev-skills pull` |
| no key stored | Er staat nog geen sleutel. Plak er een |

Plakken kan alleen als het dashboard lokaal draait. Via de ssh-tunnel werkt het ook. Een draaiende gateway gebruikt een nieuwe sleutel pas na de volgende herstart.

## 5. Het blok Front desk, onderdeel voor onderdeel

Kies bovenaan het dashboard het profiel. Heb je geen extra profielen, dan is dat `default`.

### De schakelaar: Off, Shadow, On

| Stand | Wat er gebeurt |
|---|---|
| **Off** | De receptie doet niets. Alles gaat zoals zonder deze fork. |
| **Shadow** | De receptie vraagt Jev en schrijft op wie het had moeten beantwoorden, maar stuurt niets door. Je receptie beantwoordt alles. Dit is de proefstand. |
| **On** | De receptie stuurt moeilijke berichten echt door. |

Een wijziging werkt vanaf je volgende bericht, zonder herstart.

Onder de schakelaar kan een gele melding staan. Die zegt waarom berichten toch allemaal bij je receptie blijven. Bijvoorbeeld: er is geen Jev-sleutel, geen agent staat aan, of de privacy staat op "Only this machine". De melding zegt ook wat je eraan doet.

Staat in dit profiel ook de gewone Jev-routering (`/jev routing`) aan? Dan wijkt die vanzelf zolang de Front desk op Shadow of On staat. Er beslist maar één tegelijk, en dat is de receptie. Je hoeft niets uit te zetten.

Het vinkje **"Say so when a hard turn was answered here"** laat Hermes één regel toevoegen als een moeilijk bericht toch hier is beantwoord, bijvoorbeeld omdat het privé was of Claude vol zat. Die regel begint met `[dispatch]`. Dit werkt alleen in de stand On.

### Privacy

Per profiel kies je waar moeilijke berichten heen mogen:

| Keuze | Betekenis |
|---|---|
| **Only this machine** | Niets gaat naar buiten, en Jev wordt niets gevraagd. Dit is de standaard, en de veiligste keuze. |
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
| **Model** | Welk model de agent gebruikt. Bij Claude: `opus`, `sonnet` of `haiku`. Leeg betekent het standaardmodel van dat programma. OpenRouter heeft altijd een model nodig. Daar kan ook `typesafe/jev-router`: dan kiest OpenRouter zelf per bericht een model. Lukt dat niet, dan antwoordt je receptie. |
| **Only repository work** | Alleen bij Claude, en standaard aangevinkt: Claude krijgt dan alleen programmeerwerk. Haal het vinkje weg als Claude ook plannen, reviews en ander moeilijk werk mag doen. |
| **Status** | `found` als het programma op deze machine staat, `not found` als het ontbreekt. Bij OpenRouter: `key present`, of het commando om de sleutel te koppelen. `cooling N s` betekent dat de agent even wordt overgeslagen, bijvoorbeeld omdat je limiet op was; **Reset** heft dat op. |
| **Test** | Stuurt één vast testbericht ("Reply with only the word: ok") via die agent, met de **opgeslagen** instellingen. Je ziet of hij antwoordde, met welk model, en het begin van het antwoord. Of je ziet de reden waarom het mislukte. |

### Order

Hier staat wie het eerst wordt gevraagd, apart voor **Repository work** (programmeerwerk) en **Other hard work** (al het andere). Met **Swap** draai je de twee om. OpenRouter komt altijd als laatste, en alleen voor publieke berichten.

Is de eerste agent uit, vol of niet bereikbaar, dan gaat het bericht naar de volgende. Lukt geen enkele, dan antwoordt je receptie. Je chat blijft dus altijd werken.

### Opslaan: Preview, Confirm & save

Wijzigingen bij Privacy, Agents en Order worden niet meteen bewaard:

1. Klik **Preview front desk changes**. Je ziet een lijst van wat verandert.
2. Klik **Confirm & save**. Het dashboard maakt een back-up, schrijft `dispatch.json` en leest het terug ter controle. Je ziet **"Saved and verified"** met het pad van de back-up.

Wil je het toch niet, klik dan **Discard**. De schakelaar en het notice-vinkje worden wel meteen bewaard.

### Checks

| Regel | Moet zijn | Zo niet |
|---|---|---|
| Plugin installed | ✓ | Draai `python3 ~/hermes-jev-skills/install.py`. |
| Enabled in this profile | ✓ | Draai het commando dat erbij staat en herstart de gateway één keer. |
| llm_execution | yes | Je Hermes is te oud voor de receptie. Werk Hermes bij. |
| Settings file | readable, of "not there yet" | Het bestand is kapot. Zet de laatste back-up terug (die staat ernaast) of los het met de hand op. Tot dan staan alle agents uit. |

### Recent front desk decisions

Boven de tabel staat één regel die de berichten in de tabel samenvat. Bijvoorbeeld: "Last 12 turns: Jev called 9 (mean 410 ms) · fail-open 2 · not asked 1 · handed over 3 · answered here 9 (2 warnings)". Daaronder staat welke versie van Jev antwoordde. Is Jev bij geen enkel bericht gevraagd of bereikt, dan wordt die regel een gele melding. Dan beantwoordde je receptie alles, en dat moet je weten.

Daaronder staat per bericht één rij:

| Kolom | Wat het is |
|---|---|
| Time, Profile, Mode | De tijd, het profiel en de stand |
| **Jev** | `called · 412 ms · OpenRouter`: Jev is gevraagd, zo lang duurde het en via die weg. `(features)` betekent dat Jev alleen grove kenmerken las. `fail-open · rate_limited`: Jev antwoordde niet, met de reden. `not called`: Jev is niet gevraagd, de reden staat in de rij |
| **Tier · kind** | Hoe moeilijk Jev het vond (`simple`, `medium`, `hard`) en wat voor werk het is (`coding`, `writing` …) |
| **Answered by** | De agent en het model, of `receptionist` met het chatmodel. In Shadow staat er `would be`: wie het zou hebben beantwoord |
| Privacy | De privacyklasse, en waarom |
| Reason | Waarom het zo ging |
| Attempts | Welke agents geprobeerd zijn, en wat er misging |

Een **gele rij** is een bericht dat je receptie beantwoordde terwijl er iets mis was. Jev antwoordde niet, het profiel houdt alles hier, of een moeilijk bericht bleef hier omdat geen agent het mocht of kon overnemen. Zo'n stil lokaal antwoord mag er niet uitzien alsof alles goed ging.

Zo zie je in de proefstand of de keuzes kloppen.

## 6. De eerste keer instellen

Voor de situatie waarin je lokaal begint, Claude (Max) als eerste hulp neemt en ChatGPT voorlopig niet gebruikt.

Doe eerst dit:

- de gateway is na de installatie één keer herstart;
- `claude` is geïnstalleerd en ingelogd, als **dezelfde gebruiker** die Hermes draait. Log in door `claude` in je eigen terminal te starten; op een machine zonder browser toont het een link die je op een ander apparaat opent.

Dan in het dashboard:

1. **Keys**: klik **Check** bij de sleutel waarmee Jev bereikbaar is. Je moet `ok` zien. Geen sleutel? Plak hem eerst en klik **Save**.
2. **Main model**: kies de receptie, bijvoorbeeld een lokaal model uit **Local (Ollama)** (zie hoofdstuk 3). Herstart daarna de gateway één keer.
3. **Checks**: alles heeft een vinkje.
4. **Privacy**: zet `default` op **May go to an agent**.
5. **Agents**: vink **Use** aan bij Claude Code en kies model `opus`. Haal het vinkje **Only repository work** weg als Claude ook plannen en reviews mag doen. Laat ChatGPT en OpenRouter uit.
6. **Order**: klik **Swap** bij "Other hard work", zodat Claude ook daar eerst staat.
7. Klik **Preview front desk changes** en daarna **Confirm & save**.
8. Klik **Test** bij Claude. Je moet "ok" zien. Zie je een fout, kijk dan bij hoofdstuk 8.
9. Zet de schakelaar op **Shadow**.
10. Chat een dag gewoon verder en kijk af en toe naar **Recent front desk decisions**. Staat bij Jev steeds `called`, en staat er bij moeilijke vragen `would be Claude Code`? Dan werkt de keten.
11. Klopt het? Zet de schakelaar op **On**.

ChatGPT later toevoegen: draai `codex login` in je eigen terminal. Vink daarna ChatGPT aan bij Agents, sla op en test.

## 7. Dagelijks gebruik

- **Een doorgestuurd antwoord herken je** aan de eerste regel, bijvoorbeeld `[claude · opus]`. Daaronder staat het antwoord van die agent, ongewijzigd. Zonder zo'n regel komt het antwoord van je receptie.
- **Een gesprek met Claude loopt door.** Het volgende bericht in dezelfde Hermes-sessie gaat verder in hetzelfde Claude-gesprek.
- **Limiet op?** Dan wordt die agent een tijd overgeslagen: je ziet `cooling` in het dashboard. De volgende agent of je receptie neemt het over. **Reset** probeert het meteen weer.
- **Jev even niet bereikbaar?** Dat merk je niet in de chat: je receptie antwoordt gewoon, zonder foutmelding. In het dashboard staat die rij geel, met `fail-open`.
- **Zelf een model kiezen in één chat?** Kies je in een chat met `/model` een ander model, dan vraagt de receptie in die chat Jev niets meer en stuurt niets door. Het gekozen model antwoordt. Andere chats merken er niets van. Terug: kies het receptiemodel weer, of begin een nieuwe chat.
- **Even niets naar buiten?** Zet de schakelaar op **Off** of **Shadow**. Dat werkt meteen, en je instellingen blijven bewaard.
- **Nooit doorgestuurd** worden:
  - berichten van geplande taken (cron);
  - berichten van subagents;
  - de stappen binnen één beurt, zoals tool-aanroepen. Alleen het begin van je bericht telt.

## 8. Problemen oplossen

| Je ziet | Oorzaak | Oplossing |
|---|---|---|
| Het blok **Front desk** ontbreekt | Je dashboard komt uit een oude installatie | `git -C ~/hermes-jev-skills pull && python3 ~/hermes-jev-skills/install.py`, dan `jev dashboard` opnieuw |
| Jev: `not called`, reden "pinned" | Je koos in die chat een ander model met `/model` | Kies weer het receptiemodel, of begin een nieuwe chat |
| Jev: `fail-open` | Jev antwoordde niet: geen sleutel, geen tegoed, of geen verbinding | **Keys** → **Check**. Die zegt in woorden wat er mis is |
| Elke beslissing is `receptionist`, met reden "highly sensitive" | Het profiel staat op **Only this machine** | **Privacy** op **May go to an agent**, dan Preview, dan Confirm & save |
| `receptionist … cannot hand a turn over yet (codex_responses)` | Je receptie is de ChatGPT-login. Doorgeven vanaf die login kan nog niet | Kies een andere receptie. Doorgeven vanaf de ChatGPT-login komt in een volgende versie |
| Claude staat op `not found` | `claude` is niet geïnstalleerd, of niet te vinden voor deze gebruiker | Installeer Claude Code als de gebruiker die Hermes draait en test opnieuw |
| **Test** geeft een login- of auth-fout | Claude is niet ingelogd voor deze gebruiker | `claude` starten in je eigen terminal en inloggen, dan opnieuw **Test** |
| Claude krijgt alleen programmeervragen | **Only repository work** staat aangevinkt | Vinkje weg, dan Preview, dan Confirm & save |
| Een moeilijke vraag bleef toch lokaal | Er stond een wachtwoord, IBAN of gevoelig woord in, of in de berichten ervoor | Zo bedoeld. De reden staat in de lijst onderaan |
| "Local (Ollama): none found" | Ollama antwoordt niet op deze machine, of heeft geen modellen | Start Ollama, of haal een model op met `ollama pull` |
| **Enabled in this profile** heeft een ✗ | De plugin staat niet aan in `config.yaml` | Draai het commando dat erbij staat en herstart de gateway één keer |
| Settings file "cannot be read" | `dispatch.json` is met de hand kapotgemaakt | Zet de back-up ernaast terug. Tot dan staan alle agents veilig uit |

## 9. Liever via de chat?

Het dashboard en deze commando's schrijven dezelfde bestanden. Je mag ze door elkaar gebruiken:

| Commando in Hermes | Doet |
|---|---|
| `/dispatch` | Toont de stand, de privacyklasse en de agents |
| `/dispatch shadow`, `/dispatch on`, `/dispatch off` | Zelfde als de schakelaar |
| `/dispatch notice on`, `/dispatch notice off` | Zelfde als het notice-vinkje |

In een terminal toont `jev dispatch check` welke agents deze machine kan bereiken.
