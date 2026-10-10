"""Application container: wires configuration into concrete providers/connectors once per process."""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from nodo.config import Settings, get_settings
from nodo.core.orchestrator import NodoCore
from nodo.core.permissions import PermissionPolicy
from nodo.db.models import Organization, User
from nodo.night.notify import Notifier, build_notifier
from nodo.night.probe import BrowserProber, HttpProber, Prober
from nodo.providers.base import ModelProvider
from nodo.providers.registry import build_providers
from nodo.tools.github import FakeGitHubConnector, GitHubConnector, HttpGitHubConnector
from nodo.tools.github_repo import GitHubRepoClient, HttpGitHubRepoClient
from nodo.tools.vercel import HttpVercelConnector, VercelConnector
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
    vercel: VercelConnector | None = None
    repo: GitHubRepoClient | None = None
    prober: Prober = field(default_factory=HttpProber)
    notifier: Notifier | None = None
    demo: object | None = None            # DemoWorld in NODO_ENV=DEMO
    scheduler: object | None = None       # NightScheduler when running inside the API
    night_verify_delay: float = 20.0
    night_retry_delay: float = 5.0
    night_poll_interval: float = 15.0

    def core(self, session: Session) -> NodoCore:
        org, user = ensure_workspace(session)
        return NodoCore(session, self.settings, org.id, self.providers, self.github, self.policy, user.id,
                        container=self)

    def night_deps(self):
        from nodo.night.shift import NightDeps
        return NightDeps(prober=self.prober, vercel=self.vercel, repo=self.repo, github=self.github,
                         providers=self.providers, notifier=self.notifier or build_notifier(self.settings),
                         verify_delay=self.night_verify_delay, retry_delay=self.night_retry_delay,
                         poll_interval=self.night_poll_interval)

    def night_shift(self, session: Session):
        from nodo.night.briefing import tzinfo
        from nodo.night.shift import NightShift
        org, _ = ensure_workspace(session)
        return NightShift(session, self.settings, org.id, self.night_deps(), tz=tzinfo(self.settings.timezone))


def build_container(settings: Settings | None = None, **overrides) -> Container:
    s = settings or get_settings()
    gh: GitHubConnector
    if s.github_connector == "fake" or (s.github_connector == "auto" and s.env in ("TEST", "DEMO")):
        gh = FakeGitHubConnector()
    else:
        gh = HttpGitHubConnector(s.github_token)
    stt, tts = build_voice(s)
    providers = build_providers(s)
    vercel: VercelConnector | None = None
    repo: GitHubRepoClient | None = None
    prober: Prober
    demo = None
    if s.env == "DEMO":
        from nodo.night.simulation import build_demo_world
        demo = build_demo_world()
        vercel, repo, prober = demo.vercel, demo.github, demo.prober
        providers = providers[:-1] + [demo.model] + providers[-1:]  # keep deterministic as the last resort
    else:
        fake_vercel = s.vercel_connector == "fake" or (s.vercel_connector == "auto" and s.env == "TEST")
        fake_repo = s.github_connector == "fake" or (s.github_connector == "auto" and s.env == "TEST")
        if fake_vercel or fake_repo:
            from nodo.night.simulation import FakeGitHubRepo, FakeVercel, SimulatedProber
            fv = FakeVercel()
            vercel = fv if fake_vercel else None
            repo = FakeGitHubRepo(fv) if fake_repo else None
        if not fake_vercel and s.vercel_token:
            vercel = HttpVercelConnector(s.vercel_token, s.vercel_team_id)
        if not fake_repo and s.github_token:
            repo = HttpGitHubRepoClient(s.github_token)
        if fake_vercel:
            prober = SimulatedProber(vercel)  # type: ignore[arg-type]
        else:
            prober = BrowserProber() if s.night_browser_checks else HttpProber()
    c = Container(s, providers, gh, stt, tts, VoiceSessionStore(), PermissionPolicy(), vercel=vercel, repo=repo,
                  prober=prober, notifier=build_notifier(s), demo=demo)
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
