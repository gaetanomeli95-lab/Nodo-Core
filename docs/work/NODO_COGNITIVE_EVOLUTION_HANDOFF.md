# NODO Core — Cognitive Evolution: passaggio di consegne

Aggiornato: 19 ottobre 2026, Europe/Rome. Sessione 02 (Devin): **S02 completata**.

## Obiettivo e limiti della sessione

NODO è un assistente personale con interazione principalmente vocale, su Windows 11. Evolvere velocità, memoria persistente/associativa, recupero pertinente, scelta di strumenti/modelli, apprendimento dalle correzioni, continuità tra sessioni e visualizzazione di attività reali. Riutilizzare FastAPI, SQLAlchemy/SQLite, React/Vite, VoiceRuntime, Brain, Toolbox, ModelRouter e MindGraph.

Una micro-attività per sessione; riservare tempo a test, commit e push. Nessun nuovo modello neurale, GRU, dipendenza pesante o riscrittura senza necessità e confronto misurato. Nessun agente parallelo necessario in questa sessione. Non avviare fasi successive automaticamente.

**Risultato di questa sessione:** audit circoscritto del percorso richiesta e della telemetria, una lacuna riprodotta e questo documento. Nessun codice applicativo modificato, nessun miglioramento di velocità dichiarato. La baseline qui è strutturale e diagnostica: mancano tempi reali prima/dopo sul PC.

## Repository e checkpoint

- Repository verificato: `gaetanomeli95-lab/Nodo-Core`, ID GitHub `1404795275`; accesso lettura/scrittura confermato.
- Riferimento controllato: `integration/nodo-core-v01`.
- Commit di partenza: `3683065fc55f68b54f44a882aa5f9400133cd94a`, del 17 ottobre 2026, «fix(voce+provider): il diario ignora il rumore straniero e le chiavi mal incollate non passano piu'».
- Branch creato da quel commit: **`work/nodo-cognitive-evolution-v01`**. Tutto il lavoro di questa iniziativa deve restare qui.
- La precedente [PR #8](https://github.com/gaetanomeli95-lab/Nodo-Core/pull/8) risulta integrata; il suo commit `5cb85ccf0df62191cd898d75fb21e2419d98bb6f` è antenato della base verificata. Non riapplicare la patch né riutilizzare i vecchi risultati di test come prova dell'attuale versione.
- Non sono stati modificati `main` o `integration/nodo-core-v01`; nessun merge, release, deploy o force push.
- File modificato in questa sessione: soltanto `docs/work/NODO_COGNITIVE_EVOLUTION_HANDOFF.md`.
- Ultimo commit pubblicato e ricevuta del push: vedere il registro checkpoint in fondo. Per lo SHA della revisione che contiene questo documento usare `git log -1 --format=%H -- docs/work/NODO_COGNITIVE_EVOLUTION_HANDOFF.md`; non tentare di inserire in un commit il suo stesso hash.

## Architettura osservata e percorso effettivo

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

1. **CORRETTO in S02 (Devin, 2026-10-19):** `VoiceSession.begin_turn()` azzerava `stamps`, perdeva `speech_end`/`speech_started`/`transcript_partial_first` raccolti nella stessa acquisizione, e `mark()` con `setdefault` impediva la rimarcatura nel turno successivo. Ora `begin_turn` conserva i timbri più recenti dell'ultimo `turn_done`, `mark_acquisition()` sovrascrive i timbri pre-turno, e `discard_acquisition_stamps()` pulisce i percorsi che abortiscono senza turno (audio troppo corto, STT fallita/vuota, rumore). Verificato su DB reale: i turni precedenti mostravano solo `model_total`/`tts_total`/`total` — `stt`, `context`, `model_ttft`, `tts_tfa` mancavano in ogni riga di `voice_turn_metrics`.
2. **Osservato nel codice:** `_live_finalize_input` emette `transcript.final` senza marcare `transcript_final`; la copertura delle misure Live è diversa da quella non-Live. La prima trascrizione parziale Live non è necessariamente l'inizio fisico del parlato. Correggere questo punto in una sessione separata dalla prossima.
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
| Fase 2 | Non iniziata | Recupero associativo: riusare memoria e ricerca esistenti; misurare prima di scegliere nuove tecnologie. |
| Fase 3 | Non iniziata | Evoluzione del router e prevenzione ricerche ripetitive. |
| Fase 4 | Non iniziata | Valutare le lezioni già presenti e trasformare correzioni in regressioni misurabili. |
| Fase 5 | Non iniziata | Collegare recuperi identificabili a MindGraph; mantenere distinta attività reale da corrispondenza grafica di parole. |

**S03, confine preciso:** leggere solo `_live_finalize_input` e i marchi di timestamp nel percorso Live in `voice/runtime.py`, `voice/live.py` e i relativi test (`test_voice_live.py`). Coprire `transcript_final`/fine-parlato dove l'evento reale è disponibile; non inventare un `speech_end` fittizio. Verificare le metriche Live su due turni e un'interruzione; niente nuove colonne, motori, routing, cache o interfaccia. Fermarsi dopo test, HANDOFF, commit e push.

## Verifiche effettivamente eseguite in S01

- Accesso GitHub, branch/base, checkout pulito e presenza della PR #8 tra gli antenati: verificati.
- **Tre verifiche diagnostiche Python riuscite**, senza rete o modelli: formula con timestamp noti; riproduzione dell'azzeramento; omissione degli stadi non misurati. I valori sintetici non sono tempi del prodotto.
- Nessuna suite pytest completa, build frontend, prova audio o test Windows eseguiti in S01: modifica solo documentale. Non presentare i 344 test della precedente sessione come risultati attuali.
- Verifiche del documento e pubblicazione: riportate nel registro checkpoint.

Riproduzione eseguita dalla radice repository (Bash, Python 3.12+):

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
5. Nell'ambiente Python esistente, dalla cartella `apps/api`, comandi di verifica previsti per S02 (non eseguiti in S01):

```text
python -m pytest -q tests/test_voice_ws.py tests/test_responsiveness.py
python -m ruff check nodo/voice/base.py nodo/voice/runtime.py tests/test_voice_ws.py tests/test_responsiveness.py
```

Se manca l'ambiente, creare una venv Python 3.12 e installare `python -m pip install -e ".[dev]"` da `apps/api`; non installare dipendenze audio locali pesanti per test con provider fake. Eseguire dalla venv, senza modificare configurazioni o chiavi dell'utente. Per regressioni Live aggiungere i test Live solo quando quel percorso viene modificato.

6. Aggiornare test/esiti, file, decisione e prossima singola attività in questo HANDOFF; `git diff --check`; aggiungere solo file previsti, mai `.env`, database o log personali.
7. Commit descrittivo e `git push origin HEAD:refs/heads/work/nodo-cognitive-evolution-v01`. Verificare lo SHA remoto con `git ls-remote`. Se il push è respinto, ispezionare le modifiche remote: nessun force push, nessuna falsa conferma.

## Registro checkpoint e CHANGELOG

- 2026-10-19, S01: verificato repository e commit `3683065fc55f68b54f44a882aa5f9400133cd94a`; creato il branch sperimentale dalla base senza modificare altri branch.
- 2026-10-19, S01: audit dei percorsi avvio/voce/Core/Brain, censimento telemetria esistente, tre verifiche diagnostiche riuscite. Scelta S02: correzione limitata del timestamp non-Live, non una nuova infrastruttura di metriche.
- Checkpoint audit: documento pubblicato come `0ef09a5ed82645afb2a21d2dd609df5995584cd2` su `work/nodo-cognitive-evolution-v01`.
- 2026-10-19, S02 (Devin): correzione del ciclo di vita dei timestamp in `nodo/voice/base.py` e `nodo/voice/runtime.py`. `begin_turn` ora conserva i timbri registrati dopo l'ultimo `turn_done` (stessa acquisizione), `mark_acquisition` sovrascrive i timbri pre-turno residui del turno precedente, `discard_acquisition_stamps` pulisce le acquisizioni abortite (audio breve, STT fallita/vuota, rumore). File modificati: `nodo/voice/base.py`, `nodo/voice/runtime.py`, `tests/test_voice_ws.py` (4 test nuovi: carry-over, scarto, span STT su WS, due turni consecutivi). Test eseguiti: `pytest -q tests/test_voice_ws.py tests/test_responsiveness.py` → 40/40 passati; `ruff check` sui file modificati → pulito. Evidenza dal DB reale: `voice_turn_metrics` non conteneva `stt`/`context`/`model_ttft`/`tts_tfa` in nessuna riga; turni con `total` 17–52 s contro `model_total` ~5 s. Prossima singola attività: S03 (timestamp nel percorso Live).
