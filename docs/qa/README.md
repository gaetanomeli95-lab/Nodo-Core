# Verifica voce e presenza — 11 ottobre 2026

Ambiente di verifica: Linux, Python 3.12.14, google-genai 2.29.0, Node 24.19.0, Chromium 134 (Playwright 1.51.1). Nessuna chiave di produzione usata. Il comportamento fisico del microfono su Windows e la disponibilità Gemini del progetto non sono verificabili con questi test.

- Suite completa API: 145 test superati prima dell'ultimo caso stop/riprendi; successivo test di regressione verificato nella suite vocale (totale repository: 146 test).
- Ruff: superato sui moduli modificati e relativi test.
- TypeScript + build Vite: superati. JS circa 192 kB, 63 kB gzip; nessuna dipendenza frontend di produzione aggiunta.
- Worklet: un secondo a 16 / 44,1 / 48 kHz produce 50 pacchetti e 16.000 campioni con ampiezza stabile.
- Browser con API DEMO reale: richiesta scritta, risposta nel diario, nessun errore JavaScript; viewport 1440×1000 e 390×844, nessuno scorrimento orizzontale sul mobile.
- Browser con eventi WS e microfono simulati: attivazione microfono; stato parlato fino al termine della riproduzione, anche dopo `voice.turn.done`; scheda dati; conferma vincolata all'ID e mantenuta fino al risultato; quota→browser con arresto audio/mic e avviso visibile a diario chiuso; riconoscimento browser cumulativo senza parole duplicate.

Le tre immagini sono schermate dell'app effettiva. `presenza-decisione.png` usa una proposta dimostrativa inviata dal test, non un deploy reale.

## Riproduzione

```sh
cd apps/api
python -m pytest -q
ruff check nodo/voice nodo/doctor.py nodo/brain/persona.py tests/test_voice_live.py tests/test_voice_diagnostics.py
```

```sh
cd apps/web
npm ci
npm run build
node verify-pcm.cjs
```

Per la verifica browser serve un server DEMO con DB migrato, ad esempio alla porta 8124. Installare Playwright solo nell'ambiente di verifica (non è una dipendenza del prodotto), poi:

```sh
# In apps/web, con il pacchetto playwright disponibile al processo Node:
node verify-presence.cjs
# NODO_QA_URL può indicare un'altra porta/istanza locale DEMO.
```

`verify-presence.cjs` intercetta il solo WebSocket vocale: simula dati e conferme senza eseguire azioni reali. Non dimostra qualità audio, latenza di rete o affidabilità del provider.

La prova Windows di 30 minuti e i criteri di accettazione sono in [VOICE_PRESENCE_REVIEW.md](../VOICE_PRESENCE_REVIEW.md).
