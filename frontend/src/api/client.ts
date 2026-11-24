import type {
  AncestorMutationWarningResponse, BranchMergeResponse, BranchResponse, ChatResponse,
  MessageResponse, RegenerateReplyResponse, RunTarget, MessageActiveResponse, ChatGraphResponse,
} from './types'

const apiPrefix = '/api/v1'

export class ApiError extends Error {
  readonly status: number
  readonly body: unknown

  constructor(status: number, body: unknown) {
    const detail = body && typeof body === 'object' && 'detail' in body ? body.detail : undefined
    super(typeof detail === 'string' ? detail : `Сервер вернул ошибку HTTP ${status}.`)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

export function requestErrorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

export function isAncestorWarning(error: unknown): error is ApiError & { body: AncestorMutationWarningResponse } {
  if (!(error instanceof ApiError) || error.status !== 409) return false
  const { body } = error
  return body !== null && typeof body === 'object'
    && 'detail' in body && body.detail === 'ANCESTOR_MUTATION_WARNING'
    && 'affected_branch_count' in body && typeof body.affected_branch_count === 'number'
    && 'message' in body && typeof body.message === 'string'
}

export function savedRegenerationFork(error: unknown): { branch_id: string; head_message_id: string } | undefined {
  if (!(error instanceof ApiError) || (error.status !== 413 && error.status !== 502)) return
  const { body } = error
  if (body && typeof body === 'object'
    && 'branch_id' in body && typeof body.branch_id === 'string' && body.branch_id
    && 'head_message_id' in body && typeof body.head_message_id === 'string' && body.head_message_id) {
    return { branch_id: body.branch_id, head_message_id: body.head_message_id }
  }
}

async function request<T>(path: string, options: RequestInit): Promise<T> {
  const response = await fetch(`${apiPrefix}${path}`, options)
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null)
    throw new ApiError(response.status, body)
  }
  return response.json() as Promise<T>
}

function post<T>(path: string, body: object, signal?: AbortSignal): Promise<T> {
  return jsonRequest<T>(path, 'POST', body, signal)
}

function jsonRequest<T>(path: string, method: 'POST' | 'PATCH' | 'PUT', body: object, signal?: AbortSignal): Promise<T> {
  return request<T>(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
}

export const api = {
  getGraph: (chatId: string, signal?: AbortSignal) =>
    request<ChatGraphResponse>(`/chats/${encodeURIComponent(chatId)}/graph`, { signal }),

  listChats: (signal?: AbortSignal) => request<ChatResponse[]>('/chats', { signal }),

  createChat: (title: string, signal?: AbortSignal) => post<ChatResponse>('/chats', { title }, signal),

  listBranches: (chatId: string, signal?: AbortSignal) =>
    request<BranchResponse[]>(`/chats/${encodeURIComponent(chatId)}/branches`, { signal }),

  getTimeline: (branchId: string, signal?: AbortSignal) =>
    request<MessageResponse[]>(
      `/branches/${encodeURIComponent(branchId)}/timeline?include_inactive=true`, { signal },
    ),

  sendMessage: (branchId: string, content: string, submissionKey: string) =>
    request<MessageResponse>(`/branches/${encodeURIComponent(branchId)}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': submissionKey },
      body: JSON.stringify({ content }),
    }),

  runTurn: (branchId: string, target: RunTarget) =>
    post<MessageResponse>(`/branches/${encodeURIComponent(branchId)}/run`, {
      expected_head_id: target.headId,
      expected_question_id: target.questionId,
    }),

  editMessage: (messageId: string, content: string, force: boolean) =>
    jsonRequest<MessageResponse>(`/messages/${encodeURIComponent(messageId)}`, 'PATCH', { content, force }),

  setMessageActive: (messageId: string, isActive: boolean) =>
    jsonRequest<MessageActiveResponse>(`/messages/${encodeURIComponent(messageId)}/active`, 'PUT', { is_active: isActive }),

  createBranch: (chatId: string, targetMessageId: string, name: string) =>
    post<BranchResponse>(`/chats/${encodeURIComponent(chatId)}/branches`, { target_message_id: targetMessageId, name }),

  mergeIntoMain: (branchId: string) =>
    post<BranchMergeResponse>(`/branches/${encodeURIComponent(branchId)}/merge`, { target_branch: 'main' }),

  regenerate: (branchId: string, headId: string, name: string) =>
    post<RegenerateReplyResponse>(`/branches/${encodeURIComponent(branchId)}/regenerate`, {
      expected_head_id: headId,
      new_branch_name: name,
    }),
}
