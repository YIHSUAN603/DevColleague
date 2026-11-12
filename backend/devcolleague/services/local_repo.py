"""唯讀 Git 存取，不執行 shell、外部 diff 或 Git hook。"""

import asyncio
import os
from pathlib import Path

MAX_GIT_BYTES = 4_000_000


async def git(root: str, *args: str) -> str:
    path = Path(root).expanduser().resolve()
    if not path.is_dir():
        raise ValueError("本機 Repo 路徑不存在")
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "GIT_PAGER": "cat"}
    proc = await asyncio.create_subprocess_exec(
        "git", "--no-pager", "-c", "core.fsmonitor=false", "-C", str(path), *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
    )
    async def capture():
        async def read_output():
            chunks = bytearray()
            while chunk := await proc.stdout.read(65536):
                chunks.extend(chunk)
                if len(chunks) > MAX_GIT_BYTES:
                    raise ValueError("Git 資料超過 4 MB，請縮小分支差異範圍")
            return chunks
        output, error = await asyncio.gather(read_output(), proc.stderr.read(65536))
        await proc.wait()
        if proc.returncode:
            raise ValueError("Git 查詢失敗：" + error.decode("utf-8", errors="replace")[:500])
        return output.decode("utf-8", errors="replace")
    try:
        return await asyncio.wait_for(capture(), 30)
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


async def inspect_repo(root: str) -> dict:
    top = (await git(root, "rev-parse", "--show-toplevel")).strip()
    branches = (await git(top, "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes")).splitlines()
    files = (await git(top, "ls-files", "-z")).split("\0")
    return {"root": top, "branches": branches, "files": [p for p in files if p and not is_sensitive(p)]}


def is_sensitive(file: str) -> bool:
    parts = Path(file).parts
    name = Path(file).name.lower()
    return any(p.lower() in (".git", ".aws", ".ssh", ".codex", ".agents") for p in parts) or (
        name.startswith(".env") or name.endswith((".pem", ".key", ".pfx", ".p12"))
        or name in ("credentials", "id_rsa", "id_ed25519")
    )


async def resolve_commit(root: str, ref: str) -> str:
    if not ref or ref.startswith("-") or len(ref) > 250:
        raise ValueError("分支名稱無效")
    return (await git(root, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}")).strip()


async def branch_diff(root: str, source: str, target: str) -> dict:
    head = await resolve_commit(root, source)
    base = await resolve_commit(root, target)
    common = (await git(root, "merge-base", base, head)).strip()
    diff = await git(root, "diff", "--no-ext-diff", "--no-textconv", common, head, "--")
    commits = await git(root, "log", "--format=%h %s", f"{common}..{head}", "--")
    return {"source_branch": source, "target_branch": target, "head": head, "base": common,
            "diff": diff, "commits": commits}


async def read_files(root: str, selected: list[str]) -> dict[str, str]:
    info = await inspect_repo(root)
    allowed = set(info["files"])
    path = Path(info["root"]).resolve()
    result = {}
    for file in selected:
        if file not in allowed or is_sensitive(file):
            raise ValueError(f"檔案不在可讀取的追蹤清單：{file}")
        target = (path / file).resolve()
        if (not target.is_relative_to(path) or is_sensitive(target.relative_to(path).as_posix())
                or not target.is_file() or target.stat().st_size > 200000):
            raise ValueError(f"檔案超出 Repo 範圍、不存在或超過 200 KB：{file}")
        raw = target.read_bytes()
        if b"\0" in raw:
            raise ValueError(f"無法分析二進位檔案：{file}")
        result[file] = raw.decode("utf-8", errors="replace")
    return result
