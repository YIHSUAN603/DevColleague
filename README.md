# DevColleague

Your AI colleague for everything you build.

給開發者個人使用的桌面 AI 助理，整合 ADS/TFS 與 GitHub，以 LLM 為核心提供儀表板、AI 對話、工作摘要等功能。

- 需求規格：[docs/requirements.md](docs/requirements.md)
- 架構設計：[docs/architecture.md](docs/architecture.md)

## 目前進度

第一階段 MVP（F1–F5）已實作：

| 功能 | 說明 |
|---|---|
| F1 平台連線管理 | ADS/TFS、GitHub 多連線；Token 存於作業系統憑證儲存區；可測試連線 |
| F2 統一儀表板 | 我建立的 PR、待我審查的 PR、指派給我的 Work Item／Issue、最近的建置；可依平台、專案／Repo 篩選；先顯示快取再背景更新 |
| F3 AI 對話助理 | 串流回覆、工具呼叫過程顯示、對話紀錄管理、對話中切換 Provider／模型 |
| F4 工作摘要 | 今日／昨日／本週／自訂區間的站會報告與週報，顯示送給 LLM 的資料，可複製為 Markdown |
| F5 LLM Provider 設定 | API 模式：Claude、OpenAI 相容端點（含 Ollama 等本機模型）；CLI 模式：Claude Code、Codex CLI |

另外追加：

| 功能 | 說明 |
|---|---|
| F15 日報告 | 依日報告專案分組彙整當天活動，進度由 Work Item 父子階層計算，可手動補充、編輯，依專案複製貼到團隊日報告文件 |

第二階段（LLM 驅動的助理）進行中：

| 功能 | 說明 |
|---|---|
| F16 背景常駐與通知 | 關閉視窗縮到系統匣、背景繼續執行，從系統匣選「結束」才停止；開機自動啟動（可在設定頁關閉）；系統通知，點擊開啟對應頁面 |

## 開發環境需求

- Python 3.12 以上與 [uv](https://docs.astral.sh/uv/)
- Node.js 20 以上
- Rust（Tauri 2 需要），Windows 需安裝 WebView2（Windows 11 已內建）
- 選用：`claude`（Claude Code）、`codex`（Codex CLI），需先在終端機完成登入

## 開發

```bash
# 1. 安裝後端相依
cd backend
uv sync

# 2. 安裝前端相依
cd ../app
npm install

# 3. 啟動桌面 App（Tauri 會自動以 backend/.venv 的 Python 啟動後端）
npm run tauri dev
```

### 只在瀏覽器開發前端

後端與前端可分開啟動，不需要每次都經過 Tauri：

```bash
# 終端機 A：啟動後端（session token 至少 16 字元，自行產生即可）
cd backend
DEVCOLLEAGUE_SESSION_TOKEN=<自行產生的隨機字串> uv run python -m devcolleague --port 8765

# 終端機 B：在 app/.env.local（不納入版控）設定相同的 token 後啟動 Vite
#   VITE_BACKEND_PORT=8765
#   VITE_SESSION_TOKEN=<同上>
cd app
npm run dev    # http://localhost:1420
```

開發時可設定 `DEVCOLLEAGUE_DATA_DIR` 指定資料目錄，避免動到正式資料（預設為 `%APPDATA%\DevColleague`）。

### 測試與檢查

```bash
cd backend && uv run pytest        # 後端測試
cd app && npm run build && npm run lint   # 前端型別檢查、建置與 lint
```

## 打包

```bash
# 1. 以 PyInstaller 打包後端，輸出到 app/src-tauri/binaries/
cd backend
uv run python scripts/build_sidecar.py

# 2. 打包 Windows 安裝檔（NSIS）
cd ../app
npm run tauri:build
```
