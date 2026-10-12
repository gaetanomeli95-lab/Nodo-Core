# Conoscenza personale: portare in NODO quello che sai già

NODO ragiona su **fatti con una fonte**, non su chat intere incollate nel prompt (vedi [MEMORY.md](MEMORY.md)).
Questa parte serve a riempire quella memoria con ciò che hai già raccontato ad altri assistenti e, in seguito,
a mail e documenti.

## Come funziona

1. **Import** (offline, deterministico): il file di export viene letto sul tuo PC. Ogni conversazione diventa
   un `Document` (`kind=chat:chatgpt|claude`, `uri=<provider>:<id>`), spezzata in `KnowledgeChunk` da ~1800
   caratteri e indicizzata in una tabella FTS5 (`knowledge_fts`). Niente esce dal computer in questa fase.
2. **Distillazione** (in background, con il cervello): dalla conversazione più recente alla più vecchia, una
   chiamata al modello per conversazione (`RouteRequest(purpose="memory")`, stesso router del brain). Il modello
   restituisce al massimo 8 fatti duraturi; NODO li salva come `MemoryFact(status=INFERRED, source_type="document",
   source_id=<Document.id>)`. I quasi-duplicati (somiglianza ≥ 90) e i fatti con confidenza < 0,45 vengono
   saltati. Nulla di già noto viene sovrascritto.
3. **Uso**: il brain (testo e voce Live, stesso `Toolbox`) ha due strumenti nuovi, `search_knowledge` e
   `read_knowledge`; `recall` suggerisce la ricerca nelle conversazioni quando la memoria è vuota. Lo snapshot
   del contesto dice quante conversazioni e fatti sono disponibili, e mostra fino a 25 fatti su di te (prima i
   certi, poi per confidenza).

Il progresso è visibile nella scheda **Memoria** del pannello e in `GET /api/v1/knowledge`. Gli stati sono onesti:
`imported`, `distilling`, `done`, `partial` (fermato o modello finito a metà), `no_model` (nessun cervello
configurato: il fallback deterministico non può leggere), `failed`, `stopped`.

## Esportare le chat

- **ChatGPT**: Impostazioni → Controlli dati → *Esporta dati*. Arriva una mail con uno zip (`conversations.json`
  dentro). Caricalo nella scheda Memoria così com'è, oppure incolla il percorso del file sul disco.
- **Claude**: Impostazioni → Privacy → *Esporta dati*. Stesso procedimento.

Rifare l'import con un export più recente aggiorna solo le conversazioni cresciute e le rimette in coda per la
distillazione. *Rimuovi* cancella il testo importato; i fatti già distillati diventano `ARCHIVED`, non spariscono
dalla storia.

## API

| Metodo | Percorso | Cosa fa |
|---|---|---|
| GET | `/api/v1/knowledge` | contatori, import con progresso, ultimi fatti distillati |
| POST | `/api/v1/knowledge/import` | multipart `file` (zip o json), `distill_now` |
| POST | `/api/v1/knowledge/import-path` | `{"path": "C:\\...\\export.zip"}` per file grandi |
| POST | `/api/v1/knowledge/sources/{id}/distill` · `/stop` | avvia/continua o ferma la lettura |
| DELETE | `/api/v1/knowledge/sources/{id}` | rimuove il testo importato |
| GET | `/api/v1/knowledge/search?q=` | la stessa ricerca di `search_knowledge` |

Impostazioni: `NODO_KNOWLEDGE_DISTILL=true`, `NODO_KNOWLEDGE_DISTILL_MAX=300` (conversazioni per esecuzione).

## Privacy, onestà, limiti

- I testi importati sono `CONFIDENTIAL`. Con la scelta «pragmatica» attuale la distillazione può usare anche
  modelli cloud (Claude, ChatGPT, Gemini, Groq): il testo della conversazione viene inviato al modello scelto dal
  router. Per restare locali servirebbe Ollama con un modello adeguato (`NODO_MODE=PRIVATE`).
- Il DB SQLite non è cifrato. Cifratura a riposo (DPAPI) e permessi del solo utente sono il passo successivo
  prima di importare posta.
- La distillazione segna i fatti come **dedotti**: il modello può fraintendere; la fonte è sempre consultabile.
  Confermare o correggere i fatti dal pannello è lavoro futuro.
- Non esiste un'API per leggere le chat di ChatGPT/Claude in tempo reale: l'export è l'unica via.

## Prossimi passi

Gmail in sola lettura (OAuth) → stessi `Document`/`KnowledgeChunk`, distillazione per thread; riepilogo utente di
600–1000 token aggiornato ogni N fatti; conferma/correzione dei fatti dal pannello; recupero mirato per progetto.
