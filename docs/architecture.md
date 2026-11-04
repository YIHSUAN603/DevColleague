# DevColleague 架構設計

> 狀態：草案
> 最後更新：2026-11-04
> 相關文件：[需求規格](requirements.md)

## 1. 技術棧

| 層 | 技術 | 說明 |
|---|---|---|
| 桌面外殼 | Tauri 2 | 負責視窗、系統通知、系統匣、啟動與管理後端子行程 |
| 前端 | React + Vite + TypeScript | 儀表板、AI 對話、設定畫面 |
| 後端 | Python + FastAPI | 平台整合、LLM Gateway、資料儲存 |
| 資料庫 | SQLite | 設定、對話紀錄、快取、記憶 |
| 機密儲存 | 作業系統憑證儲存區 | 透過 Python `keyring` 存放 Token 與 API Key |

## 2. 整體架構

```
┌──────────────────── Tauri 桌面應用程式 ────────────────────┐
│                                                            │
│  React 前端                                                │
│   ├─ Dashboard（統一儀表板）                                │
│   ├─ Chat（AI 對話）                                       │
│   └─ Settings（連線、Provider 設定）                        │
│          │                                                 │
│          │ HTTP + SSE（127.0.0.1:<動態 port>，帶 session token）│
│          ▼                                                 │
│  Python FastAPI 後端（Tauri sidecar）                       │
│   ├─ api/            HTTP 路由                              │
│   ├─ services/       功能邏輯（摘要、Review、診斷…）          │
│   ├─ integrations/   ADS/TFS、GitHub（統一資料模型）          │
│   ├─ llm/            LLM Gateway                           │
│   │    ├─ providers/api/   Claude、OpenAI、Ollama…          │
│   │    └─ providers/cli/   claude -p 等 CLI 工具             │
│   ├─ tools/          提供給 LLM 呼叫的工具                    │
│   └─ storage/        SQLite、keyring                        │
└────────────────────────────────────────────────────────────┘
          │                         │                 │
          ▼                         ▼                 ▼
   ADS/TFS、GitHub API        LLM API 服務       本機 CLI（claude 等）
```

## 3. 行程模型

1. 使用者啟動 Tauri App。
2. Tauri 以 sidecar 方式啟動 Python 後端執行檔，並傳入：
   - 一個可用的動態 port
   - 一組隨機產生的 session token
3. 後端只監聽 `127.0.0.1`，所有請求都必須帶 session token，避免本機其他程式或網頁存取。
4. 前端透過 Tauri 取得 port 與 token 後，開始呼叫後端。
5. 關閉視窗時，App 縮到系統匣，後端繼續執行背景工作（例如每 5 分鐘檢查一次主動提醒）。
6. 從系統匣選「結束」時，Tauri 負責結束後端子行程。

開發階段可以分開啟動：後端用 `uvicorn` 執行，前端用 Vite dev server，不需要每次都打包。

## 4. LLM Gateway

LLM Gateway 是本專案的核心，需要同時支援 **API 模式**（可切換多家）和 **CLI 模式**（例如 `claude -p`）。

### 4.1 兩種模式的差異

| | API 模式 | CLI 模式 |
|---|---|---|
| 驗證 | API Key | CLI 本身的登入狀態（可使用訂閱方案） |
| Tool use | 由 DevColleague 定義工具並執行 tool loop | 由 CLI 自行執行 agent loop，DevColleague 只接收結果 |
| 串流 | 各家 SSE 串流 | 解析 CLI 的串流輸出（例如 `--output-format stream-json`） |
| 存取本機檔案 | 需透過自訂工具 | CLI 原生支援讀取工作目錄 |
| 適合的功能 | 查詢平台資料、摘要、PR 描述 | 本機 Repo 理解、需要讀大量程式碼的任務 |

### 4.2 統一介面

每個 Provider 實作同一個介面，並宣告自己的能力。上層功能根據能力決定流程，不直接依賴特定 Provider。

```python
from dataclasses import dataclass
from typing import AsyncIterator, Protocol


@dataclass
class ProviderCapabilities:
    # 是否支援由 DevColleague 定義的工具（API 模式通常為 True）
    custom_tools: bool
    # 是否能自行讀取本機工作目錄（CLI 模式通常為 True）
    local_workspace: bool
    # 是否支援串流輸出
    streaming: bool


class LLMProvider(Protocol):
    id: str
    capabilities: ProviderCapabilities

    async def chat(self, request: "ChatRequest") -> AsyncIterator["ChatEvent"]:
        """送出對話請求，以事件串流回傳文字片段、工具呼叫與最終結果。"""
        ...
```

`ChatEvent` 統一包含以下事件類型，讓前端不需要知道底層是哪家 Provider：

- `text_delta`：文字片段
- `tool_call`／`tool_result`：工具呼叫與結果（用於在 UI 顯示「正在查詢 PR…」）
- `done`：完成，附帶 token 用量等資訊
- `error`：錯誤

### 4.3 API Providers

- 第一版至少支援 Claude（Anthropic API）。
- 每家各寫一個薄的 adapter（例如 `providers/api/anthropic.py`、`providers/api/openai.py`），
  負責把統一的 `ChatRequest` 轉成該家格式，並把回應轉回 `ChatEvent`。
  不使用 LiteLLM，以便完整運用各家特有功能（例如 Claude 的 prompt caching）。
- Tool loop 由 Gateway 負責：收到工具呼叫 → 執行 `tools/` 中的工具 → 把結果送回 LLM → 直到完成。

### 4.4 CLI Providers

- 第一版支援以下 CLI，以子行程執行：

  | CLI | 指令範例 | 串流格式 |
  |---|---|---|
  | Claude Code | `claude -p "<prompt>" --output-format stream-json` | JSON Lines |
  | Codex CLI | `codex exec --json "<prompt>"` | JSON Lines |

- 每個 CLI 各寫一個 adapter，解析其串流輸出並轉換為統一的 `ChatEvent`。
- 實際的參數與輸出格式以實作時各 CLI 的官方文件為準。
- 執行時指定工作目錄（`cwd`），讓 CLI 能讀取對應的本機 Repo。
- 啟動前偵測 CLI 是否已安裝、是否已登入，並在設定畫面顯示狀態。
- 需處理逾時與取消（使用者中斷時結束子行程）。

## 5. 平台整合

### 5.1 統一資料模型

ADS/TFS 與 GitHub 的資料轉換為共用模型，上層功能與 UI 不需要區分平台：

| 共用模型 | ADS/TFS | GitHub |
|---|---|---|
| `PullRequest` | Pull Request | Pull Request |
| `WorkItem` | Work Item | Issue |
| `Build` | Build／Pipeline Run | Actions Workflow Run |
| `Commit` | Commit | Commit |

### 5.2 Integration 介面

每個平台實作同一組查詢方法（例如 `list_my_pull_requests`、`list_assigned_work_items`、`get_build_log`），
並以「連線」為單位管理，支援同時設定多個連線。

### 5.3 提供給 LLM 的工具

`tools/` 中的工具會呼叫 Integration 層，例如：

- `search_pull_requests`：依條件查詢 PR
- `get_pull_request_diff`：取得 PR 的變更內容
- `list_work_items`：查詢 Work Item／Issue
- `get_build_log`：取得建置 log

工具只回傳 LLM 需要的欄位，避免浪費 token。

## 6. 資料儲存

| 資料 | 儲存位置 |
|---|---|
| Token、API Key | 作業系統憑證儲存區（`keyring`） |
| 連線設定、Provider 設定 | SQLite |
| 對話紀錄 | SQLite |
| 平台資料快取 | SQLite（附更新時間，背景定期更新） |
| 個人記憶、知識庫 | SQLite，使用 FTS5 全文搜尋（之後再評估向量搜尋） |

資料庫檔案放在使用者資料目錄（例如 Windows 的 `%APPDATA%\DevColleague\`）。

## 7. 安全性設計

- 機密資料不以明文寫入資料庫、設定檔或 log。
- 後端只監聽 `127.0.0.1`，並驗證 session token。
- 呼叫 CLI 時以參數陣列傳遞，不經過 shell 組字串，避免指令注入。
- 送給外部 LLM 的內容（例如 PR diff、build log）需在 UI 讓使用者知道。

## 8. 建議的專案結構

```
DevColleague/
├─ docs/                     # 專案文件
├─ app/                      # Tauri + React 前端
│  ├─ src/                   # React 原始碼
│  └─ src-tauri/             # Tauri（Rust）設定與程式
├─ backend/                  # Python FastAPI 後端
│  ├─ devcolleague/
│  │  ├─ api/
│  │  ├─ services/
│  │  ├─ integrations/
│  │  ├─ llm/
│  │  ├─ tools/
│  │  └─ storage/
│  └─ tests/
└─ CLAUDE.md
```

## 9. 打包與發佈

1. 使用 PyInstaller 把後端打包成執行檔。
2. 將執行檔設定為 Tauri 的 sidecar（`externalBin`）。
3. 使用 Tauri 打包成 Windows 安裝檔。
