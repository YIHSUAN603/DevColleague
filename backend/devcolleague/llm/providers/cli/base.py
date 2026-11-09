"""CLI Provider 共用邏輯：以子行程執行 CLI、逐行解析 JSON Lines 串流。

安全性：一律以參數陣列啟動子行程，不經過 shell；使用者輸入透過 stdin 傳遞，不放在命令列。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from ...types import ChatEvent, ChatMessage, ChatRequest, ProviderCapabilities

DEFAULT_TIMEOUT_SECONDS = 600
STATUS_TIMEOUT_SECONDS = 20
MAX_TOOL_OUTPUT_CHARS = 2000


@dataclass
class CliStatus:
    installed: bool
    version: str | None = None
    logged_in: bool | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "installed": self.installed,
            "version": self.version,
            "logged_in": self.logged_in,
            "detail": self.detail,
        }


def flatten_prompt(messages: list[ChatMessage]) -> str:
    """CLI 為單次執行，把對話歷史攤平成一段提示。"""
    turns = [m for m in messages if m.content and not m.tool_results]
    if not turns:
        return ""
    *history, last = turns
    if not history:
        return last.content
    lines = ["以下是先前的對話紀錄："]
    for m in history:
        speaker = "使用者" if m.role == "user" else "助理"
        lines.append(f"【{speaker}】\n{m.content}")
    lines.append("\n請接續上面的對話，回覆使用者的最新訊息：")
    lines.append(last.content)
    return "\n\n".join(lines)


def truncate(text: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + "…（已截斷）"


async def run_capture(args: list[str], timeout: float = STATUS_TIMEOUT_SECONDS) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode or 0, out.decode("utf-8", errors="replace")


class CliProvider(ABC):
    capabilities: ProviderCapabilities
    executable_name: str

    def __init__(
        self,
        provider_id: str,
        default_model: str | None = None,
        executable: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.id = provider_id
        self._default_model = default_model
        self._executable = executable
        self._timeout = timeout

    def resolve_executable(self) -> str | None:
        return self._executable or shutil.which(self.executable_name)

    @abstractmethod
    def build_args(self, exe: str, request: ChatRequest) -> list[str]: ...

    @abstractmethod
    def parse_line(self, obj: dict[str, Any], state: dict[str, Any]) -> list[ChatEvent]:
        """把一行 JSON 轉成 ChatEvent。state 可在同一次執行中保存狀態。"""

    @abstractmethod
    async def status(self) -> CliStatus: ...

    async def chat(self, request: ChatRequest) -> AsyncIterator[ChatEvent]:
        exe = self.resolve_executable()
        if not exe:
            yield ChatEvent.error(f"找不到 {self.executable_name} 指令，請先安裝並確認已加入 PATH")
            return
        prompt = flatten_prompt(request.messages)
        args = self.build_args(exe, request)
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=request.cwd or None,
                limit=16 * 1024 * 1024,
            )
        except OSError as exc:
            yield ChatEvent.error(f"無法啟動 {self.executable_name}：{exc}")
            return

        stderr_chunks: list[bytes] = []

        async def drain_stderr() -> None:
            assert proc.stderr is not None
            while chunk := await proc.stderr.read(4096):
                stderr_chunks.append(chunk)

        stderr_task = asyncio.create_task(drain_stderr())
        state: dict[str, Any] = {}
        finished = False
        try:
            assert proc.stdin is not None and proc.stdout is not None
            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()

            loop = asyncio.get_running_loop()
            deadline = loop.time() + self._timeout
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError
                line = await asyncio.wait_for(proc.stdout.readline(), remaining)
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text.startswith("{"):
                    continue
                try:
                    obj = json.loads(text)
                except json.JSONDecodeError:
                    continue
                for event in self.parse_line(obj, state):
                    if event.type in ("done", "error"):
                        finished = True
                    yield event
            await proc.wait()
            await stderr_task
            if not finished:
                err = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()
                if proc.returncode:
                    yield ChatEvent.error(
                        f"{self.executable_name} 執行失敗（結束代碼 {proc.returncode}）：{truncate(err, 500)}"
                    )
                else:
                    yield ChatEvent("done", {"stop_reason": "end_turn", "usage": {}})
        except TimeoutError:
            yield ChatEvent.error(f"{self.executable_name} 執行逾時（{int(self._timeout)} 秒）")
        finally:
            # 使用者中斷（連線關閉導致 generator 被取消）或逾時時，確實結束子行程
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await proc.wait()
            if not stderr_task.done():
                stderr_task.cancel()
