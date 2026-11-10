import { useCallback, useEffect, useRef, useState } from 'react'
import { del, get, post, stream } from '../api/backend'
import type { Conversation, ConversationSummary, Message, StoredEvent } from '../api/types'
import { Markdown } from '../components/Markdown'
import { ProviderPicker } from '../components/ProviderPicker'
import { RoutineProposals } from '../components/RoutineProposals'
import { errorMessage, timeAgo } from '../utils'

const TOOL_LABEL: Record<string, string> = {
  search_pull_requests: '查詢 PR',
  list_work_items: '查詢 Work Item',
  list_builds: '查詢建置',
  list_my_commits: '查詢 commit',
}

export function ChatPage({ active, openRequest }: { active: boolean; openRequest: { id: string } | null }) {
  const [list, setList] = useState<ConversationSummary[]>([])
  const [current, setCurrent] = useState<Conversation | null>(null)
  const [input, setInput] = useState('')
  const [cwd, setCwd] = useState('')
  const [providerId, setProviderId] = useState<string | null>(null)
  const [model, setModel] = useState<string | null>(null)
  const [streaming, setStreaming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  const loadList = useCallback(async () => {
    setList(await get<ConversationSummary[]>('/conversations'))
  }, [])

  useEffect(() => {
    loadList().catch((err) => setError(errorMessage(err)))
  }, [loadList])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' })
  }, [current?.messages])

  // 點擊系統通知時開啟指定對話（例如例行流程的執行結果）；串流進行中則等結束後再切換
  const handledOpenRef = useRef<{ id: string } | null>(null)
  useEffect(() => {
    if (!openRequest || streaming || handledOpenRef.current === openRequest) return
    handledOpenRef.current = openRequest
    setError(null)
    Promise.all([get<Conversation>(`/conversations/${openRequest.id}`), loadList()])
      .then(([conv]) => {
        setCurrent(conv)
        setProviderId(conv.provider_id)
        setModel(conv.model)
      })
      .catch((err) => setError(errorMessage(err)))
  }, [openRequest, streaming, loadList])

  const open = async (id: string) => {
    if (streaming) return
    setError(null)
    const conv = await get<Conversation>(`/conversations/${id}`)
    setCurrent(conv)
    setProviderId(conv.provider_id)
    setModel(conv.model)
  }

  const newConversation = () => {
    if (streaming) return
    setCurrent(null)
    setError(null)
  }

  const remove = async (id: string) => {
    if (streaming) return
    await del(`/conversations/${id}`)
    if (current?.id === id) setCurrent(null)
    await loadList()
  }

  const updateAssistant = (fn: (m: Message) => Message) =>
    setCurrent((c) => {
      if (!c) return c
      const messages = [...c.messages]
      messages[messages.length - 1] = fn(messages[messages.length - 1])
      return { ...c, messages }
    })

  const send = async () => {
    const content = input.trim()
    if (!content || streaming) return
    setError(null)
    setInput('')
    setStreaming(true)
    const controller = new AbortController()
    abortRef.current = controller
    try {
      const conv = current ?? (await post<Conversation>('/conversations', { provider_id: providerId, model }))
      setCurrent({
        ...conv,
        messages: [
          ...conv.messages,
          { id: `u-${Date.now()}`, role: 'user', content, events: [] },
          { id: `a-${Date.now()}`, role: 'assistant', content: '', events: [] },
        ],
      })
      await stream(
        `/conversations/${conv.id}/messages`,
        { content, provider_id: providerId, model, cwd: cwd.trim() || null },
        (event) => {
          if (event.type === 'text_delta') {
            updateAssistant((m) => ({ ...m, content: m.content + String(event.data.text ?? '') }))
          } else if (['tool_call', 'tool_result', 'error', 'done'].includes(event.type)) {
            updateAssistant((m) => ({ ...m, events: [...m.events, { type: event.type, ...event.data } as StoredEvent] }))
          }
        },
        controller.signal,
      )
    } catch (err) {
      if (!controller.signal.aborted) setError(errorMessage(err))
    } finally {
      setStreaming(false)
      abortRef.current = null
      await loadList().catch(() => undefined)
    }
  }

  return (
    <div className="chat">
      <aside className="chat-list">
        <button className="primary block" onClick={newConversation} disabled={streaming}>
          ＋ 新對話
        </button>
        <ul>
          {list.map((c) => (
            <li key={c.id} className={current?.id === c.id ? 'active' : ''} onClick={() => void open(c.id)}>
              <div className="chat-title">{c.title}</div>
              <div className="muted small">{timeAgo(c.updated_at)}</div>
              <button
                className="icon-btn"
                title="刪除對話"
                onClick={(e) => {
                  e.stopPropagation()
                  void remove(c.id)
                }}
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <section className="chat-main">
        <div className="chat-header">
          <ProviderPicker
            providerId={providerId}
            model={model}
            disabled={streaming}
            refreshKey={active}
            onChange={(p, m) => {
              setProviderId(p)
              setModel(m)
            }}
          />
          <input
            className="cwd"
            value={cwd}
            disabled={streaming}
            onChange={(e) => setCwd(e.target.value)}
            placeholder="工作目錄（選填，CLI Provider 會讀取此本機 Repo）"
          />
        </div>

        <div className="messages">
          {!current || current.messages.length === 0 ? (
            <div className="empty muted">
              <p>可以問我平台上的工作資訊，例如：</p>
              <p>「我有哪些 PR 擱超過三天？」「昨晚有哪些建置失敗？」</p>
            </div>
          ) : (
            current.messages.map((m) => <MessageView key={m.id} message={m} />)
          )}
          {streaming && <div className="muted small typing">回覆中…</div>}
          {current && <RoutineProposals key={current.id} conversationId={current.id} isStreaming={streaming} />}
          <div ref={bottomRef} />
        </div>

        {error && <div className="alert error">{error}</div>}
        <div className="composer">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault()
                void send()
              }
            }}
            placeholder="輸入訊息，Enter 送出、Shift+Enter 換行"
            rows={3}
          />
          {streaming ? (
            <button onClick={() => abortRef.current?.abort()}>停止</button>
          ) : (
            <button className="primary" onClick={() => void send()} disabled={!input.trim()}>
              送出
            </button>
          )}
        </div>
      </section>
    </div>
  )
}

function MessageView({ message }: { message: Message }) {
  if (message.role === 'user') {
    return <div className="msg user">{message.content}</div>
  }
  const results = new Map(
    message.events.filter((e) => e.type === 'tool_result').map((e) => [String(e.call_id), e]),
  )
  const calls = message.events.filter((e) => e.type === 'tool_call')
  const errors = message.events.filter((e) => e.type === 'error')
  return (
    <div className="msg assistant">
      {calls.length > 0 && (
        <div className="tool-calls">
          {calls.map((c) => {
            const result = results.get(String(c.id))
            const name = String(c.name)
            return (
              <details key={String(c.id)} className="tool-call">
                <summary>
                  {result ? (result.is_error ? '⚠' : '✓') : '…'} {TOOL_LABEL[name] ?? name}
                </summary>
                <pre>{JSON.stringify(c.arguments, null, 2)}</pre>
                {result && <pre className="tool-result">{String(result.content)}</pre>}
              </details>
            )
          })}
        </div>
      )}
      {message.content && <Markdown text={message.content} />}
      {errors.map((e, i) => (
        <div key={i} className="alert error">
          {String(e.message)}
        </div>
      ))}
    </div>
  )
}
