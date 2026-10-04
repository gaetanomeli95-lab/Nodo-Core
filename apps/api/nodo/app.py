"""Application container: wires configuration into concrete providers/connectors once per process."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from nodo.config import Settings, get_settings
from nodo.core.orchestrator import NodoCore
from nodo.core.permissions import PermissionPolicy
from nodo.db.models import Organization, User
from nodo.providers.base import ModelProvider
from nodo.providers.registry import build_providers
from nodo.tools.github import FakeGitHubConnector, GitHubConnector, HttpGitHubConnector
from nodo.voice.base import VoiceSessionStore
from nodo.voice.providers import build_voice


@dataclass
class Container:
    settings: Settings
    providers: list[ModelProvider]
    github: GitHubConnector
    stt: object
    tts: object
    voice_sessions: VoiceSessionStore
    policy: PermissionPolicy

    def core(self, session: Session) -> NodoCore:
        org, user = ensure_workspace(session)
        return NodoCore(session, self.settings, org.id, self.providers, self.github, self.policy, user.id)


def build_container(settings: Settings | None = None, **overrides) -> Container:
    s = settings or get_settings()
    gh: GitHubConnector
    if s.github_connector == "fake" or (s.github_connector == "auto" and s.env in ("TEST", "DEMO")):
        gh = FakeGitHubConnector()
    else:
        gh = HttpGitHubConnector(s.github_token)
    stt, tts = build_voice(s)
    c = Container(s, build_providers(s), gh, stt, tts, VoiceSessionStore(), PermissionPolicy())
    for k, v in overrides.items():
        setattr(c, k, v)
    return c


def ensure_workspace(session: Session, name: str = "Default Workspace") -> tuple[Organization, User]:
    """Single-workspace V0.1: first org/user are created lazily. Multi-tenant boundaries already exist in the schema."""
    org = session.scalars(select(Organization).order_by(Organization.created_at).limit(1)).first()
    if org is None:
        org = Organization(name=name, slug="default")
        session.add(org)
        session.flush()
    user = session.scalars(select(User).where(User.organization_id == org.id).limit(1)).first()
    if user is None:
        user = User(organization_id=org.id, name="Owner")
        session.add(user)
        session.flush()
    return org, user
