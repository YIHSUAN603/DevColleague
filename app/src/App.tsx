import { useEffect, useState } from 'react'
import { waitForBackend } from './api/backend'
import { ChatPage } from './pages/ChatPage'
import { DashboardPage } from './pages/DashboardPage'
import { SettingsPage } from './pages/SettingsPage'
import { SummaryPage } from './pages/SummaryPage'
import { errorMessage } from './utils'

type Page = 'dashboard' | 'chat' | 'summary' | 'settings'

const NAV: { id: Page; label: string; icon: string }[] = [
  { id: 'dashboard', label: '儀表板', icon: '▦' },
  { id: 'chat', label: 'AI 對話', icon: '✦' },
  { id: 'summary', label: '工作摘要', icon: '☰' },
  { id: 'settings', label: '設定', icon: '⚙' },
]

export default function App() {
  const [page, setPage] = useState<Page>('dashboard')
  const [backend, setBackend] = useState<'starting' | 'ready' | string>('starting')

  useEffect(() => {
    waitForBackend()
      .then(() => setBackend('ready'))
      .catch((err) => setBackend(errorMessage(err)))
  }, [])

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
          <ChatPage />
        </div>
        {page === 'dashboard' && <DashboardPage onOpenSettings={() => setPage('settings')} />}
        {page === 'summary' && <SummaryPage />}
        {page === 'settings' && <SettingsPage />}
      </main>
    </div>
  )
}
