import type {
  AncestorMutationWarningResponse, BranchResponse, ChatResponse, MessageResponse, ToggleActiveResponse,
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

function jsonRequest<T>(path: string, method: 'POST' | 'PATCH', body: object, signal?: AbortSignal): Promise<T> {
  return request<T>(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
}

export const api = {
  listChats: (signal?: AbortSignal) => request<ChatResponse[]>('/chats', { signal }),

  createChat: (title: string, signal?: AbortSignal) => post<ChatResponse>('/chats', { title }, signal),

  listBranches: (chatId: string, signal?: AbortSignal) =>
    request<BranchResponse[]>(`/chats/${encodeURIComponent(chatId)}/branches`, { signal }),

  getTimeline: (branchId: string, signal?: AbortSignal) =>
    request<MessageResponse[]>(
      `/branches/${encodeURIComponent(branchId)}/timeline?include_inactive=true`, { signal },
    ),

  sendMessage: (branchId: string, content: string) =>
    post<MessageResponse>(`/branches/${encodeURIComponent(branchId)}/messages`, { content }),

  runTurn: (branchId: string, expectedHeadId: string) =>
    post<MessageResponse>(`/branches/${encodeURIComponent(branchId)}/run`, { expected_head_id: expectedHeadId }),

  editMessage: (messageId: string, content: string, force: boolean) =>
    jsonRequest<MessageResponse>(`/messages/${encodeURIComponent(messageId)}`, 'PATCH', { content, force }),

  toggleMessage: (messageId: string) =>
    request<ToggleActiveResponse>(`/messages/${encodeURIComponent(messageId)}/toggle-active`, { method: 'POST' }),

  createBranch: (chatId: string, targetMessageId: string, name: string) =>
    post<BranchResponse>(`/chats/${encodeURIComponent(chatId)}/branches`, { target_message_id: targetMessageId, name }),
}
