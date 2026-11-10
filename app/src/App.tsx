import { useEffect, useState } from 'react'
import { waitForBackend } from './api/backend'
import { startNotificationBridge } from './api/notifications'
import { ChatPage } from './pages/ChatPage'
import { DailyReportPage } from './pages/DailyReportPage'
import { DashboardPage } from './pages/DashboardPage'
import { SettingsPage } from './pages/SettingsPage'
import { SummaryPage } from './pages/SummaryPage'
import { MemoryPage } from './pages/MemoryPage'
import { RoutinesPage } from './pages/RoutinesPage'
import { errorMessage } from './utils'

type Page = 'dashboard' | 'chat' | 'summary' | 'daily' | 'settings' | 'routines' | 'memory'

const NAV: { id: Page; label: string; icon: string }[] = [
  { id: 'dashboard', label: '儀表板', icon: '▦' },
  { id: 'chat', label: 'AI 對話', icon: '✦' },
  { id: 'summary', label: '工作摘要', icon: '☰' },
  { id: 'daily', label: '日報告', icon: '✎' },
  { id: 'routines', label: '背景助理', icon: '◷' },
  { id: 'memory', label: '個人記憶', icon: '◇' },
  { id: 'settings', label: '設定', icon: '⚙' },
]

export default function App() {
  const [page, setPage] = useState<Page>('dashboard')
  const [backend, setBackend] = useState<'starting' | 'ready' | string>('starting')

  // 點擊通知要開啟的對話；以物件包裝，重複開啟同一段對話時也會觸發
  const [openConversation, setOpenConversation] = useState<{ id: string } | null>(null)

  useEffect(() => {
    waitForBackend()
      .then(() => setBackend('ready'))
      .catch((err) => setBackend(errorMessage(err)))
  }, [])

  // 後端就緒後開始接收背景通知（F16）
  useEffect(() => {
    if (backend !== 'ready') return
    return startNotificationBridge((target) => {
      if (!NAV.some((n) => n.id === target.page)) return
      setPage(target.page as Page)
      if (target.page === 'chat' && target.conversation_id) {
        setOpenConversation({ id: target.conversation_id })
      }
    })
  }, [backend])

  if (backend !== 'ready') {
    return (
      <div className="splash">
        <h1>DevColleague</h1>
        {backend === 'starting' ? (
          <p className="muted">正在啟動後端服務…</p>
        ) : (
          <p className="error">無法連線到後端：{backend}</p>
        )}
      </div>
    )
  }

  return (
    <div className="layout">
      <nav className="sidebar">
        <div className="brand">DevColleague</div>
        {NAV.map((n) => (
          <button key={n.id} className={page === n.id ? 'nav active' : 'nav'} onClick={() => setPage(n.id)}>
            <span className="nav-icon">{n.icon}</span>
            {n.label}
          </button>
        ))}
      </nav>
      <main className="content">
        {/* 對話頁保持掛載，切換頁面時不中斷進行中的串流 */}
        <div hidden={page !== 'chat'} className="page-fill">
          <ChatPage active={page === 'chat'} openRequest={openConversation} />
        </div>
        {page === 'dashboard' && <DashboardPage onOpenSettings={() => setPage('settings')} />}
        {page === 'summary' && <SummaryPage />}
        {page === 'daily' && <DailyReportPage />}
        {page === 'settings' && <SettingsPage />}
        {page === 'memory' && <MemoryPage />}
        {page === 'routines' && <RoutinesPage onOpenConversation={(id) => {
          setOpenConversation({ id })
          setPage('chat')
        }} />}
      </main>
    </div>
  )
}
