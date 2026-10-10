"""Brain: conversation with tools, guarded decisions, site changes in background, Claude subscription provider."""
from __future__ import annotations

import json
import os
import re
import stat
import sys
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select

from nodo.app import build_container
from nodo.brain import background as BG
from nodo.brain.tools import Toolbox, is_no, is_yes
from nodo.config import Settings
from nodo.db.models import Approval, Conversation, FixProposal, Message, Site, Task
from nodo.night.notify import FakeNotifier
from nodo.night.simulation import DEMO_OWNER
from nodo.providers.base import ChatChunk, FunctionSpec, ModelDescriptor, ProviderError, ToolCall, ToolTurn
from nodo.providers.claude_cli import ClaudeCodeProvider, _parse
from nodo.providers.deterministic import FakeModelProvider


# ------------------------------------------------------------------------------------------------ a scripted brain
class ScriptedBrain:
    """Plays the model: decides which tool to call from the user's words, then answers with the results."""

    name = "scripted"

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.seen: list[list[dict]] = []

    def models(self) -> list[ModelDescriptor]:
        return [ModelDescriptor(provider=self.name, model="brain-1", reasoning=3, coding=0, tool_calling=True,
                                is_local=True, tier="local", latency_class="low")]

    async def available(self) -> bool:
        return True

    async def stream(self, model, messages, *, temperature=0.3, max_tokens=800) -> AsyncIterator[ChatChunk]:
        yield ChatChunk(delta="{}")
        yield ChatChunk(done=True)

    async def complete_tools(self, model, messages, tools, *, temperature=0.4, max_tokens=1200) -> ToolTurn:
        if self.fail:
            raise ProviderError("modello spento")
        self.seen.append(messages)
        last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
        user = messages[last_user]["content"].lower()
        results = [m for m in messages[last_user:] if m["role"] == "tool"]
        if results:
            return ToolTurn("Ecco: " + " | ".join(r["content"] for r in results))
        system = messages[0]["content"]
        presented = re.findall(r"([0-9a-f-]{36}): ", system)

        def call(name, **args):
            return ToolTurn("", [ToolCall(f"c{len(self.seen)}", name, args)])
        if "come stanno" in user:
            return call("sites_status")
        if "stanotte" in user:
            return call("night_report")
        if "cambia" in user:
            return call("request_site_change", site="prosperya", instruction=messages[last_user]["content"])
        if "ricordati" in user:
            return call("remember", fact="Preferisce i report brevi")
        if presented and ("pubblica" in user or "pensi" in user or user.startswith("sì")):
            return call("decide", approval_id=presented[0], approve=True)
        if "decidi" in user:  # tries an approval that was never presented
            a = user.split()[-1]
            return call("decide", approval_id=a, approve=True)
        return ToolTurn("Dimmi pure.")


def brain_container(world, brain: ScriptedBrain | None = None, settings: Settings | None = None):
    c = build_container(settings or Settings(), providers=[brain or ScriptedBrain(), world.model, FakeModelProvider()],
                        vercel=world.vercel, repo=world.github, prober=world.prober, notifier=FakeNotifier())
    c.night_verify_delay = c.night_retry_delay = c.night_poll_interval = 0
    return c


async def setup_night(db, c, world, broken: bool = True):
    shift = c.night_shift(db)
    await shift.discover()
    for s in db.scalars(select(Site)):
        if s.name == "pbcare-platform":
            s.key_pages = [{"path": "/contatti", "name": "Contatti"}]
    db.flush()
    await shift.run_pass("light", "test")
    if broken:
        world.break_things()
        await c.night_shift(db).run_pass("night", "test")


async def talk(core, text, conv_id=None):
    events = [e async for e in core.handle(text, conv_id)]
    assert events[-1]["type"] == "done", events[-1]
    return events


# ------------------------------------------------------------------------------------------------ conversation
async def test_brain_uses_tools_and_answers(db, seeded, world):
    c = brain_container(world)
    await setup_night(db, c, world, broken=False)
    core = c.core(db)
    events = await talk(core, "Nodo, come stanno i siti?")
    done = events[-1]
    assert done["intent"] == "conversation" and done["provider"] == "scripted"
    assert "prosperya-crm" in done["text"] and "cf-materassi-sito" in done["text"]
    steps = [e["step"] for e in events if e["type"] == "step"]
    assert "tool:sites_status" in steps
    assert [e for e in events if e["type"] == "sentence"]
    # the conversation is remembered: the second turn sees the first one
    await talk(core, "ok grazie", done["conversation_id"])
    history = c.providers[0].seen[-1]
    assert any(m["role"] == "assistant" and "prosperya-crm" in m["content"] for m in history)


async def test_decisions_need_presentation_and_an_explicit_yes(db, seeded, world):
    c = brain_container(world)
    await setup_night(db, c, world)
    core = c.core(db)
    fix = db.scalars(select(FixProposal)).one()
    # 1) an approval never presented in this conversation cannot be decided
    ev = await talk(core, f"decidi {fix.approval_id}")
    assert "non l'hai ancora presentata" in ev[-1]["text"]
    assert db.get(Approval, fix.approval_id).status == "pending"
    # 2) presented through the night report (and bound for a bare spoken "sì")
    ev = await talk(core, "cosa hai fatto stanotte?", ev[-1]["conversation_id"])
    conv_id = ev[-1]["conversation_id"]
    bound = [e for e in ev if e["type"] == "approval.requested"]
    assert bound and bound[0]["approval_id"] == fix.approval_id
    assert fix.approval_id in db.get(Conversation, conv_id).active_context["presented_approvals"]
    # 3) the model tries to approve, but Gaetano did not say yes: refused in code
    ev = await talk(core, "che ne pensi di questa correzione?", conv_id)
    assert "sì esplicito" in ev[-1]["text"] and db.get(Approval, fix.approval_id).status == "pending"
    # 4) explicit yes: executed (merge), then verified in background
    ev = await talk(core, "sì, pubblicala pure", conv_id)
    assert "approvata" in ev[-1]["text"]
    assert db.get(Approval, fix.approval_id).status == "approved" and fix.status == "merged"
    db.commit()
    await BG.drain()


async def test_site_change_in_background_then_publish_with_yes(db, seeded, world):
    c = brain_container(world)
    await setup_night(db, c, world, broken=False)
    core = c.core(db)
    ev = await talk(core, "Sul sito di prosperya cambia il titolo 'CRM' in 'Gestionale Clienti'")
    conv_id = ev[-1]["conversation_id"]
    assert '"started": true' in ev[-1]["text"]
    db.commit()
    await BG.drain()
    db.expire_all()
    prop = db.scalars(select(FixProposal)).one()
    assert prop.incident_id is None and prop.branch.startswith("nodo/change-prosperya")
    assert prop.pr_number and prop.preview_status == "ok" and prop.approval_id
    repo = world.github.repos[f"{DEMO_OWNER}/prosperya-crm"]
    head = repo["branches"][prop.branch]
    assert "Gestionale Clienti" in repo["commits"][head]["files"]["src/app/page.tsx"]
    assert db.get(Approval, prop.approval_id).status == "pending"   # nothing published without you
    msgs = db.scalars(select(Message).where(Message.conversation_id == conv_id, Message.role == "nodo")).all()
    assert any("La pubblico?" in m.content for m in msgs)
    assert any("modifica" in m for m in c.notifier.sent) and any(c.notifier.buttons)   # phone: Pubblica/Rifiuta
    # "sì" in the same conversation publishes exactly that change
    ev = await talk(core, "sì, pubblica", conv_id)
    db.expire_all()
    assert db.get(Approval, prop.approval_id).status == "approved"
    assert db.get(FixProposal, prop.id).status == "merged"
    db.commit()
    await BG.drain()


async def test_brain_falls_back_to_rules_when_model_fails(db, seeded, world):
    c = brain_container(world, ScriptedBrain(fail=True))
    await setup_night(db, c, world, broken=False)
    out = await c.core(db).run_once("come stanno i siti?")
    assert out.intent == "sites_status" and "prosperya-crm" in out.text


async def test_brain_can_be_switched_off(db, seeded, world):
    c = brain_container(world, settings=Settings(brain="off"))
    await setup_night(db, c, world, broken=False)
    out = await c.core(db).run_once("come stanno i siti?")
    assert out.intent == "sites_status"


async def test_toolbox_tasks_memory_and_drafts(db, seeded, world):
    c = brain_container(world)
    core = c.core(db)
    conv = Conversation(organization_id=core.org, channel="text", active_context={})
    db.add(conv)
    db.flush()
    box = Toolbox(core, conv, "r1", "segnati di chiamare il commercialista")
    out = await box.call("create_task", {"title": "Chiamare il commercialista", "due_date": "2026-10-12",
                                         "priority": 2})
    assert out["created"] and db.scalars(select(Task).where(Task.title == "Chiamare il commercialista")).one()
    assert (await box.call("complete_task", {"task": "commercialista"}))["done"]
    assert (await box.call("remember", {"fact": "Gaetano preferisce i report brevi"}))["saved"]
    rec = await box.call("recall", {"query": "report brevi"})
    assert rec["results"] and "brevi" in rec["results"][0]["fact"]
    assert (await box.call("save_draft", {"title": "Post lancio", "body": "Testo"}))["saved"]
    assert "error" in await box.call("nope", {})
    assert "parametri non validi" in (await box.call("create_task", {"wrong": 1}))["error"]
    proj = await box.call("projects", {})
    assert proj["projects"]


def test_yes_no_words():
    for t in ("sì", "si vai", "ok pubblicala", "vai, fallo", "applica pure", "Sì, procedi"):
        assert is_yes(t), t
    for t in ("no", "come si pubblica?", "non pubblicarla", "aspetta", "dopo", "sì ma aspetta"):
        assert not is_yes(t), t
    assert is_no("no, lascia stare") and is_no("rifiutala") and not is_no("vai")


# ------------------------------------------------------------------------------------------------ Claude subscription
FAKE_CLAUDE = r'''#!{python}
import json, os, sys
args = sys.argv[1:]
if args[:2] == ["auth", "status"]:
    print(json.dumps({{"loggedIn": True, "authMethod": "claude.ai"}}))
    sys.exit(0)
if "--no-session-persistence" in args:
    print("error: unknown option '--no-session-persistence'", file=sys.stderr)
    sys.exit(1)
system = open(args[args.index("--system-prompt-file") + 1], encoding="utf-8").read()
prompt = sys.stdin.read()
with open(os.environ["FAKE_CLAUDE_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({{"args": args, "system": system, "prompt": prompt, "key": os.environ.get("ANTHROPIC_API_KEY")}}) + "\n")
if "[RISULTATO" in prompt:
    result = json.dumps({{"say": "Tutto regolare su 3 siti.", "calls": []}})
elif "--tools" in args and "## Strumenti" in system:
    result = "```json\n" + json.dumps({{"say": "", "calls": [{{"name": "sites_status", "input": {{"site": "pb"}}}}]}}) + "\n```"
else:
    result = "Ciao Gaetano."
print(json.dumps({{"type": "result", "is_error": False, "result": result,
                   "usage": {{"input_tokens": 10, "cache_read_input_tokens": 90, "output_tokens": 7}}}}))
'''


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    exe = tmp_path / "claude"
    exe.write_text(FAKE_CLAUDE.format(python=sys.executable), encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-be-used")
    return str(exe), log


async def test_claude_subscription_provider(fake_claude):
    exe, log = fake_claude
    p = ClaudeCodeProvider("sonnet", executable=exe)
    assert await p.available()
    tools = [FunctionSpec("sites_status", "stato dei siti", {"type": "object", "properties": {}})]
    turn = await p.complete_tools("sonnet", [{"role": "system", "content": "Sei NODO."},
                                             {"role": "user", "content": "come stanno i siti?"}], tools)
    assert [(c.name, c.arguments) for c in turn.tool_calls] == [("sites_status", {"site": "pb"})]
    assert turn.tokens_in == 100 and turn.tokens_out == 7
    turn2 = await p.complete_tools("sonnet", [
        {"role": "system", "content": "Sei NODO."}, {"role": "user", "content": "come stanno i siti?"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "x", "name": "sites_status", "arguments": {}}]},
        {"role": "tool", "tool_call_id": "x", "name": "sites_status", "content": "{\"sites\": []}"}], tools)
    assert turn2.text == "Tutto regolare su 3 siti." and not turn2.tool_calls
    chunks = [ch async for ch in p.stream("sonnet", [])]
    assert chunks[0].delta == "Ciao Gaetano." and chunks[-1].done
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert all(c["key"] is None for c in calls)                       # subscription login, never the API key
    assert all("--no-session-persistence" not in c["args"] for c in calls)   # unknown flag dropped automatically
    assert calls[0]["args"][calls[0]["args"].index("--tools") + 1] == ""      # Claude Code tools all disabled
    assert "sites_status" in calls[0]["system"] and "[GAETANO]" in calls[0]["prompt"]
    assert "[RISULTATO sites_status]" in calls[1]["prompt"]
    assert not os.listdir(p._cwd) or all(not f.startswith("nodo-sp-") for f in os.listdir(p._cwd))


def test_claude_protocol_parsing():
    assert _parse('{"say": "ok", "calls": [{"name": "a", "arguments": {"x": 1}}]}') == ("ok", [("a", {"x": 1})])
    assert _parse('```json\n{"say": "", "calls": [{"name": "a", "input": {"y": 2}}]}\n```') == ("", [("a", {"y": 2})])
    assert _parse("Solo testo, niente JSON.") == ("Solo testo, niente JSON.", [])
    assert _parse('{"say": "x", "calls": [{"nome": "a"}, "boh"]}') == ("x", [])


async def test_claude_provider_missing_or_logged_out(tmp_path):
    p = ClaudeCodeProvider(executable=None)
    p.executable = None
    assert not await p.available()
    with pytest.raises(ProviderError):
        await p.complete_tools("sonnet", [{"role": "user", "content": "x"}], [])


async def test_doctor_reports_the_brain(world):
    from nodo.doctor import run_checks
    checks = {c.name: c for c in await run_checks(brain_container(world))}
    assert checks["Cervello"].status == "ok" and "scripted" in checks["Cervello"].detail
