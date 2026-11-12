import { useState } from 'react'
import { post } from '../api/backend'
import type { DevelopmentInput, PublishInput } from '../api/developmentTypes'
import { errorMessage } from '../utils'
import { Markdown } from './Markdown'

interface Props { text: string; input: DevelopmentInput; canPublish: boolean }

export function DevelopmentResult({ text, input, canPublish }: Props) {
  const [content, setContent] = useState(input.mode === 'pr' ? text.split('\n').slice(1).join('\n').trim() : text)
  const [title, setTitle] = useState(input.mode === 'pr' ? text.split('\n')[0].replace(/^#+\s*/, '') : '')
  const [proposal, setProposal] = useState<PublishInput | null>(null)
  const [isPublishing, setIsPublishing] = useState(false)
  const [message, setMessage] = useState('')
  const [isPublished, setIsPublished] = useState(false)
  const publish = async () => {
    if (!proposal) { return }
    setIsPublishing(true)
    try {
      const result = await post<{ id: string }>('/development/publish', proposal)
      setMessage(`已發布，平台編號：${result.id}`)
      setIsPublished(true)
      setProposal(null)
    } catch (err) {
      setMessage(errorMessage(err))
    } finally {
      setIsPublishing(false)
    }
  }
  const prepare = () => {
    setProposal({ action: input.mode === 'pr' ? 'create' : 'comment', confirmed: true,
      connection_id: input.connection_id, project: input.project, repo: input.repo, number: input.number,
      source_branch: input.source_branch, target_branch: input.target_branch, title, content })
  }
  return <div className="card development-result">
    <h3>分析結果</h3><Markdown text={text} />
    <label className="daily-notes">可編輯／複製的 Markdown
      <textarea rows={12} value={content} disabled={!!proposal || isPublished} onChange={(e) => setContent(e.target.value)} />
    </label>
    <div className="form-row">
      <button onClick={() => { void navigator.clipboard.writeText(content).then(() => setMessage('已複製')).catch(
        (err) => setMessage(errorMessage(err))) }}>複製 Markdown</button>
      {canPublish && !isPublished && <>
        {input.mode === 'pr' && <label>PR 標題<input value={title} disabled={!!proposal}
          onChange={(e) => setTitle(e.target.value)} /></label>}
        <button disabled={!content.trim() || !!proposal || (input.mode === 'pr' && !title.trim())}
          onClick={prepare}>{input.mode === 'pr' ? '檢查並建立 PR' : '檢查並發布 PR 留言'}</button>
      </>}
    </div>
    {proposal && <div className="alert warn">
      <p>即將發布到：{input.project} / {input.repo}，連線：{input.connection_id}</p>
      <p>{proposal.action === 'create' ? `${title}（${input.source_branch} → ${input.target_branch}）` : `PR #${input.number} 留言`}</p>
      <pre>{proposal.content}</pre>
      <div className="row-actions"><button className="primary" disabled={isPublishing} onClick={() => void publish()}>
        {isPublishing ? '發布中…' : '確認發布'}</button>
        <button disabled={isPublishing} onClick={() => setProposal(null)}>返回編輯</button></div>
    </div>}
    {message && <p role="status">{message}</p>}
  </div>
}
