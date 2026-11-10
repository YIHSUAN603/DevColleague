export interface Routine {
  id: string
  name: string
  prompt: string
  schedule: string | null
  command: string | null
  provider: string | null
  model: string | null
  enabled: boolean
  notify: boolean
  catch_up: boolean
  allow_side_effects: string[]
}

export interface AssistantRun {
  id: string
  routine_id: string
  trigger: string
  conversation_id: string
  started_at: string
  finished_at: string | null
  status: string
  error: string | null
  prompt?: string
  system_prompt?: string
  events?: unknown
}

export interface Memory {
  id: string
  kind: string
  content: string
  source: string
  updated_at: string
}

export interface RoutineProposal {
  id: string
  definition: Routine
  status: 'pending' | 'approved' | 'rejected'
}
