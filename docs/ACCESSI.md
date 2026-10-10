# Accessi: cosa inserire e dove

Tutto va nel file **`apps/api/.env`**, che `avvia-nodo.bat` crea la prima volta copiando `.env.example`.
Aprilo con il Blocco note, incolla le chiavi dopo il segno `=` (senza spazi e senza virgolette), salva e
riavvia NODO. Il file non finisce mai su GitHub: è escluso da `.gitignore`.

Per controllare che funzioni: pannello **Accessi** in NODO, oppure `python -m nodo.doctor` da `apps/api`.

Ti servono in tutto 4 cose: le prime 3 bastano per il turno di notte completo.

---

## 1. Vercel (obbligatorio): vedere deploy e log, fare rollback

1. Vai su <https://vercel.com/account/settings/tokens>.
2. **Create Token**. Nome: `NODO`. Scope: il tuo account, oppure il team dove stanno i progetti. Scadenza: a
   scelta (quando scade, NODO te lo dice nel pannello Accessi).
3. Copia il token e mettilo qui:
   ```
   NODO_VERCEL_TOKEN=il_token_copiato
   ```
4. Il team giusto lo trova NODO da solo: anche gli account personali su Vercel sono «team»
   (`tuonome-projects`) e NODO usa quello predefinito. Solo se i siti stanno in **un altro team**: Vercel →
   quel team → Settings → General → *Team ID* (inizia con `team_`):
   ```
   NODO_VERCEL_TEAM_ID=team_xxxxxxxx
   ```

## 2. GitHub (obbligatorio per le correzioni e le vulnerabilità)

1. Vai su <https://github.com/settings/personal-access-tokens/new> (fine-grained token).
2. Nome `NODO`, scadenza a scelta, *Resource owner*: il tuo account (o l'organizzazione dei repo).
3. *Repository access*: **All repositories**, oppure solo quelli dei siti.
4. *Repository permissions*:
   - **Contents**: Read and write (serve a creare il branch con la correzione)
   - **Pull requests**: Read and write
   - **Dependabot alerts**: Read-only
   - **Actions**: Read-only
   - **Issues**: Read and write (facoltativo)
   - *Metadata* diventa Read-only da solo
5. **Generate token**, copialo:
   ```
   NODO_GITHUB_TOKEN=github_pat_...
   ```
   NODO non scrive mai direttamente sul branch principale: crea `nodo/fix-…` e una pull request. Il merge
   avviene solo quando lo approvi tu.

## 3. Un'AI che sa scrivere codice (per preparare le correzioni)

Consigliato **Anthropic (Claude)**:

1. <https://console.anthropic.com> → *Settings* → *API Keys* → **Create Key**.
2. Ricarica un po' di credito (*Billing*): NODO non spende più di $1 a notte (`NODO_NIGHT_MAX_COST_USD`).
3. ```
   NODO_ANTHROPIC_API_KEY=sk-ant-...
   ```
In alternativa va bene `NODO_OPENAI_API_KEY`. Senza nessuna delle due, NODO controlla, fa rollback, rilancia
le build e ti scrive la diagnosi, ma non prepara correzioni.

## 4. Telegram (facoltativo, consigliato): il briefing sul telefono

1. Su Telegram cerca **@BotFather** → `/newbot` → scegli un nome (es. `NODO di Gaetano`) e un username che
   finisca con `bot`.
2. BotFather ti dà un token:
   ```
   NODO_TELEGRAM_BOT_TOKEN=123456:ABC...
   ```
3. Riavvia NODO, apri il tuo nuovo bot su Telegram e scrivigli «ciao».
4. Apri <http://127.0.0.1:8000/api/v1/setup/telegram>: trovi il tuo `chat_id`.
   ```
   NODO_TELEGRAM_CHAT_ID=123456789
   ```
5. Riavvia NODO. Da ora ricevi il briefing alle 7:30, e subito un messaggio se di notte un sito va giù.

---

## Facoltativi

| Variabile | A cosa serve |
|---|---|
| `NODO_VERCEL_PROTECTION_BYPASS` | Di solito le anteprime Vercel sono protette da login, quindi NODO non può testarle da solo (te lo dice: «anteprima protetta»). Per farlo testare: progetto Vercel → Settings → Deployment Protection → **Protection Bypass for Automation** → genera il segreto. Ogni progetto ha il suo, quindi scrivi `nome-progetto=segreto,altro-progetto=segreto2`. |
| `NODO_GROQ_API_KEY` | Gratis (<https://console.groq.com>): NODO risponde in modo più naturale e scrive le bozze dei contenuti. |
| `NODO_NIGHT_START`, `NODO_NIGHT_END`, `NODO_BRIEFING_TIME` | Orari del turno (predefiniti 01:00-07:00, briefing alle 07:30). |
| `NODO_NIGHT_AUTO_ROLLBACK=false` | Se preferisci che anche il rollback aspetti il tuo ok. |
| `NODO_NIGHT_FIX_ALLOW_PAID=false` | Spesa zero: niente modelli a pagamento nemmeno per le correzioni. |
| `NODO_DAY_INTERVAL_MINUTES=120` | Controlli anche di giorno, ogni 2 ore. |

## Se qualcosa non va

- **«il token non funziona»** nel pannello Accessi: è scaduto o è stato copiato male (spazi, virgolette).
- **NODO non vede i progetti Vercel**: il token è stato creato per un altro team (scope), oppure i siti stanno in
  un team diverso da quello predefinito: imposta `NODO_VERCEL_TEAM_ID`.
- **Nessuna correzione proposta**: guarda nel pannello Notte la riga «correzione». Il motivo c'è sempre
  (manca il modello, è finito il budget, il repository non è collegato, il modello non era sicuro…).
- **Di notte non è successo niente**: il PC era spento o NODO chiuso. Per farlo partire da solo all'accesso a
  Windows: `powershell -ExecutionPolicy Bypass -File scripts\avvio-automatico.ps1`.
