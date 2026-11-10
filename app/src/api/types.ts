// 後端統一資料模型與 API 回應型別

export type Platform = 'ado' | 'github'

export interface PullRequest {
  id: string
  platform: Platform
  connection_id: string
  title: string
  url: string
  project: string | null
  repo: string
  author: string
  status: string
  is_draft: boolean
  source_branch: string | null
  target_branch: string | null
  created_at: string | null
  updated_at: string | null
}

export interface WorkItem {
  id: string
  platform: Platform
  connection_id: string
  title: string
  url: string
  project: string | null
  repo: string | null
  type: string
  state: string
  assigned_to: string | null
  updated_at: string | null
}

export interface Build {
  id: string
  platform: Platform
  connection_id: string
  name: string
  url: string
  project: string | null
  repo: string | null
  branch: string | null
  status: string
  result: string | null
  requested_by: string | null
  started_at: string | null
  finished_at: string | null
}

export interface ConnectionError {
  connection_id: string
  connection: string
  message: string
}

export interface DashboardData {
  my_pull_requests: PullRequest[]
  review_requests: PullRequest[]
  work_items: WorkItem[]
  builds: Build[]
  errors: ConnectionError[]
}

export interface DashboardResponse {
  data: DashboardData | null
  fetched_at: string | null
}

export interface Connection {
  id: string
  kind: Platform
  name: string
  base_url: string
  scopes: string[]
  has_token: boolean
}

export interface ProviderKind {
  kind: string
  label: string
  mode: 'api' | 'cli'
  needs_api_key: boolean
  api_key_optional: boolean
  default_base_url: string | null
  suggested_models: string[]
}

export interface Provider {
  id: string
  kind: string
  name: string
  base_url: string | null
  default_model: string | null
  mode: 'api' | 'cli'
  has_api_key: boolean
  suggested_models: string[]
}

export interface ProvidersResponse {
  providers: Provider[]
  default_provider_id: string | null
  kinds: ProviderKind[]
}

export interface ProviderStatus {
  ready: boolean
  detail?: string | null
  installed?: boolean
  version?: string | null
  logged_in?: boolean | null
}

export interface TestResult {
  ok: boolean
  message: string
}

/** 對話中保存的事件（工具呼叫、錯誤、完成資訊） */
export interface StoredEvent {
  type: 'tool_call' | 'tool_result' | 'error' | 'done'
  [key: string]: unknown
}

export interface Message {
  id: number | string
  role: 'user' | 'assistant'
  content: string
  events: StoredEvent[]
  created_at?: string
}

export interface ConversationSummary {
  id: string
  title: string
  provider_id: string | null
  model: string | null
  created_at: string
  updated_at: string
}

export interface Conversation extends ConversationSummary {
  messages: Message[]
}

/** 日報告中的專案與其對應的平台專案（ADS 專案名稱或 GitHub repo） */
export interface ReportProject {
  name: string
  source: string
}

export interface DailyReportSettingsResponse {
  projects: ReportProject[]
  default_date: string
}

export interface DailyReportSources {
  sources: string[]
  errors: ConnectionError[]
}

export interface DailyReport {
  date: string
  content: string
  notes: string
  status?: 'draft' | 'final'
  updated_at: string | null
}

export interface StreamEvent {
  type: string
  data: Record<string, unknown>
}

/** 點擊通知後要開啟的位置（F16） */
export interface NotificationTarget {
  page: string
  conversation_id: string | null
}

export interface AppNotification {
  id: string
  title: string
  body: string
  target: NotificationTarget | null
  created_at: string
}
