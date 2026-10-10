"""Models cooperating: ChatGPT subscription (Codex CLI), pause on usage limits, second opinion on code."""
from __future__ import annotations

import json
import stat
import sys
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select

from nodo.app import build_container
from nodo.config import Settings
from nodo.db.models import FixProposal, NightAction, Site
from nodo.night.notify import FakeNotifier
from nodo.night.simulation import DEMO_OWNER
from nodo.providers.base import ChatChunk, ChatMessage, FunctionSpec, ModelDescriptor, ProviderError, collect
from nodo.providers.codex_cli import CodexCliProvider
from nodo.providers.deterministic import FakeModelProvider
from nodo.providers.router import ModelRouter, RouteRequest, cooldowns

# ------------------------------------------------------------------------------------------------ ChatGPT via Codex
FAKE_CODEX = r'''#!{python}
import json, os, sys
args = sys.argv[1:]
if args[:2] == ["login", "status"]:
    print("Logged in using ChatGPT")
    sys.exit(0)
if "--ephemeral" in args:
    print("error: unexpected argument '--ephemeral' found", file=sys.stderr)
    sys.exit(2)
out = args[args.index("--output-last-message") + 1]
prompt = sys.stdin.read()
with open(os.environ["FAKE_CODEX_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({{"args": args, "prompt": prompt, "key": os.environ.get("OPENAI_API_KEY")}}) + "\n")
if "[RISULTATO sites_status]" in prompt:
    result = json.dumps({{"say": "Tutto ok, 3 siti regolari.", "calls": []}})
elif "## Strumenti" in prompt:
    result = json.dumps({{"say": "Un attimo.", "calls": [{{"name": "sites_status", "arguments": {{}}}}]}})
else:
    result = "Ciao Gaetano."
open(out, "w", encoding="utf-8").write(result)
print("tokens used: 123")
'''


@pytest.fixture
def fake_codex(tmp_path, monkeypatch):
    exe = tmp_path / "codex"
    exe.write_text(FAKE_CODEX.format(python=sys.executable), encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "codex.jsonl"
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-be-used")
    return str(exe), log


async def test_chatgpt_subscription_provider(fake_codex):
    exe, log = fake_codex
    p = CodexCliProvider(executable=exe)
    assert await p.available()
    tools = [FunctionSpec("sites_status", "stato dei siti", {"type": "object", "properties": {}})]
    turn = await p.complete_tools("chatgpt", [{"role": "system", "content": "Sei NODO."},
                                              {"role": "user", "content": "come stanno i siti?"}], tools)
    assert turn.text == "Un attimo." and [c.name for c in turn.tool_calls] == ["sites_status"]
    turn2 = await p.complete_tools("chatgpt", [
        {"role": "system", "content": "Sei NODO."}, {"role": "user", "content": "come stanno i siti?"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "x", "name": "sites_status", "arguments": {}}]},
        {"role": "tool", "tool_call_id": "x", "name": "sites_status", "content": "{}"}], tools)
    assert turn2.text.startswith("Tutto ok") and not turn2.tool_calls
    text = (await collect(p.stream("chatgpt", [ChatMessage("user", "ciao")]))).text
    assert text == "Ciao Gaetano."
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert all(c["key"] is None for c in calls)                       # ChatGPT login, never the API key
    assert all("--ephemeral" not in c["args"] for c in calls)         # unknown flag dropped automatically
    assert all(c["args"][c["args"].index("--sandbox") + 1] == "read-only" for c in calls)
    assert all(c["args"][-1] == "-" for c in calls)                   # prompt from stdin
    assert "Non eseguire comandi" in calls[0]["prompt"] and "[GAETANO]" in calls[0]["prompt"]


async def test_chatgpt_provider_missing():
    p = CodexCliProvider()
    p.executable = None
    assert not await p.available()
    with pytest.raises(ProviderError):
        await p.complete_tools("chatgpt", [{"role": "user", "content": "x"}], [])


# ------------------------------------------------------------------------------------------------ pause on limits
class Limited:
    def __init__(self, name: str, error: str | None):
        self.name, self.error, self.calls = name, error, 0

    def models(self):
        return [ModelDescriptor(provider=self.name, model="m", reasoning=5, coding=5, tool_calling=True,
                                tier="free", latency_class="low" if self.error else "high")]

    async def available(self):
        return True

    async def stream(self, model, messages, *, temperature=0.3, max_tokens=800) -> AsyncIterator[ChatChunk]:
        self.calls += 1
        if self.error:
            raise ProviderError(self.error)
        yield ChatChunk(delta=f"risposta da {self.name}")
        yield ChatChunk(done=True)


async def test_router_pauses_a_model_that_hit_its_limit():
    first = Limited("primo", "You've hit your usage limit · resets Oct 12, 3pm")
    second = Limited("secondo", None)
    router = ModelRouter([first, second], cooldown_minutes=30)
    out = await collect(router.stream([ChatMessage("user", "ciao")], RouteRequest()))
    assert out.text == "risposta da secondo" and first.calls == 1
    assert "primo" in cooldowns()
    await collect(router.stream([ChatMessage("user", "ancora")], RouteRequest()))
    assert first.calls == 1                       # skipped while paused: no time wasted
    other = Limited("altro", "connection reset by peer")   # a normal error does not pause
    await collect(ModelRouter([other, second]).stream([ChatMessage("user", "x")], RouteRequest()))
    assert "altro" not in cooldowns()


# ------------------------------------------------------------------------------------------------ second opinion
class Reviewer:
    """A second model that only reviews: answers [NODO_REVIEW_REQUEST] with a fixed verdict."""

    name = "revisore"

    def __init__(self, verdict: str, note: str = "nota del revisore"):
        self.verdict, self.note, self.seen = verdict, note, []

    def models(self):
        return [ModelDescriptor(provider=self.name, model="r1", reasoning=5, coding=5, tier="free",
                                latency_class="high")]

    async def available(self):
        return True

    async def stream(self, model, messages, *, temperature=0.3, max_tokens=800) -> AsyncIterator[ChatChunk]:
        prompt = "\n".join(m.content for m in messages)
        self.seen.append(prompt)
        text = json.dumps({"verdict": self.verdict, "note": self.note}) if "[NODO_REVIEW_REQUEST]" in prompt else "{}"
        yield ChatChunk(delta=text)
        yield ChatChunk(done=True)


def coop_container(world, reviewer=None, settings=None):
    providers = [world.model] + ([reviewer] if reviewer else []) + [FakeModelProvider()]
    c = build_container(settings or Settings(), providers=providers, vercel=world.vercel, repo=world.github,
                        prober=world.prober, notifier=FakeNotifier())
    c.night_verify_delay = c.night_retry_delay = c.night_poll_interval = 0
    return c


async def broken_night(db, c, world):
    shift = c.night_shift(db)
    await shift.discover()
    for s in db.scalars(select(Site)):
        if s.name == "pbcare-platform":
            s.key_pages = [{"path": "/contatti", "name": "Contatti"}]
    db.flush()
    await shift.run_pass("light", "test")
    world.break_things()
    await c.night_shift(db).run_pass("night", "test")


def pr_body(world, fix: FixProposal) -> str:
    return world.github.prs[(f"{DEMO_OWNER}/pbcare-platform", fix.pr_number)]["body"]


async def test_review_ok_goes_in_the_pr(db, seeded, world):
    rv = Reviewer("ok", "la correzione è corretta")
    c = coop_container(world, rv)
    await broken_night(db, c, world)
    fix = db.scalars(select(FixProposal)).one()
    assert fix.model.startswith("simulated") and rv.seen and "DIFF PROPOSTO" in rv.seen[0]
    assert "Secondo controllo (revisore:r1):** ok" in pr_body(world, fix)
    assert fix.confidence == "high"


async def test_review_doubts_lower_confidence_and_reach_you(db, seeded, world):
    c = coop_container(world, Reviewer("dubbi", "manca il controllo sul caso vuoto in un altro punto"))
    await broken_night(db, c, world)
    fix = db.scalars(select(FixProposal)).one()
    assert fix.confidence == "low" and "Secondo parere (revisore:r1)" in fix.diagnosis
    assert "DUBBI" in pr_body(world, fix)
    assert any("Secondo parere" in m for m in c.notifier.sent)       # the phone shows the doubt too


async def test_review_blocks_the_pr(db, seeded, world):
    c = coop_container(world, Reviewer("bloccante", "cancella metà della pagina"))
    await broken_night(db, c, world)
    assert db.scalars(select(FixProposal)).all() == []
    act = db.scalars(select(NightAction).where(NightAction.action == "fix_pr")).one()
    assert act.status == "skipped" and "cancella metà della pagina" in act.summary
    assert not world.github.prs


async def test_no_second_model_means_review_skipped(db, seeded, world):
    c = coop_container(world)
    await broken_night(db, c, world)
    fix = db.scalars(select(FixProposal)).one()
    assert "non eseguito" in pr_body(world, fix)


async def test_review_can_be_turned_off(db, seeded, world):
    rv = Reviewer("bloccante")
    c = coop_container(world, rv, Settings(code_review=False))
    await broken_night(db, c, world)
    assert db.scalars(select(FixProposal)).one() and not rv.seen
