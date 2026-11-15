export interface ChatResponse {
  id: string
  title: string
  created_at: string
  updated_at: string
}

export interface BranchResponse {
  id: string
  chat_id: string
  name: string
  head_message_id: string | null
  forked_from_id: string | null
  last_merged_message_id: string | null
  status: 'active' | 'merged' | 'archived'
  created_at: string
}

export interface MessageResponse {
  id: string
  chat_id: string
  parent_id: string | null
  role: 'user' | 'model' | 'system_merge'
  content: string
  is_edited: boolean
  is_active: boolean
  metadata: Record<string, unknown> | null
  created_at: string
  updated_at: string | null
}
