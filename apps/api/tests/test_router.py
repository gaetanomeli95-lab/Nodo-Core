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
