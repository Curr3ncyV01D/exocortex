import { createStore } from 'zustand/vanilla'
import { api, requestErrorMessage } from '../../api/client'
import type { ChatGraphResponse } from '../../api/types'

export interface Camera { x: number; y: number; zoom: number }
type Resource = { status: 'idle' | 'loading' } | { status: 'ready'; data: ChatGraphResponse }
  | { status: 'error'; message: string }

interface ExplorerState {
  chatId: string | null
  resource: Resource
  selectedId: string | null
  viewports: Record<string, Camera>
  open: (chatId: string) => Promise<void>
  close: () => void
  select: (id: string | null) => void
  saveViewport: (chatId: string, camera: Camera) => void
}

export function createExplorerStore() {
  return createStore<ExplorerState>()((set) => {
    let request: AbortController | undefined
    let generation = 0
    return {
      chatId: null, resource: { status: 'idle' }, selectedId: null, viewports: {},
      async open(chatId) {
        request?.abort()
        const controller = new AbortController()
        request = controller
        const started = ++generation
        set({ chatId, resource: { status: 'loading' }, selectedId: null })
        try {
          const data = await api.getGraph(chatId, controller.signal)
          if (generation === started && !controller.signal.aborted) set({ resource: { status: 'ready', data } })
        } catch (error) {
          if (generation === started && !controller.signal.aborted) {
            set({ resource: { status: 'error', message: requestErrorMessage(error, 'Не удалось загрузить граф.') } })
          }
        }
      },
      close() {
        request?.abort()
        generation++
        set({ chatId: null, resource: { status: 'idle' }, selectedId: null })
      },
      select: (selectedId) => set({ selectedId }),
      saveViewport: (chatId, camera) => set((state) => ({ viewports: { ...state.viewports, [chatId]: camera } })),
    }
  })
}

export const explorerStore = createExplorerStore()
