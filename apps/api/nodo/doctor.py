"""NODO doctor: checks every access the night shift needs and says, in plain Italian, what is missing.

CLI:  python -m nodo.doctor        (from apps/api, with your .env)
API:  GET /api/v1/setup/check
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from dataclasses import asdict, dataclass

import httpx

from nodo.providers.router import ModelRouter, RouteRequest, cooldowns


@dataclass
class Check:
    name: str
    status: str          # ok | missing | error | optional | warning
    detail: str
    how: str = ""


async def run_checks(c) -> list[Check]:
    s = c.settings
    out: list[Check] = []

    # --- Vercel
    if c.vercel is None:
        out.append(Check("Vercel", "missing", "token non configurato: NODO può controllare solo i siti aggiunti a mano "
                                              "(senza deploy, log, rollback).",
                         "Vercel → Account Settings → Tokens → Create. Copialo in NODO_VERCEL_TOKEN (e, se i progetti "
                         "sono in un team, NODO_VERCEL_TEAM_ID)."))
    else:
        try:
            who = await c.vercel.whoami()
            projects = await c.vercel.list_projects()
            linked = sum(1 for p in projects if p.repo_name)
            out.append(Check("Vercel", "ok", f"collegato come {who.get('username')}: {len(projects)} progetti "
                                             f"({linked} collegati a GitHub)."))
        except Exception as e:
            out.append(Check("Vercel", "error", f"il token non funziona: {str(e)[:200]}",
                             "Rigenera il token su Vercel e controlla NODO_VERCEL_TEAM_ID."))
    # --- GitHub
    if c.repo is None:
        out.append(Check("GitHub", "missing", "token non configurato: niente correzioni automatiche né controllo "
                                              "delle vulnerabilità.",
                         "GitHub → Settings → Developer settings → Fine-grained tokens. Permessi: Contents (read/write), "
                         "Pull requests (read/write), Metadata (read), Dependabot alerts (read), Actions (read). "
                         "Copialo in NODO_GITHUB_TOKEN."))
    else:
        try:
            who = await c.repo.whoami()
            out.append(Check("GitHub", "ok", f"collegato come {who.get('login')}."))
        except Exception as e:
            out.append(Check("GitHub", "error", f"il token non funziona: {str(e)[:200]}",
                             "Rigenera il fine-grained token e includi i repository dei tuoi siti."))
    # --- brain (conversation with tools)
    cc = next((p for p in c.providers if p.name == "claude-subscription"), None)
    brain_router = ModelRouter(c.providers, s.mode, s.allow_paid_in_free_mode)
    brains = [d.descriptor.id for d in brain_router.eligible(RouteRequest(purpose="brain", needs_tools=True,
                                                                           min_reasoning=3))
              if hasattr(c.providers_by_name().get(d.descriptor.provider), "complete_tools")]
    if cc is not None:
        ok = await cc.available()
        out.append(Check("Cervello (abbonamento Claude)", "ok" if ok else "error",
                         f"Claude Code trovato: NODO ragiona con il tuo abbonamento ({cc.model}), senza costi extra."
                         if ok else "Claude Code è installato ma non hai fatto l'accesso.",
                         "" if ok else "Apri il Terminale e scrivi: claude   poi segui l'accesso con il tuo account "
                                       "Claude (Pro). Riavvia NODO."))
    cx = next((p for p in c.providers if p.name == "chatgpt-subscription"), None)
    if cx is not None:
        ok = await cx.available()
        out.append(Check("Cervello di riserva (abbonamento ChatGPT)", "ok" if ok else "error",
                         "Codex CLI trovato: ChatGPT risponde quando Claude non può e rilegge le modifiche al codice."
                         if ok else "Codex CLI è installato ma non hai fatto l'accesso con ChatGPT.",
                         "" if ok else "Apri PowerShell, scrivi: codex login  e accedi con il tuo account ChatGPT. "
                                       "Riavvia NODO."))
    elif cc is not None:
        out.append(Check("Cervello di riserva (abbonamento ChatGPT)", "optional",
                         "non collegato: quando Claude arriva al limite NODO passa alle risposte fisse (o a Gemini).",
                         "Se hai ChatGPT Plus: installa Codex CLI e fai l'accesso (docs/CERVELLO.md)."))
    # --- live voice (Gemini Live): talk to NODO like to a person ---
    from nodo.voice.live import missing_reason
    why = missing_reason(s, c)
    out.append(Check("Voce dal vivo (Gemini Live)", "ok" if why is None else "optional",
                     "parli con NODO in tempo reale, voce naturale, ~1 secondo di attesa."
                     if why is None else f"non attiva: {why}. La voce normale del browser funziona comunque.",
                     "" if why is None else
                     "Chiave gratuita: aistudio.google.com → Get API key → copiala in NODO_GEMINI_API_KEY "
                     "nel file apps/api/.env, poi riavvia NODO."))
    paused = cooldowns()
    if paused:
        out.append(Check("Modelli in pausa", "warning",
                         ", ".join(f"{k} per altri {max(1, v // 60)} min" for k, v in paused.items())
                         + ": hanno raggiunto il limite d'uso, NODO usa gli altri.", ""))
    if cc is not None or cx is not None:
        pass  # the subscription rows above already describe the brain
    elif s.brain == "off":
        out.append(Check("Cervello", "optional", "disattivato (NODO_BRAIN=off): risposte a regole.", "NODO_BRAIN=auto"))
    elif brains:
        out.append(Check("Cervello", "ok", f"conversazione con strumenti attiva con {brains[0]}."))
    else:
        out.append(Check("Cervello", "optional", "nessun modello capace di usare strumenti: NODO risponde con regole "
                                                 "fisse (funziona, ma non dialoga davvero).",
                         "Consigliato (usa il tuo abbonamento Claude Pro): installa Claude Code e fai l'accesso, "
                         "vedi docs/CERVELLO.md. Gratis: NODO_GEMINI_API_KEY (aistudio.google.com) o "
                         "NODO_GROQ_API_KEY."))
    # --- models
    fix_router = ModelRouter(c.providers, s.mode, s.allow_paid_in_free_mode or s.night_fix_allow_paid)
    coders = [d.descriptor.id for d in fix_router.eligible(RouteRequest(purpose="code_fix", min_coding=4,
                                                                         min_reasoning=3))]
    if coders:
        out.append(Check("AI per le correzioni", "ok", f"userò {coders[0]}" + (f" (alternative: {', '.join(coders[1:3])})"
                                                                              if len(coders) > 1 else "") +
                         f". Tetto di spesa: ${s.night_max_cost_usd:.2f} a notte."))
    else:
        out.append(Check("AI per le correzioni", "missing", "nessun modello adatto al codice: NODO farà diagnosi e "
                                                            "rollback, ma non preparerà correzioni.",
                         "Installa Claude Code e fai l'accesso con il tuo abbonamento (docs/CERVELLO.md), oppure "
                         "imposta NODO_ANTHROPIC_API_KEY o NODO_OPENAI_API_KEY."))
    talk = [d.descriptor.id for d in ModelRouter(c.providers, s.mode, s.allow_paid_in_free_mode)
            .eligible(RouteRequest(min_reasoning=3)) if d.descriptor.provider != "deterministic"]
    out.append(Check("AI per conversazione e bozze", "ok" if talk else "optional",
                     f"userò {talk[0]}" if talk else "nessun modello: NODO risponde con i dati, senza riformulare, e "
                                                     "non scrive bozze.",
                     "" if talk else "Gratis: NODO_GROQ_API_KEY (console.groq.com) o NODO_OPENROUTER_API_KEY."))
    # --- notifications
    if c.notifier is not None and c.notifier.enabled:
        ok = await _telegram_ok(s.telegram_bot_token)
        out.append(Check("Notifiche Telegram", "ok" if ok else "error",
                         ("il bot risponde: briefing e decisioni arrivano sul telefono"
                          + (", e puoi scrivergli e approvare con i pulsanti." if s.telegram_commands else ".")) if ok else
                         "il token del bot non è valido.", "" if ok else "Ricrea il token con @BotFather."))
    else:
        out.append(Check("Notifiche Telegram", "optional", "non configurate: il briefing resta nel pannello di NODO.",
                         "Crea un bot con @BotFather, poi imposta NODO_TELEGRAM_BOT_TOKEN e NODO_TELEGRAM_CHAT_ID "
                         "(vedi docs/ACCESSI.md)."))
    # --- browser checks
    has_pw = importlib.util.find_spec("playwright") is not None
    out.append(Check("Controlli con browser", "ok" if has_pw else "optional",
                     "Playwright installato: vedo anche gli errori JavaScript." if has_pw else
                     "Playwright non installato: controllo le pagine via HTTP (niente errori JavaScript).",
                     "" if has_pw else "pip install playwright  e poi  python -m playwright install chromium"))
    # --- previews
    out.append(Check("Anteprime protette", "ok" if s.vercel_protection_bypass else "optional",
                     "posso testare le anteprime protette." if s.vercel_protection_bypass else
                     "se le anteprime Vercel sono protette da login non potrò testarle da solo.",
                     "" if s.vercel_protection_bypass else "Vercel → progetto → Settings → Deployment Protection → "
                                                           "Protection Bypass for Automation → NODO_VERCEL_PROTECTION_BYPASS."))
    # --- time zone
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(s.timezone)
        out.append(Check("Fuso orario", "ok", f"{s.timezone}: turno {s.night_start}-{s.night_end}, briefing "
                                              f"alle {s.briefing_time}."))
    except Exception:
        out.append(Check("Fuso orario", "error", f"fuso {s.timezone} non disponibile.",
                         "Su Windows: pip install tzdata"))
    out.append(Check("Turno di notte", "ok" if s.night_enabled else "warning",
                     "attivo: parte da solo finché NODO è acceso." if s.night_enabled else
                     "disattivato (NODO_NIGHT_ENABLED=false).", "" if s.night_enabled else "NODO_NIGHT_ENABLED=true"))
    return out


async def _telegram_ok(token: str | None) -> bool:
    if not token:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"https://api.telegram.org/bot{token}/getMe")
        return r.status_code == 200 and r.json().get("ok") is True
    except httpx.HTTPError:
        return False


def as_dicts(checks: list[Check]) -> list[dict]:
    return [asdict(x) for x in checks]


def main() -> int:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # la console Windows non sa stampare →
    from nodo.app import build_container
    c = build_container()
    checks = asyncio.run(run_checks(c))
    icon = {"ok": "[OK]  ", "missing": "[--]  ", "error": "[ERR] ", "optional": "[opz] ", "warning": "[!]   "}
    print("\nNODO doctor — accessi per il turno di notte\n")
    for x in checks:
        print(f"{icon.get(x.status, '      ')}{x.name}: {x.detail}")
        if x.how:
            print(f"        come fare: {x.how}")
    bad = [x for x in checks if x.status in ("missing", "error")]
    print("\n" + ("Tutto pronto." if not bad else f"{len(bad)} cose da sistemare (vedi sopra e docs/ACCESSI.md)."))
    return 1 if any(x.status == "error" for x in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
