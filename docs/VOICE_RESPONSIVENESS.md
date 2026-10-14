# Voce e memoria: correzioni verificabili

Base: `integration/nodo-core-v01`, commit `07cb90f`. Branch di proposta: `work/nodo-responsive-memory-v08`.
Questa modifica è codice da integrare e provare: non certifica i tempi del PC di Gaetano e non modifica le quote Google.

## Diagnosi e modifica

| Problema nel codice | Correzione | Effetto atteso e limite |
| --- | --- | --- |
| Cancellare `to_thread(subprocess.run)` lascia lavorare il programma CLI | Esecuzione con `Popen`, segnale di cancellazione e chiusura dei processi figli della sola chiamata, su Windows e POSIX | Interruzione e timeout fermano il lavoro CLI; le azioni già completate non sono annullate |
| `pensa` ripetuto accoda o ricomincia lo stesso lavoro | Identità della richiesta legata a generazione, turno, domanda e trascrizione; riuso dei risultati identici solo nel turno; cancellazione delle richieste superate | Evita duplicati identici. Nessuna cache dei saldi tra turni. Parafrasi diverse non sono equiparate automaticamente |
| Il cervello continua dopo un'interruzione | Anche barge-in nativo e pulsante fermano `pensa`; una risposta di una generazione vecchia non torna alla voce | Il vecchio risultato non parla sopra la domanda nuova |
| Domande esatte sul registro fanno partire un modello | `quick_read` riconosce poche letture complete e usa lo stesso `Toolbox`: sospesi e riepilogo oggi/ieri | Zero chiamate al modello per quella lettura. Trascrizione e audio Live restano remoti; richieste ambigue, filtrate, composte e scritture usano il percorso normale |
| Memoria e cronologia possono gonfiare ogni prompt; il taglio `snapshot()[:6000]` può escludere regole | Memoria di lavoro entro 6.000 caratteri con regole pertinenti prima, fatti interi e importazioni solo pertinenti; snapshot entro 10.000; cronologia entro 8.000 | Nessuna cancellazione dal database. Con molte regole non entrano tutte: le altre restano ricercabili. I messaggi lunghi sono abbreviati nel prompt, con originale nel diario |
| Una persona conosciuta può portare nel prompt tutte le sue vecchie note importate | Le schede sempre presenti usano soltanto fonti manuali/conversazione | Le informazioni importate restano nel recupero per pertinenza |
| La memoria esplicita dipende dall'archivista in background, che può essere senza quota | Il prompt chiede `remember` subito e conferma dopo il risultato | Il salvataggio esplicito non viene affidato soltanto al lavoro periodico; l'uso effettivo dello strumento va verificato nel log |
| Istruzioni di testo e segnale `[silenzio]` raggiungono la voce | Persona Live dedicata; richiesta diretta poco chiara produce una breve domanda di chiarimento | Meno istruzioni inutili al modello vocale; restano i filtri del rumore esistenti |
| Riaprire NODO rifà controlli e riavvia un server già pronto | `/health` espone identità e impronta di codice/config/build; il launcher riapre l'istanza identica prima di installazione e migrazione | Avvio caldo più breve. Un aggiornamento o una modifica alla configurazione invalida il riuso. Nessuna promessa sull'avvio a freddo |
| Il launcher può marcare un'installazione fallita come riuscita e terminare altri programmi sulla porta | Lo stamp richiede installazione riuscita; un programma estraneo sulla porta produce un messaggio e non viene terminato | Ripristino più prevedibile |
| L'orbita ha raggio negativo con inclinazione negativa: `canvas.ellipse` interrompe il disegno | Raggi positivi in tutto l'intervallo; trascinamento solo nell'area della mente; gestione `pointercancel`; sospensione del disegno a scheda nascosta | Corregge una causa riproducibile della scomparsa; resa visiva da verificare su Windows |

Le guardie delle approvazioni rimangono nel Toolbox. Un sì vocale continua a valere soltanto per la proposta mostrata. Nessuna chiave, database personale, log o build generata viene aggiunta al repository.

## Verifica automatica

I test nuovi esercitano cancellazione e timeout di veri sottoprocessi Python, inclusi i figli e un processo estraneo da non toccare; duplicati, supersessione e interruzioni manuali/native; letture fresche senza modello; memoria affollata; cronologia senza request ID; identità dell'istanza e raggi dell'orbita.

Verifica locale del 14 ottobre 2026: **344 test Python passati, 1 saltato**, Ruff senza errori, build TypeScript/Vite riuscita e **5 test frontend passati**. Il job `windows-voice` verifica inoltre sintassi PowerShell, terminazione dei processi e regressioni dei provider su Windows. Consultare l'esito della PR prima di integrare.

La prova visiva con Chromium nell'ambiente di sviluppo è stata bloccata: la revisione automatica dei permessi ha rifiutato l'esecuzione richiesta. I test numerici del canvas non sostituiscono la prova del trascinamento nella finestra reale. Mancano prove acustiche sul PC, quota Google reale, rumore del negozio e latenza completa microfono→altoparlante.

## Prova su Windows con Devin

1. Salvare le modifiche locali non ancora pubblicate, poi integrare la proposta senza sovrascriverle. Non usare `carica-su-github.bat` per scaricare un aggiornamento: quel comando pubblica il lavoro locale.
2. Eseguire `avvia-nodo.bat` normalmente e annotare il tempo a freddo. Chiudere solo la finestra lasciando il server attivo, riaprire dal bat e misurare l'avvio caldo. Una modifica a sorgenti/config deve richiedere un riavvio.
3. Chiedere tre volte «Maria, quali sono i sospesi?». Verificare risposta e totale contro Registro; il log `pensa finished` deve indicare `provider=nodo model=register-read`, senza CLI avviato per quel conteggio.
4. Con una riga di prova, registrare un pagamento e ripetere la domanda: il totale deve cambiare. «I sospesi di Marcello» e le scritture non devono usare la scorciatoia delle letture complete.
5. Dare una nuova regola con «ricordati», verificare `remember` e la presenza in Memoria, riaprire e richiamarla. Ripetere con molte chat importate; distinguere memoria salvata da memoria selezionata per la risposta.
6. Avviare una richiesta lenta, interromperla a voce e dal pulsante, poi fare una domanda diversa. Non devono accumularsi CLI appartenenti alle richieste interrotte o arrivare risposte della domanda vecchia.
7. Trascinare la mente ai limiti mentre pensa e mentre entrano schede; provare finestra piccola, navigazione e preferenza movimento ridotto. La mano non deve comparire sull'intero pannello.
8. Fare 10 minuti di conversazione, con pausa, rumore e perdita della quota Live. Verificare fallback e approvazione mostrata; raccogliere tempi e codici di errore senza pubblicare chiavi o contenuti privati.

Il nuovo log `pensa finished request=… elapsed_ms=… provider=… model=… tools=…` misura il cervello, non la risposta acustica completa. Correlare con `voice_turn_metrics` e con una registrazione cronometrata. Confrontare mediana e casi peggiori, non solo il saluto iniziale.

## Ricerca e prossimo esperimento

- [LiveKit: analizzare la latenza degli agenti vocali](https://livekit.com/blog/understand-and-improve-agent-latency): misurare separatamente riconoscimento, modello, strumenti, sintesi e rete. Qui si elimina un passaggio di modello nelle letture certe e si impedisce lavoro duplicato.
- [Google: strumenti Live](https://ai.google.dev/gemini-api/docs/live-api/tools): comportamento e supporto asincrono dipendono dal modello. Manteniamo `BLOCKING` esplicito, senza attribuire a tutta la famiglia Gemini un unico default.
- [Google: caching](https://ai.google.dev/gemini-api/docs/caching): istruzioni comuni all'inizio possono favorire il riuso dove supportato. La persona Live mette il contesto variabile alla fine; questo non prova un cache hit Live e non rende illimitata la quota gratuita.
- [Python: cancellazione asyncio](https://docs.python.org/3.12/library/asyncio-task.html) e [sottoprocessi](https://docs.python.org/3.12/library/subprocess.html): interrompere l'attesa Python non equivale a terminare il processo esterno.

Per ridurre la dipendenza dalla quota audio, il prossimo esperimento separato è STT locale + letture locali/brain selettivo + TTS locale, valutato sull'hardware reale. Whisper/Piper richiedono modelli e risorse: non vengono installati da questa PR. Web Speech ed Edge TTS non vanno presentati come garanzia di funzionamento offline. Il cervello CLI resta utile per lavoro complesso, ma mantiene latenza e limiti dell'abbonamento. La soluzione di questa PR riduce il lavoro richiesto, senza aggiungere servizi a pagamento obbligatori.
