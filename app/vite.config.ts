import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Tauri 開發模式固定使用 1420 port（需與 src-tauri/tauri.conf.json 的 devUrl 一致）
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    watch: { ignored: ['**/src-tauri/**'] },
  },
  envPrefix: ['VITE_', 'TAURI_ENV_'],
})
