"""以 PyInstaller 打包後端，輸出為 Tauri sidecar（app/src-tauri/binaries/）。

用法（於 backend/ 目錄）：
    uv run python scripts/build_sidecar.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
BINARIES_DIR = BACKEND_DIR.parent / "app" / "src-tauri" / "binaries"
NAME = "devcolleague-backend"


def target_triple() -> str:
    # Tauri 要求 sidecar 檔名帶有目標平台的 target triple
    out = subprocess.run(["rustc", "-vV"], capture_output=True, text=True, check=True).stdout
    for line in out.splitlines():
        if line.startswith("host:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError("無法從 rustc -vV 取得 target triple")


def main() -> None:
    triple = target_triple()
    work = BACKEND_DIR / "build" / "pyinstaller"
    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller",
            "--noconfirm", "--clean", "--onefile",
            "--name", NAME,
            "--distpath", str(work / "dist"),
            "--workpath", str(work / "work"),
            "--specpath", str(work),
            # uvicorn 與 keyring 以動態方式載入模組，需明確收錄
            "--collect-submodules", "uvicorn",
            "--collect-submodules", "keyring.backends",
            "--copy-metadata", "keyring",
            str(BACKEND_DIR / "scripts" / "sidecar_entry.py"),
        ],
        check=True,
        cwd=BACKEND_DIR,
    )
    suffix = ".exe" if sys.platform == "win32" else ""
    BINARIES_DIR.mkdir(parents=True, exist_ok=True)
    target = BINARIES_DIR / f"{NAME}-{triple}{suffix}"
    shutil.copy2(work / "dist" / f"{NAME}{suffix}", target)
    print(f"已輸出 sidecar：{target}")


if __name__ == "__main__":
    main()
