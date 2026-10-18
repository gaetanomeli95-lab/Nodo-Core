import pytest

from nodo.config import NodoMode
from nodo.db.models import Sensitivity
from nodo.providers.base import ChatMessage, ModelDescriptor, collect
from nodo.providers.deterministic import DeterministicProvider
from nodo.providers.router import ModelRouter, NoEligibleModel, RouteRequest


class PaidProvider:
    name = "paid"

    def models(self):
        return [ModelDescriptor("paid", "big", reasoning=5, coding=5, cost_in_per_1k=0.01, cost_out_per_1k=0.03,
                                tier="paid", max_sensitivity=Sensitivity.CONFIDENTIAL)]

    async def available(self):
        return True

    async def stream(self, model, messages, **kw):
        from nodo.providers.base import ChatChunk
        yield ChatChunk(delta="paid answer")
        yield ChatChunk(done=True, tokens_in=10, tokens_out=2)


class FreeCloud:
    name = "freecloud"

    def models(self):
        return [ModelDescriptor("freecloud", "llm", reasoning=3, coding=3, tier="free",
                                max_sensitivity=Sensitivity.INTERNAL)]

    async def available(self):
        return True

    async def stream(self, model, messages, **kw):
        from nodo.providers.base import ProviderError
        raise ProviderError("quota exhausted")
        yield  # pragma: no cover


def test_free_mode_never_selects_paid():
    r = ModelRouter([PaidProvider(), FreeCloud(), DeterministicProvider()], NodoMode.FREE)
    chosen = r.select(RouteRequest())
    assert chosen.descriptor.provider == "freecloud"
    assert all(not c.descriptor.is_paid for c in r.eligible(RouteRequest()))


def test_free_mode_explicit_override_allows_paid():
    r = ModelRouter([PaidProvider(), DeterministicProvider()], NodoMode.FREE, allow_paid_in_free=True)
    assert r.select(RouteRequest(min_reasoning=5)).descriptor.provider == "paid"


def test_sensitivity_blocks_cloud_provider():
    r = ModelRouter([FreeCloud(), DeterministicProvider()], NodoMode.FREE)
    chosen = r.select(RouteRequest(sensitivity=Sensitivity.CLIENT_PRIVATE))
    assert chosen.descriptor.provider == "deterministic"


def test_private_mode_local_only():
    r = ModelRouter([PaidProvider(), FreeCloud(), DeterministicProvider()], NodoMode.PRIVATE)
    assert all(c.descriptor.is_local for c in r.eligible(RouteRequest()))


def test_performance_mode_prefers_capability():
    r = ModelRouter([PaidProvider(), FreeCloud()], NodoMode.PERFORMANCE)
    assert r.select(RouteRequest()).descriptor.provider == "paid"


class SlowSubscription:
    """A smart but slow brain (subscription CLI): wins on quality, loses on speed."""
    name = "slowsub"

    def models(self):
        return [ModelDescriptor("slowsub", "big", reasoning=5, coding=5, tier="free", latency_class="high",
                                max_sensitivity=Sensitivity.CONFIDENTIAL)]

    async def available(self):
        return True


class FastCloud:
    """A faster, slightly weaker API model (e.g. Groq/Gemini free tier)."""
    name = "fastcloud"

    def models(self):
        return [ModelDescriptor("fastcloud", "small", reasoning=3, coding=3, tier="free", latency_class="low",
                                max_sensitivity=Sensitivity.INTERNAL)]

    async def available(self):
        return True


def test_prefer_fast_puts_quick_models_ahead():
    r = ModelRouter([SlowSubscription(), FastCloud()], NodoMode.FREE)
    # a written question still goes to the smartest brain
    assert r.select(RouteRequest(min_reasoning=3)).descriptor.provider == "slowsub"
    # a spoken question goes to the fastest one, so the reply arrives in seconds
    assert r.select(RouteRequest(min_reasoning=3, prefer_fast=True)).descriptor.provider == "fastcloud"


def test_no_eligible_model_raises():
    r = ModelRouter([FreeCloud()], NodoMode.FREE)
    with pytest.raises(NoEligibleModel):
        r.select(RouteRequest(sensitivity=Sensitivity.HIGHLY_SENSITIVE))


async def test_fallback_on_provider_error_is_recorded():
    records = []
    r = ModelRouter([FreeCloud(), DeterministicProvider()], NodoMode.FREE, recorder=records.append)
    msgs = [ChatMessage("user", "[GROUNDED_SUMMARY]\nciao\n[/GROUNDED_SUMMARY]")]
    res = await collect(r.stream(msgs, RouteRequest(), request_id="req"))
    assert res.text == "ciao"
    assert [x.status for x in records] == ["failed", "ok"]
    assert records[1].provider == "deterministic" and records[1].estimated_cost == 0


async def test_openai_compat_passes_reasoning_effort_to_thinking_models():
    """Gemini 3.x bills hidden thinking inside max_tokens: without `reasoning_effort` the answer came back empty
    with finish_reason=length (verified on the real API). The provider must send it in both code paths."""
    import json

    import httpx

    from nodo.providers.base import FunctionSpec
    from nodo.providers.openai_compat import OpenAICompatProvider, gemini_descriptors

    bodies: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        if bodies[-1].get("stream"):
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"[]"}}]}\n\ndata: [DONE]\n\n')
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {}})

    p = OpenAICompatProvider("gemini", "https://example.test/v1", "k", gemini_descriptors("gemini-3.5-flash"),
                             transport=httpx.MockTransport(handle), reasoning_effort="none")
    assert (await collect(p.stream("gemini-3.5-flash", [ChatMessage("user", "ciao")]))).text == "[]"
    await p.complete_tools("gemini-3.5-flash", [{"role": "user", "content": "ciao"}],
                           [FunctionSpec("f", "d", {"type": "object", "properties": {}})])
    assert [b["reasoning_effort"] for b in bodies] == ["none", "none"]

    plain = OpenAICompatProvider("groq", "https://example.test/v1", "k", gemini_descriptors("x"),
                                 transport=httpx.MockTransport(handle))
    await plain.complete_tools("x", [{"role": "user", "content": "ciao"}], [])
    assert "reasoning_effort" not in bodies[-1]


async def test_openai_compat_echoes_gemini_thought_signatures_on_tool_calls():
    """Gemini 3.x returns `tool_calls[i].extra_content.google.thought_signature` and answers HTTP 400 on the next
    turn if it is not sent back with that call (seen live: 'Function call is missing a thought_signature')."""
    import json

    import httpx

    from nodo.providers.base import FunctionSpec
    from nodo.providers.openai_compat import OpenAICompatProvider, gemini_descriptors

    bodies: list[dict] = []
    sig = {"google": {"thought_signature": "abc"}}

    def handle(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"tool_calls": [
            {"id": "call_1", "type": "function", "extra_content": sig,
             "function": {"name": "clock", "arguments": "{\"city\": \"Palermo\"}"}}]}}], "usage": {}})

    p = OpenAICompatProvider("gemini", "https://example.test/v1", "k", gemini_descriptors("gemini-3.5-flash"),
                             transport=httpx.MockTransport(handle))
    spec = [FunctionSpec("clock", "ora", {"type": "object", "properties": {}})]
    turn = await p.complete_tools("gemini-3.5-flash", [{"role": "user", "content": "che ore sono?"}], spec)
    assert turn.tool_calls[0].id == "call_1" and turn.tool_calls[0].arguments == {"city": "Palermo"}
    history = [{"role": "user", "content": "che ore sono?"},
               {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "name": "clock", "arguments": {"city": "Palermo"}}]},
               {"role": "tool", "tool_call_id": "call_1", "name": "clock", "content": "{\"time\": \"10:00\"}"}]
    await p.complete_tools("gemini-3.5-flash", history, spec)
    sent = bodies[-1]["messages"][1]["tool_calls"][0]
    assert sent["extra_content"] == sig and sent["function"]["name"] == "clock"
    # unknown ids (another provider's history) are sent untouched
    history[1]["tool_calls"][0]["id"] = "other"
    history[2]["tool_call_id"] = "other"
    await p.complete_tools("gemini-3.5-flash", history, spec)
    assert "extra_content" not in bodies[-1]["messages"][1]["tool_calls"][0]


# ---------------------------------------------------------------- adaptive learning (P4)
class SlowFast:
    """Two identical free models: one measured slow, one measured fast. The router must prefer the fast one."""

    def __init__(self, name):
        self.name = name

    def models(self):
        return [ModelDescriptor(self.name, "m", reasoning=3, coding=3, tier="free",
                                max_sensitivity=Sensitivity.INTERNAL)]

    async def available(self):
        return True


def _record_ms(router, prov: str, ms: int, status: str = "ok"):
    from nodo.providers.router import _ATTEMPTS, _LAT
    d = router.catalog()[[x.provider for x in router.catalog()].index(prov)]
    _ATTEMPTS.setdefault(d.id, {"ok": 0, "failed": 0})["ok" if status == "ok" else "failed"] += 1
    if status == "ok":
        _LAT[d.id] = ms if d.id not in _LAT else 0.7 * _LAT[d.id] + 0.3 * ms


def test_router_learns_from_measured_latency():
    from nodo.providers.router import clear_learned, router_stats
    clear_learned()
    slow, fast = SlowFast("slow"), SlowFast("fast")
    r = ModelRouter([slow, fast, DeterministicProvider()], NodoMode.BALANCED)
    base = r.select(RouteRequest()).descriptor.provider
    for _ in range(3):                       # three slow attempts: the truth outweighs the brochure
        _record_ms(r, "slow", 30000)
        _record_ms(r, "fast", 400)
    chosen = r.select(RouteRequest()).descriptor.provider
    assert chosen == "fast" or base == "fast"
    st = router_stats()
    assert st["latency_ms"] and st["attempts"]["slow:m"]["ok"] == 3


def test_failing_model_sinks():
    from nodo.providers.router import _ATTEMPTS, clear_learned
    clear_learned()
    flaky, solid = SlowFast("flaky"), SlowFast("solid")
    r = ModelRouter([flaky, solid, DeterministicProvider()], NodoMode.BALANCED)
    fid = next(d for d in r.catalog() if d.provider == "flaky").id
    _ATTEMPTS[fid] = {"ok": 0, "failed": 5}
    sid = next(d for d in r.catalog() if d.provider == "solid").id
    _ATTEMPTS[sid] = {"ok": 5, "failed": 0}
    assert r.select(RouteRequest()).descriptor.provider == "solid"
