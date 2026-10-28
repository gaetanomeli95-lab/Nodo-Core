"""Which tools the brain sees for THIS request.

Sending all 64 tools costs ~25.000 characters on every model call (more than the persona and memory together):
seconds of waiting on every answer. The request and the last few lines of the conversation decide which groups
matter; the basics (memory, board, screen, reminders, decisions, time) are always there."""
from __future__ import annotations

import re

ALWAYS = {"work_status", "now", "remember", "recall", "forget", "search_conversations", "search_knowledge", "board_pin",
          "board_done", "board_list", "show_on_screen", "close_screen", "set_reminder", "list_reminders",
          "cancel_reminder", "decide", "pending_decisions", "notify_me", "web_search"}

GROUPS: list[tuple[re.Pattern, set[str]]] = [
    (re.compile(r"\d|euro|€|vend|segn|regist|client|materass|ret[ei]\b|cuscin|sospes|consegn|spes[ae]|incass|prezz|"
                r"listin|credit|deve|debit|pagat|ritir|sostitu|ordin|arianna|andromeda|morph|morf|softcomfort|"
                r"negozio|giornata|chiusura|conti|uscit|entrat|cancella|correggi|sbagliat|scontr|accont|capar|saldat|"
                r"anticip|bonific|bancomat|contanti|scheda|ha preso|comprat", re.I),
     {"ledger_add", "ledger_update", "ledger_report", "ledger_prices", "ledger_close_day", "ledger_stats",
      "ledger_open", "ledger_pay", "ledger_customer", "ledger_pdf"}),
    (re.compile(r"\bsit[oi]\b|vercel|github|deploy|domini|ssl|certificat|codice|notte|url|http|www|pagina|online|"
                r"\bnodo\b|aggiornament|errore del sito|pb.?care|careprogram", re.I),
     {"night_report", "sites_status", "check_sites", "sync_sites", "request_site_change", "list_site_files",
      "read_site_file", "site_commits", "site_deployments", "check_url", "fetch_url", "ssl_status", "start_work",
      "nodo_updates", "nodo_health"}),
    (re.compile(r"progett|attivit|task|client[ei]|agenda|calendari|appuntament|da fare|priorit|impegn|domani|"
                r"settimana", re.I),
     {"projects", "today_agenda", "create_task", "complete_task", "list_tasks", "update_task", "clients",
      "create_client", "create_project", "update_project", "calendar_events"}),
    (re.compile(r"file|pdf|word|excel|document|immagin|foto|bozz|desktop|apri|mostr|crea|scriv|letter|present|"
                r"disegn|salva|stampa|tabella|preventiv|fattur|volantin|logo|sito|pagina|html|landing|bozza", re.I),
     {"create_file", "generate_image", "create_document", "ledger_pdf", "create_website", "save_draft", "list_drafts", "update_draft",
      "list_files", "read_file", "save_to_desktop", "open_on_pc", "read_photo"}),
    (re.compile(r"\bpc\b|computer|\bdisco\b|cartell|desktop|programm|analizz|esplor|scansion|cosa faccio|"
                r"cosa c'?è (sul|nel|dentro)", re.I),
     {"pc_overview", "show_on_screen", "open_on_pc", "save_to_desktop"}),
    (re.compile(r"chatgpt|\bgpt\b|claude|chat\b|conversazion|impara|vecchi|studi|stanotte|notte|lezion|miglior", re.I),
     {"read_knowledge", "learn_old_chats", "show_transcript", "study_status"}),
    (re.compile(r"ultimi \d+ minuti|minuti|detto prima|ci siamo detti|trascri", re.I), {"show_transcript"}),
]


def pick(names: list[str], text: str) -> set[str]:
    """The tool names to offer for `text` (the request plus a little recent conversation)."""
    keep = set(ALWAYS)
    for rx, group in GROUPS:
        if rx.search(text or ""):
            keep |= group
    return {n for n in names if n in keep}
