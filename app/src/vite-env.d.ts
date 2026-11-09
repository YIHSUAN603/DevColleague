/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 瀏覽器開發模式（未透過 Tauri 啟動）時使用的後端 port */
  readonly VITE_BACKEND_PORT?: string
  /** 瀏覽器開發模式時使用的 session token，請放在 .env.local（不納入版控） */
  readonly VITE_SESSION_TOKEN?: string
}
