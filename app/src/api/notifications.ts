// 背景事件轉系統通知（F16）：訂閱後端事件通道，收到通知事件後交給 Tauri 顯示系統通知。
// 視窗縮到系統匣時只是隱藏，WebView 仍在執行，因此可以持續接收事件。

import { invoke, isTauri } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import { subscribeEvents } from './backend'
import type { AppNotification, NotificationTarget } from './types'

/** 與 Rust 端的 NOTIFICATION_ACTIVATED_EVENT 一致 */
const NOTIFICATION_ACTIVATED_EVENT = 'notification-activated'

/**
 * 開始接收背景通知；點擊通知時以 onOpen 通知呼叫端開啟對應位置。
 * 回傳停止接收的函式。
 */
export function startNotificationBridge(onOpen: (target: NotificationTarget) => void): () => void {
  if (!isTauri()) {
    // 瀏覽器開發模式沒有桌面外殼，不顯示系統通知
    return () => {}
  }

  const controller = new AbortController()
  void subscribeEvents((event) => {
    if (event.type !== 'notification') return
    const n = event.data as unknown as AppNotification
    invoke('show_notification', { title: n.title, body: n.body, target: n.target }).catch((err) =>
      console.warn('無法顯示系統通知', err),
    )
  }, controller.signal)

  const unlisten = listen<NotificationTarget | null>(NOTIFICATION_ACTIVATED_EVENT, (event) => {
    if (event.payload) onOpen(event.payload)
  })

  return () => {
    controller.abort()
    void unlisten.then((fn) => fn())
  }
}
