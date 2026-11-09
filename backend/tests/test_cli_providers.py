from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import Any

from devcolleague.llm.providers.cli.base import CliProvider, CliStatus, flatten_prompt
from devcolleague.llm.providers.cli.claude_code import ClaudeCodeProvider
from devcolleague.llm.providers.cli.codex import CodexProvider
from devcolleague.llm.types import ChatEvent, ChatMessage, ChatRequest, ProviderCapabilities


def test_claude_code_args_use_stdin_and_read_only_tools() -> None:
    p = ClaudeCodeProvider("cc", default_model="sonnet")
    args = p.build_args("claude", ChatRequest(messages=[], system="sys"))
    assert args[:2] == ["claude", "-p"]
    assert "stream-json" in args
    assert args[args.index("--allowedTools") + 1 : args.index("--allowedTools") + 4] == ["Read", "Grep", "Glob"]
    assert args[args.index("--model") + 1] == "sonnet"
    assert args[args.index("--append-system-prompt") + 1] == "sys"


def test_claude_code_parse_stream() -> None:
    p = ClaudeCodeProvider("cc")
    state: dict[str, Any] = {}
    lines = [
        {"type": "system", "subtype": "init"},
        {"type": "stream_event", "parent_tool_use_id": None, "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}}},
        {"type": "stream_event", "parent_tool_use_id": "x", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "nested"}}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "a.py"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "ok"}]}]}},
        {"type": "stream_event", "parent_tool_use_id": None, "event": {"type": "message_start"}},
        {"type": "result", "is_error": False, "stop_reason": "end_turn", "usage": {"input_tokens": 3, "output_tokens": 4}, "total_cost_usd": 0.01},
    ]
    events = [e for line in lines for e in p.parse_line(line, state)]
    assert [(e.type, e.data.get("text")) for e in events] == [
        ("text_delta", "hi"), ("tool_call", None), ("tool_result", None), ("text_delta", "\n\n"), ("done", None),
    ]
    assert events[2].data["content"] == "ok"
    assert events[-1].data["usage"] == {"input_tokens": 3, "output_tokens": 4}


def test_codex_parse_stream() -> None:
    p = CodexProvider("cx")
    state: dict[str, Any] = {}
    lines = [
        {"type": "thread.started", "thread_id": "x"},
        {"type": "item.started", "item": {"id": "i1", "type": "command_execution", "command": "ls"}},
        {"type": "item.completed", "item": {"id": "i1", "type": "command_execution", "aggregated_output": "a.py", "exit_code": 0}},
        {"type": "item.completed", "item": {"id": "i2", "type": "agent_message", "text": "完成"}},
        {"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 2}},
    ]
    events = [e for line in lines for e in p.parse_line(line, state)]
    assert [e.type for e in events] == ["tool_call", "tool_result", "text_delta", "done"]
    assert events[0].data["arguments"] == {"command": "ls"}
    assert p.parse_line({"type": "turn.failed", "error": {"message": "quota"}}, {})[0].type == "error"


def test_codex_args() -> None:
    args = CodexProvider("cx", default_model="gpt-5").build_args("codex", ChatRequest(messages=[], cwd="C:/repo"))
    assert args[:3] == ["codex", "exec", "--json"]
    assert args[-1] == "-"
    assert args[args.index("-C") + 1] == "C:/repo"
    assert args[args.index("-s") + 1] == "read-only"


def test_flatten_prompt() -> None:
    assert flatten_prompt([ChatMessage("user", "只有一句")]) == "只有一句"
    text = flatten_prompt([ChatMessage("user", "A"), ChatMessage("assistant", "B"), ChatMessage("user", "C")])
    assert "【使用者】\nA" in text and "【助理】\nB" in text and text.endswith("C")


class ScriptProvider(CliProvider):
    """以 Python 腳本模擬 CLI，驗證子行程、stdin 與逾時處理。"""

    executable_name = "python"
    capabilities = ProviderCapabilities(custom_tools=False, local_workspace=True, streaming=True)

    def __init__(self, script: Path, timeout: float = 30) -> None:
        super().__init__("script", executable=sys.executable, timeout=timeout)
        self._script = script

    def build_args(self, exe: str, request: ChatRequest) -> list[str]:
        return [exe, str(self._script)]

    def parse_line(self, obj: dict[str, Any], state: dict[str, Any]) -> list[ChatEvent]:
        if obj.get("t") == "text":
            return [ChatEvent.text(obj["v"])]
        if obj.get("t") == "done":
            return [ChatEvent("done", {"stop_reason": "end_turn"})]
        return []

    async def status(self) -> CliStatus:
        return CliStatus(installed=True)


async def test_cli_subprocess_reads_stdin_and_streams(tmp_path: Path) -> None:
    script = tmp_path / "fake_cli.py"
    script.write_text(textwrap.dedent("""
        import json, sys
        prompt = sys.stdin.read()
        print("not json")
        print(json.dumps({"t": "text", "v": "收到:" + prompt}, ensure_ascii=False), flush=True)
        print(json.dumps({"t": "done"}), flush=True)
    """), encoding="utf-8")
    events = [e async for e in ScriptProvider(script).chat(ChatRequest(messages=[ChatMessage("user", "測試 \"引號\" & 符號")]))]
    assert events[0].data["text"] == '收到:測試 "引號" & 符號'
    assert events[-1].type == "done"


async def test_cli_nonzero_exit_reports_stderr(tmp_path: Path) -> None:
    script = tmp_path / "fail.py"
    script.write_text("import sys; sys.stderr.write('not logged in'); sys.exit(3)", encoding="utf-8")
    events = [e async for e in ScriptProvider(script).chat(ChatRequest(messages=[ChatMessage("user", "x")]))]
    assert events[-1].type == "error"
    assert "not logged in" in events[-1].data["message"] and "3" in events[-1].data["message"]


async def test_cli_timeout_kills_process(tmp_path: Path) -> None:
    script = tmp_path / "slow.py"
    script.write_text("import time, sys; sys.stdin.read(); time.sleep(30)", encoding="utf-8")
    events = [e async for e in ScriptProvider(script, timeout=1).chat(ChatRequest(messages=[ChatMessage("user", "x")]))]
    assert events[-1].type == "error" and "逾時" in events[-1].data["message"]
