# NODO V07 — voce senza quota Gemini (Windows)

## Obiettivo: separare microfono, cervello e altoparlanti

Questa versione integra **STT offline con faster-whisper** e **TTS offline con Piper**.
Non fa chiamate a Google, OpenAI o altri provider per *riconoscere* e *sintetizzare* la voce:
l'inferenza vocale è locale dopo il download iniziale dei modelli.
Il **cervello** `ModelRouter` di NODO è distinto: può utilizzare modelli online (Claude/Codex/Groq/Gemini)
o un modello locale Ollama. Eliminare la quota vocale di Google non elimina per magia
le quote di tutti i modelli di ragionamento.

I modelli STT/TTS sono risorse dell'utente su disco; non fanno parte del repository GitHub.
Nessun codice scarica modelli durante un turno. Le dipendenze sono **opzionali**; il
normale avvio `avvia-nodo.bat` rimane leggero e invariato.

## Installazione con doppio clic

1. Prima avvia almeno una volta `avvia-nodo.bat`, che crea `apps/api/.venv`.
2. Chiudi NODO e fai doppio clic su **`installa-voce-locale.bat`** dalla radice del progetto.
3. Lo script installa `.[local-voice]` soltanto nell'ambiente Python di NODO; scarica
   `faster-whisper/base` (italiano) e `it_IT-paola-medium` di Piper, salvandoli in
   `apps/api/.local-voice/`. La prima configurazione richiede internet e spazio su disco.
4. Lo script aggiorna *soltanto* le seguenti variabili in `apps/api/.env`:
   ```
   NODO_VOICE_ENGINE=browser
   NODO_STT_PROVIDER=local
   NODO_TTS_PROVIDER=local
   NODO_LOCAL_VOICE_DIR=.local-voice
   NODO_WHISPER_MODEL=base
   NODO_WHISPER_DEVICE=cpu
   NODO_WHISPER_COMPUTE_TYPE=int8
   NODO_PIPER_VOICE=it_IT-paola-medium
   ```
   La chiave Gemini non viene rimossa, letta o modificata. `VOICE_ENGINE=browser` qui
   significa **trasporto WebSocket browser → Core**: non usare Google Live;
   non significa usare Google SpeechRecognition.
5. Riavvia `avvia-nodo.bat`. `GET /api/v1/voice/config` mostra `ready=true`
   per STT e TTS quando modelli e pacchetti esistono; altrimenti il microfono viene
   disabilitato con un messaggio chiaro.

## Comportamento

- In Chrome/Edge, quando `stt.location=local`, il codice **non crea SpeechRecognition**:
  usa MediaRecorder e invia il contenuto al server locale per Whisper. Il browser può
  leggere il microfono, ma non invia il parlato al riconoscitore del suo produttore.
- Il backend trascrive il formato WebM/Opus con PyAV, incluso in faster-whisper. Niente
  FFmpeg di sistema da installare.
- Whisper mantiene il modello caricato dopo il primo turno, in CPU INT8 predefinito.
- Le risposte passano dal Core esistente, con stessa memoria e controlli approvazione.
- Piper carica una voce italiana ONNX una volta, genera un WAV per ogni frase e il client
  riproduce i segmenti nell'ordine. Non chiama servizi di sintesi remoti.
- `speech.end` attiva il riconoscimento locale su audio registrato; la registrazione
  non viene archiviata dal backend. I testi conversazionali, come già prima, restano
  nel database di NODO.
- Audio massimo per richiesta: 12 MiB; sintesi massima per frase: 1.600 caratteri.
- Il bottone "Interrompi" resta disponibile, ma questo percorso è **half-duplex**.
  Non promette interruzioni vocali mentre Piper parla. La V06 browser hands-free è
  una PR distinta che usa Web Speech: non confondere i due percorsi o attivare
  automaticamente un microfono senza consenso.

## Prestazioni e qualità: misurare, non indovinare

`base` e CPU `int8` sono scelte prudenziali. Su hardware debole possono esserci
secondi di attesa; italiano/dialetto, nomi e rumore richiedono test reali.
Per maggiore accuratezza si può preparare `small` e impostare
`NODO_WHISPER_MODEL=small`, o passare a `cuda` solo con CUDA/driver compatibili.
Piper Paola è una baseline gratuita, non necessariamente la voce umana finale.
Il candidato per TTS naturale in italiano è `kokoro-onnx` con voce italiana
(`im_nicola`), da benchmarkare prima di sceglierlo come default.

## Costi e licenze

Inferenza STT e TTS locali: nessun costo API per minuto o richiesta.
Download iniziale, hardware, elettricità e supporto hanno costi reali.
Piper 1.x è distribuito con **GPLv3**; verificare obblighi di licenza prima di
distribuire un installer proprietario. Ogni modello/voce ha propria licenza
e model card: controllarle prima di commercializzare.
ChatGPT Plus e Claude Pro non sono crediti API utilizzabili da qualunque provider.

## Prove di accettazione Windows

- Nessuna API key Gemini e `NODO_VOICE_ENGINE=browser`: il microfono avvia la registrazione.
- Una domanda italiana di 5–10 secondi produce `transcript.final` con `provider=faster-whisper`.
- `nodo.response` appare con il medesimo `conversation_id` dei messaggi scritti.
- La risposta viene riprodotta come `tts.chunk` `audio/wav` di `piper`, senza sintesi browser.
- Due turni consecutivi, domanda su un progetto e riferimento "e quello di prima?".
- Prova "Ferma", microfono negato, stop rapido, errori di download/modello assente.
- Controlla il Task Manager che RAM/CPU restino stabili; misura tempi primo turno/turni successivi.
- Ripeti scollegando internet con **Ollama già installato** se vuoi testare
  *l'intera* catena offline; con cervello cloud il parlato è locale ma l'inferenza non lo è.

## Architettura successiva

Per arrivare a un Jarvis naturale occorrono VAD locale (Silero/AudioWorklet), gestione
di pre-roll, barge-in acustico e echo suppression, streaming STT/TTS e job persistenti.
La PR V07 rende il **percorso vocale offline sostituibile e verificabile**,
senza inventare che il full-duplex sia già completato.
