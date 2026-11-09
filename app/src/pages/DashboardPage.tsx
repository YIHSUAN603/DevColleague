import { useCallback, useEffect, useMemo, useState } from 'react'
import { get, post } from '../api/backend'
import type { Build, Connection, DashboardData, DashboardResponse, Platform, PullRequest, WorkItem } from '../api/types'
import { errorMessage, formatDateTime, openExternal, timeAgo } from '../utils'

const PLATFORM_LABEL: Record<Platform, string> = { ado: 'ADS/TFS', github: 'GitHub' }

interface Filters {
  platform: '' | Platform
  scope: string // 專案或 Repo
}

type Scoped = { platform: Platform; project: string | null; repo: string | null }

function scopeOf(item: Scoped): string {
  return item.project ?? item.repo ?? ''
}

function matches(item: Scoped, f: Filters): boolean {
  if (f.platform && item.platform !== f.platform) return false
  if (f.scope && scopeOf(item) !== f.scope && item.repo !== f.scope) return false
  return true
}

export function DashboardPage({ onOpenSettings }: { onOpenSettings: () => void }) {
  const [data, setData] = useState<DashboardData | null>(null)
  const [fetchedAt, setFetchedAt] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [connections, setConnections] = useState<Connection[] | null>(null)
  const [filters, setFilters] = useState<Filters>({ platform: '', scope: '' })

  const refresh = useCallback(async () => {
    setRefreshing(true)
    setError(null)
    try {
      const fresh = await post<DashboardResponse>('/dashboard/refresh')
      setData(fresh.data)
      setFetchedAt(fresh.fetched_at)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    // 先顯示快取，再於背景更新
    void (async () => {
      try {
        const [cached, conns] = await Promise.all([
          get<DashboardResponse>('/dashboard'),
          get<Connection[]>('/connections'),
        ])
        setData(cached.data)
        setFetchedAt(cached.fetched_at)
        setConnections(conns)
        if (conns.length > 0) void refresh()
      } catch (err) {
        setError(errorMessage(err))
      }
    })()
  }, [refresh])

  const scopes = useMemo(() => {
    if (!data) return []
    const all = [...data.my_pull_requests, ...data.review_requests, ...data.work_items, ...data.builds]
    return [...new Set(all.filter((i) => !filters.platform || i.platform === filters.platform).map(scopeOf))]
      .filter(Boolean)
      .sort()
  }, [data, filters.platform])

  if (connections && connections.length === 0) {
    return (
      <div className="page">
        <h2>儀表板</h2>
        <div className="empty">
          <p>尚未設定任何平台連線。</p>
          <button className="primary" onClick={onOpenSettings}>
            前往設定 ADS/TFS 或 GitHub 連線
          </button>
        </div>
      </div>
    )
  }

  const filtered = data && {
    my: data.my_pull_requests.filter((i) => matches(i, filters)),
    review: data.review_requests.filter((i) => matches(i, filters)),
    work: data.work_items.filter((i) => matches(i, filters)),
    builds: data.builds.filter((i) => matches(i, filters)),
  }

  return (
    <div className="page">
      <div className="page-header">
        <h2>儀表板</h2>
        <div className="toolbar">
          <select
            value={filters.platform}
            onChange={(e) => setFilters({ platform: e.target.value as Filters['platform'], scope: '' })}
          >
            <option value="">所有平台</option>
            <option value="ado">ADS/TFS</option>
            <option value="github">GitHub</option>
          </select>
          <select value={filters.scope} onChange={(e) => setFilters({ ...filters, scope: e.target.value })}>
            <option value="">所有專案／Repo</option>
            {scopes.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <span className="muted small">
            {refreshing ? '更新中…' : fetchedAt ? `更新於 ${formatDateTime(fetchedAt)}` : ''}
          </span>
          <button onClick={() => void refresh()} disabled={refreshing}>
            重新整理
          </button>
        </div>
      </div>

      {error && <div className="alert error">{error}</div>}
      {data?.errors.map((e) => (
        <div key={e.connection_id} className="alert warn">
          連線「{e.connection}」：{e.message}
        </div>
      ))}

      {!filtered ? (
        <p className="muted">{refreshing ? '正在讀取資料…' : '尚無資料'}</p>
      ) : (
        <div className="grid">
          <Section title="待我審查的 PR" count={filtered.review.length}>
            {filtered.review.map((pr) => (
              <PrRow key={`${pr.connection_id}-${pr.id}`} pr={pr} showAuthor />
            ))}
          </Section>
          <Section title="我建立的 PR" count={filtered.my.length}>
            {filtered.my.map((pr) => (
              <PrRow key={`${pr.connection_id}-${pr.id}`} pr={pr} />
            ))}
          </Section>
          <Section title="指派給我的 Work Item／Issue" count={filtered.work.length}>
            {filtered.work.map((w) => (
              <WorkItemRow key={`${w.connection_id}-${w.id}`} item={w} />
            ))}
          </Section>
          <Section title="最近的建置／Pipeline" count={filtered.builds.length}>
            {filtered.builds.map((b) => (
              <BuildRow key={`${b.connection_id}-${b.id}`} build={b} />
            ))}
          </Section>
        </div>
      )}
    </div>
  )
}

function Section({ title, count, children }: { title: string; count: number; children: React.ReactNode }) {
  return (
    <section className="card">
      <h3>
        {title} <span className="badge">{count}</span>
      </h3>
      {count === 0 ? <p className="muted small">沒有項目</p> : <ul className="items">{children}</ul>}
    </section>
  )
}

function Row({ url, children }: { url: string; children: React.ReactNode }) {
  return (
    <li className="item" onClick={() => void openExternal(url)} title="在瀏覽器開啟">
      {children}
    </li>
  )
}

function PrRow({ pr, showAuthor }: { pr: PullRequest; showAuthor?: boolean }) {
  return (
    <Row url={pr.url}>
      <div className="item-title">
        {pr.is_draft && <span className="tag">草稿</span>}#{pr.id} {pr.title}
      </div>
      <div className="item-meta">
        <span className="tag">{PLATFORM_LABEL[pr.platform]}</span>
        {pr.project ? `${pr.project} / ` : ''}
        {pr.repo}
        {pr.source_branch && ` · ${pr.source_branch} → ${pr.target_branch}`}
        {showAuthor && ` · ${pr.author}`}
        {` · ${timeAgo(pr.created_at)}`}
      </div>
    </Row>
  )
}

function WorkItemRow({ item }: { item: WorkItem }) {
  return (
    <Row url={item.url}>
      <div className="item-title">
        #{item.id} {item.title}
      </div>
      <div className="item-meta">
        <span className="tag">{PLATFORM_LABEL[item.platform]}</span>
        {item.type} · {item.state} · {scopeOf(item)}
        {item.updated_at && ` · ${timeAgo(item.updated_at)}更新`}
      </div>
    </Row>
  )
}

const RESULT_LABEL: Record<string, string> = {
  succeeded: '成功',
  failed: '失敗',
  canceled: '取消',
  partially_succeeded: '部分成功',
}

function BuildRow({ build }: { build: Build }) {
  const label = build.status === 'completed' ? (RESULT_LABEL[build.result ?? ''] ?? build.result ?? '完成') : build.status === 'queued' ? '排隊中' : '執行中'
  const cls = build.result === 'failed' ? 'status failed' : build.result === 'succeeded' ? 'status ok' : 'status'
  return (
    <Row url={build.url}>
      <div className="item-title">
        <span className={cls}>{label}</span> {build.name}
      </div>
      <div className="item-meta">
        <span className="tag">{PLATFORM_LABEL[build.platform]}</span>
        {scopeOf(build)}
        {build.branch && ` · ${build.branch}`}
        {build.requested_by && ` · ${build.requested_by}`}
        {build.started_at && ` · ${timeAgo(build.started_at)}`}
      </div>
    </Row>
  )
}
