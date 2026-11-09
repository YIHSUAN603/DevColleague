"""組裝後端各元件。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import httpx

from .config import Settings
from .integrations.registry import ConnectionService
from .llm.gateway import Gateway
from .llm.registry import ProviderService
from .services.activity import ActivityService
from .services.conversations import ConversationService
from .services.dashboard import DashboardService
from .services.summary import SummaryService
from .storage.db import Database
from .storage.secrets import KeyringSecretStore, SecretStore
from .tools.base import ToolRegistry
from .tools.platform import register_platform_tools


@dataclass
class Container:
    settings: Settings
    db: Database
    secrets: SecretStore
    connections: ConnectionService
    providers: ProviderService
    activity: ActivityService
    tools: ToolRegistry
    gateway: Gateway
    dashboard: DashboardService
    conversations: ConversationService
    summary: SummaryService


def build_container(
    settings: Settings,
    *,
    secrets: SecretStore | None = None,
    db: Database | None = None,
    transport_factory: Callable[[], httpx.AsyncBaseTransport | None] | None = None,
) -> Container:
    db = db or Database(settings.db_path)
    secrets = secrets or KeyringSecretStore()
    connections = ConnectionService(db, secrets, transport_factory or (lambda: None))
    providers = ProviderService(db, secrets)
    activity = ActivityService(connections)
    tools = ToolRegistry()
    register_platform_tools(tools, activity)
    gateway = Gateway(tools)
    return Container(
        settings=settings,
        db=db,
        secrets=secrets,
        connections=connections,
        providers=providers,
        activity=activity,
        tools=tools,
        gateway=gateway,
        dashboard=DashboardService(db, activity),
        conversations=ConversationService(db, providers, gateway),
        summary=SummaryService(activity, providers, gateway),
    )
