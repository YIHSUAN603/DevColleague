# DevColleague 架構設計

> 狀態：草案
> 最後更新：2026-11-10
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
│   ├─ scheduler/      背景排程（事件提醒、Routine）             │
│   ├─ tools/          提供給 LLM 呼叫的工具（內建 + MCP）       │
│   │    ├─ mcp/       MCP Client 與工具轉接                   │
│   │    └─ mcp_server/ 把內建工具提供給 CLI Provider          │
│   └─ storage/        SQLite、keyring                        │
└────────────────────────────────────────────────────────────┘
       │                  │               │              │
       ▼                  ▼               ▼              ▼
 ADS/TFS、GitHub API   LLM API 服務   本機 CLI（claude 等）  MCP Server
                                                     （Email、行事曆、筆記…）
```

## 3. 行程模型

1. 使用者啟動 Tauri App；設定開機啟動時，由作業系統在登入後啟動，此時不顯示主視窗，直接常駐系統匣。
   （第二階段 F16 實作；目前只能手動啟動）
2. Tauri 以 sidecar 方式啟動 Python 後端執行檔，並傳入：
   - 一個可用的動態 port（命令列參數 `--port`）
   - 一組隨機產生的 session token（環境變數 `DEVCOLLEAGUE_SESSION_TOKEN`，不放在命令列，避免被其他行程看見）
3. 後端只監聽 `127.0.0.1`，所有請求都必須帶 `Authorization: Bearer <session token>`，避免本機其他程式或網頁存取。
4. 前端透過 Tauri 指令 `backend_info` 取得 port 與 token，輪詢 `/api/health` 直到後端就緒後開始呼叫。
   SSE 串流以 `fetch` 讀取（`EventSource` 無法帶 Authorization header）。
5. 關閉視窗時，App 縮到系統匣，後端繼續執行背景工作（Routine 排程、每 5 分鐘檢查一次事件提醒）。
   （第二階段 F16 實作；目前關閉視窗即結束 App）
6. 從系統匣選「結束」時，Tauri 負責結束後端子行程。
7. 後端以 `--exit-on-stdin-eof` 啟動，stdin 接到 Tauri 行程；Tauri 異常結束時 stdin 關閉，後端隨之退出，不會留下孤兒行程。

### 3.1 背景事件與系統通知（F16）

背景工作（Routine、事件提醒）的結果需要由後端主動送到桌面外殼：

- 後端提供一條長連線的 SSE 事件通道（例如 `/api/events`），送出「Routine 執行完成」「有新的提醒」等事件。
- 視窗縮到系統匣時只是隱藏，WebView 仍在執行；前端持續訂閱事件通道，收到事件後透過 Tauri 發送系統通知。
  點擊通知時顯示主視窗並開啟對應的對話或頁面。
- 隱藏中的 WebView 是否能穩定維持連線需在實作時驗證；若不可靠，改由 Tauri（Rust）端訂閱事件通道並發送通知。
- 系統匣、開機啟動、系統通知使用 Tauri 的官方外掛，實際用法以實作時的官方文件為準。

開發階段可以分開啟動：後端用 `python -m devcolleague` 執行，前端用 Vite dev server，不需要每次都打包。
`npm run tauri dev` 時，Tauri 直接以 `backend/.venv` 的 Python 啟動後端；發佈版才使用 PyInstaller 打包的 sidecar。

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

- 第一版支援 Claude（Anthropic API，使用官方 `anthropic` SDK）與 OpenAI Chat Completions 相容端點
  （`providers/api/openai_compat.py`，同時涵蓋 OpenAI 與 Ollama 等提供相容端點的本機模型）。
- Claude 預設模型為 `claude-opus-5-5`；支援的模型會啟用伺服器端 refusal fallback（`fallbacks: "default"`）。
- 每家各寫一個薄的 adapter（例如 `providers/api/anthropic.py`、`providers/api/openai_compat.py`），
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
- 實際的參數與輸出格式以實作時各 CLI 的官方文件為準。目前實作：
  - Claude Code：`claude -p --output-format stream-json --verbose --include-partial-messages --allowedTools Read Grep Glob`
  - Codex CLI：`codex exec --json --skip-git-repo-check -s read-only -`
- 提示（含攤平後的對話歷史）一律透過 stdin 傳入，不放在命令列參數。
- 非互動執行時只允許唯讀能力（Claude Code 限定 Read／Grep／Glob；Codex 使用 read-only sandbox），
  避免 CLI 在無人確認的情況下修改檔案或執行指令。
- 執行時指定工作目錄（`cwd`），讓 CLI 能讀取對應的本機 Repo。
- 啟動前偵測 CLI 是否已安裝（`--version`）、是否已登入（`claude auth status`、`codex login status`），並在設定畫面顯示狀態。
- 需處理逾時與取消（使用者中斷時結束子行程），預設逾時 600 秒。

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

- `search_pull_requests`：依條件查詢 PR（已實作）
- `list_work_items`：查詢 Work Item／Issue（已實作）
- `list_builds`：查詢最近的建置結果（已實作）
- `list_my_commits`：查詢我最近的 commit（已實作）
- `get_pull_request_diff`：取得 PR 的變更內容（第三階段，配合 F6／F7 實作）
- `get_build_log`：取得建置 log（第三階段，配合 F8 實作）

第二階段另外新增讓 LLM 能「行動」的工具，這些工具不經過 Integration 層（名稱為暫定，以實作為準）：

| 工具 | 用途 | 風險等級 |
|---|---|---|
| `create_daily_report_draft` | 產生指定日期的日報告草稿（沿用 F15 的彙整與進度計算） | 本機寫入 |
| `get_daily_report` | 讀取指定日期的日報告與其狀態（草稿／定稿） | 唯讀 |
| `notify_user` | 發送系統通知 | 通知 |
| `search_memory`／`save_memory` | 查詢、記下記憶（F10） | 唯讀／本機寫入 |
| `list_routines`／`save_routine`／`disable_routine` | 查詢、建立或修改、停用 Routine（F14） | 唯讀／需使用者確認 |

工具只回傳 LLM 需要的欄位，避免浪費 token。

### 5.4 平台 API 對應（第一版）

| 查詢 | ADS/TFS（REST API 6.0） | GitHub |
|---|---|---|
| 目前使用者 | `_apis/connectionData` | `GET /user` |
| 我建立／待我審查的 PR | 各專案 `_apis/git/pullrequests`（`creatorId`／`reviewerId`） | Search API `is:pr author:`／`review-requested:` |
| 指派給我的項目 | WIQL `[System.AssignedTo] = @Me` | Search API `is:issue assignee:` |
| 建置 | 各專案 `_apis/build/builds` | 各 repo `actions/runs` |
| 我的 commit | 各 repo `commits?searchCriteria.author=` | Search API `search/commits` |

- ADS/TFS 連線的 `base_url` 為 Collection 網址；可在連線設定中指定要查詢的專案，未指定時查詢全部專案（上限 30 個）。
- GitHub 連線可指定要查詢建置的 repo，未指定時取最近推送的 5 個 repo。
- 單一專案無權限時略過；單一連線失敗時在 UI 顯示錯誤，不影響其他連線。

### 5.5 日報告（F15）

`services/daily_report.py` 沿用上述的統一資料模型與 `ActivityService`，流程如下：

1. 依報告日期取得當天的 commit、PR、異動的 Work Item。
2. 呼叫 Integration 的 `list_work_item_progress`，依父子階層算出上層 Work Item 的進度：
   異動的項目若有子項目，以它本身為計算單位，否則以它的父項目為單位；
   進度 = 已完成的子項目數 ÷ 子項目總數，已移除的子項目不列入。
   平台沒有父子階層時（例如 GitHub Issue）回傳空清單，由上層改用前一份日報告的數字。
3. 依「日報告專案對應」設定，以項目的平台專案名稱（沒有專案時用 repo 名稱）分組；對應不到的歸在「未分類」。
4. 連同手動補充內容與最近一份日報告，先以 `activity` 事件送到前端揭露，再交給 LLM 產生 Markdown
   （每個專案一個標題，底下為巢狀條列）。百分比由後端算好後放在資料中，提示詞要求 LLM 原樣使用。
5. 前端依標題切成各專案的區塊，可編輯並逐一複製（同時寫入 HTML 與純文字到剪貼簿）。

ADS/TFS 的父子階層以 `_apis/wit/workitems?$expand=relations` 取得，
連結類型為 `System.LinkTypes.Hierarchy-Reverse`（父）與 `System.LinkTypes.Hierarchy-Forward`（子）。

草稿與定稿（第二階段，配合 F14 實作）：

- 每天的日報告帶有狀態：`draft`（草稿）或 `final`（定稿）。
- Routine 透過 `create_daily_report_draft` 產生的內容一律為草稿；當天已有使用者編輯過的內容或定稿時不覆蓋，
  工具回傳「已存在」讓 LLM 據此回報。
- 使用者在日報告頁補充、編輯並儲存後成為定稿。帶入「最近一份日報告」時優先使用定稿。

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

### 6.5 把內建工具提供給 CLI（F17）

CLI Provider 的 `custom_tools` 為 False，Gateway 無法替它執行 tool loop，因此 CLI 預設看不到 `tools/` 中的內建工具。
為了讓 CLI 模式也能執行 Routine 與事件提醒，後端另外扮演 MCP Server 的角色（`tools/mcp_server/`）：

- 以 Streamable HTTP 掛在後端同一個 FastAPI 行程上，只監聽 `127.0.0.1`，並驗證 token。
- 提供的工具來自同一個工具註冊表，與 API 模式看到的工具一致；工具的風險分級與無人值守的限制同樣適用。
- 執行 CLI 時，把這個 MCP Server 加進傳給 CLI 的 MCP 設定，並在允許的工具清單中加入這組工具；
  CLI 仍不得修改檔案或執行指令。
- token 以環境變數傳給 CLI 子行程，不寫入暫存設定檔。
- 與 6.1 的 MCP Client 方向相反：6.1 是 DevColleague 使用別人的工具，這裡是把自己的工具給 CLI 使用。
- 各 CLI 指定 MCP Server 與允許工具的參數，以實作時官方文件為準；這是第二階段中不確定性最高的部分，
  動工前需先查證。

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

Routine 與事件提醒是「LLM 驅動」的核心：由程式決定何時喚醒 LLM，由 LLM 決定做什麼、是否通知使用者。

| 喚醒來源 | 由誰判斷時機 | 說明 |
|---|---|---|
| 時間 | 排程器比對 cron | 例如工作日 08:30 的晨間流程；錯過時於下次啟動補跑一次 |
| 平台變化 | 排程器輪詢並比對快照 | 有變化才喚醒 LLM，見 8.4 |
| 使用者交代 | 對話中的 LLM | 透過 `save_routine` 等工具建立或調整 Routine，套用前請使用者確認 |

### 8.1 Routine 定義

Routine 同樣以 Markdown 檔定義，放在使用者資料目錄的 `routines/` 下：

```markdown
---
id: morning-brief
name: 晨間簡報
role: butler               # 執行角色（F13 完成後生效；在此之前以預設的助理設定執行）
provider: claude-api       # 選填，無人值守執行使用的 Provider；未指定時使用預設 Provider
schedule: "30 8 * * 1-5"   # cron 格式；省略時只能手動執行
command: /morning          # 選填，在對話中輸入即可手動觸發
notify: true               # 完成後是否發送系統通知
catch_up: true             # App 未執行而錯過時，下次啟動是否補跑一次
allow_side_effects: []     # 無人值守時允許使用的、會修改外部狀態的工具，預設為空
---

今天是 {{today}}，上次執行時間是 {{last_run_at}}。
請產生上一個工作日的日報告草稿，並整理今天的待辦：待我審查的 PR、指派給我的 Work Item、昨晚失敗的建置……
```

- 範本變數由後端在執行前代入，第一版提供 `{{today}}`、`{{yesterday}}`、`{{last_run_at}}`。
- 內建範本：晨間流程，以及 F4 的站會報告與週報。
- 提示只描述目標，要查哪些資料、是否需要產生草稿、通知內容怎麼寫，由 LLM 透過工具自行決定。
- LLM 透過 `save_routine` 建立或修改 Routine 時，後端寫入同一種 Markdown 檔，與手動編輯的結果一致。

### 8.2 執行流程

1. 排程器到點（或使用者手動觸發）時，代入範本變數，開一段新對話並送出；有指定角色時以該角色執行。
2. 執行過程與一般對話相同（可呼叫工具；F13 完成後可派工），結果存成一段對話。
3. 若 `notify` 為真，發送系統通知；點擊通知開啟該對話，使用者可接續追問。
4. 執行紀錄（開始／結束時間、成功或失敗、對應對話、送給 LLM 的資料與工具呼叫過程）寫入 SQLite。

無人值守時沒有人能確認，因此依工具的風險等級決定能否執行：

| 風險等級 | 例子 | 無人值守時 |
|---|---|---|
| 唯讀 | 查詢 PR、讀取日報告、查詢記憶 | 允許 |
| 本機寫入 | 產生日報告草稿、記下記憶 | 允許（不覆蓋使用者編輯過的內容） |
| 通知 | 發送系統通知 | 允許 |
| 修改外部狀態 | PR 留言、寄信、建立事件 | 只有列在 `allow_side_effects` 中的才能執行，其餘拒絕並在結果中說明 |
| 改變自動行為 | 建立、修改、停用 Routine | 不允許；只能在使用者參與的對話中經確認後執行 |

資料揭露：手動執行的功能（例如工作摘要、日報告頁）仍在送出前顯示資料；無人值守執行時無法事前顯示，
改為把當次送給 LLM 的提示、工具呼叫與工具結果完整存入執行紀錄，使用者可在 UI 事後查看。

Provider：使用 Routine 指定的 Provider，未指定時使用預設 Provider。Provider 的 `custom_tools` 為 False 時（CLI 模式），
需透過 6.5 的 MCP 轉接取得工具；F17 完成前，這類 Provider 不能執行無人值守的 Routine，並在設定畫面提示。

### 8.3 背景排程器

- `scheduler/` 是與 F9 事件提醒共用的背景排程器，隨後端啟動，在 App 縮到系統匣時繼續運作。
- 啟動時比對每個 Routine 的上次執行時間與排程，若有錯過且 `catch_up` 為真，補跑一次（不會補跑多次）。
- 同一個 Routine 同時只會有一個執行中的實例。

### 8.4 事件提醒（F9）

1. 排程器定期（預設每 5 分鐘）透過 Integration 層查詢：待我審查的 PR、被提及的項目、最近的建置、我建立的 PR。
2. 由程式與上次保存的快照比對，找出新的事件：新的審查請求或提及、新的建置失敗、PR 擱置超過指定天數。
   同一個事件只會產生一次，不重複提醒。
3. 沒有新事件時到此結束，不呼叫 LLM。
4. 有新事件時喚醒 LLM，提供事件內容與相關記憶，由 LLM 判斷是否值得通知、如何摘要，
   需要時可再呼叫唯讀工具查詢細節，最後以 `notify_user` 通知或決定不打擾。
5. 過程與結果同樣寫入執行紀錄，工具權限與資料揭露的規則同 8.2。

- 每日喚醒 LLM 的次數設有上限；達到上限後，當天其餘事件改以固定格式的通知顯示，不再呼叫 LLM。
- 短時間內的多個事件合併成一次喚醒。

## 9. 資料儲存

| 資料 | 儲存位置 |
|---|---|
| Token、API Key、MCP Server 機密值 | 作業系統憑證儲存區（`keyring`） |
| 連線設定、Provider 設定、MCP Server 設定 | SQLite |
| 角色、Routine 定義 | 使用者資料目錄下的 `roles/`、`routines/`（Markdown 檔） |
| 對話紀錄（含 Routine 產生的對話） | SQLite |
| Routine 與事件提醒的執行紀錄（含送給 LLM 的資料與工具呼叫過程） | SQLite |
| 事件提醒的平台狀態快照、已提醒過的事件 | SQLite |
| 平台資料快取 | SQLite（附更新時間，背景定期更新） |
| 日報告專案對應、每天的日報告內容（含草稿／定稿狀態）與手動補充 | SQLite |
| 個人記憶、知識庫 | SQLite，使用 FTS5 全文搜尋（之後再評估向量搜尋） |

資料庫檔案放在使用者資料目錄（例如 Windows 的 `%APPDATA%\DevColleague\`）。

### 9.1 記憶（F10）

- 每筆記憶包含：類型（偏好、進行中的工作、設計決策、踩坑筆記）、內容、來源（使用者手動新增，或助理在哪一段對話中記下）、
  建立與更新時間。
- LLM 透過 `save_memory` 記下、`search_memory` 查詢；使用者可在 UI 查看、修改、刪除所有記憶。
- 對話、Routine、事件提醒開始時，後端以當次的提示內容做全文搜尋，把相關記憶放進 system prompt；
  偏好類的記憶數量少，固定全部帶入。
- 記憶內容同樣不得包含 Token、API Key 等機密。

## 10. 安全性設計

- 機密資料不以明文寫入資料庫、設定檔或 log。
- 後端只監聽 `127.0.0.1`，並驗證 session token。
- 呼叫 CLI 與啟動 MCP Server 時以參數陣列傳遞，不經過 shell 組字串，避免指令注入。
- 送給外部 LLM 的內容（例如 PR diff、build log）需在 UI 讓使用者知道：手動執行時於送出前顯示；
  無人值守執行時存入執行紀錄，可事後查看。
- MCP Server 是第三方程式，新增時需在 UI 提示使用者確認其來源。
- 有副作用的工具預設需經使用者確認；無人值守執行時只開放唯讀、本機寫入與通知，
  會修改外部狀態的工具需在 Routine 中明確允許，改變自動行為的工具（Routine 管理）一律需使用者確認。
- 提供給 CLI 的內建工具 MCP Server 只監聽 `127.0.0.1` 並驗證 token。
- 角色只能使用工具白名單內的工具。

## 11. 建議的專案結構

```
DevColleague/
├─ docs/                     # 專案文件
├─ app/                      # Tauri + React 前端
│  ├─ src/                   # React 原始碼
│  └─ src-tauri/             # Tauri（Rust）設定與程式
├─ backend/                  # Python FastAPI 後端（uv 管理相依）
│  ├─ devcolleague/
│  │  ├─ __main__.py         # 進入點（sidecar 啟動）
│  │  ├─ app.py              # FastAPI 應用程式、CORS
│  │  ├─ container.py        # 組裝各元件
│  │  ├─ api/
│  │  ├─ services/
│  │  ├─ integrations/
│  │  ├─ llm/
│  │  ├─ agents/
│  │  ├─ routines/
│  │  ├─ scheduler/
│  │  ├─ tools/
│  │  │  ├─ mcp/
│  │  │  └─ mcp_server/
│  │  └─ storage/
│  ├─ defaults/              # 內建 Routine 範本（第二階段）與角色範本（第四階段），首次啟動時複製到使用者資料目錄
│  ├─ scripts/               # PyInstaller 打包腳本
│  └─ tests/
└─ CLAUDE.md
```

## 12. 打包與發佈

1. 使用 PyInstaller 把後端打包成單一執行檔（`backend/scripts/build_sidecar.py`），
   輸出為 `app/src-tauri/binaries/devcolleague-backend-<target triple>.exe`。
2. 將執行檔設定為 Tauri 的 sidecar（`externalBin`）。此設定放在 `tauri.release.conf.json`，
   只在打包時合併，開發與 `cargo check` 不需要先產生 sidecar。
3. 使用 Tauri 打包成 Windows 安裝檔（`npm run tauri:build`，NSIS）。
