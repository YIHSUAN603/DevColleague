# CLAUDE.md

本文件提供 Claude Code 在此專案中工作時的指引。

## 專案簡介

DevColleague 是給開發者個人使用的桌面 AI 助理，整合 ADS/TFS 與 GitHub，
並以 LLM 為核心提供儀表板、AI 對話、工作摘要、PR 助理等功能。

- 需求規格：[docs/requirements.md](docs/requirements.md)
- 架構設計：[docs/architecture.md](docs/architecture.md)

目前已完成第一階段 MVP（F1–F5）、F15 與第二階段（F16、F14、F9、F10、F17）的程式實作。
背景排程與事件提醒預設停用，需由使用者在「背景助理」啟用；真實平台與 CLI 的端到端驗收狀態見 README。

## 常用指令

- 後端測試：`cd backend && uv run pytest`
- 前端檢查：`cd app && npm run build && npm run lint`
- 啟動桌面 App（開發模式）：`cd app && npm run tauri dev`
- 打包：`cd backend && uv run python scripts/build_sidecar.py`，再 `cd app && npm run tauri:build`
- 詳細說明見 [README.md](README.md)

## 技術棧

- 桌面外殼：Tauri 2
- 前端：React + Vite + TypeScript（位於 `app/`）
- 後端：Python + FastAPI（位於 `backend/`），以 Tauri sidecar 方式執行
- 資料庫：SQLite；機密資料使用作業系統憑證儲存區（`keyring`）
- LLM：同時支援 API 模式（每家自行撰寫 adapter，不使用 LiteLLM）與 CLI 模式（Claude Code、Codex CLI）
- 打包：後端以 PyInstaller 打包，作為 Tauri sidecar 發佈

## 語言規範

- 專案文件、程式碼註解、commit message 一律使用**繁體中文（台灣用語）**。
- 程式碼中的識別字（變數、函式、類別名稱等）使用英文。

## 架構原則

- 上層功能只依賴 `LLMProvider` 介面與 Provider 宣告的能力（capabilities），不直接依賴特定 LLM 廠商或 CLI。
- 上層功能只使用統一資料模型（`PullRequest`、`WorkItem`、`Build`、`Commit`），不直接依賴 ADS 或 GitHub 的原始格式。
- 新增平台或 LLM Provider 時，應只需新增實作，不需修改上層功能。

## 安全性規範

- Token、API Key 不得以明文寫入資料庫、設定檔、log 或原始碼。
- 後端只監聽 `127.0.0.1`，所有 API 都需驗證 session token。
- 呼叫外部 CLI 時以參數陣列傳遞，不透過 shell 組合指令字串。

## 文件維護

- 需求或架構有變更時，同步更新 `docs/` 中對應的文件。
- 重要的技術決策記錄在 `docs/requirements.md` 的「決策紀錄」，包含日期、決定與理由。
