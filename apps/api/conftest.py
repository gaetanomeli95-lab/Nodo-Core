from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

os.environ.update({"NODO_ENV": "TEST", "NODO_MODE": "FREE", "NODO_DATABASE_URL": "sqlite:///:memory:",
                   "NODO_STT_PROVIDER": "fake", "NODO_TTS_PROVIDER": "fake", "NODO_GITHUB_CONNECTOR": "fake"})

from nodo.app import Container, build_container, ensure_workspace  # noqa: E402
from nodo.config import Settings  # noqa: E402
from nodo.db.models import Base  # noqa: E402
from nodo.db.session import configure_engine, make_engine, session_scope  # noqa: E402
from nodo.main import create_app  # noqa: E402
from nodo.providers.deterministic import FakeModelProvider  # noqa: E402
from nodo.seed import seed_demo  # noqa: E402
from nodo.tools.github import FakeGitHubConnector  # noqa: E402
from nodo.voice.providers import FakeSTT, FakeTTS  # noqa: E402


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def engine():
    eng = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    configure_engine(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    with session_scope() as s:
        yield s


@pytest.fixture
def seeded(db):
    seed_demo(db)
    db.flush()
    return ensure_workspace(db)[0].id


@pytest.fixture
def github() -> FakeGitHubConnector:
    return FakeGitHubConnector()


@pytest.fixture
def container(settings, github) -> Container:
    return build_container(settings, providers=[FakeModelProvider()], github=github, stt=FakeSTT(), tts=FakeTTS())


@pytest.fixture
def core(db, seeded, container):
    return container.core(db)


@pytest.fixture
def client(engine, settings, container):
    app = create_app(settings, container)
    with TestClient(app) as c:
        with session_scope() as s:
            seed_demo(s)
        yield c
