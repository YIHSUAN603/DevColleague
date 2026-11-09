import { useCallback, useEffect, useState } from 'react'
import { del, get, patch, post, put } from '../api/backend'
import type { Connection, Platform, Provider, ProvidersResponse, ProviderStatus, TestResult } from '../api/types'
import { errorMessage } from '../utils'

export function SettingsPage() {
  return (
    <div className="page">
      <div className="page-header">
        <h2>設定</h2>
      </div>
      <p className="muted small">Token 與 API Key 存放於作業系統的憑證儲存區（Windows 認證管理員），不會以明文寫入設定或資料庫。</p>
      <ConnectionsSettings />
      <ProvidersSettings />
    </div>
  )
}

/** 需按兩次才執行的刪除按鈕，避免誤刪 */
function DeleteButton({ onConfirm }: { onConfirm: () => void }) {
  const [armed, setArmed] = useState(false)
  useEffect(() => {
    if (!armed) return
    const t = setTimeout(() => setArmed(false), 3000)
    return () => clearTimeout(t)
  }, [armed])
  return (
    <button className={armed ? 'danger' : ''} onClick={() => (armed ? onConfirm() : setArmed(true))}>
      {armed ? '確定刪除？' : '刪除'}
    </button>
  )
}

function ResultText({ result }: { result: TestResult | null | 'pending' }) {
  if (result === null) return null
  if (result === 'pending') return <span className="muted small">測試中…</span>
  return <span className={result.ok ? 'ok small' : 'error small'}>{result.message}</span>
}

// ---------------- 平台連線 ----------------

const URL_PLACEHOLDER: Record<Platform, string> = {
  ado: 'Collection 網址，例如 https://devops.example.com/DefaultCollection',
  github: '留空使用 https://api.github.com；GHES 填 https://<主機>/api/v3',
}
const SCOPE_PLACEHOLDER: Record<Platform, string> = {
  ado: '要查詢的專案名稱，以逗號分隔（留空查詢全部專案）',
  github: '要查詢建置的 repo（owner/name），以逗號分隔（留空使用最近推送的 repo）',
}

interface ConnectionForm {
  kind: Platform
  name: string
  base_url: string
  scopes: string
  token: string
}

const emptyConnection: ConnectionForm = { kind: 'ado', name: '', base_url: '', scopes: '', token: '' }

function splitScopes(value: string): string[] {
  return value.split(',').map((s) => s.trim()).filter(Boolean)
}

function ConnectionsSettings() {
  const [items, setItems] = useState<Connection[]>([])
  const [form, setForm] = useState<ConnectionForm | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [results, setResults] = useState<Record<string, TestResult | 'pending'>>({})
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => setItems(await get<Connection[]>('/connections')), [])
  useEffect(() => {
    load().catch((err) => setError(errorMessage(err)))
  }, [load])

  const test = async (key: string, run: () => Promise<TestResult>) => {
    setResults((r) => ({ ...r, [key]: 'pending' }))
    try {
      const result = await run()
      setResults((r) => ({ ...r, [key]: result }))
    } catch (err) {
      setResults((r) => ({ ...r, [key]: { ok: false, message: errorMessage(err) } }))
    }
  }

  const save = async () => {
    if (!form) return
    setError(null)
    const body = { ...form, scopes: splitScopes(form.scopes) }
    try {
      if (editingId) {
        await patch(`/connections/${editingId}`, { ...body, token: form.token || null })
      } else {
        await post('/connections', body)
      }
      setForm(null)
      setEditingId(null)
      await load()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  return (
    <section className="card">
      <div className="card-title">
        <h3>平台連線（ADS/TFS、GitHub）</h3>
        {!form && (
          <button className="primary" onClick={() => setForm(emptyConnection)}>
            ＋ 新增連線
          </button>
        )}
      </div>
      {error && <div className="alert error">{error}</div>}
      <ul className="settings-list">
        {items.map((c) => (
          <li key={c.id}>
            <div>
              <strong>{c.name}</strong> <span className="tag">{c.kind === 'ado' ? 'ADS/TFS' : 'GitHub'}</span>
              <div className="muted small">
                {c.base_url}
                {c.scopes.length > 0 && ` · ${c.scopes.join(', ')}`}
                {!c.has_token && ' · ⚠ 未設定 Token'}
              </div>
              <ResultText result={results[c.id] ?? null} />
            </div>
            <div className="row-actions">
              <button onClick={() => void test(c.id, () => post<TestResult>(`/connections/${c.id}/test`))}>測試</button>
              <button
                onClick={() => {
                  setEditingId(c.id)
                  setForm({ kind: c.kind, name: c.name, base_url: c.base_url, scopes: c.scopes.join(', '), token: '' })
                }}
              >
                編輯
              </button>
              <DeleteButton onConfirm={() => void del(`/connections/${c.id}`).then(load)} />
            </div>
          </li>
        ))}
        {items.length === 0 && !form && <li className="muted">尚未設定連線</li>}
      </ul>

      {form && (
        <div className="form">
          <label>
            平台
            <select
              value={form.kind}
              disabled={!!editingId}
              onChange={(e) => setForm({ ...form, kind: e.target.value as Platform })}
            >
              <option value="ado">ADS／TFS（Azure DevOps Server）</option>
              <option value="github">GitHub</option>
            </select>
          </label>
          <label>
            名稱
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="例如：公司 ADS" />
          </label>
          <label>
            伺服器網址
            <input
              value={form.base_url}
              onChange={(e) => setForm({ ...form, base_url: e.target.value })}
              placeholder={URL_PLACEHOLDER[form.kind]}
            />
          </label>
          <label>
            查詢範圍
            <input
              value={form.scopes}
              onChange={(e) => setForm({ ...form, scopes: e.target.value })}
              placeholder={SCOPE_PLACEHOLDER[form.kind]}
            />
          </label>
          <label>
            Personal Access Token
            <input
              type="password"
              autoComplete="off"
              value={form.token}
              onChange={(e) => setForm({ ...form, token: e.target.value })}
              placeholder={editingId ? '留空表示不變更' : ''}
            />
          </label>
          <div className="form-actions">
            <ResultText result={results.new ?? null} />
            <button
              disabled={!form.token}
              onClick={() =>
                void test('new', () =>
                  post<TestResult>('/connections/test', { ...form, name: form.name || 'test', scopes: splitScopes(form.scopes) }),
                )
              }
            >
              測試連線
            </button>
            <button
              onClick={() => {
                setForm(null)
                setEditingId(null)
                setResults((r) => {
                  const next = { ...r }
                  delete next.new
                  return next
                })
              }}
            >
              取消
            </button>
            <button className="primary" disabled={!form.name || (!editingId && !form.token)} onClick={() => void save()}>
              儲存
            </button>
          </div>
        </div>
      )}
    </section>
  )
}

// ---------------- LLM Provider ----------------

interface ProviderForm {
  kind: string
  name: string
  base_url: string
  default_model: string
  api_key: string
}

function ProvidersSettings() {
  const [data, setData] = useState<ProvidersResponse | null>(null)
  const [statuses, setStatuses] = useState<Record<string, ProviderStatus | 'pending'>>({})
  const [tests, setTests] = useState<Record<string, TestResult | 'pending'>>({})
  const [form, setForm] = useState<ProviderForm | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const loadStatus = useCallback(async (p: Provider) => {
    setStatuses((s) => ({ ...s, [p.id]: 'pending' }))
    try {
      const status = await get<ProviderStatus>(`/providers/${p.id}/status`)
      setStatuses((s) => ({ ...s, [p.id]: status }))
    } catch (err) {
      setStatuses((s) => ({ ...s, [p.id]: { ready: false, detail: errorMessage(err) } }))
    }
  }, [])

  const load = useCallback(async () => {
    const resp = await get<ProvidersResponse>('/providers')
    setData(resp)
    resp.providers.forEach((p) => void loadStatus(p))
  }, [loadStatus])

  useEffect(() => {
    load().catch((err) => setError(errorMessage(err)))
  }, [load])

  if (!data) return null
  const kindMeta = (kind: string) => data.kinds.find((k) => k.kind === kind)
  const formKind = form ? kindMeta(form.kind) : undefined

  const save = async () => {
    if (!form) return
    setError(null)
    try {
      const body = {
        name: form.name,
        base_url: form.base_url || null,
        default_model: form.default_model || null,
        api_key: form.api_key || null,
      }
      if (editingId) await patch(`/providers/${editingId}`, body)
      else await post('/providers', { ...body, kind: form.kind })
      setForm(null)
      setEditingId(null)
      await load()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const runTest = async (id: string) => {
    setTests((t) => ({ ...t, [id]: 'pending' }))
    try {
      const result = await post<TestResult>(`/providers/${id}/test`)
      setTests((t) => ({ ...t, [id]: result }))
    } catch (err) {
      setTests((t) => ({ ...t, [id]: { ok: false, message: errorMessage(err) } }))
    }
  }

  return (
    <section className="card">
      <div className="card-title">
        <h3>LLM Provider</h3>
        {!form && (
          <button
            className="primary"
            onClick={() => setForm({ kind: 'anthropic', name: 'Claude', base_url: '', default_model: 'claude-opus-5-5', api_key: '' })}
          >
            ＋ 新增 Provider
          </button>
        )}
      </div>
      {error && <div className="alert error">{error}</div>}
      <ul className="settings-list">
        {data.providers.map((p) => {
          const status = statuses[p.id]
          return (
            <li key={p.id}>
              <div>
                <label className="inline">
                  <input
                    type="radio"
                    name="default-provider"
                    checked={data.default_provider_id === p.id}
                    onChange={() => void put('/providers/default', { provider_id: p.id }).then(load)}
                  />
                  <strong>{p.name}</strong>
                </label>{' '}
                <span className="tag">{p.mode === 'cli' ? 'CLI' : 'API'}</span>{' '}
                {data.default_provider_id === p.id && <span className="tag">預設</span>}
                <div className="muted small">
                  {kindMeta(p.kind)?.label}
                  {p.default_model && ` · 預設模型 ${p.default_model}`}
                  {p.base_url && p.kind === 'openai' && ` · ${p.base_url}`}
                </div>
                <div className="small">
                  {status === 'pending' || !status ? (
                    <span className="muted">檢查狀態中…</span>
                  ) : (
                    <span className={status.ready ? 'ok' : 'error'}>
                      {status.ready ? '● 可使用' : '● 無法使用'}
                      {status.version && ` · ${status.version}`}
                      {status.installed === false && ' · 未安裝'}
                      {status.logged_in === false && ' · 未登入'}
                      {status.detail && ` · ${status.detail}`}
                    </span>
                  )}
                </div>
                <ResultText result={tests[p.id] ?? null} />
              </div>
              <div className="row-actions">
                <button onClick={() => void runTest(p.id)} title="送出一個極短的請求（會消耗少量 token）">
                  測試
                </button>
                <button
                  onClick={() => {
                    setEditingId(p.id)
                    setForm({ kind: p.kind, name: p.name, base_url: p.base_url ?? '', default_model: p.default_model ?? '', api_key: '' })
                  }}
                >
                  編輯
                </button>
                <DeleteButton onConfirm={() => void del(`/providers/${p.id}`).then(load)} />
              </div>
            </li>
          )
        })}
        {data.providers.length === 0 && !form && <li className="muted">尚未設定 Provider</li>}
      </ul>

      {form && (
        <div className="form">
          <label>
            類型
            <select
              value={form.kind}
              disabled={!!editingId}
              onChange={(e) => {
                const meta = kindMeta(e.target.value)
                setForm({
                  ...form,
                  kind: e.target.value,
                  base_url: meta?.default_base_url ?? '',
                  default_model: meta?.suggested_models[0] ?? '',
                })
              }}
            >
              {data.kinds.map((k) => (
                <option key={k.kind} value={k.kind}>
                  {k.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            名稱
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </label>
          {form.kind === 'openai' && (
            <label>
              API 網址
              <input
                value={form.base_url}
                onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                placeholder="https://api.openai.com/v1；Ollama 為 http://127.0.0.1:11434/v1"
              />
            </label>
          )}
          <label>
            預設模型
            <input
              list="provider-models"
              value={form.default_model}
              onChange={(e) => setForm({ ...form, default_model: e.target.value })}
              placeholder={formKind?.mode === 'cli' ? '留空使用 CLI 本身的預設模型' : ''}
            />
            <datalist id="provider-models">
              {formKind?.suggested_models.map((m) => <option key={m} value={m} />)}
            </datalist>
          </label>
          {formKind?.needs_api_key && (
            <label>
              API Key{formKind.api_key_optional && '（本機模型可留空）'}
              <input
                type="password"
                autoComplete="off"
                value={form.api_key}
                onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                placeholder={editingId ? '留空表示不變更' : ''}
              />
            </label>
          )}
          {formKind?.mode === 'cli' && (
            <p className="muted small">CLI 模式使用 CLI 本身的登入狀態，請先在終端機完成登入（例如 claude、codex login）。</p>
          )}
          <div className="form-actions">
            <button
              onClick={() => {
                setForm(null)
                setEditingId(null)
              }}
            >
              取消
            </button>
            <button className="primary" disabled={!form.name} onClick={() => void save()}>
              儲存
            </button>
          </div>
        </div>
      )}
    </section>
  )
}
