import { describe, expect, it, vi } from 'vitest'
import { api, ApiError } from '../src/api/client'
import { chat, main, timeline } from './fixtures'

describe('REST API client', () => {
  it('loads chats, branches and the branch timeline with masked messages', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json([chat]))
      .mockResolvedValueOnce(Response.json([main]))
      .mockResolvedValueOnce(Response.json(timeline))
    vi.stubGlobal('fetch', fetchMock)
    const signal = new AbortController().signal

    expect(await api.listChats(signal)).toEqual([chat])
    expect(await api.listBranches(chat.id, signal)).toEqual([main])
    expect(await api.getTimeline(main.id, signal)).toEqual(timeline)
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      '/api/v1/chats',
      '/api/v1/chats/chat-1/branches',
      '/api/v1/branches/branch-main/timeline?include_inactive=true',
    ])
    expect(fetchMock.mock.calls.every(([, options]) => options.signal === signal)).toBe(true)
  })

  it('creates a chat with a JSON body', async () => {
    const fetchMock = vi.fn().mockResolvedValue(Response.json(chat, { status: 201 }))
    vi.stubGlobal('fetch', fetchMock)

    expect(await api.createChat(chat.title)).toEqual(chat)
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/chats', expect.objectContaining({
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: chat.title }),
    }))
  })

  it('posts user content and passes the saved message id to a separate run request', async () => {
    const saved = timeline[0]
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json(saved, { status: 201 }))
      .mockResolvedValueOnce(Response.json(timeline[1]))
    vi.stubGlobal('fetch', fetchMock)

    const key = '1fa3bfaf-ff23-43d5-be33-acaa0dd57843'
    expect(await api.sendMessage(main.id, saved.content, key)).toEqual(saved)
    expect(fetchMock.mock.calls[0][1].headers['Idempotency-Key']).toBe(key)
    expect(await api.runTurn(main.id, { headId: 'structural-head', questionId: saved.id })).toEqual(timeline[1])
    expect(fetchMock.mock.calls.map(([url, options]) => [url, options.method, JSON.parse(options.body)])).toEqual([
      ['/api/v1/branches/branch-main/messages', 'POST', { content: saved.content }],
      ['/api/v1/branches/branch-main/run', 'POST', {
        expected_head_id: 'structural-head', expected_question_id: saved.id,
      }],
    ])
  })

  it('keeps the HTTP status and structured error body for callers', async () => {
    const body = { detail: 'Ветка не найдена.' }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(body, { status: 404 })))

    const failure = api.getTimeline(main.id)
    await expect(failure).rejects.toBeInstanceOf(ApiError)
    await expect(failure).rejects.toMatchObject({ status: 404, message: body.detail, body })
  })

  it('uses the edit, mask and fork contracts with their respective IDs', async () => {
    const message = timeline[0]
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json(message))
      .mockResolvedValueOnce(Response.json({ message_id: message.id, is_active: false }))
      .mockResolvedValueOnce(Response.json(main, { status: 201 }))
    vi.stubGlobal('fetch', fetchMock)

    await api.editMessage(message.id, '**текст**', true)
    await api.setMessageActive(message.id, false)
    await api.createBranch(chat.id, message.id, 'feature/notes')

    expect(fetchMock.mock.calls.map(([path, options]) => [
      path, options.method, options.body && JSON.parse(options.body),
    ])).toEqual([
      ['/api/v1/messages/question', 'PATCH', { content: '**текст**', force: true }],
      ['/api/v1/messages/question/active', 'PUT', { is_active: false }],
      ['/api/v1/chats/chat-1/branches', 'POST', { target_message_id: 'question', name: 'feature/notes' }],
    ])
  })

  it('reports non-JSON HTTP failures with their status', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('Bad gateway', { status: 502 })))

    await expect(api.listChats()).rejects.toMatchObject({ status: 502 })
  })
})
