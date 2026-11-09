# DevColleague 架構設計

> 狀態：草案
> 最後更新：2026-11-09
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
│   ├─ Chat（AI 對話、角色派工過程）                          │
│   └─ Settings（連線、Provider、MCP、角色、Routine 設定）     │
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
│   ├─ agents/         角色載入、管家派工                       │
│   ├─ routines/       例行流程定義與執行                       │
│   ├─ scheduler/      背景排程（主動提醒、Routine）             │
│   ├─ tools/          提供給 LLM 呼叫的工具（內建 + MCP）       │
│   │    └─ mcp/       MCP Client 與工具轉接                   │
│   └─ storage/        SQLite、keyring                        │
└────────────────────────────────────────────────────────────┘
       │                  │               │              │
       ▼                  ▼               ▼              ▼
 ADS/TFS、GitHub API   LLM API 服務   本機 CLI（claude 等）  MCP Server
                                                     （Email、行事曆、筆記…）
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
    # 是否能直接連接 MCP Server（CLI 模式由 CLI 自行連線；API 模式由 DevColleague 轉接）
    native_mcp: bool
    # 是否原生支援 subagent，可由 CLI 自行負責角色派工（例如 Claude Code）
    native_subagents: bool


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
- `delegate_start`／`delegate_end`：管家派工給角色的開始與結束（用於在 UI 顯示「已交給 PR 秘書處理…」）
- `confirm_required`：有副作用的工具需要使用者確認後才繼續執行
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

## 6. MCP 整合

ADS/TFS、GitHub 這類核心平台需要統一資料模型與儀表板，仍由 `integrations/` 自行實作；
其他外部系統（Email、行事曆、筆記軟體等）則透過 MCP Server 接入，不另寫 integration。

### 6.1 MCP Client

- 使用 MCP 官方 Python SDK 實作 Client，支援兩種傳輸方式：
  - **stdio**：由後端以子行程啟動本機 MCP Server（以參數陣列傳遞，不經過 shell）
  - **Streamable HTTP**：連接遠端或本機已啟動的 MCP Server
- 後端啟動時連接所有已啟用的 MCP Server，取得工具清單並註冊到工具註冊表。
- 工具名稱加上命名空間 `mcp__<server>__<tool>`，避免與內建工具或其他 Server 衝突。
- MCP 工具與內建工具使用同一個介面，Gateway 的 tool loop 不需要區分來源。
- Server 斷線時自動重試，並在設定畫面顯示狀態；連不上的 Server 其工具暫時不提供給 LLM。
- 實際的 SDK 用法與協定細節以實作時 MCP 官方文件為準。

### 6.2 設定格式

設定格式相容常見的 `mcpServers` 寫法，機密值不直接寫在設定中，而是引用 keyring 中的項目：

```json
{
  "mcpServers": {
    "calendar": {
      "command": "npx",
      "args": ["-y", "<calendar-mcp-server>"],
      "env": { "API_TOKEN": "keyring:mcp/calendar/API_TOKEN" }
    }
  }
}
```

- 啟動 MCP Server 前，後端才從 keyring 讀出機密值，以環境變數傳給子行程。
- 每個工具可個別啟用／停用。

### 6.3 工具風險分級

| 等級 | 說明 | 預設行為 |
|---|---|---|
| 唯讀 | 只查詢資料（例如讀信、查行程） | 直接執行 |
| 有副作用 | 會修改外部狀態（例如寄信、建立事件、留言） | 送出 `confirm_required`，經使用者確認才執行 |

- 預設等級參考 MCP 工具宣告的 annotations（例如 `readOnlyHint`），沒有宣告的一律視為「有副作用」。
- 使用者可在設定畫面調整個別工具的等級。

### 6.4 與 CLI 模式的關係

- Provider 宣告 `native_mcp` 時（例如 Claude Code、Codex CLI），由 DevColleague 依同一份設定
  產生 CLI 需要的 MCP 設定並傳給 CLI，由 CLI 自行連線。
- 機密值同樣以環境變數傳給 CLI 子行程，不寫入暫存設定檔。
- 各 CLI 指定 MCP 設定的參數與格式，以實作時各 CLI 的官方文件為準。

## 7. 角色與派工

### 7.1 角色定義

角色以 Markdown 檔定義，放在使用者資料目錄的 `roles/` 下，一個角色一個檔：

```markdown
---
id: pr-secretary
name: PR 秘書
description: 追蹤待審查與擱置的 PR，產生 PR 摘要與描述
tools:
  - search_pull_requests
  - get_pull_request_diff
  - mcp__calendar__*
provider: claude-api      # 選填，未指定時使用預設 Provider
model: <model-id>          # 選填
---

你是 PR 秘書，說話簡潔直接……（人設、職責範圍、回覆格式）
```

- `description` 是管家判斷派工的依據，應簡短明確。
- `tools` 是白名單，支援 `*` 萬用字元；角色只看得到白名單內的工具。
- 內文為角色的 system prompt。
- 安裝時附帶數個內建角色範本，使用者可修改或新增。

### 7.2 管家派工流程（API 模式）

1. 使用者訊息送給管家。管家的 context 只包含各角色的 `id` 與 `description`，以及一個 `delegate_to_role` 工具。
2. 管家呼叫 `delegate_to_role(role_id, task)`；同一輪可呼叫多次，Gateway 會並行執行。
3. Gateway 為每個被指派的角色開一個子 session：此時才載入該角色的完整 prompt 與工具白名單，執行該角色自己的 tool loop。
4. 角色完成後，最終結果以 `tool_result` 回給管家。
5. 管家彙整所有結果後回覆使用者。

規則：

- **只允許一層派工**：角色拿不到 `delegate_to_role`，不能再派工。角色之間需要轉交時（例如需要另一個角色的資訊），由角色在結果中說明，再由管家決定下一步派給誰。
- 每次派工有 token 與執行時間上限，超過時中止並回報管家。
- 子 session 的過程以 `delegate_start`／`delegate_end` 及其中的 `tool_call` 事件送到前端，讓使用者看得到誰在做什麼。

使用者也可以在對話中直接指定角色，此時略過管家，直接以該角色的設定開始對話。

### 7.3 CLI 模式

- Provider 宣告 `native_subagents` 時（例如 Claude Code），DevColleague 把角色定義轉換成該 CLI 的
  subagent 格式，放在 DevColleague 管理的工作目錄中，由 CLI 自行負責派工。
- Provider 不支援 subagent 時，該 Provider 只能以「直接指定角色」的方式對話，不提供管家派工。
- 各 CLI 的 subagent 格式以實作時官方文件為準。

## 8. 例行流程（Routine）

### 8.1 Routine 定義

Routine 同樣以 Markdown 檔定義，放在使用者資料目錄的 `routines/` 下：

```markdown
---
id: morning-brief
name: 晨間簡報
role: butler               # 執行角色，預設為管家
schedule: "30 8 * * 1-5"   # cron 格式；省略時只能手動執行
command: /morning          # 選填，在對話中輸入即可手動觸發
notify: true               # 完成後是否發送系統通知
catch_up: true             # App 未執行而錯過時，下次啟動是否補跑一次
allow_side_effects: []     # 無人值守時允許使用的有副作用工具，預設為空
---

今天是 {{today}}，上次執行時間是 {{last_run_at}}。
請彙整：待我審查的 PR、昨晚失敗的建置、今天的行程……
```

- 範本變數由後端在執行前代入，第一版提供 `{{today}}`、`{{yesterday}}`、`{{last_run_at}}`。
- F4 的站會報告與週報以內建 Routine 範本提供。

### 8.2 執行流程

1. 排程器到點（或使用者手動觸發）時，代入範本變數，以指定角色開一段新對話並送出。
2. 執行過程與一般對話相同（可派工、可呼叫工具），結果存成一段對話。
3. 若 `notify` 為真，發送系統通知；點擊通知開啟該對話，使用者可接續追問。
4. 執行紀錄（開始／結束時間、成功或失敗、對應對話）寫入 SQLite。

無人值守時沒有人能確認，因此有副作用的工具只有列在 `allow_side_effects` 中的才能執行，
其餘一律拒絕並在結果中說明。

### 8.3 背景排程器

- `scheduler/` 是與 F9 主動提醒共用的背景排程器，在 App 縮到系統匣時繼續運作。
- 啟動時比對每個 Routine 的上次執行時間與排程，若有錯過且 `catch_up` 為真，補跑一次（不會補跑多次）。
- 同一個 Routine 同時只會有一個執行中的實例。

## 9. 資料儲存

| 資料 | 儲存位置 |
|---|---|
| Token、API Key、MCP Server 機密值 | 作業系統憑證儲存區（`keyring`） |
| 連線設定、Provider 設定、MCP Server 設定 | SQLite |
| 角色、Routine 定義 | 使用者資料目錄下的 `roles/`、`routines/`（Markdown 檔） |
| 對話紀錄（含 Routine 產生的對話） | SQLite |
| Routine 執行紀錄 | SQLite |
| 平台資料快取 | SQLite（附更新時間，背景定期更新） |
| 個人記憶、知識庫 | SQLite，使用 FTS5 全文搜尋（之後再評估向量搜尋） |

資料庫檔案放在使用者資料目錄（例如 Windows 的 `%APPDATA%\DevColleague\`）。

## 10. 安全性設計

- 機密資料不以明文寫入資料庫、設定檔或 log。
- 後端只監聽 `127.0.0.1`，並驗證 session token。
- 呼叫 CLI 與啟動 MCP Server 時以參數陣列傳遞，不經過 shell 組字串，避免指令注入。
- 送給外部 LLM 的內容（例如 PR diff、build log）需在 UI 讓使用者知道。
- MCP Server 是第三方程式，新增時需在 UI 提示使用者確認其來源。
- 有副作用的工具預設需經使用者確認；無人值守的 Routine 只能使用明確允許的有副作用工具。
- 角色只能使用工具白名單內的工具。

## 11. 建議的專案結構

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
│  │  ├─ agents/
│  │  ├─ routines/
│  │  ├─ scheduler/
│  │  ├─ tools/
│  │  │  └─ mcp/
│  │  └─ storage/
│  ├─ defaults/              # 內建角色與 Routine 範本，首次啟動時複製到使用者資料目錄
│  └─ tests/
└─ CLAUDE.md
```

## 12. 打包與發佈

1. 使用 PyInstaller 把後端打包成執行檔。
2. 將執行檔設定為 Tauri 的 sidecar（`externalBin`）。
3. 使用 Tauri 打包成 Windows 安裝檔。
