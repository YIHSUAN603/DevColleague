"""LLM Provider 設定管理：設定存 SQLite、API Key 存 keyring，並依設定建立 Provider 實例。"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from ..storage.db import Database, utc_now
from ..storage.secrets import SecretStore, provider_api_key_key
from .providers.api.anthropic import AnthropicProvider
from .providers.api.openai_compat import OpenAICompatProvider
from .providers.cli.claude_code import ClaudeCodeProvider
from .providers.cli.codex import CodexProvider
from .types import LLMProvider

DEFAULT_PROVIDER_SETTING = "default_provider_id"


@dataclass(frozen=True)
class ProviderKind:
    kind: str
    label: str
    mode: str  # api / cli
    needs_api_key: bool
    api_key_optional: bool = False
    default_base_url: str | None = None
    suggested_models: tuple[str, ...] = ()


PROVIDER_KINDS: dict[str, ProviderKind] = {
    "anthropic": ProviderKind(
        "anthropic", "Claude（Anthropic API）", "api", True,
        suggested_models=("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"),
    ),
    "openai": ProviderKind(
        "openai", "OpenAI／相容端點（含 Ollama 等本機模型）", "api", True,
        api_key_optional=True, default_base_url="https://api.openai.com/v1",
    ),
    "claude_code": ProviderKind("claude_code", "Claude Code CLI（claude -p）", "cli", False),
    "codex": ProviderKind("codex", "Codex CLI（codex exec）", "cli", False),
}


@dataclass
class ProviderConfig:
    id: str
    kind: str
    name: str
    base_url: str | None
    default_model: str | None
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "provider"


class ProviderService:
    def __init__(self, db: Database, secrets: SecretStore) -> None:
        self._db = db
        self._secrets = secrets

    def list(self) -> list[ProviderConfig]:
        return [ProviderConfig(**r) for r in self._db.fetch_all("SELECT * FROM providers ORDER BY created_at")]

    def get(self, provider_id: str) -> ProviderConfig | None:
        row = self._db.fetch_one("SELECT * FROM providers WHERE id = ?", (provider_id,))
        return ProviderConfig(**row) if row else None

    def has_api_key(self, provider_id: str) -> bool:
        return bool(self._secrets.get(provider_api_key_key(provider_id)))

    def create(
        self,
        *,
        kind: str,
        name: str,
        base_url: str | None = None,
        default_model: str | None = None,
        api_key: str | None = None,
    ) -> ProviderConfig:
        meta = PROVIDER_KINDS.get(kind)
        if meta is None:
            raise ValueError(f"不支援的 Provider 類型：{kind}")
        if meta.needs_api_key and not meta.api_key_optional and not api_key:
            raise ValueError("此 Provider 需要 API Key")
        base_id = _slugify(name if name.isascii() else kind)
        provider_id = base_id
        n = 2
        while self.get(provider_id):
            provider_id = f"{base_id}-{n}"
            n += 1
        if api_key:
            self._secrets.set(provider_api_key_key(provider_id), api_key)
        self._db.execute(
            "INSERT INTO providers(id, kind, name, base_url, default_model, created_at) VALUES(?, ?, ?, ?, ?, ?)",
            (provider_id, kind, name, base_url or meta.default_base_url, default_model or None, utc_now()),
        )
        if self.default_provider_id() is None:
            self.set_default(provider_id)
        return self.get(provider_id)  # type: ignore[return-value]

    def update(
        self,
        provider_id: str,
        *,
        name: str | None = None,
        base_url: str | None = None,
        default_model: str | None = None,
        api_key: str | None = None,
    ) -> ProviderConfig:
        current = self.get(provider_id)
        if current is None:
            raise KeyError(provider_id)
        if api_key:
            self._secrets.set(provider_api_key_key(provider_id), api_key)
        self._db.execute(
            "UPDATE providers SET name = ?, base_url = ?, default_model = ? WHERE id = ?",
            (
                name if name is not None else current.name,
                base_url if base_url is not None else current.base_url,
                default_model if default_model is not None else current.default_model,
                provider_id,
            ),
        )
        return self.get(provider_id)  # type: ignore[return-value]

    def delete(self, provider_id: str) -> bool:
        self._secrets.delete(provider_api_key_key(provider_id))
        deleted = self._db.execute("DELETE FROM providers WHERE id = ?", (provider_id,)) > 0
        if self._db.get_setting(DEFAULT_PROVIDER_SETTING) == provider_id:
            remaining = self.list()
            self.set_default(remaining[0].id if remaining else None)
        return deleted

    def default_provider_id(self) -> str | None:
        value = self._db.get_setting(DEFAULT_PROVIDER_SETTING)
        return value if value and self.get(value) else None

    def set_default(self, provider_id: str | None) -> None:
        if provider_id is not None and self.get(provider_id) is None:
            raise KeyError(provider_id)
        self._db.set_setting(DEFAULT_PROVIDER_SETTING, provider_id)

    def build(self, provider_id: str | None = None) -> tuple[LLMProvider, ProviderConfig]:
        """依設定建立 Provider 實例；未指定時使用預設 Provider。"""
        provider_id = provider_id or self.default_provider_id()
        if not provider_id:
            raise LookupError("尚未設定任何 LLM Provider，請先到設定頁新增")
        config = self.get(provider_id)
        if config is None:
            raise LookupError(f"找不到 Provider：{provider_id}")
        api_key = self._secrets.get(provider_api_key_key(provider_id))
        provider: LLMProvider
        match config.kind:
            case "anthropic":
                if not api_key:
                    raise LookupError(f"Provider「{config.name}」尚未設定 API Key")
                provider = AnthropicProvider(config.id, api_key, config.default_model, config.base_url)
            case "openai":
                provider = OpenAICompatProvider(config.id, api_key, config.default_model, config.base_url)
            case "claude_code":
                provider = ClaudeCodeProvider(config.id, config.default_model)
            case "codex":
                provider = CodexProvider(config.id, config.default_model)
            case _:
                raise LookupError(f"不支援的 Provider 類型：{config.kind}")
        return provider, config
