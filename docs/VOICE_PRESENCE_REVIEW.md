# NODO Core — stabilità vocale e presenza

Verifica del 11 ottobre 2026. Ramo `work/nodo-voice-presence-v02`, integrato con il lavoro di Devin fino a `0d4861f`. Questo documento distingue correzioni implementate, ipotesi da provare sul PC e sviluppo successivo. Non sono state usate chiavi reali né effettuati deploy dei siti.

## A. Stabilità della sessione Live

### Diagnosi principale, verificata nel codice

In `google-genai 2.29.0`, `AsyncSession.receive()` termina **alla fine di un turno**. NODO lo chiamava una sola volta e interpretava l'uscita come disconnessione. La stringa «sessione terminata», senza un errore del provider, poteva quindi essere prodotta da NODO dopo una risposta normale. Non è una prova che la quota fosse quasi esaurita.

La correzione conserva la connessione e riapre l'iteratore del turno:

```python
while not self._dead:
    async for message in self._session.receive():
        # acquisire resumption update, poi tutti gli eventi del messaggio
        await handle(message)
```

Il codice effettivo gestisce anche eccezioni, chiusure vuote e cancellazione. Il fake dei test ora termina a ogni `turn_complete`, come l'SDK: il vecchio fake nascondeva il problema. Il test invia due turni, verifica una sola connessione e quattro messaggi persistiti.

Un errore esplicito di quota resta un problema distinto. `1011` da solo è generico; `1011 ... exceeded ... quota` è un segnale di quota. La pausa locale di un'ora introdotta da Devin evita tentativi ripetuti: **non indica quando Google ripristinerà la disponibilità**. I limiti sono legati al progetto, possono differire per modello; cambiare chiave nello stesso progetto non li azzera. Consultare i limiti effettivi in AI Studio. [G2]

### Cause da separare, verifica e rimedio

| Possibile causa | Come verificarla | Soluzione concreta / stato |
|---|---|---|
| Fine turno scambiata per chiusura | `turn_complete` seguito da «sessione terminata»; due turni sul fake fedele all'SDK | Ciclo esterno su `receive()`; implementato |
| Handle di ripresa ignorato | SessionResumptionUpdate ricevuto ma nuova config senza handle | Conservare l'ultimo handle valido e riutilizzarlo con lo stesso modello; implementato |
| Quota / frequenza / concorrenza del progetto | Testo dell'errore, orario e limiti AI Studio; chiudere altre sessioni di prova | Fermare la scaletta, mantenere il cooldown, browser disponibile; implementato |
| Errore interno `1011` senza quota | Motivo completo e riproducibilità sul medesimo modello | Retry limitati sullo stesso modello; implementato |
| Config incompatibile `1007` | Confronto con una sessione solo AUDIO e senza opzioni extra | Escludere solo il modello/config incompatibile, non ogni modello dopo un guasto di rete; implementato |
| Modello ritirato / non accessibile `404` | Verificare il modello esatto e la disponibilità per il progetto | Scaletta deduplicata, modelli correnti; override esplicito per confronti |
| VAD automatico più activity esplicite | Ispezionare input e config: due autorità diverse sulle pause | Baseline solo VAD automatico. Per VAD locale disabilitare quello remoto e inviare start/end coerenti |
| VAD troppo sensibile, eco, ventilatore | Stessa frase con cuffie e altoparlanti; contare falsi `interrupted` | AEC nel browser, niente ritorno del microfono, sensibilità bassa, silenzio iniziale 600 ms da tarare |
| `activity_handling` errato | Parlare sopra una risposta e osservare evento + arresto audio | `START_OF_ACTIVITY_INTERRUPTS`; svuotare subito anche la coda di riproduzione |
| Microfono fermato senza fine stream | Disattivare il mic dopo una frase corta e controllare se resta in attesa | `audio_stream_end=True` nel percorso VAD automatico; implementato |
| Finestra di contesto / durata connessione | Distinguere durata conversazione, connessione e GoAway | Compressione, handle e rotazione programmata; implementati, prova lunga reale necessaria |
| `media_resolution`, video o proactivity non compatibili | Rimuovere le opzioni e riprovare audio-only | Ometterli nella baseline: non curano una disconnessione audio. Abilitazione separata per capacità del modello |
| Proxy, VPN, firewall, sospensione Windows | Stesso test su altra rete; orario sospensione; errore di trasporto | Riconnessione con backoff. Verificare i ping del trasporto installato prima di introdurre heartbeat applicativi |
| Timeout preview a riposo | Sessione minima sullo stesso account, senza NODO, confronto con conversazione attiva | Connessione pigra già presente; catturare il vero motivo. Non inventare un limite free di 15–30 secondi |
| Audio inviato troppo frammentato / frequenza sbagliata | Un secondo di sorgente deve produrre 16.000 campioni; osservare pressione WS | Worklet continuo, pacchetti da 20 ms, limite al backlog; implementato |
| Tool lento blocca la ricezione | Tool sospeso e barge-in durante l'attesa | Task separato con esecuzione serializzata; test aggiunto. Lavori lunghi dovranno diventare job persistenti |
| UI rimasta su una build precedente | Commit locale, data build e `public/pcm-worklet.js` | Avvio controlla anche `public` e manifest; nuovo `diagnosi-nodo.bat` |

La documentazione distingue limiti del contesto e della connessione: senza compressione circa 15 minuti audio-only; la connessione ha una durata propria di circa 10 minuti. La ripresa usa l'handle, valido per due ore; GoAway avvisa prima della chiusura. Nessuno di questi dati dimostra un normale cutoff a 15–30 secondi. [G1]

### Config implementata

`apps/api/nodo/voice/live.py::live_config` è la fonte effettiva. Config audio-only, con SDK fissato alla versione verificata:

```python
config = {
    "response_modalities": ["AUDIO"],
    "system_instruction": system_prompt,
    "speech_config": {"voice_config": {
        "prebuilt_voice_config": {"voice_name": "Kore"}}},
    "input_audio_transcription": {},
    "output_audio_transcription": {},
    "realtime_input_config": {
        "automatic_activity_detection": {
            "disabled": False,
            "start_of_speech_sensitivity": "START_SENSITIVITY_LOW",
            "end_of_speech_sensitivity": "END_SENSITIVITY_LOW",
            "prefix_padding_ms": 100,
            "silence_duration_ms": 600,
        },
        "activity_handling": "START_OF_ACTIVITY_INTERRUPTS",
        "turn_coverage": "TURN_INCLUDES_ONLY_ACTIVITY",
    },
    "context_window_compression": {"sliding_window": {}},
    "session_resumption": {"handle": handle} if handle else {},
    "tools": [{"function_declarations": declarations}],
}
```

L'italiano è richiesto nel prompt. Non vengono imposte opzioni di thinking o proactivity comuni a modelli con capacità diverse. La documentazione attuale indica `gemini-3.8-live` come default e `gemini-3.1-flash-live-preview` come modello precedente; la baseline usa questi due, preservando `NODO_GEMINI_LIVE_MODEL` per un override. Validazione dello schema eseguita con l'SDK; compatibilità effettiva del progetto da provare con la sua chiave. [G3]

### Resilienza implementata e impatto

Un solo task possiede la riconnessione. Al massimo tre tentativi consecutivi senza un turno riuscito, attese circa 0,5 / 1 / 2 secondi più jitter; timeout per handshake 8 secondi, limite esterno 20. Un turno riuscito ripristina il budget. Quota e autorizzazione interrompono subito i retry. Un handle rifiutato consente il tentativo successivo senza handle; il diario ricostruisce parte del contesto. Non vengono riprodotti automaticamente comandi vocali persi.

**Impatto atteso alto:** elimina una causa deterministica di chiusura dopo ogni risposta. Il worklet passa da circa 375 messaggi/s a 48 kHz a 50 messaggi/s (circa −87%); ora conserva 16.000 campioni/s anche da 44,1 kHz. Questi sono risultati di verifica del codice, non una misura di latenza su Windows. Durante una vera riconnessione può esserci un breve vuoto e l'utente può dover ripetere la frase.

## B. Esperienza voce-prima

### Diagnosi → soluzione implementata

Una presenza centrale, il diario chiuso per impostazione iniziale, una singola azione «Parla con NODO» e schede contestuali. Le viste Notte, Siti e Accessi restano disponibili. Nessuna nuova dipendenza di produzione.

| Componente | Responsabilità | Segnale reale |
|---|---|---|
| `CommandCenter` | Conversazione, scheda di lavoro, approvazione contestuale, diario | Eventi WS e risultati strumenti |
| `Presence` | Filamenti Canvas 2D, massimo circa 30 fps, rispetto movimento ridotto | Stato + energia PCM del microfono / riproduzione |
| `useVoiceSession` | Microfono, riproduzione, interrupt, connessione, conversazione condivisa | MediaStream, Web Audio, protocollo |
| `voice-controls` | Apri, interrompi, chiudi voce, testo facoltativo | Stato reale di acquisizione |
| `context-sheet` | Siti, priorità, progetti, briefing | `ui.present`, dati selezionati lato server |
| `decision-sheet` | Proposta, anteprima, conferma/rifiuto | `approval.required/resolved/ignored` |

«NODO parla» segue la riproduzione effettiva: `turn_complete` non significa che l'ultimo campione sia già uscito dagli altoparlanti. Pensiero e lavoro seguono `voice.session.state` e `nodo.step/tool.*`. Il movimento ambientale indica uno stato, non una percentuale di lavoro inventata. Nel fallback browser non c'è una misura dell'energia di `speechSynthesis`: la presenza segue l'inizio/fine della lettura.

La conferma resta sullo schermo fino alla risposta del server. La decisione del modello deve riferirsi all'approvazione attuale e a un sì/no riconoscibile; Toolbox e le guardie originarie rimangono autoritativi. Un'interruzione vocale ferma il parlato: non promette di annullare un'azione già eseguita.

**Impatto atteso alto sulla leggibilità:** un luogo in cui parlare e lavorare, con trascrizione secondaria. Screenshot in `docs/qa/`: desktop, mobile e approvazione dimostrativa. Il caso di approvazione nello screenshot usa eventi simulati e non pubblica nulla.

## C. Naturalità e memoria

### Diagnosi → soluzione implementata

Il prompt aggiunto in `brain/persona.py::LIVE_STYLE` richiede italiano naturale, interventi brevi, una domanda alla volta, niente elenchi recitati o frasi meccaniche. Mantiene l'onestà sui risultati degli strumenti. Estratto di comportamento desiderato:

> Parla come un collaboratore competente e tranquillo. Parti dal punto utile. Usa frasi brevi e pause naturali. Se serve decidere, spiega la proposta e fai una domanda. Non leggere percorsi, markdown o liste tecniche: mettili nella scheda. Dichiara concluso un lavoro solo dopo il risultato dello strumento.

Non inserire pause artificiali o SSML in un protocollo che non li prevede. La qualità prosodica va valutata ascoltando; il prompt non la garantisce.

Ora voce e testo condividono l'ID del diario, i turni Live vengono salvati e il contesto viene chiuso/ripristinato quando cambia conversazione. La nuova connessione riceve un contesto operativo limitato a 6.000 caratteri e otto messaggi recenti da massimo 300 caratteri ciascuno; la ripresa con handle non reinietta la cronologia breve. Sono limiti prudenziali in caratteri, non budget precisi in token.

**Passaggio successivo, non implementato:** memoria strutturata per fatti con fonte/data, decisioni e stato del lavoro; riepilogo di circa 600–1.000 token aggiornato ogni 6–10 turni; recupero mirato per progetto da SQLite/FTS. Iniettare solo cambiamenti rilevanti quando NODO non sta parlando, con versione del riepilogo. Non usare l'intera chat come memoria e non trasformare una supposizione del modello in un fatto confermato. Conservare gli ultimi turni completi per riparare i tagli del riepilogo.

**Impatto atteso medio-alto:** meno perdita del filo e minore contesto iniziale; restano da provare cambi di argomento, nomi siciliani, interruzioni e trascrizioni tardive. Il diario testuale non certifica quanto audio l'utente abbia effettivamente ascoltato.

## D. Architettura, alternative e vincoli

### Diagnosi onesta

L'applicazione e il database sono locali; Gemini, Claude, Codex autenticato e altri provider cloud richiedono servizi esterni. Il percorso browser non è automaticamente offline: il riconoscimento può usare server del browser. Le funzioni locali emergenti richiedono supporto e pacchetti lingua verificati. [W1]

La conversazione deve essere un canale del Core, non il proprietario dei lavori. Il ramo migliora la concorrenza, ma non introduce ancora un job runner durevole. Prossimo passo: `jobs(id, idempotency_key, status, result, approval_id)` in SQLite; risposta immediata «Ho iniziato il controllo», risultati attraverso eventi, riconnessione che si riaggancia al job. La cache dei tool call ID oggi vale solo durante il processo: non garantisce exactly-once dopo un crash. Le approvazioni persistenti restano il vincolo per azioni irreversibili.

| Percorso | Pro reali | Contro / cosa serve | Stato |
|---|---|---|---|
| A — Gemini Live | Audio nativo e barge-in, integrazione già presente | Internet, disponibilità/quota variabili, modelli preview | Corretto in questo ramo; campo da verificare |
| B — STT browser → Core/ModelRouter → TTS browser | Nessuna nuova installazione vocale, usa i cervelli già configurati | Qualità dipendente da Windows/browser; spesso internet; maggiore latenza e turni meno fluidi | Fallback esistente migliorato |
| C — whisper.cpp → Ollama → Piper | Voce e ragionamento locali dopo il download, niente quota API obbligatoria | Modelli su disco, RAM/CPU/GPU; qualità italiana e tempo risposta da misurare | Adattatori da implementare |
| D — OpenAI Realtime opzionale | Secondo motore nativo, architettura sostituibile | API a consumo, internet; non è la quota Codex di ChatGPT Plus | Non attivato, nessun costo sostenuto |
| WebGPU / WebLLM | Inferenza nel browser compatibile | Non risolve da solo STT e TTS; download, memoria e vita della scheda da gestire | Sperimentale per NODO, non base del turno di notte |

Per C: `whisper.cpp` supporta Windows; Ollama offre installazione nativa Windows; Piper espone sintesi Python a blocchi. Tenere i modelli caricati, STT su segmenti VAD, TTS frase per frase e cancellazione immediata del player. Riutilizzare gli stessi protocolli STT/TTS e Toolbox. Su PC solo CPU partire con modelli piccoli, misurare e proporre il browser se il tempo risposta è eccessivo; non promettere un secondo senza benchmark. Controllare la licenza della voce italiana e dei modelli prima di distribuirli. [L1–L3]

Prezzi API verificati l'11/10/2026: `gpt-realtime-2.1`, audio $32 input / $64 output per milione di token; `gpt-realtime-2.1-mini`, $10 / $20. Testo e contesto hanno proprie tariffe; non convertire queste cifre in un costo fisso/minuto senza misurare uso e cache. Il listino presenta anche `gpt-live-1` a $0,05 per minuto di sessione, con modelli/tool backend separati. Per NODO ogni percorso a pagamento deve essere opt-in con budget, mai un fallback silenzioso. [O1]

### Soluzioni ai vincoli operativi

- **Gratis:** A e B finché disponibili; C per autonomia dalle quote; fallback deterministico dichiarato quando nessun cervello risponde, senza fingere ragionamento umano.
- **Guardie fluide:** una proposta visibile, ID corrente vincolato dal server, sì/no contestuale; per deploy mostrare destinatario e modifica. Prossima estensione: scadenza e versione della proposta per invalidarla se il lavoro cambia.
- **Segreti:** `.env` escluso da git e pannello sono una baseline, non cifratura. Diagnosi nuova senza leggere `.env` né esportare righe di log. Evoluzione: Windows Credential Manager/DPAPI, scrittura atomica, accessi del solo utente, controllo Origin del WebSocket e token locale; non esporre l'app su `0.0.0.0` per comodità.
- **Windows Home, niente Docker:** processi Python/Node locali e modelli opzionali. Per il turno di notte serve PC acceso; un processo locale non lavora durante sospensione o spegnimento.
- **Doppio click:** l'avvio esistente installa e costruisce; ora ricostruisce anche dopo modifiche al worklet. `diagnosi-nodo.bat` genera `nodo-diagnosi.json` con commit locale, SDK, build e conteggi errori. Non misura il microfono e non verifica la quota remota. Il doctor ora distingue configurazione disponibile da conversazione riuscita.
- **Pacchetto successivo:** release Windows versionata con frontend già costruito, runtime Python incluso, download modelli con checksum e ripresa, verifica disco/microfono, rollback della release precedente e dati in `%LOCALAPPDATA%\NODO`. Node serve allo sviluppo/build, può sparire dal PC dell'utente nel pacchetto finale. Installer e firme non sono stati prodotti in questo intervento.

**Impatto:** resilienza e diagnostica subito; autonomia completamente locale richiede una fase hardware e di confezionamento dedicata. La soluzione non è accumulare modelli nella scaletta, ma separare trasporto vocale, lavori, memoria e decisioni.

## Prova sul PC: criterio di accettazione, non dichiarazione di successo

1. Devin integra questo ramo nella copia di prova, chiude il vecchio processo e riavvia `avvia-nodo.bat` senza `-SkipBuild`. Non sovrascrivere `.env` o database e non fare `reset --hard` sul lavoro locale.
2. Aprire `diagnosi-nodo.bat`: controllare commit atteso, SDK 2.29.0 e pannello costruito. La diagnosi descrive il disco, non il processo aperto.
3. Cuffie, una sola scheda: 10 domande consecutive e 2 minuti di silenzio. Annotare orario e problema preciso se si interrompe.
4. Prova di 30 minuti, se la quota lo consente: due cambi di argomento, tool di lettura, barge-in, voce→testo→voce, chiudi/riapri microfono. Controllare numero connessioni, motivi di chiusura e continuità del diario.
5. Scollegare brevemente la rete: recupero oppure fallback esplicito, mai doppia esecuzione della richiesta. Con quota esaurita: nessuna scaletta ripetuta a ogni click.
6. Approvazione **dimostrativa**: sì valido solo sulla proposta visibile; sì vecchio, ambiguo o senza proposta non esegue. Nessun deploy reale necessario per la verifica.
7. Ripetere con altoparlanti. Obiettivi da misurare: zero chiusure locali per `turn_complete`, zero audio vecchio dopo stop, fine parola→primo suono tipicamente sotto 2 secondi con rete buona; gli ultimi valori sono obiettivi, non risultati già ottenuti.

Verifiche automatiche e schermate sono descritte in `docs/qa/README.md`. Il microfono Windows, la chiave dell'utente e una sessione reale di 30 minuti rimangono da verificare.

## Fonti primarie

- [G1 — gestione sessioni Gemini Live](https://ai.google.dev/gemini-api/docs/live-api/session-management)
- [G2 — quote per progetto e limiti effettivi](https://ai.google.dev/gemini-api/docs/rate-limits)
- [G3 — capacità e differenze dei modelli Live](https://ai.google.dev/gemini-api/docs/live-api/capabilities)
- [G4 — protocollo Live API](https://ai.google.dev/api/live)
- [S1 — SDK python-genai, implementazione di receive](https://github.com/googleapis/python-genai/blob/main/google/genai/live.py), verificata anche nel pacchetto installato 2.29.0
- [W1 — Web Speech Recognition](https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition)
- [L1 — whisper.cpp](https://github.com/ggml-org/whisper.cpp)
- [L2 — Ollama su Windows](https://docs.ollama.com/windows)
- [L3 — Piper, API Python mantenuta da OHF-Voice](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/API_PYTHON.md)
- [O1 — listino OpenAI API](https://developers.openai.com/api/docs/pricing)
