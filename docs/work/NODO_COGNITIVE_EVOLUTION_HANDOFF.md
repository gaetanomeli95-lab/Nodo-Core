# NODO Core — Cognitive Evolution: passaggio di consegne

Aggiornato: 20 ottobre 2026, Europe/Rome. **S02 e P4.5 già pubblicate da Devin; S03 resta la prossima singola attività.** Questa ripresa chiude soltanto il checkpoint documentale della Fase 0.

## Obiettivo e limiti della sessione

NODO è un assistente personale con interazione principalmente vocale, su Windows 11. Evolvere velocità, memoria persistente/associativa, recupero pertinente, scelta di strumenti/modelli, apprendimento dalle correzioni, continuità tra sessioni e visualizzazione di attività reali. Riutilizzare FastAPI, SQLAlchemy/SQLite, React/Vite, VoiceRuntime, Brain, Toolbox, ModelRouter e MindGraph.

Una micro-attività per sessione; riservare tempo a test, commit e push. Nessun nuovo modello neurale, GRU, dipendenza pesante o riscrittura senza necessità e confronto misurato. Nessun agente parallelo necessario in questa sessione. Non avviare fasi successive automaticamente.

**Risultato di S01 (19 ottobre):** audit circoscritto del percorso richiesta e della telemetria, una lacuna riprodotta e questo documento. Nessun codice applicativo modificato, nessun miglioramento di velocità dichiarato. La baseline qui è strutturale e diagnostica: mancano tempi reali prima/dopo sul PC.

## Repository e checkpoint

- Repository verificato: `gaetanomeli95-lab/Nodo-Core`, ID GitHub `1404795275`; accesso lettura/scrittura confermato.
- Riferimento controllato: `integration/nodo-core-v01`.
- Commit di partenza: `3683065fc55f68b54f44a882aa5f9400133cd94a`, del 17 ottobre 2026, «fix(voce+provider): il diario ignora il rumore straniero e le chiavi mal incollate non passano piu'».
- Branch creato da quel commit: **`work/nodo-cognitive-evolution-v01`**. Tutto il lavoro di questa iniziativa deve restare qui.
- La precedente [PR #8](https://github.com/gaetanomeli95-lab/Nodo-Core/pull/8) risulta integrata; il suo commit `5cb85ccf0df62191cd898d75fb21e2419d98bb6f` è antenato della base verificata. Non riapplicare la patch né riutilizzare i vecchi risultati di test come prova dell'attuale versione.
- Non sono stati modificati `main` o `integration/nodo-core-v01`; nessun merge, release, deploy o force push.
- File modificato in S01 e nella ripresa documentale del 20 ottobre: soltanto `docs/work/NODO_COGNITIVE_EVOLUTION_HANDOFF.md`.
- Ultimo commit pubblicato e ricevuta del push: vedere il registro checkpoint in fondo. Per lo SHA della revisione che contiene questo documento usare `git log -1 --format=%H -- docs/work/NODO_COGNITIVE_EVOLUTION_HANDOFF.md`; non tentare di inserire in un commit il suo stesso hash.

## Architettura osservata e percorso effettivo

La mappa seguente descrive la base S01 (`3683065`). Le evoluzioni S02, P1–P4 e P4.5 sono registrate sotto: riutilizzarle, senza ripetere l'implementazione.

| Passaggio | Codice pertinente | Comportamento attuale da riutilizzare |
| --- | --- | --- |
| Avvio Windows | `avvia-nodo.bat`, `scripts/avvia-nodo.ps1`, `apps/api/nodo/launch.py` | Prima prova a riaprire un server sano con impronta identica di sorgenti/config/build. Altrimenti prepara ambiente, migrazioni, eventuale build e avvia NODO tramite tray/server. |
| Preparazione API | `apps/api/nodo/main.py:create_app/lifespan` | Costruisce il container e l'impronta, prepara schema e dati; applica correzioni del registro nelle condizioni previste; avvia scheduler/Telegram se abilitati. Espone `/health` e frontend statico. |
| Connessione voce | `apps/api/nodo/api/voice_ws.py`, `apps/web/src/components/Voice.tsx` | WS `/api/v1/voice/stream`, messaggi tipizzati, runtime per sessione, invio degli eventi tramite coda. |
| Preparazione Live | `apps/api/nodo/voice/runtime.py:_on_start/_prewarm/_start_live` | La versione attuale preriscalda Live all'apertura. `_live_context` prepara memoria, cronologia e lavagna tramite `asyncio.to_thread`; il collegamento al provider segue. Non descriverla ancora come connessione soltanto pigra. |
| Voce Live | `runtime.py:_on_live_event_inner/_live_tool_calls/_live_think` | Riceve audio/trascrizioni e richieste di strumenti. `pensa` entra in Brain con richiesta diretta; altri strumenti passano nel Toolbox. Contesto della lavagna versionato, aggiornamenti periodici e dopo operazioni sul registro. |
| Voce non-Live | `runtime.py:_on_speech_end/_on_final/_begin_turn/run_turn/_pipeline`, `apps/api/nodo/voice/providers.py` | Trascrizione del provider o del browser → Core → frasi nella coda di sintesi → audio/browser. Esistono già provider locali Whisper/Piper opzionali e percorso API: non ricrearli. |
| Richiesta testuale/Core | `apps/api/nodo/core/orchestrator.py:handle` | Registra richiesta/conversazione, classifica intento, usa Brain quando abilitato o per letture esatte previste; mantiene fallback deterministico e guardie. |
| Ragionamento | `apps/api/nodo/brain/loop.py:run`, `quick_read.py`, `tools.py` | Prima azioni dirette/letture riconosciute con Toolbox; poi snapshot, eventuale ricerca preventiva, cronologia, selezione strumenti e ciclo di chiamate al modello. |
| Routing | `apps/api/nodo/providers/router.py:complete_tools` | Filtra e ordina candidati, verifica disponibilità, applica fallback e cooldown. Nei percorsi `prefer_fast` limita l'attesa del tentativo. |
| Memoria | `apps/api/nodo/memory/mind.py`, `brain/loop.py:snapshot` | Fatti persistenti, fonte/validità, ranking fuzzy, regole e lezioni, diario e archivista differito. Limiti di caratteri e recupero selettivo già presenti. |
| Interfaccia cognitiva | `apps/web/src/components/holo/MindGraph.tsx` | Usa stato, livelli audio, testo ascoltato/pronunciato e attività. Alcuni nodi si accendono per corrispondenza di parole: questo non dimostra che quel fatto sia stato recuperato dal cervello. |

Gli aggiornamenti successivi alla PR #8 includono lavagna viva, ricerca preventiva sui ricordi, lezioni di memoria, provider NVIDIA, riparazione dei modelli ritirati, gestione dell'eco e validazione delle chiavi. Sono stati identificati dalla cronologia e dai punti di integrazione pertinenti, non certificati integralmente sul campo da questa sessione.

## Misure già esistenti

| Misura | Dove | Significato e limite |
| --- | --- | --- |
| `TURN_STAMPS` e `VoiceSession.latency_ms()` | `apps/api/nodo/voice/base.py` | `stt`, `context`, `model_ttft`, `model_total`, `tts_tfa`, `tts_total`, `total`. Gli stadi privi di timestamp sono omessi. Non creare una seconda tabella per le stesse misure. |
| Persistenza del turno | `runtime.py:_persist_metrics`, `VoiceTurnMetric` in `apps/api/nodo/db/models.py` | Tabella `voice_turn_metrics`, sessione/turno/richiesta e timestamp. Evento `voice.turn.done` con metriche. La completezza dipende dal percorso effettivamente attraversato. |
| Tentativi di modello | `providers/router.py:_record`, `apps/api/nodo/core/telemetry.py` | `InferenceRun`: provider/modello, latenza, token, stato/errore, costo stimato e request ID. Il timer parte dopo `provider.available()`: la disponibilità non è inclusa nella durata registrata. |
| Preparazione Live | `runtime.py:_start_live` | Log `live ready ... context ... ms, connection ... ms`, già separato in preparazione e collegamento. Non misura tutto l'avvio Windows. |
| Durata di `pensa` | `runtime.py:_live_think` | Log `pensa finished request=... elapsed_ms=... provider=... model=... tools=...`; durata del cervello, non dell'ascolto fisico della risposta. |
| Diagnosi locale | `apps/api/nodo/voice/diagnostics.py` | Versioni, commit, stato build e conteggi di errori senza esportare righe di log/conversazioni/chiavi. Non è un benchmark di latenza. |

`tts_first_audio` indica un evento lato server; non equivale al primo campione effettivamente riprodotto dagli altoparlanti. `tts_tfa` è attualmente calcolato da `transcript_final`, mentre `total` usa `speech_end` quando presente. Tenere queste definizioni esplicite nei confronti.

## Lacune e ipotesi da distinguere

1. **CORRETTO in S02 (Devin, 2026-10-19):** `VoiceSession.begin_turn()` azzerava `stamps`, perdeva `speech_end`/`speech_started`/`transcript_partial_first` raccolti nella stessa acquisizione, e `mark()` con `setdefault` impediva la rimarcatura nel turno successivo. Ora `begin_turn` conserva i timbri più recenti dell'ultimo `turn_done`, `mark_acquisition()` sovrascrive i timbri pre-turno, e `discard_acquisition_stamps()` pulisce i percorsi che abortiscono senza turno (audio troppo corto, STT fallita/vuota, rumore). Secondo la verifica sul DB reale riportata da Devin (non ripetuta in questa ripresa): i turni precedenti mostravano solo `model_total`/`tts_total`/`total` — `stt`, `context`, `model_ttft`, `tts_tfa` mancavano in ogni riga di `voice_turn_metrics`.
2. **Osservato nel codice:** `_live_finalize_input` emette `transcript.final` senza marcare `transcript_final`; la copertura delle misure Live è diversa da quella non-Live. La prima trascrizione parziale Live non è necessariamente l'inizio fisico del parlato. Confermato al commit `aec24fe`: è il punto di partenza della prossima attività S03. Gli eventi `audio`, `output`, `tool_call` e fine turno non sono una misura fisica di fine parlato; controllarne l'ordine prima di calcolare una latenza.
3. **Da misurare:** costo delle scansioni memoria e della preparazione del contesto. I limiti di caratteri riducono l'input al modello, non garantiscono meno letture dal database.
4. **Da misurare:** le richieste di ricordo riconosciute eseguono già `search_knowledge` e `search_conversations` prima del modello; se non trovano risultati invitano a cercare ancora. Non introdurre subito un'altra cache: prima contare le chiamate e verificare query, scadenza e invalidazione.
5. **Da misurare:** fallback seriali, `provider.available()` e CLI possono dominare l'attesa. I provider in cooldown vengono spostati in fondo, non sempre esclusi. `_live_think` ha nuovamente un margine di 25 secondi oltre `brain_deadline_s`; non ridurlo alla cieca, perché serve anche alla risposta finale e ai lavori lunghi.
6. **Non verificato sul PC:** avvio freddo/caldo, qualità voce, eco, rumore del negozio, durata sessione e quota reale. Niente percentuali o promesse di risposta in un numero fisso di secondi.

## Metriche da raccogliere quando la strumentazione è corretta

- Avvio freddo: lancio bat → `/health` pronto; separatamente finestra pronta, Live connesso e primo audio. Avvio caldo con server già sano va misurato a parte.
- Risposta: fine parlato → trascrizione finale → contesto → modello/strumenti → primo audio ricevuto; riproduzione reale richiede misura frontend/acustica distinta.
- Per turno: numero di chiamate modello, tentativi falliti, ricerche e strumenti; motore vocale e provider effettivo, interruzioni e motivo fallback.
- Contesto: dimensione selezionata e fatti pertinenti recuperati; correttezza di regole/conti e freschezza dopo una modifica del registro.
- Confrontare stessi input/dati/configurazione/hardware, almeno 20 campioni per percorso quando fattibile; riportare numerosità, mediana, p95, errori e metriche mancanti. Sono criteri proposti, non misure già raccolte.

## Micro-attività e prossima sessione

| ID | Stato | Risultato |
| --- | --- | --- |
| S01 / Fase 0 | Completata in questo documento | Base remota verificata, percorso e telemetria censiti, lacuna timestamp riprodotta, checkpoint documentale. |
| S02 / Fase 1 | **Completata (Devin, 2026-10-19)** | `begin_turn` conserva i timbri successivi all'ultimo `turn_done`; `mark_acquisition` sovrascrive i timbri pre-turno; `discard_acquisition_stamps` evita eredità da acquisizioni abortite. 4 test nuovi, 40/40 verdi in `test_voice_ws.py`+`test_responsiveness.py`. |
| S03 / Fase 1 | **Prossima singola attività** | Chiarire/coprirne i timestamp nel percorso Live senza inventare fine parlato non disponibile. |
| P3 / Agenda-3 (Devin, 2026-10-19) | **Completata** | `nodo/memory/routines.py`: mining firma+fascia oraria → fatti `routine` (source `agent`, supersede ogni passata, refresh in-place). `anticipations()` in `memory_context` come sezione "probabili bisogni, non certezze"; greeting anticipa la richiesta abituale; re-mining orario in housekeeping. 6 test verdi. Commit `062c3c6`. |
| P1 / Agenda-1 (Devin, 2026-10-19) | **Completata** | `nodo/social/`: bozza `ContentItem`+`Approval` livello 4 → con il sì `social.publish` esporta il pacchetto (caption/meta/ISTRUZIONI) in `files/social/`; `publish_remote` è il seam per token reali (`NODO_SOCIAL_*` in config). Tool `social_draft`/`social_queue`; bozza notturna una-al-giorno se c'è un task social aperto. Niente pubblica da solo. 5 test verdi. Commit `4b36662`. |
| P2 / Agenda-2 (Devin, 2026-10-19) | **Completata** | `/api/v1/activity` (feed EventLog+VoiceTurnMetric); MindGraph lo poll-a: hub toccati si accendono, il core emette un'onda d'urto, il log racconta. Polling saltato a pagina nascosta, rallentato in `lite`. `agent.work` emesso anche a inizio tool. Build web verde. Commit `8ad8f6e`. |
| P4 / Agenda-4 (Devin, 2026-10-19) | **Completata** | `docs/ARCHITECTURE_STUDY.md` (mappa onesta + strada proprietaria). Router: `_LAT`/`_ATTEMPTS` a livello modulo (core per-request) → EWMA latenza e affidabilità misurate pesano in `_score`; `router_stats()` in `/api/v1/system`. X.2: `_foreign_reply` nel brain loop → risposte inglesi/spagnole/CJK ripetute in italiano. 2 test nuovi. Commit `4e78916`. |
| Fase 2 | Parzialmente (P3) | Routines = prima pietra associativa predittiva; resta il recupero semantico. |
| P4.5 / Agenda-4 | **Completata (Devin)** | Intent → requisiti di `RouteRequest` e purpose per-intent. Commit `aec24fe95401d58d4f7886dd7e1079f1087c4d8b`, verificato sul remoto il 20 ottobre. |
| Fase 3 | Parzialmente (P4 + P4.5) | Pesi da latenza/esiti e routing per intento presenti; efficacia e copertura dei percorsi da misurare. Non ripetere l'implementazione intent → RouteRequest. |
| Fase 4 | Non iniziata | Valutare le lezioni già presenti e trasformare correzioni in regressioni misurabili. |
| Fase 5 | Parzialmente (P2) | Feed attività collegato a MindGraph; mancano archi tra ricordi richiamati e nodi specifici. |

**S03, confine preciso:** leggere solo `_live_finalize_input` e i marchi di timestamp nel percorso Live in `voice/runtime.py`, `voice/live.py` e i relativi test (`test_voice_live.py`). Coprire `transcript_final`/fine-parlato dove l'evento reale è disponibile; non inventare un `speech_end` fittizio. Verificare le metriche Live su due turni, un'interruzione e l'ordine audio/trascrizione: non retrodatare eventi o attribuire al turno successivo i timbri precedenti. Se manca una coppia di eventi attendibili, lasciare la misura assente e documentarne il motivo; niente nuove colonne, motori, routing, cache o interfaccia. Fermarsi dopo test, HANDOFF, commit e push.

## Verifiche effettivamente eseguite in S01

- Accesso GitHub, branch/base, checkout pulito e presenza della PR #8 tra gli antenati: verificati.
- **Tre verifiche diagnostiche Python riuscite**, senza rete o modelli: formula con timestamp noti; riproduzione dell'azzeramento; omissione degli stadi non misurati. I valori sintetici non sono tempi del prodotto.
- Nessuna suite pytest completa, build frontend, prova audio o test Windows eseguiti in S01: modifica solo documentale. Non presentare i 344 test della precedente sessione come risultati attuali.
- Verifiche del documento e pubblicazione: riportate nel registro checkpoint.

Riproduzione storica eseguita alla base `3683065fc55f68b54f44a882aa5f9400133cd94a` dalla radice repository (Bash, Python 3.12+). Non è il test di accettazione della versione attuale: S02 ha modificato intenzionalmente `begin_turn()`. Per le regressioni usare pytest come indicato sotto:

```bash
PYTHONPATH=apps/api python3 - <<'PY'
from nodo.voice.base import VoiceSession
s = VoiceSession()
s.stamps = {
    'turn_started': '2026-10-18T20:00:00+00:00',
    'speech_end': '2026-10-18T20:00:01+00:00',
    'transcript_final': '2026-10-18T20:00:01.200000+00:00',
    'tts_first_audio': '2026-10-18T20:00:01.800000+00:00',
    'turn_done': '2026-10-18T20:00:03+00:00',
}
assert s.latency_ms() == {'stt': 200, 'tts_tfa': 600, 'total': 2000}
s.begin_turn()
assert set(s.stamps) == {'turn_started'} and 'stt' not in s.latency_ms()
s.stamps = {'turn_started': '2026-10-18T20:00:00+00:00',
            'tts_first_audio': '2026-10-18T20:00:01+00:00',
            'turn_done': '2026-10-18T20:00:02+00:00'}
assert s.latency_ms() == {'total': 2000}
print('3 verifiche diagnostiche passate; azzeramento riprodotto, non corretto')
PY
```

## Come riprendere con Devin, Claude o Codex

1. Leggere questo documento. Prima di cambiare branch controllare `git status --short`; conservare modifiche di altri senza reset/stash automatici o sovrascritture.
2. `git fetch origin work/nodo-cognitive-evolution-v01` e `git switch work/nodo-cognitive-evolution-v01` (oppure creare il checkout da `origin/work/nodo-cognitive-evolution-v01` se assente). Usare `git pull --ff-only`; se diverge, ispezionare e non forzare.
3. Controllare `git rev-parse HEAD` e `git ls-remote origin refs/heads/work/nodo-cognitive-evolution-v01`. Se la base di integrazione è avanzata, annotarlo; non unirla automaticamente a questa micro-attività.
4. Eseguire **soltanto S03**, mantenendo guardie di approvazione, registro, memoria, voce e Night Shift.
5. Nell'ambiente Python esistente, dalla cartella `apps/api`, comandi di verifica previsti per **S03** (non eseguiti nella ripresa documentale del 20 ottobre):

```text
python -m pytest -q tests/test_voice_live.py tests/test_voice_ws.py
python -m ruff check nodo/voice/runtime.py nodo/voice/live.py tests/test_voice_live.py
```

Se manca l'ambiente, creare una venv Python 3.12 e installare `python -m pip install -e ".[dev]"` da `apps/api`; non installare dipendenze audio locali pesanti per test con provider fake. Eseguire dalla venv, senza modificare configurazioni o chiavi dell'utente. I test non-Live controllano le regressioni del runtime condiviso. Se S03 richiede anche una modifica a `base.py`, includerlo nel controllo Ruff; mantenere il perimetro limitato ai timestamp.

6. Aggiornare test/esiti, file, decisione e prossima singola attività in questo HANDOFF; `git diff --check`; aggiungere solo file previsti, mai `.env`, database o log personali.
7. Commit descrittivo e `git push origin HEAD:refs/heads/work/nodo-cognitive-evolution-v01`. Verificare lo SHA remoto con `git ls-remote`. Se il push è respinto, ispezionare le modifiche remote: nessun force push, nessuna falsa conferma.

## Registro checkpoint e CHANGELOG

- 2026-10-19, S01: verificato repository e commit `3683065fc55f68b54f44a882aa5f9400133cd94a`; creato il branch sperimentale dalla base senza modificare altri branch.
- 2026-10-19, S01: audit dei percorsi avvio/voce/Core/Brain, censimento telemetria esistente, tre verifiche diagnostiche riuscite. Scelta S02: correzione limitata del timestamp non-Live, non una nuova infrastruttura di metriche.
- Checkpoint audit: documento pubblicato come `0ef09a5ed82645afb2a21d2dd609df5995584cd2` su `work/nodo-cognitive-evolution-v01`.
- 2026-10-19, S02 (Devin): correzione del ciclo di vita dei timestamp in `nodo/voice/base.py` e `nodo/voice/runtime.py`. `begin_turn` ora conserva i timbri registrati dopo l'ultimo `turn_done` (stessa acquisizione), `mark_acquisition` sovrascrive i timbri pre-turno residui del turno precedente, `discard_acquisition_stamps` pulisce le acquisizioni abortite (audio breve, STT fallita/vuota, rumore). File modificati: `nodo/voice/base.py`, `nodo/voice/runtime.py`, `tests/test_voice_ws.py` (4 test nuovi: carry-over, scarto, span STT su WS, due turni consecutivi). Test eseguiti: `pytest -q tests/test_voice_ws.py tests/test_responsiveness.py` → 40/40 passati; `ruff check` sui file modificati → pulito. Evidenza dal DB reale: `voice_turn_metrics` non conteneva `stt`/`context`/`model_ttft`/`tts_tfa` in nessuna riga; turni con `total` 17–52 s contro `model_total` ~5 s. Prossima singola attività: S03 (timestamp nel percorso Live).
- Checkpoint S02: pubblicato come `4fd7b73276441d7bf19ac0e80a62d443cd443c66` su `work/nodo-cognitive-evolution-v01` (verificato con `git ls-remote`). Lavoro eseguito in git worktree separato; il checkout principale su `integration/nodo-core-v01` non è stato toccato.
- 2026-10-19, sessione estesa Devin (su richiesta esplicita del proprietario, oltre il protocollo una-micro-attività): implementati i 4 punti-agenda dettati a Maria il 18/10. Commit pubblicati e verificati via `ls-remote` su `work/nodo-cognitive-evolution-v01`: `062c3c6` (P3 routines), `4b36662` (P1 social), `8ad8f6e` (P2 living interface), `4e78916` (P4 router+studio+X.2 lingua). Test: `test_routines` 6, `test_social` 5, `test_activity` 2, `test_router` 12 (+2 nuovi), suite spot `test_mind`+`test_night` 33, `test_brain`+`test_brain_extra`+`test_api` 31 — tutti verdi; ruff pulito; `npm run build` web verde. File nuovi: `nodo/memory/routines.py`, `nodo/social/{__init__,service,night}.py`, `nodo/api/activity.py`, `docs/ARCHITECTURE_STUDY.md`, `tests/{test_routines,test_social,test_activity}.py`. Modificati: `mind.py`, `scheduler.py`, `greeting.py`, `briefing.py`, `approvals.py`, `extra.py`, `tools.py`, `router.py`, `loop.py`, `command.py`, `config.py`, `main.py`, `MindGraph.tsx`, `test_router.py`. Nota tecnica: su questa macchina il file editor ha mostrato due volte disallineamenti lettura/disco (CRLF) — verificare sempre con `grep`/`python` prima di affidarsi a `read` per file modificati di recente.



### P4.5 — Router per-intent (completamento P4)

Commit `aec24fe95401d58d4f7886dd7e1079f1087c4d8b`; implementazione verificata nel diff remoto il 20 ottobre. I risultati di test seguenti sono quelli riportati da Devin, non nuove esecuzioni di questa ripresa.

- `nodo/providers/router.py`: tabella `INTENT_ROUTES` + `intent_route(intent)`. Ogni intent riconosciuto
  dichiara cosa serve davvero al modello: intent dati (`today`, `night_report`, `pending_approvals`,
  `sites_status`, `run_checks`, `list_projects`) e decisioni nude (`approve`/`reject`/`stop`) →
  `prefer_fast`; intent analitici (`project_status`, `compare` → reasoning 4, `tech_status` →
  reasoning 3 + coding 2, `prepare_content` → reasoning 3). Intent sconosciuti → nessuna modifica.
- `nodo/core/orchestrator.py`: l'intent classificato entra nel brain turn e nel percorso regole;
  `RouteRequest.purpose` diventa `brain:<intent>` / `synthesis:<intent>` → telemetria per-intent
  gratuita in `inference_runs` (colonna 50 char, verificato).
- `nodo/brain/loop.py`: `Brain(..., intent_name=...)`; il requisito applica il massimo tra il pavimento
  del chiamante (reasoning 3) e quello dell'intent — un intent non abbassa mai sotto il minimo del
  chiamante. Voce auto continua a forzare `prefer_fast` come prima.
- Test: `tests/test_router.py` +3 (shaping per intent, intent ignoto neutro, purpose entro colonna).
- Verifiche: router 15/15, brain 11/11, core 15/15, responsiveness 24/24, ruff pulito.

### 2026-10-20 — Chiusura del checkpoint documentale (Codex)

- Base di questa ripresa: remoto `work/nodo-cognitive-evolution-v01` a `aec24fe95401d58d4f7886dd7e1079f1087c4d8b` (P4.5). `integration/nodo-core-v01` resta a `3683065fc55f68b54f44a882aa5f9400133cd94a` al controllo iniziale; nessuna modifica richiesta a quel branch.
- Verificati con `git ls-remote` e lettura GitHub: HEAD di lavoro e documento; letti il diff P4.5, `VoiceSession.begin_turn/mark_acquisition/discard_acquisition_stamps` e i punti pertinenti del runtime Live. Presenza delle modifiche confermata; prestazioni reali non misurate.
- Unico file modificato: questo HANDOFF. Corretti i riferimenti alla sessione, registrato P4.5 nella roadmap, aggiornati i comandi per S03 e distinta l'evidenza storica dai controlli attuali. Nessuna modifica al codice applicativo.
- Test di prodotto: nessuna nuova esecuzione pytest, build, prova Windows o audio. I conteggi S02/P1–P4/P4.5 e i dati del PC sono risultati riportati da Devin; non presentarli come verifica indipendente del 20 ottobre.
- Verifiche documentali superate: sezioni obbligatorie, delimitatori Markdown, assenza di spazi finali, conservazione dei checkpoint precedenti, corrispondenza SHA/blob remoto e presenza dei quattro percorsi per i prossimi test. `git diff --no-index --check` senza diagnostica; il codice 1 indica differenze tra i documenti, non errori di spaziatura.
- Stato del salvataggio: `aec24fe95401d58d4f7886dd7e1079f1087c4d8b` è l'ultimo checkpoint applicativo verificato prima di questa revisione. Pubblicare questa sola revisione sul medesimo branch e verificare SHA remoto/contenuto; il rapporto finale della sessione riporta lo SHA risultante. Per risalire alla revisione documentale usare il comando indicato nella sezione Repository.
- Prossima singola attività: **S03, timestamp Live**, nel confine definito sopra. Non iniziata in questa ripresa.
- Rischio aperto: l'ordine dei messaggi Live può rendere la finalizzazione della trascrizione successiva al primo audio; aggiungere soltanto un timestamp non basta a garantire una misura valida. Servono test con eventi espliciti, senza inventare la fine del parlato.
- Continuità locale: il checkout della precedente S01 può divergere dal remoto perché il commit documentale locale e quello pubblicato hanno SHA diversi. Non forzare il push o azzerare modifiche: prima ispezionare la divergenza, poi usare un checkout pulito del branch remoto preservando il lavoro esistente.
