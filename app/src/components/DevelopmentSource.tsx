import type { ChangeEvent } from 'react'
import type { Connection } from '../api/types'
import type { DevelopmentInput, RepoInfo } from '../api/developmentTypes'

interface Props {
  input: DevelopmentInput
  repoInfo: RepoInfo | null
  connections: Connection[]
  onChange: (patch: Partial<DevelopmentInput>) => void
  onInspect: () => void
}

function PlatformFields({ input, connections, onChange }: Props) {
  const connection = connections.find((c) => c.id === input.connection_id)
  return <div className="form-row">
    <label>平台連線<select aria-label="平台連線" value={input.connection_id} onChange={(e) => onChange({ connection_id: e.target.value })}>
      <option value="">選擇連線</option>
      {connections.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
    </select></label>
    {connection?.kind === 'ado' && <label>ADS/TFS 專案
      <input value={input.project} onChange={(e) => onChange({ project: e.target.value })} />
    </label>}
    <label>{connection?.kind === 'github' ? 'Repo（owner/name）' : 'Repo 名稱或 ID'}
      <input value={input.repo} onChange={(e) => onChange({ repo: e.target.value })} />
    </label>
    {input.mode !== 'pr' && <label>{input.mode === 'diagnose' ? 'Build／Run ID' : 'PR 編號'}
      <input type="number" min="1" value={input.number} onChange={(e) => onChange({ number: Number(e.target.value) })} />
    </label>}
  </div>
}

function BranchFields({ input, repoInfo, onChange }: Props) {
  return <div className="form-row">
    <datalist id="development-branches">{repoInfo?.branches.map((b) => <option key={b} value={b} />)}</datalist>
    <label>來源分支<input list="development-branches" value={input.source_branch}
      onChange={(e) => onChange({ source_branch: e.target.value })} /></label>
    <label>目標分支<input list="development-branches" value={input.target_branch}
      onChange={(e) => onChange({ target_branch: e.target.value })} /></label>
  </div>
}

function FileFields({ input, repoInfo, onChange }: Props) {
  const selectFiles = (event: ChangeEvent<HTMLSelectElement>) => {
    onChange({ files: Array.from(event.target.selectedOptions, (o) => o.value) })
  }
  return <>
    <label className="daily-notes">提供給模型的檔案（最多 30 個，可按 Ctrl 多選）
      <select multiple size={8} value={input.files} onChange={selectFiles}>
        {repoInfo?.files.map((file) => <option key={file} value={file}>{file}</option>)}
      </select>
    </label>
    <label className="inline"><input type="checkbox" checked={input.workspace_access}
      onChange={(e) => onChange({ workspace_access: e.target.checked })} />
      允許支援工作目錄的 CLI Provider 唯讀查閱此 Repo（可能讀取其他檔案並傳給模型）
    </label>
    <label className="daily-notes">問題或分析重點<textarea rows={3} value={input.question}
      onChange={(e) => onChange({ question: e.target.value })} /></label>
  </>
}

export function DevelopmentSource(props: Props) {
  const { input, onChange, onInspect } = props
  const hasBranches = ['pr', 'review', 'impact'].includes(input.mode) && input.source === 'local'
  return <>
    {input.mode === 'review' && <label>資料來源<select aria-label="資料來源" value={input.source}
      onChange={(e) => onChange({ source: e.target.value as DevelopmentInput['source'] })}>
      <option value="local">本機分支差異</option><option value="platform">平台 PR</option>
    </select></label>}
    {input.source === 'local' && <div className="form-row">
      <label className="development-root">本機 Repo 完整路徑<input value={input.root}
        onChange={(e) => onChange({ root: e.target.value, files: [] })} placeholder="C:\Projects\MyRepo" /></label>
      <button onClick={onInspect} disabled={!input.root}>讀取分支與檔案</button>
    </div>}
    {(input.source === 'platform' || input.mode === 'pr') && <PlatformFields {...props} />}
    {hasBranches && <BranchFields {...props} />}
    {['question', 'impact'].includes(input.mode) && <FileFields {...props} />}
  </>
}
