"""Function calling for models reached through a command-line program (Claude Code, Codex): the tool catalog
goes in the prompt and the model answers with one JSON object {"say": "...", "calls": [...]}, parsed leniently."""
from __future__ import annotations

import json
import re

TOOL_PROTOCOL = """

## Strumenti
Puoi usare questi strumenti (nome: descrizione | parametri JSON-schema):
{catalog}

## Formato della risposta (obbligatorio)
Rispondi SEMPRE e SOLO con un oggetto JSON valido, senza testo prima o dopo e senza blocchi ```:
{{"say": "testo per Gaetano (vuoto se prima devi usare strumenti)", "calls": [{{"name": "nome_strumento", "arguments": {{...}}}}]}}
- Se ti servono dati o devi fare un'azione: metti le chiamate in "calls" (anche più di una) e lascia "say" vuoto o
  al massimo una frase brevissima ("Un attimo, controllo."): viene detta subito, quindi dopo non ripeterla.
- Quando hai tutto quello che serve: "calls": [] e in "say" la risposta finale.
- I risultati degli strumenti ti arrivano nei messaggi successivi come [RISULTATO nome]."""


def _render_transcript(messages: list[dict]) -> str:
    out = []
    for m in messages:
        role = m["role"]
        if role == "system":
            continue
        if role == "user":
            out.append(f"[GAETANO]\n{m['content']}")
        elif role == "assistant":
            if m.get("content"):
                out.append(f"[NODO]\n{m['content']}")
            for c in m.get("tool_calls") or []:
                out.append(f"[CHIAMATA {c['name']}] {json.dumps(c['arguments'], ensure_ascii=False)}")
        elif role == "tool":
            out.append(f"[RISULTATO {m.get('name', 'strumento')}]\n{m['content']}")
    out.append("[ORA TOCCA A TE: rispondi con il JSON richiesto]")
    return "\n\n".join(out)


def _parse(text: str) -> tuple[str, list[tuple[str, dict]]]:
    """Lenient parse of the JSON protocol. Plain prose (no JSON) is taken as the final answer."""
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        data = json.loads(t[t.index("{"):t.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        return text.strip(), []
    if not isinstance(data, dict):
        return text.strip(), []
    calls: list[tuple[str, dict]] = []
    for c in data.get("calls") or data.get("tool_calls") or []:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        args = next((c[k] for k in ("arguments", "input", "args", "parameters") if isinstance(c.get(k), dict)), {})
        calls.append((str(c["name"]), args))
    say = data.get("say")
    if say is None:
        say = data.get("text") or data.get("message") or ""
    return str(say), calls


def catalog_text(tools) -> str:
    return "\n".join(f"- {t.name}: {t.description} | {json.dumps(t.parameters, ensure_ascii=False)}" for t in tools)
