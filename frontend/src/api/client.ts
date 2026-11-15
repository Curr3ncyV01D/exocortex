import type { BranchResponse, ChatResponse, MessageResponse } from './types'

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

async function request<T>(path: string, options: RequestInit): Promise<T> {
  const response = await fetch(`${apiPrefix}${path}`, options)
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null)
    throw new ApiError(response.status, body)
  }
  return response.json() as Promise<T>
}

export const api = {
  listChats: (signal?: AbortSignal) => request<ChatResponse[]>('/chats', { signal }),

  createChat: (title: string, signal?: AbortSignal) => request<ChatResponse>('/chats', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
    signal,
  }),

  listBranches: (chatId: string, signal?: AbortSignal) =>
    request<BranchResponse[]>(`/chats/${encodeURIComponent(chatId)}/branches`, { signal }),

  getTimeline: (branchId: string, signal?: AbortSignal) =>
    request<MessageResponse[]>(
      `/branches/${encodeURIComponent(branchId)}/timeline?include_inactive=true`, { signal },
    ),
}
