import { describe, expect, it, vi } from 'vitest'
import { api } from '../src/api/client'
import type { ChatGraphResponse } from '../src/api/types'
import { createExplorerStore } from '../src/features/explorer/state'
import { buildGraph, branchColor } from '../src/features/explorer/graph'

const time = '2026-11-23T10:00:00Z'
export const graph: ChatGraphResponse = {
  chat_id: 'chat',
  nodes: [
    { id: 'root', parent_id: null, role: 'user', preview_truncated: false, preview: 'Question', is_active: true, is_edited: false, created_at: time, updated_at: null },
    { id: 'main-head', parent_id: 'root', role: 'model', preview_truncated: false, preview: 'Answer', is_active: true, is_edited: false, created_at: time, updated_at: null },
    { id: 'side-head', parent_id: 'root', role: 'model', preview_truncated: false, preview: 'Hidden', is_active: false, is_edited: false, created_at: time, updated_at: null },
    { id: 'merge', parent_id: 'main-head', role: 'system_merge', preview_truncated: false, preview: 'Summary', is_active: true, is_edited: false, created_at: time, updated_at: null },
    { id: 'orphan', parent_id: null, role: 'user', preview_truncated: false, preview: 'Unassigned', is_active: true, is_edited: false, created_at: time, updated_at: null },
  ],
  branches: [
    { id: 'main', chat_id: 'chat', name: 'main', head_message_id: 'merge', forked_from_id: null, last_merged_message_id: null, status: 'active', created_at: time },
    { id: 'side', chat_id: 'chat', name: 'side', head_message_id: 'side-head', forked_from_id: 'root', last_merged_message_id: 'side-head', status: 'active', created_at: time },
  ],
  merges: [{ message_id: 'merge', source_branch_name: 'side', source_head_message_id: 'side-head', compressed_messages_count: 1 }],
}

describe('compact graph client and state', () => {
  it('uses the graph route and forwards cancellation', async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json(graph))
    vi.stubGlobal('fetch', fetch)
    const signal = new AbortController().signal
    expect(await api.getGraph('chat/id', signal)).toEqual(graph)
    expect(fetch).toHaveBeenCalledWith('/api/v1/chats/chat%2Fid/graph', { signal })
  })

  /** A provider may finish an aborted request; only the currently opened chat can win. */
  it('ignores late results and failures after another chat is opened or the panel closes', async () => {
    let resolveFirst!: (value: ChatGraphResponse) => void
    let rejectSecond!: (reason: Error) => void
    const load = vi.spyOn(api, 'getGraph')
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve }))
      .mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectSecond = reject }))
    const store = createExplorerStore()
    const first = store.getState().open('chat')
    const second = store.getState().open('other')
    expect(load.mock.calls[0][1]?.aborted).toBe(true)
    resolveFirst(graph)
    await first
    expect(store.getState().chatId).toBe('other')
    expect(store.getState().resource.status).toBe('loading')
    store.getState().close()
    rejectSecond(new Error('late failure'))
    await second
    expect(store.getState().resource.status).toBe('idle')
    load.mockRestore()
  })

  it('supports retry, selection reset and in-memory cameras isolated by chat', async () => {
    const load = vi.spyOn(api, 'getGraph').mockRejectedValueOnce(new Error('offline')).mockResolvedValue(graph)
    const store = createExplorerStore()
    await store.getState().open('chat')
    expect(store.getState().resource.status).toBe('error')
    await store.getState().open('chat')
    expect(store.getState().resource).toEqual({ status: 'ready', data: graph })
    store.getState().select('root')
    store.getState().saveViewport('chat', { x: 1, y: 2, zoom: 0.7 })
    store.getState().close()
    expect(store.getState().selectedId).toBeNull()
    expect(store.getState().viewports.chat).toEqual({ x: 1, y: 2, zoom: 0.7 })
    expect(store.getState().viewports.other).toBeUndefined()
    expect(createExplorerStore().getState().viewports).toEqual({})
    load.mockRestore()
  })
})

describe('graph presentation model', () => {
  it('uses stable branch colors, main ownership for common ancestors and distinct shapes', () => {
    const result = buildGraph(graph, 'side')
    const node = (id: string) => result.nodes.find((item) => item.id === id)!
    expect(node('root').data.color).toBe(branchColor('main', 'main'))
    expect(node('side-head').data.color).toBe(branchColor('side', 'side'))
    expect(node('root').data.active).toBe(true)
    expect(node('main-head').data.active).toBe(false)
    expect(node('root').data.shape).toBe('user')
    expect(node('main-head').data.shape).toBe('model')
    expect(node('merge').data.shape).toBe('system_merge')
    expect(node('side-head').data.message.is_active).toBe(false)
    expect(node('orphan')).toBeDefined()
  })

  it('lays out every parent above its child, keeps main vertical and separates side lanes', () => {
    const result = buildGraph(graph, 'main')
    const positions = new Map(result.nodes.map((node) => [node.id, node.position]))
    for (const node of graph.nodes) {
      if (node.parent_id) expect(positions.get(node.id)!.y).toBeGreaterThan(positions.get(node.parent_id)!.y)
    }
    expect(positions.get('root')!.x).toBe(positions.get('main-head')!.x)
    expect(positions.get('side-head')!.x).not.toBe(positions.get('main-head')!.x)
    expect(buildGraph({ ...graph, nodes: [...graph.nodes].reverse(), branches: [...graph.branches].reverse() }, 'main')).toEqual(result)
  })

  it('keeps provenance separate from layout and omits unknown legacy links', () => {
    const result = buildGraph(graph, 'main')
    expect(result.edges.filter((edge) => edge.data?.kind === 'history')).toHaveLength(3)
    const provenance = result.edges.find((edge) => edge.data?.kind === 'merge')!
    expect(provenance.source).toBe('side-head')
    expect(provenance.target).toBe('merge')
    expect(provenance.style?.strokeDasharray).toBeDefined()
    const legacy = buildGraph({ ...graph, merges: [{ ...graph.merges[0], source_head_message_id: null }] }, 'main')
    expect(legacy.edges).toHaveLength(3)
    expect(legacy.nodes).toEqual(result.nodes)
  })
})
