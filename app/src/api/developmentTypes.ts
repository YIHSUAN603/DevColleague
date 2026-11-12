export type DevelopmentMode = 'pr' | 'summary' | 'review' | 'diagnose' | 'question' | 'impact'

export interface DevelopmentInput {
  mode: DevelopmentMode
  source: 'local' | 'platform'
  root: string
  source_branch: string
  target_branch: string
  connection_id: string
  project: string
  repo: string
  number: number
  files: string[]
  question: string
  rules: string
  workspace_access: boolean
  provider_id: string | null
  model: string | null
}

export interface RepoInfo { root: string; branches: string[]; files: string[] }
export interface DevelopmentPreview { id: string; payload: string; truncated: boolean; workspace_access: boolean }
export interface PublishInput {
  action: 'create' | 'comment'
  confirmed: true
  connection_id: string
  project: string
  repo: string
  number: number
  source_branch: string
  target_branch: string
  title: string
  content: string
}

export const DEVELOPMENT_MODES: { id: DevelopmentMode; label: string }[] = [
  { id: 'pr', label: '產生 PR' }, { id: 'summary', label: '摘要 PR' },
  { id: 'review', label: 'Code Review' }, { id: 'diagnose', label: '建置失敗診斷' },
  { id: 'question', label: 'Repo 問答' }, { id: 'impact', label: '變更影響分析' },
]

export const INITIAL_DEVELOPMENT_INPUT: DevelopmentInput = {
  mode: 'pr', source: 'local', root: '', source_branch: '', target_branch: '',
  connection_id: '', project: '', repo: '', number: 1, files: [], question: '', rules: '',
  workspace_access: false, provider_id: null, model: null,
}
