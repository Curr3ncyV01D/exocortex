import { createStore } from 'zustand/vanilla'
import { api, requestErrorMessage } from '../../api/client'
import type { ChatGraphResponse } from '../../api/types'

export interface Camera { x: number; y: number; zoom: number }
type Resource = { status: 'idle' | 'loading' } | { status: 'ready'; data: ChatGraphResponse }
  | { status: 'error'; message: string }

interface ExplorerState {
  chatId: string | null
  resource: Resource
  refreshing: boolean
  refreshError: string | null
  refresh: () => Promise<void>
  selectedId: string | null
  viewports: Record<string, Camera>
  open: (chatId: string) => Promise<void>
  close: () => void
  select: (id: string | null) => void
  saveViewport: (chatId: string, camera: Camera) => void
}

export function createExplorerStore() {
  return createStore<ExplorerState>()((set, get) => {
    let request: AbortController | undefined
    let generation = 0
    async function load(chatId: string, background: boolean) {
      request?.abort()
      const controller = request = new AbortController(), started = ++generation
      if (background) set({ refreshing: true, refreshError: null })
      else set({ chatId, resource: { status: 'loading' }, selectedId: null, refreshing: false, refreshError: null })
      try {
        const data = await api.getGraph(chatId, controller.signal)
        if (generation !== started || controller.signal.aborted) return
        const selected = get().selectedId
        set({ resource: { status: 'ready', data }, refreshing: false, refreshError: null,
          selectedId: selected && data.nodes.some(({ id }) => id === selected) ? selected : null })
      } catch (error) {
        if (generation !== started || controller.signal.aborted) return
        const message = requestErrorMessage(error, 'Не удалось загрузить граф.')
        if (background) set({ refreshing: false, refreshError: message })
        else set({ resource: { status: 'error', message } })
      }
    }
    return {
      chatId: null, resource: { status: 'idle' }, selectedId: null, viewports: {}, refreshing: false, refreshError: null,
      open: (chatId) => load(chatId, false),
      async refresh() { const state = get(); if (state.chatId) await load(state.chatId, state.resource.status === 'ready') },
      close() {
        request?.abort()
        generation++
        set({ chatId: null, resource: { status: 'idle' }, selectedId: null, refreshing: false, refreshError: null })
      },
      select: (selectedId) => set({ selectedId }),
      saveViewport: (chatId, camera) => set((state) => ({ viewports: { ...state.viewports, [chatId]: camera } })),
    }
  })
}

export const explorerStore = createExplorerStore()
