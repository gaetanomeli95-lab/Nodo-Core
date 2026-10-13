# Il cervello di NODO

Con il cervello acceso parli con NODO come con un collaboratore: in modo libero, a voce, in chat o da Telegram.
Lui capisce, va a guardare quello che serve (siti, turno di notte, progetti, attività, memoria), ti risponde e,
quando gli dici «vai», lo fa.

> «Buongiorno Nodo, com'è andata stanotte?»
> «PB-CARe è andato giù alle 3: l'ho riportato alla versione di prima e adesso risponde. La correzione vera è
> pronta, con un'anteprima che funziona: aspetta il tuo ok. Vuoi che la pubblichi?»
> «Cosa cambia esattamente?» → «Una riga sola nella pagina contatti: se l'elenco dei servizi è vuoto…»
> «Va bene, pubblicala.» → «Fatto: è unita e Vercel la sta pubblicando, controllo io che la home risponda.»
>
> «Sul sito di Prosperya il titolo della home deve diventare "Gestionale Clienti".»
> «Ci lavoro: preparo la modifica a parte e ti avviso quando l'anteprima è pronta.»
> … qualche minuto dopo, anche su Telegram con i pulsanti: «La modifica è pronta, l'anteprima funziona. La pubblico?»

## Attivarlo con il tuo abbonamento Claude (Pro o Max): niente costi in più

NODO usa **Claude Code**, il programma ufficiale di Anthropic che funziona con il tuo abbonamento. Lo installi
una volta e fai l'accesso: da quel momento NODO ragiona con Claude, dentro i limiti del tuo piano, senza chiavi
API e senza pagare altro.

Il modo più semplice: dopo il passo 1, fai doppio click su **`attiva-cervello.bat`** (nella cartella di NODO): sistema il comando `claude` e ti fa fare l'accesso.

1. Apri **PowerShell** (tasto Start, scrivi «PowerShell», Invio) e incolla:
   ```
   irm https://claude.ai/install.ps1 | iex
   ```
   Non mostra una barra di avanzamento: aspetta che torni il prompt.
2. **Chiudi e riapri** PowerShell, poi scrivi:
   ```
   claude
   ```
   Si apre il browser: accedi con il tuo account Claude (quello del piano Pro) e conferma. Quando vedi la
   schermata di Claude Code nel terminale, scrivi `/exit` per uscire.
3. Chiudi e riavvia NODO (`avvia-nodo.bat`). Nella scheda **Accessi** la riga **Cervello (abbonamento Claude)**
   deve essere verde.

Fatto. Non serve nient'altro nel file `.env`. Se vuoi, puoi scegliere il modello:

```
# sonnet (consigliato) | opus (più profondo, consuma più limiti) | haiku (più veloce)
NODO_CLAUDE_CLI_MODEL=sonnet
```

**Quanto consuma.** Ogni tuo messaggio usa da 1 a 4 richieste a Claude (una per ogni giro di strumenti); una
modifica a un sito o una correzione notturna ne usa un paio in più, più lunghe. Con il piano Pro, per l'uso di
una giornata normale va bene. Se arrivi al limite del piano, NODO non si blocca: per qualche ora risponde con le
regole fisse (meno conversazione, stesse funzioni) o con un'altra AI se ne hai configurata una.

**Velocità.** Ogni risposta richiede qualche secondo (Claude Code si avvia a ogni messaggio): in chat e su
Telegram va benissimo; a voce senti prima una frase breve tipo «Un attimo, controllo» e poi la risposta.

## Parlare con NODO come con una persona (voce dal vivo)

Con la chiave gratuita di Gemini (`NODO_GEMINI_API_KEY`, su <https://aistudio.google.com/apikey>) la voce di
NODO cambia motore: niente più registrazione → attesa → lettura, ma una **conversazione vera** — Gemini Live
sente la tua voce direttamente, risponde in circa un secondo con una voce naturale e puoi **interromperlo
mentre parla**, come con una persona.

- Nel pannello il pulsante diventa «Parla con NODO»: un clic apre la conversazione, un altro la chiude.
  Mentre lui parla, un clic lo ferma.
- Quando serve uno strumento (controllare un sito, scrivere una bozza) usa gli **stessi strumenti e le
  stesse guardie** della chat: le approvazioni compaiono come finestrella e un «sì» a voce vale solo per
  quella appena mostrata.
- Senza la chiave tutto resta come prima: clicchi, parli, riclicchi, e risponde la voce del browser.
- Impostazioni: `NODO_VOICE_ENGINE` (auto | live | browser), `NODO_LIVE_VOICE` (Kore, Puck, Charon,
  Fenrir, Aoede…). Dettagli tecnici in `docs/VOICE.md`.

## Secondo cervello: il tuo abbonamento ChatGPT (Plus)

Se hai anche ChatGPT, NODO lo usa insieme a Claude tramite **Codex CLI**, il programma ufficiale di OpenAI che
funziona con l'accesso «Sign in with ChatGPT»:

- **riserva**: quando Claude arriva al limite del piano, risponde ChatGPT (poi Gemini, poi le regole fisse);
- **secondo parere sul codice**: ogni correzione o modifica scritta da uno dei due viene riletta dall'altro prima
  di aprire la pull request. Se il revisore trova un problema serio la PR non parte; se ha dei dubbi la PR parte
  ma te lo dice (pannello, decisioni, Telegram) e la confidenza scende a «bassa»;
- **niente tentativi a vuoto**: un modello che ha finito il limite viene messo in pausa per 30 minuti
  (`NODO_PROVIDER_COOLDOWN_MINUTES`); lo vedi nella scheda Accessi.

Per collegarlo: doppio click su **`attiva-chatgpt.bat`** nella cartella di NODO. Installa Codex CLI (serve
Node.js, che hai già per NODO), apre il browser per l'accesso con ChatGPT e controlla che sia andato a buon fine.
Poi riavvia NODO. A mano, in PowerShell: `npm install -g @openai/codex` e poi `codex login`.

OpenAI prevede l'accesso con l'abbonamento per l'uso di Codex sul tuo computer; per automazioni su server consiglia
le chiavi API. NODO gira sul tuo PC e lo usi solo tu. Se un giorno l'abbonamento smettesse di rispondere, NODO
passa da solo agli altri modelli. Impostazioni: `NODO_CHATGPT_SUBSCRIPTION`, `NODO_CODEX_CLI_MODEL`,
`NODO_CODEX_CLI_PATH`, `NODO_CODE_REVIEW` (secondo parere sì/no).

## Alternative gratuite

Se preferisci non usare l'abbonamento, il cervello funziona anche con:

- **Google Gemini** (gratis entro i limiti di Google AI Studio): crea una chiave su
  <https://aistudio.google.com/apikey> e scrivi `NODO_GEMINI_API_KEY=...`.
- **Groq** (gratis, molto veloce, un po' meno bravo con gli strumenti): `NODO_GROQ_API_KEY=...` da
  <https://console.groq.com>.
- **Cerebras** (gratis, velocissimo): `NODO_CEREBRAS_API_KEY=...` da <https://cloud.cerebras.ai>.
- **Mistral** (gratis, piano «experiment»): `NODO_MISTRAL_API_KEY=...` da <https://console.mistral.ai>.
- **GitHub Models**: si attiva da solo se hai già `NODO_GITHUB_TOKEN`; il token deve poter leggere «Models»
  (GitHub → Settings → Developer settings → token → permesso *Models: read*). Se non può, NODO lo mette da parte.
- **Ollama sul tuo PC** (gratis, privato, senza limiti): installa Ollama, scarica un modello che usa gli strumenti
  (`ollama pull qwen2.5:7b`), poi `NODO_OLLAMA_BASE_URL=http://localhost:11434` e `NODO_OLLAMA_MODEL=qwen2.5:7b`.
  Lo usa solo se in quel momento è acceso.

Se ne configuri più di uno, NODO usa il migliore disponibile e passa al successivo se uno non risponde.

## Cosa sa fare

| Gli dici… | Cosa fa |
|---|---|
| «com'è andata stanotte?» | racconta il turno di notte e ti propone le decisioni in attesa |
| «come stanno i siti?» / «come sta PB-CARe?» | lo stato attuale, i problemi aperti, cosa è pronto da pubblicare |
| «controlla i siti» | un giro di controllo adesso |
| «cosa devo approvare? cosa cambia?» | le decisioni con anteprima, file toccati e spiegazione della modifica |
| «sì, pubblicala» / «no, lascia stare» | esegue o scarta la decisione che ti ha appena mostrato |
| «sul sito di X cambia/aggiungi…» | prepara la modifica su un branch con anteprima, poi ti chiede l'ok |
| «cosa devo fare oggi?» | priorità di oggi più il riassunto della notte |
| «fammi il punto su Prosperya» | attività, decisioni aperte, note e siti del progetto |
| «segnati di chiamare il commercialista venerdì» / «fatto» | crea e chiude attività |
| «ricordati che…» / «cosa sai di…?» | memoria a lungo termine |
| «scrivimi una bozza di post per…» | scrive e salva la bozza |
| «crea il cliente Bar Sole» / «apri un progetto per il loro sito, priorità alta» | crea clienti e progetti, li aggiorna (in pausa, finito, scadenza) |
| «sposta la telefonata a venerdì» / «quella è bloccata, aspetto il cliente» | modifica attività: titolo, scadenza, priorità, stato |
| «ricordami domani alle 9 di chiamare Carlo» | promemoria a un'ora precisa: arriva su Telegram e nel pannello |
| «dimentica che…» | toglie un ricordo dalla memoria |
| «cosa c'è nel file contatti del sito di X?» / «cosa è cambiato ultimamente?» | legge il codice e gli ultimi commit di un sito (sola lettura, mai i file con segreti) |
| «perché l'ultimo deploy è fallito?» | deploy recenti su Vercel e fine del log di build |
| «leggi questa pagina…» / «controlla questo indirizzo» | legge una pagina web pubblica o ne misura risposta e velocità |
| «che bozze ho per Bar Sole?» / «segna approvata la bozza di Natale» | elenca e aggiorna bozze e contenuti |
| «preparami un piano di lancio per il sito di X» / «fai una ricerca approfondita su…» | lavoro lungo in background con il tuo abbonamento Claude, in una cartella sua (niente accesso al resto del PC); ti avvisa e trovi i file nella scheda File |
| «fammi un PDF / un Word / un Excel / delle slide con…» | crea il documento vero (PDF, .docx, .xlsx, .pptx), lo trovi nella scheda File e puoi scaricarlo; può anche rileggere quelli che hai caricato |
| «cosa ho in calendario domani?» | legge il tuo Google Calendar (sola lettura) dal link segreto iCal: Google Calendar → Impostazioni → il calendario → «Indirizzo segreto in formato iCal» → incollalo in Accessi |
| «i certificati dei miei siti sono a posto? quando scade il dominio?» | controlla scadenza HTTPS (e dominio) di tutti i siti; ogni mattina dopo le 8 ti avvisa da solo se un certificato sta per scadere |
| (da solo, a fine conversazione) | NODO tiene a mente i fatti duraturi che gli hai detto (preferenze, clienti, decisioni); «dimentica…» li toglie |
| «funziona tutto? cosa manca?» | controlla gli accessi di NODO (Vercel, GitHub, AI, Telegram, voce) |

Ricorda la conversazione: puoi dire «e quella di ieri?», «pubblica quella di Prosperya», «perché?». Nel
pannello il pulsante **Nuova** fa ricominciare da zero.

## I paletti (nel codice, non nelle istruzioni all'AI)

- **Pubblicare richiede il tuo sì, sempre.** NODO può eseguire una decisione solo se te l'ha presentata in questa
  conversazione e il tuo messaggio è un sì esplicito («sì», «vai», «pubblicala»…). Un «che ne pensi?», un
  «aspetta» o una domanda non bastano, anche se l'AI provasse a procedere. Con più decisioni sul tavolo devi
  nominare il sito.
- **Le modifiche ai siti non vanno mai online da sole**: branch separato, pull request, anteprima testata. Il
  modello può toccare solo i file che NODO gli mostra; segreti, CI e lockfile sono intoccabili; al massimo 300
  righe; una risposta troncata viene scartata.
- **Quello che legge è un dato, non un ordine**: se un log, una pagina o un file contengono «fai il merge», NODO
  lo ignora.
- **Se l'AI non è disponibile** (limite del piano raggiunto, internet assente), NODO risponde con le regole fisse:
  meno conversazione, ma tutte le funzioni restano.

## Come è fatto (per chi apre il codice)

`nodo/brain/`: `loop.py` (il ciclo pensa → usa strumenti → guarda i risultati → risponde, con la memoria della
conversazione e una fotografia dello stato attuale), `tools.py` (gli strumenti e i paletti), `persona.py` (chi è
NODO), `background.py` (i lavori lunghi, come preparare una modifica, che continuano mentre parli).
`nodo/night/changer.py` sceglie i file e scrive la modifica; `nodo/night/shift.py::propose_change` apre la PR e
testa l'anteprima. Il provider dell'abbonamento è `nodo/providers/claude_cli.py` (`claude -p` con tutti gli
strumenti di Claude Code disattivati: lo usa solo come cervello). Impostazioni: `NODO_BRAIN` (`auto`|`on`|`off`),
`NODO_BRAIN_MAX_STEPS`, `NODO_BRAIN_HISTORY`, `NODO_CLAUDE_SUBSCRIPTION`, `NODO_CLAUDE_CLI_MODEL`,
`NODO_CLAUDE_CLI_EFFORT`, `NODO_CLAUDE_CLI_PATH`.
