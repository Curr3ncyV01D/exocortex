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

export interface ToggleActiveResponse {
  message_id: string
  is_active: boolean
}

export interface RunTarget {
  headId: string
  questionId: string
}

export interface BranchMergeResponse {
  status: 'success'
  merge_message_id: string
  source_branch: string
  target_branch: 'main'
  compressed_messages_count: number
  summary: string
}

export interface RegenerateReplyResponse {
  branch: BranchResponse
  message: MessageResponse
}

export interface AncestorMutationWarningResponse {
  detail: 'ANCESTOR_MUTATION_WARNING'
  affected_branch_count: number
  message: string
}
