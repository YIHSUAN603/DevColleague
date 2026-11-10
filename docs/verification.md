# 第二階段功能實測紀錄

日期：2026-11-10（台北時間）。透過 Playwright 操作實際 React 頁面與 FastAPI 後端，
使用已登入的 Claude Code CLI 2.1.322 執行真實模型與 MCP 工具呼叫，未模擬模型回覆。

## 測試環境

- 網頁：`http://127.0.0.1:1420`；後端：`127.0.0.1:8876`。
- 獨立資料目錄：`backend/build/ui-check`，未使用正式應用程式資料。
- 平台連線為空；提示、記憶與日報告皆為合成測試內容。
- 新增的兩個測試 Routine 保持停用、無排程；事件輪詢測試結束後已還原停用。

## 已通過

| 項目 | 實際觀察 |
|---|---|
| Provider | 設定頁偵測 Claude Code 已登入，按「測試」收到真實回覆 |
| 例行流程 | 從介面新增 `e2e-smoke`，按立即執行；執行中按鈕停用，完成後保存對話 |
| 自動引用記憶 | 執行紀錄的系統提示包含先前手動建立的繁體中文與草稿確認偏好 |
| MCP 查詢與寫入記憶 | 真實呼叫 `search_memory`、`save_memory`；記憶頁顯示新內容及來源對話 |
| 日報告草稿 | 真實呼叫 `save_daily_report_draft`，回傳 `saved=true`；日報告頁顯示草稿及預覽 |
| 讀回報告 | `read_daily_report` 回傳與草稿相同的內容與狀態 |
| 結果對話與續問 | 執行紀錄可開啟結果對話，接續訊息成功呼叫 `save_routine` |
| 變更確認 | 提案產生後，`e2e-confirm.md` 不存在；按套用後才建立，設定仍為停用、無排程 |
| 對話指令 | 在對話輸入 `/e2e`，立即啟動第二次背景執行並建立另一段結果對話 |
| 定稿保護 | 手動編輯測試草稿並存為定稿後，再執行相同流程；工具回傳 `saved=false`，人工內容與 final 狀態均保留 |
| 執行稽核 | 兩次流程均有成功紀錄，保存工具名稱、輸入、完整結果；每次四個工具皆無錯誤 |
| 無事件輪詢 | 在無平台連線的測試環境暫時啟用輪詢，顯示新事件 0 筆、無錯誤，沒有新增 event 類型 LLM 執行；之後還原停用 |
| 基本 UI | 記憶新增、搜尋、編輯；背景助理檢視與編輯；1440／1024 寬度無水平溢出 |

兩次背景執行紀錄：

- `5eafbacbc083490bbeba2aebaa465fe8`：16:15:55 至 16:16:49，成功；首次草稿寫入成功。
- `680dc0d0474a41f0907179f031e49e76`：16:19:35 至 16:20:29，成功；保留人工定稿。

以 SQLite 唯讀查詢交叉驗證：兩次執行均 succeeded、首次草稿工具 saved=true、第二次 saved=false、
日報告仍為 final 且包含人工確認文字、event 類型執行數為 0。

## 截圖

- [流程結果對話](../artifacts/functional-test/routine-result.png)
- [MCP 寫入的記憶](../artifacts/functional-test/mcp-memory.png)
- [Routine 待確認設定](../artifacts/functional-test/routine-proposal-before.png)
- [人工定稿](../artifacts/functional-test/daily-final.png)
- [再次執行保留定稿](../artifacts/functional-test/final-report-protection.png)

## 尚未驗證的範圍

- Codex CLI：此測試行程顯示未登入，未執行真實 Codex 模型。
- 正式 ADS/TFS、GitHub 資料、非空平台活動的晨間摘要、實際平台變動通知。
- 桌面系統通知與點擊跳轉、系統匣常駐、開機啟動：本次使用瀏覽器，沒有 Tauri 外殼。
- cron 到點、關閉後補跑及無人值守工具拒絕：已有自動測試，本次未另做真實模型人工驗收。
- console 仍有既有的 `favicon.ico` 404，與功能 API 無關；操作過程的功能 API 請求成功。

UI 檢查發現核取方塊與文字上下排列，已在 `label.inline` 明確設定 `flex-direction: row`；
修正後重新截圖，並通過前端 build／lint。先前完整後端回歸共 57 項通過；本次功能實測未修改後端程式。
