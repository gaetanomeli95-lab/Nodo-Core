# Studio delle architetture dei grandi assistenti → la strada proprietaria di NODO

Punto 4 dell'agenda (18/10): capire come funzionano ChatGPT, Claude e Gemini per costruire
qualcosa di unico e proprietario. Questo documento è la mappa onesta: cosa fanno loro, cosa fa
già NODO, dove NODO può essere *meglio* per un assistente personale, e cosa NON conviene copiare.

## 1. Come sono fatti i grandi assistenti (livello di architettura, non di peso del modello)

Tutti e tre condividono lo stesso scheletro: **un LLM transformer + uno strato di orchestrazione**
che è dove vivono le differenze di prodotto.

### ChatGPT (OpenAI)
- **Modello**: transformer dense, RLHF/allineamento, più versioni per costo/latenza (mini, 4o, o-series reasoning).
- **Orchestrazione**: function calling nativo, "canvas", memory implicita (fatti estratti dal
  dialogo, opachi all'utente), retrieval su file caricati (RAG con embedding proprietari).
- **Voce**: Advanced Voice Mode — audio nativo end-to-end (non STT→LLM→TTS a staffetta), quindi
  latenza bassa ma pipeline chiusa e non ispezionabile.
- **Instradamento**: invisibile all'utente; il modello scelto dipende dal piano.

### Claude (Anthropic)
- **Modello**: transformer, enfasi su constitutional AI; tool use e "computer use" espliciti.
- **Orchestrazione**: artifacts, progetti con contesto persistente *esplicito* (l'utente vede cosa
  sa), MCP per gli strumenti esterni — protocollo aperto.
- **Punto di forza**: disciplina sul tool use e lungo contesto ben gestito.

### Gemini (Google)
- **Modello**: multimodale nativo (testo+immagine+audio+video nello stesso encoder), contesto
  enorme (1-2M token) usato come "memoria di lavoro a buon mercato" invece di un RAG elaborato.
- **Orchestrazione**: Live API (voce realtime a bassa latenza), grounding su Google Search.
- **Punto debole**: quota free piccola — NODO l'ha imparato a proprie spese (429 continui).

### Il pattern comune
`input → contesto assemblato (storia + memoria + strumenti) → modello → tool calls → risposta`.
La "magia" non è il modello: è **come si assembla il contesto** e **come si scelgono modello e strumenti**.

## 2. Cosa NODO ha già di proprietario (non copiato da nessuno)

| Pezzo | Dove | Perché è diverso |
|---|---|---|
| Memoria con **provenienza e validità temporale** (`MemoryFact`, supersede mai sovrascrittura) | `nodo/memory/` | ChatGPT memory è opaca; NODO sa *chi gliel'ha detto, quando, fino a quando vale* |
| **Approval gate** a livello di strumento (`decide`, level 4, binding del «sì» alla proposta presentata) | `brain/tools.py`, `night/approvals.py` | Nessun assistente consumer lega il "sì" vocale alla *specifica* proposta sul tavolo |
| **Routines** — abitudini minate da firma+fascia oraria, iniettate come previsioni | `nodo/memory/routines.py` | Anticipazione esplicita e ispezionabile, non un embedding opaco |
| **Router adattivo misurato** — costo, quota, cooldown per errore, EWMA latenza reale | `nodo/providers/router.py` | OpenAI instrada per te opaco; NODO impara sulla macchina di Gaetano e lo mostra |
| **Interfaccia vivente** guidata da eventi reali (`/activity`, MindGraph) | `apps/web` | Le animazioni sono telemetria resa visibile, non decorazione |
| Modalità **FREE first**: l'assistente funziona con quote gratuite e degrada con grazia | config + router | I grandi sono paywall-first; qui è un requisito di sopravvivenza (quota esaurita ≠ muto) |

## 3. Cosa conviene prendere in prestito (legalmente — sono pattern, non codice)

1. **Contesto enorme vs RAG** (Gemini): per NODO non serve — il suo "contesto" è il `memory_context`
   a budget, più mirato. Conviene l'idea opposta: *meno* token ma con provenienza.
2. **Tool use disciplinato** (Claude): NODO lo fa già via `complete_tools` + JSON; la lezione utile è
   *dichiarazioni minime e guard-rails in codice* — già in `PermissionPolicy`.
3. **Voce end-to-end** (ChatGPT AVM / Gemini Live): la staffetta STT→LLM→TTS è dove NODO perde i
   secondi (misurato: turni da 17-52s, modello solo ~6s). Direzione: ridurre le giunzioni, non
   necessariamente audio nativo — la telemetria S02 ora permette di vedere ogni segmento.
4. **Mixture-of-Experts concettuale**: NODO la implementa a livello di *sistema*, non di pesi —
   il router sceglie il modello giusto per scopo (voce veloce, memoria economica, coding forte).

## 4. La strada proprietaria (decisioni architetturali prese)

- **La memoria è il modello**: invece di un LLM più grande, un contesto più intelligente.
  `MemoryFact` + routines + diary + provenance = un "second brain" che i giganti non vendono
  perché è *tuo*, non loro.
- **L'orchestrazione è il prodotto**: approval, telemetria per-stage, cooldown, activity feed.
  Lo strato sopra il modello è dove NODO è unico e difendibile.
- **Niente reti neurali custom senza benchmark**: la roadmap esplicita (e la guida ChatGPT) vieta
  GRU/reti specializzate finché un bisogno misurato non lo richiede. Il "cervello proprietario"
  è il sistema, non una rete.
- **Adattamento misurato**: il router ora impara dalla latenza reale (EWMA) e dagli esiti — primo
  passo verso l'instradamento per intent. Prossimo: apprendere *quale modello risponde meglio a
  quale tipo di richiesta* su questa macchina specifica.

## 5. Aperto / prossimi passi collegati

- S03: timestamp del percorso Live (chiude il buco di telemetria residuo).
- Router per-intent: oggi `purpose` è grezzo; mappare intent riconosciuti → RouteRequest.
- Valutare compressione delle risposte TTS (segmentazione anticipata) per il tempo alla voce.
