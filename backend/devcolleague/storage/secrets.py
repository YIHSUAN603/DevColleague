"""機密資料儲存：Token、API Key 一律存放在作業系統憑證儲存區。"""

from __future__ import annotations

from typing import Protocol

import keyring
from keyring.errors import PasswordDeleteError

KEYRING_SERVICE = "DevColleague"


class SecretStore(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def delete(self, key: str) -> None: ...


class KeyringSecretStore:
    """以 Python keyring 存取作業系統憑證儲存區（Windows 為認證管理員）。"""

    def get(self, key: str) -> str | None:
        return keyring.get_password(KEYRING_SERVICE, key)

    def set(self, key: str, value: str) -> None:
        keyring.set_password(KEYRING_SERVICE, key, value)

    def delete(self, key: str) -> None:
        try:
            keyring.delete_password(KEYRING_SERVICE, key)
        except PasswordDeleteError:
            pass


class MemorySecretStore:
    """僅供測試使用的記憶體儲存。"""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def set(self, key: str, value: str) -> None:
        self._data[key] = value

    def delete(self, key: str) -> None:
        self._data.pop(key, None)


def connection_token_key(connection_id: str) -> str:
    return f"connection/{connection_id}/token"


def provider_api_key_key(provider_id: str) -> str:
    return f"provider/{provider_id}/api_key"
