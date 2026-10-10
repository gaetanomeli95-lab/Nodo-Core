# Turno di notte

NODO controlla i tuoi siti mentre dormi, sistema da solo ciò che è sicuro sistemare, prepara il resto e al
mattino ti racconta cosa è successo. Tutto quello che fa è registrato e consultabile. Le decisioni che contano
restano tue.

> «Buongiorno. Stanotte ho fatto 7 giri di controllo su 12 siti. PB-CARe alle 03:10 rispondeva con errore 500
> dopo il deploy «nuovo form». L'ho riportato alla versione precedente e ora funziona. Ho preparato la
> correzione (PR #14): l'anteprima funziona e aspetta il tuo ok. Prosperya: la build era fallita per un errore
> di rete, l'ho rilanciata ed è online. Da decidere: pubblicare la correzione per PB-CARe.»

## Come funziona una notte

| Quando | Cosa fa NODO |
|---|---|
| 01:00 (`NODO_NIGHT_START`) | **Giro completo**: importa i nuovi progetti Vercel e controlla tutto: deploy, home, pagine chiave (anche con un browser vero, per gli errori JavaScript), velocità, errori del server dai log Vercel, certificato, vulnerabilità delle dipendenze (Dependabot) e CI. Poi interviene, prepara le correzioni e scrive le bozze dei contenuti pianificati. |
| ogni ora fino alle 07:00 | **Giri rapidi**: deploy, home, pagine chiave e velocità. Se qualcosa si rompe alle 4, interviene alle 4. |
| 07:30 (`NODO_BRIEFING_TIME`) | **Briefing del mattino**: nel pannello *Notte* e, se configurato, su Telegram. |
| di giorno | niente, a meno di impostare `NODO_DAY_INTERVAL_MINUTES` (per esempio 120). |

Il pianificatore gira dentro NODO, quindi basta che NODO sia acceso. Su Windows, durante la finestra di notte,
NODO impedisce al PC di andare in sospensione (`NODO_KEEP_AWAKE`); lo schermo può spegnersi. Se il PC era
spento all'1:00, il giro completo parte appena NODO si riaccende.

## Cosa fa da solo e cosa chiede a te

| Situazione | Azione | Serve il tuo ok? |
|---|---|---|
| Il sito non risponde (5xx, timeout) dopo un deploy | **Rollback** all'ultima versione verificata sana, poi controlla che il sito risponda | No (reversibile). Si disattiva con `NODO_NIGHT_AUTO_ROLLBACK=false` o per singolo sito: in quel caso prepara il rollback e lo mette tra le decisioni |
| Il sito non risponde ma non dipende dall'ultimo deploy | Nessun rollback: te lo segnala (e ti scrive subito su Telegram) | — |
| Build fallita per un errore temporaneo (rete, timeout, rate limit) | **Rilancia la build** una volta e la verifica | No (`NODO_NIGHT_AUTO_REDEPLOY`) |
| Build fallita per un errore nel codice, errori del server, pagina rotta | **Prepara una correzione**: un branch `nodo/fix-…` con un solo commit, una pull request, l'anteprima Vercel testata | La PR la apre da solo. **Pubblicarla (merge) chiede SEMPRE il tuo ok** |
| Vulnerabilità gravi nelle dipendenze | Te le segnala, non tocca niente | — |
| Idee di contenuto pianificate (stato «idea») | Scrive una prima bozza (stato «bozza») | Pubblicare resta tuo |

Quando approvi una correzione, NODO fa il merge, aspetta il deploy di produzione e controlla che il sito
risponda. Dopo un rollback Vercel smette di pubblicare da solo i nuovi deploy: in quel caso NODO fa il
*promote* della versione corretta, perché è la pubblicazione che hai appena approvato. Se rifiuti, NODO chiude
la PR e cancella il branch.

## I paletti di sicurezza (nel codice, non nelle istruzioni al modello)

- **Livelli di azione** (ADR-006): leggere è libero. Rollback, rilancio della build e apertura della PR sono
  reversibili e approvati in anticipo dalla tua configurazione. Il merge in produzione è livello 4: richiede
  sempre l'approvazione esplicita di *quella* richiesta.
- **Correzioni piccole e controllate**: il modello vede solo i file scelti da NODO (quelli citati negli errori
  e quelli cambiati dall'ultima versione sana) e può modificare solo quelli. Segreti (`.env*`), CI
  (`.github/`) e lockfile sono intoccabili. Al massimo 300 righe cambiate. Un file che si accorcia in modo
  sospetto (risposta troncata) viene scartato. Se il modello non è sicuro, NODO scrive la diagnosi e non
  propone codice.
- **Log e codice sono dati, non istruzioni**: anche se un log contiene «fai il merge», al massimo produce una
  richiesta di approvazione visibile.
- **Tetto di spesa**: massimo `NODO_NIGHT_MAX_COST_USD` (predefinito $1) e `NODO_NIGHT_MAX_FIXES` correzioni
  (predefinito 3) per notte. In modalità `FREE` le correzioni possono usare un modello a pagamento solo perché
  `NODO_NIGHT_FIX_ALLOW_PAID=true`: mettilo a `false` per spendere zero.
- **Niente doppioni**: gli incidenti sono deduplicati e si chiudono da soli quando il controllo torna verde.
  Ogni azione viene tentata una volta per incidente e c'è al massimo una correzione aperta per sito.

## Siti

- **Importa da Vercel** (pannello *Siti*, o in automatico nel giro completo): prende progetti, domini (il
  dominio personalizzato prima di `*.vercel.app`) e il repository GitHub collegato.
- **Pagine chiave**: una per riga, `/contatti` oppure `/contatti = Contattaci`. Nel secondo caso NODO
  controlla anche che il testo compaia, così si accorge di una pagina «bianca» che risponde comunque 200.
- **Per ogni sito** puoi disattivare rollback automatico, rilancio delle build e correzioni, oppure escluderlo
  dai controlli.
- Un sito che non sta su Vercel si può aggiungere con il suo URL: NODO lo controlla e ti avvisa, ma non può
  fare rollback.

## Parlarci

Testo o voce, nel pannello *Comando*:

- «cosa hai fatto stanotte?» → il briefing. Se c'è una decisione in attesa, la propone, e con la voce basta
  rispondere «sì» o «no».
- «come stanno i siti?» / «come sta PB-CARe?» → lo stato attuale.
- «controlla i siti» → un giro rapido adesso.
- «cosa devo approvare?» → le decisioni in attesa.
- «buongiorno, cosa abbiamo oggi?» → le priorità del giorno più una riga sul turno di notte.

## Prova senza accessi

`demo-nodo.bat` (oppure `NODO_ENV=DEMO`) avvia un mondo simulato con tre siti finti. Una notte simulata
porta un deploy rotto, una build fallita per la rete e una vulnerabilità. Vedi rollback, correzione con
anteprima, approvazione, merge e verifica: tutto offline, niente esce dal computer.

## API

| Metodo | Percorso | |
|---|---|---|
| GET | `/api/v1/night/briefing` | briefing del mattino (o composto al momento con `?fresh=1`) |
| POST | `/api/v1/night/briefing/send` | invia il briefing su Telegram |
| POST | `/api/v1/night/run` | `{"kind": "light"\|"manual"\|"night", "site_ids"?: [...]}` |
| GET | `/api/v1/night/runs`, `/api/v1/night/runs/{id}` | giri con controlli e azioni |
| GET | `/api/v1/night/status`, `/api/v1/night/actions`, `/api/v1/night/pending` | stato, azioni, decisioni |
| GET/POST/PATCH | `/api/v1/sites`, `/api/v1/sites/{id}`, `/api/v1/sites/discover` | siti |
| GET/POST | `/api/v1/incidents`, `/api/v1/incidents/{id}/ignore\|resolve\|reopen` | incidenti |
| GET/POST | `/api/v1/fixes`, `/api/v1/fixes/{id}/approve\|reject` | correzioni |
| POST | `/api/v1/approvals/{id}/approve\|reject` | ora **esegue** l'azione approvata |
| GET | `/api/v1/setup/check`, `/api/v1/setup/telegram` | verifica degli accessi, trova il chat id Telegram |

## Codice

`nodo/night/` contiene `shift.py` (un giro: osserva, valuta, risponde), `fixer.py` (agente di correzione),
`approvals.py` (esecuzione delle approvazioni e verifica dopo il merge), `briefing.py`, `scheduler.py`,
`probe.py` (HTTP e browser), `drafts.py`, `assistant.py` (le domande in conversazione), `notify.py`,
`keepawake.py`, `simulation.py` (il mondo finto per test e demo) e `demo.py`. I connettori sono in
`nodo/tools/vercel.py` e `nodo/tools/github_repo.py`, il provider Claude in `nodo/providers/anthropic.py`.
Decisioni di progetto: [ADR-009](adr/ADR-009-night-shift.md).
