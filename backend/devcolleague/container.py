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
from .services.daily_report import DailyReportService
from .services.dashboard import DashboardService
from .services.events import EventBus, NotificationService
from .services.summary import SummaryService
from .services.development import DevelopmentService
from .services.memory import MemoryService
from .services.event_monitor import EventMonitor
from .services.scheduler import Scheduler
from .routines.definitions import RoutineStore
from .routines.service import RoutineService
from .tools.assistant import register_assistant_tools
from .tools.mcp_bridge import McpBridge
from .storage.db import Database
from .storage.secrets import KeyringSecretStore, SecretStore
from .storage.secrets import connection_token_key, provider_api_key_key
from .tools.base import ToolRegistry
from .tools.platform import register_platform_tools
from .tools.development import register_development_tools


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
    daily_report: DailyReportService
    events: EventBus
    notifications: NotificationService
    memory: MemoryService
    routines: RoutineService
    monitor: EventMonitor
    scheduler: Scheduler
    mcp: McpBridge
    development: DevelopmentService


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
    events = EventBus()
    notifications = NotificationService(events)
    def known_secrets() -> list[str]:
        keys = [connection_token_key(c.id) for c in connections.list()]
        keys += [provider_api_key_key(p.id) for p in providers.list()]
        return [settings.session_token] + [value for key in keys if (value := secrets.get(key))]

    memory = MemoryService(db, known_secrets)
    development = DevelopmentService(connections, providers, gateway, known_secrets)
    register_development_tools(tools, development)
    conversations = ConversationService(db, providers, gateway)
    conversations.memory = memory
    daily_report = DailyReportService(db, activity, providers, gateway)
    summary = SummaryService(activity, providers, gateway)
    routines = RoutineService(db, RoutineStore(settings.data_dir / "routines"), providers, gateway,
                              conversations, memory, notifications)
    conversations.routines = routines
    register_assistant_tools(tools, memory, daily_report, routines, notifications, summary)
    monitor = EventMonitor(db, activity, routines, notifications)
    mcp = McpBridge(tools, settings.port)
    gateway.mcp = mcp
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
        conversations=conversations,
        summary=summary,
        daily_report=daily_report,
        events=events,
        notifications=notifications,
        memory=memory, routines=routines, monitor=monitor,
        scheduler=Scheduler(routines, monitor), mcp=mcp,
        development=development,
    )
