// 與 Python 後端溝通：HTTP + SSE，所有請求都帶 session token

import { invoke, isTauri } from '@tauri-apps/api/core'
import type { StreamEvent } from './types'

interface BackendInfo {
  port: number
  token: string
}

let infoPromise: Promise<BackendInfo> | null = null

const EVENT_IDLE_TIMEOUT_MS = 45000

async function loadBackendInfo(): Promise<BackendInfo> {
  if (isTauri()) {
    // 由 Tauri 啟動 sidecar 時產生的 port 與 token
    return invoke<BackendInfo>('backend_info')
  }
  // 瀏覽器開發模式：後端以 uvicorn 另外啟動，連線資訊放在 .env.local
  const port = Number(import.meta.env.VITE_BACKEND_PORT ?? 8765)
  const token = import.meta.env.VITE_SESSION_TOKEN ?? ''
  if (!token) {
    throw new Error('未設定 VITE_SESSION_TOKEN，請參考 README 設定 app/.env.local')
  }
  return { port, token }
}

function backendInfo(): Promise<BackendInfo> {
  infoPromise ??= loadBackendInfo()
  return infoPromise
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function buildRequest(path: string, init: RequestInit = {}): Promise<[string, RequestInit]> {
  const { port, token } = await backendInfo()
  const headers = new Headers(init.headers)
  headers.set('Authorization', `Bearer ${token}`)
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  return [`http://127.0.0.1:${port}/api${path}`, { ...init, headers }]
}

async function errorFrom(resp: Response): Promise<ApiError> {
  let message = `HTTP ${resp.status}`
  try {
    const body = await resp.json()
    if (typeof body.detail === 'string') message = body.detail
    else if (Array.isArray(body.detail)) message = body.detail.map((d: { msg: string }) => d.msg).join('；')
  } catch {
    // 非 JSON 回應，保留狀態碼訊息
  }
  return new ApiError(resp.status, message)
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const [url, req] = await buildRequest(path, init)
  const resp = await fetch(url, req)
  if (!resp.ok) throw await errorFrom(resp)
  return (await resp.json()) as T
}

export const get = <T>(path: string) => api<T>(path)
export const post = <T>(path: string, body?: unknown) =>
  api<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
export const patch = <T>(path: string, body: unknown) =>
  api<T>(path, { method: 'PATCH', body: JSON.stringify(body) })
export const put = <T>(path: string, body: unknown) =>
  api<T>(path, { method: 'PUT', body: JSON.stringify(body) })
export const del = <T>(path: string) => api<T>(path, { method: 'DELETE' })

/** 等待後端啟動完成（sidecar 啟動需要數秒） */
export async function waitForBackend(timeoutMs = 30000): Promise<void> {
  const deadline = Date.now() + timeoutMs
  let lastError: unknown
  while (Date.now() < deadline) {
    try {
      await get('/health')
      return
    } catch (err) {
      lastError = err
      if (err instanceof ApiError && err.status === 401) throw err
      await new Promise((r) => setTimeout(r, 500))
    }
  }
  throw lastError instanceof Error ? lastError : new Error('後端啟動逾時')
}

/**
 * 以 POST 送出請求並逐一處理 SSE 事件。
 * EventSource 無法帶 Authorization header，因此改用 fetch 讀取串流。
 */
export async function stream(
  path: string,
  body: unknown,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const [url, req] = await buildRequest(path, { method: 'POST', body: JSON.stringify(body), signal })
  const resp = await fetch(url, req)
  await readSse(resp, onEvent)
}

/** 逐一解析 SSE 事件；只有註解（例如心跳）的區塊會被略過 */
async function readSse(
  resp: Response,
  onEvent: (event: StreamEvent) => void,
  onChunk?: () => void,
): Promise<void> {
  if (!resp.ok || !resp.body) throw await errorFrom(resp)

  const reader = resp.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    onChunk?.()
    buffer += value.replace(/\r\n/g, '\n')
    let sep: number
    while ((sep = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, sep)
      buffer = buffer.slice(sep + 2)
      let type = 'message'
      const dataLines: string[] = []
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) type = line.slice(6).trim()
        else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
      }
      if (dataLines.length) onEvent({ type, data: JSON.parse(dataLines.join('\n')) })
    }
  }
}

/**
 * 訂閱後端的背景事件通道（F16），連線中斷時自動重連，直到 signal 中止。
 */
export async function subscribeEvents(onEvent: (event: StreamEvent) => void, signal: AbortSignal): Promise<void> {
  let delay = 1000
  while (!signal.aborted) {
    // 後端每 15 秒送一次心跳；超過 EVENT_IDLE_TIMEOUT_MS 沒收到任何資料就視為斷線並重連
    const attempt = new AbortController()
    const abortAttempt = () => attempt.abort()
    signal.addEventListener('abort', abortAttempt)
    let watchdog = setTimeout(abortAttempt, EVENT_IDLE_TIMEOUT_MS)
    const resetWatchdog = () => {
      clearTimeout(watchdog)
      watchdog = setTimeout(abortAttempt, EVENT_IDLE_TIMEOUT_MS)
    }
    try {
      const [url, req] = await buildRequest('/events', { signal: attempt.signal })
      const resp = await fetch(url, req)
      delay = 1000
      await readSse(resp, onEvent, resetWatchdog)
    } catch (err) {
      if (signal.aborted) return
      console.warn('背景事件通道中斷，稍後重新連線', err)
    } finally {
      clearTimeout(watchdog)
      signal.removeEventListener('abort', abortAttempt)
    }
    await new Promise((r) => setTimeout(r, delay))
    delay = Math.min(delay * 2, 30000)
  }
}
