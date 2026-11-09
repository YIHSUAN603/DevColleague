import { useEffect, useState } from 'react'
import { get } from '../api/backend'
import type { Provider, ProvidersResponse } from '../api/types'

interface Props {
  providerId: string | null
  model: string | null
  onChange: (providerId: string | null, model: string | null) => void
  disabled?: boolean
  /** 值改變時重新讀取 Provider 清單（例如切回保持掛載的對話頁時） */
  refreshKey?: unknown
}

/** 選擇 LLM Provider 與模型；未選擇時使用預設 Provider 與其預設模型 */
export function ProviderPicker({ providerId, model, onChange, disabled, refreshKey }: Props) {
  const [data, setData] = useState<ProvidersResponse | null>(null)

  useEffect(() => {
    get<ProvidersResponse>('/providers').then(setData).catch(() => setData(null))
  }, [refreshKey])

  if (!data) return null
  if (data.providers.length === 0) {
    return <span className="muted">尚未設定 LLM Provider，請到「設定」新增</span>
  }

  const effectiveId = providerId ?? data.default_provider_id
  const current: Provider | undefined = data.providers.find((p) => p.id === effectiveId)
  const listId = `models-${current?.id ?? 'none'}`

  return (
    <div className="provider-picker">
      <select
        value={providerId ?? ''}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value || null, null)}
        title="LLM Provider"
      >
        <option value="">
          預設（{data.providers.find((p) => p.id === data.default_provider_id)?.name ?? '未設定'}）
        </option>
        {data.providers.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
      <input
        list={listId}
        value={model ?? ''}
        disabled={disabled}
        placeholder={current?.default_model ?? '預設模型'}
        onChange={(e) => onChange(providerId, e.target.value || null)}
        title="模型（留空使用 Provider 預設模型）"
      />
      <datalist id={listId}>
        {current?.suggested_models.map((m) => <option key={m} value={m} />)}
      </datalist>
    </div>
  )
}
