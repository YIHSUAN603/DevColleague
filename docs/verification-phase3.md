# 第三階段驗證紀錄

日期：2026-11-12。範圍為 F6、F7、F8、F11 的後端、React 介面及平台 API 轉接。

## 自動驗證

- 後端完整回歸：70 項通過、1 項跳過；跳過的是 Windows 未授權建立符號連結的跨目錄測試。
- 新增測試涵蓋 Git 分支與共同祖先差異、選取檔案與敏感路徑限制、無效 ref、預覽不呼叫模型、
  快照不受輸入變更影響、過期拒絕、憑證遮蔽、截斷標記、CLI 工作目錄能力與明確授權、
  外部發布需 session 與確認，以及 GitHub／ADS 的 PR、留言、差異與建置記錄合約。
- 唯讀開發工具涵蓋無人值守允許、執行稽核、輸入驗證、連線名稱解析與憑證遮蔽；
  PR 建立／留言不出現在模型工具清單。
- 前端 TypeScript、Vite 正式建置、ESLint 通過。

## 瀏覽器驗證

使用實際 React 與 FastAPI，獨立資料目錄 `backend/build/phase3-ui-data`。
平台 API 由 HTTP mock 回應、模型由合成 Provider 回應；不使用正式憑證、不建立真實 PR 或留言。

- Code Review：切換平台來源、連線與 Repo、補充規範，預覽顯示規範、PR 本文與 diff。
- 確認資料後才產生分析；結果可編輯，PR 留言需再開確認畫面；返回編輯後可重開並發布到 mock。
- Repo 問答：讀取本機 Git 追蹤檔案、選取 README、補充問題，預覽包含檔案與問題；產生分析後沒有外部發布操作。
- PR 建立：選擇本機分支並產生內容，編輯標題後開啟確認畫面，確認後寫入 mock。
- PR 摘要、建置失敗診斷與變更影響分析：均通過載入資料、預覽、確認與分析；建置預覽含失敗 log。
- 表單補上獨立的審查規範檔與文字欄位標籤。
- 1440 與 1024 寬度均無水平溢出；截圖：[1440](../artifacts/phase3-1440.png)、[1024](../artifacts/phase3-1024.png)。

## 資料範圍與限制

- 本機差異：共同祖先到來源分支的已提交內容，最多 4 MB；不包含未提交修改、不自動 fetch 或 push。
- 問答／影響分析：可選最多 30 個 Git 追蹤檔案，每個最多 200 KB，讀目前工作目錄內容；
  排除常見機密路徑與憑證檔、拒絕解析後落在 Repo 外的路徑及二進位檔案。
- CLI 工作目錄需明確授權且 Provider 宣告支援；CLI 可能再讀取其他檔案。
  未授權時在空暫存目錄執行。Provider 既有的唯讀／停用 shell 限制維持生效。
- ADS PR：以目標與來源 commit 的共同祖先重建 unified diff，最多前 100 個變更項目；
  二進位內容不供文字審查。GitHub 使用平台提供的 PR diff。
- GitHub 建置：最多查詢前 1000 個 job，載入前 10 個失敗 job 的 log，每份下載最多約 2 MB；
  下載重新導向使用獨立 HTTPS 連線，不轉送平台憑證。
- ADS 建置：載入最後 20 份 log，每份前 10000 行／200000 字元。
- 模型資料合計最多 160000 字元，超過會明確標示截斷；已知 session／平台／Provider 憑證會遮蔽。
  預覽快照僅存在記憶體，30 分鐘失效，最多保留 30 份。
- 建立 PR 與發布一般 PR 留言由使用者在介面確認，API 不提供給模型或背景 Routine。

## 尚未驗證

- 第三階段與真實 GitHub、ADS/TFS 的端到端讀寫及不同版本伺服器差異。
- 真實 Claude Code／Codex／API 模型在此頁面的分析品質與工作目錄操作。
- Tauri 安裝包內的完整第三階段操作；本次以瀏覽器驗證 React 介面。

## API 參考

- [GitHub PR API](https://docs.github.com/en/rest/pulls/pulls)
- [GitHub Actions job 與記錄](https://docs.github.com/en/rest/actions/workflow-jobs)
- [ADS/TFS commit diff](https://learn.microsoft.com/en-us/rest/api/azure/devops/git/diffs/get?view=azure-devops-rest-6.0)
- [ADS/TFS 檔案內容](https://learn.microsoft.com/en-us/rest/api/azure/devops/git/items/get?view=azure-devops-rest-6.0)
