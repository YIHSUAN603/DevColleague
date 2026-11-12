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

第二階段（LLM 驅動的助理）已實作：

| 功能 | 說明 |
|---|---|
| F16 背景常駐與通知 | 關閉視窗縮到系統匣、背景繼續執行，從系統匣選「結束」才停止；開機自動啟動（可在設定頁關閉）；系統通知，點擊開啟對應頁面 |
| F14 例行流程 | cron 排程、啟動補跑一次、手動／對話指令執行；結果可接續對話；自然語言變更需按確認卡片套用 |
| F9 事件提醒 | 背景比對審查請求、提及、建置失敗與擱置 PR；合併喚醒、持久去重、每日上限與固定格式通知 |
| F10 個人記憶 | 偏好、工作進度、決策與筆記；SQLite FTS5 中英文搜尋；對話與背景工作自動引用，可在介面管理 |
| F17 CLI 工具轉接 | 同一行程的 Streamable HTTP MCP；每次執行獨立短效憑證與工具權限；Claude Code、Codex CLI 可使用內建工具 |

### 使用開發助手

第三階段（開發流程輔助）已實作，從左側「開發助手」使用：

| 功能 | 說明 |
|---|---|
| F6 PR 助理 | 選擇本機 Repo 與來源／目標分支產生標題、描述；摘要平台 PR；確認後建立到 GitHub 或 ADS/TFS |
| F7 AI Code Review | 審查本機分支或平台 PR 差異，貼上／載入 Markdown 規範；可編輯後確認發布為平台 PR 留言 |
| F8 建置失敗診斷 | 依 GitHub Actions Run ID 或 ADS/TFS Build ID 讀取記錄，分析錯誤與修正方向 |
| F11 本機 Repo 理解 | 選取檔案進行問答、搭配分支差異分析影響；可明確開放 CLI Provider 唯讀查閱工作目錄 |

先按「載入資料預覽」，檢查資料後再按「確認資料並開始分析」。PR 建立與留言另有發布確認，
模型無法自行呼叫這些寫入 API。本機需安裝 Git；分支差異以共同祖先到來源分支的已提交內容為準，
建立 PR 前需自行把分支推送到目標平台，程式不自動 push。

對話與背景助理也能透過 `get_pull_request_diff`、`get_build_log` 唯讀工具取得 PR 與建置詳細資料，
沿用既有的工具輸出與執行稽核。

問答可選取最多 30 個 Git 追蹤檔案（每個最多 200 KB）；選取檔案讀取目前工作目錄的內容。
開放 CLI 工作目錄時，CLI 可能再讀取其他檔案並傳給所選模型；未開放時 CLI 在空暫存目錄執行。
預覽快照保存於記憶體 30 分鐘，最多 30 份；長資料會截斷並標示範圍。平台與真實模型驗收限制見
[第三階段驗證紀錄](docs/verification-phase3.md)。

### 啟用背景助理

1. 到「設定」建立 LLM Provider 與平台連線。
2. 在「背景助理」編輯晨間流程、站會報告或週報範本，確認提示、Provider 與排程後啟用。範本預設停用，也可先按「立即執行」。
3. 在對話輸入 `/morning`、`/standup`、`/weekly` 可手動執行；或以自然語言交代排程，在出現的確認卡片檢查設定後套用。
4. 事件提醒需另外開啟，預設每 5 分鐘檢查、每日最多喚醒 LLM 20 次。沒有新事件不呼叫 LLM。
5. 在「執行紀錄」查看系統提示、引用記憶及工具資料，或開啟產生的對話接續追問。

晨間流程只新增日報告草稿，保留任何既有內容；在日報告頁確認或編輯後按「儲存為定稿」。
排程採本機時區；程式關閉期間不執行，啟動後依補跑設定最多補一次。

提及偵測涵蓋 GitHub Issue／PR 本文、討論與 review 留言，以及 ADS/TFS Work Item 留言與 PR threads；
舊版 TFS 不支援留言 API 時改讀工作項目更新歷程，無權限等問題會在事件提醒區顯示錯誤。
查詢沿用平台整合的筆數上限，並非平台所有歷史事件的完整同步。
CLI 必須先在終端機登入並使用支援相關旗標的版本。已用真實 Claude Code 2.1.322 完成 MCP 記憶讀寫、
日報告草稿、結果對話續問、Routine 變更確認、對話指令與定稿保護實測；畫面也已用 Playwright 驗證。
Codex CLI、真實平台事件與 Tauri 桌面通知等範圍仍待驗收，詳見 [功能實測紀錄](docs/verification.md)。

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
