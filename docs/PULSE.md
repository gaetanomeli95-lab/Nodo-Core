# NODO Pulse V05 — operator radar

## Scopo
Trasformare «NODO, cosa abbiamo oggi?» in una risposta fondata su fatti registrati, non in una
simulazione di attività intelligente. La stessa fonte alimenta una fascia compatta sotto
la Presence vocale: **NODO ascolta** al centro, mentre le priorità sono disponibili senza
trasformare la schermata in una dashboard.

## Contratto
- `GET /api/v1/pulse`, autenticato: nessuna scrittura, nessun comando implicito.
- `nodo.pulse.build_pulse(session, organization_id, settings, now=...)`: snapshot
  deterministico e testabile, tutto filtrato per organizzazione.
- `Toolbox.today_agenda()`: conserva la risposta esistente, aggiunge `pulse` dagli stessi dati.
- Ogni item restituisce `source`, `source_id`, `observed_at`, `requires_approval` e
  `prompt` per aprire una conversazione con il cervello. Il pulsante **Esamina** invia il prompt
  al brain e non esegue azioni direttamente.
- La UI aggiorna il radar quando la conversazione termina oppure con refresh manuale.

## Cosa viene rilevato
- Task ancora aperti: scaduti, in scadenza oggi, bloccati e ad alta priorità;
  ordinamento per urgenza e priorità.
- Incidenti ancora aperti/mitigati sui soli siti attivi, indicando l'ultimo controllo
  registrato. Un incidente non implica che il problema sia ancora presente nel mondo
  reale: NODO deve verificare il sito prima di affermarlo.
- Approvazioni ancora pendenti: visualizzazione e richiesta di approfondimento;
  **nessuna approvazione viene mai concessa da Pulse**.
- Notte: `latest_or_live` ricava i controlli da record persistiti. Zero giri significa
  zero controlli, mai «tutto regolare».

## Non ancora implementato
Non è un monitor real-time, né un sistema di notifica push, né un motore di scheduling
autonomo. In futuro Pulse potrà ascoltare EventBus e motivare priorità con pesi
configurabili, permessi specifici, scadenza delle informazioni e azioni proposte.

## Test
`pytest -q tests/test_pulse.py` verifica vuoto, urgenze, incidenti/approvazioni,
separazione organizzazioni e risposta HTTP. Le animazioni/sizing di Windows vanno
validate anche in browser reale; CI non emula il comportamento percettivo.
