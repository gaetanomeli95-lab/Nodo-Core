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

from nodo.providers.router import ModelRouter, RouteRequest


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
                         "Imposta NODO_ANTHROPIC_API_KEY (consigliato) oppure NODO_OPENAI_API_KEY."))
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
