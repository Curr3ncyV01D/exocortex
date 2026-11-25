import { afterEach, expect, it, vi } from 'vitest'
import { resolveNavigationBranches } from '../src/features/explorer/navigation'
import { createExplorerStore } from '../src/features/explorer/state'
import { api } from '../src/api/client'
import type { ChatGraphResponse } from '../src/api/types'
import { chat, main, side, timeline } from './fixtures'

const graph: ChatGraphResponse = {
  chat_id: chat.id,
  nodes: [
    ...timeline.map(({ content, ...node }) => ({ ...node, role: node.role as 'user' | 'model' | 'system_merge', preview: content, preview_truncated: false })),
    { ...timeline[0], id: 'side-head', parent_id: 'question', role: 'model', preview: 'Side', preview_truncated: false },
    { ...timeline[0], id: 'orphan', parent_id: null, role: 'user', preview: 'Orphan', preview_truncated: false },
  ],
  branches: [{ ...main, status: 'active' }, { ...side, status: 'archived', head_message_id: 'side-head' }], merges: [],
}
afterEach(() => vi.restoreAllMocks())

it('keeps a containing current branch and otherwise returns stable candidates including archived paths', () => {
  expect(resolveNavigationBranches(graph, 'question', main.id).map(({ id }) => id)).toEqual([main.id])
  expect(resolveNavigationBranches(graph, 'question', null).map(({ id }) => id)).toEqual([main.id, side.id])
  expect(resolveNavigationBranches(graph, 'side-head', main.id).map(({ id }) => id)).toEqual([side.id])
  expect(resolveNavigationBranches(graph, 'masked', side.id).map(({ id }) => id)).toEqual([main.id])
  expect(resolveNavigationBranches(graph, 'orphan', main.id)).toEqual([])
})

/** Refresh failures must preserve the last usable map, selection and camera. */
it('refreshes without blanking the map or losing selection and reports background failures', async () => {
  let reject!: (reason: Error) => void
  vi.spyOn(api, 'getGraph').mockResolvedValueOnce(graph).mockImplementationOnce(() => new Promise((_resolve, fail) => { reject = fail }))
  const store = createExplorerStore()
  await store.getState().open(chat.id)
  store.getState().select('question')
  store.getState().saveViewport(chat.id, { x: 20, y: 30, zoom: 0.7 })
  const resource = store.getState().resource
  const pending = store.getState().refresh()
  expect(store.getState().resource).toBe(resource)
  expect(store.getState().refreshing).toBe(true)
  reject(new Error('offline'))
  await pending
  expect(store.getState().resource).toBe(resource)
  expect(store.getState().selectedId).toBe('question')
  expect(store.getState().viewports[chat.id].zoom).toBe(0.7)
  expect(store.getState().refreshError).toBeTruthy()
})

it('discards a refresh that finishes after the panel closes', async () => {
  let resolve!: (data: ChatGraphResponse) => void
  vi.spyOn(api, 'getGraph').mockResolvedValueOnce(graph).mockImplementationOnce(() => new Promise((finish) => { resolve = finish }))
  const store = createExplorerStore()
  await store.getState().open(chat.id)
  const pending = store.getState().refresh()
  store.getState().close()
  resolve(graph)
  await pending
  expect(store.getState().resource.status).toBe('idle')
  expect(store.getState().chatId).toBeNull()
})
