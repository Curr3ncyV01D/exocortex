import { create } from 'zustand'
import { api, requestErrorMessage } from '../api/client'
import type { BranchResponse, ChatResponse, MessageResponse } from '../api/types'

type Resource<T> =
  | { status: 'idle' | 'loading' }
  | { status: 'ready'; data: T }
  | { status: 'error'; message: string }

interface WorkspaceData {
  chats: Resource<ChatResponse[]>
  branches: Resource<BranchResponse[]>
  timeline: Resource<MessageResponse[]>
  activeChatId: string | null
  activeBranchId: string | null
}

interface WorkspaceState extends WorkspaceData {
  loadChats: () => Promise<void>
  selectChat: (chatId: string) => Promise<void>
  selectBranch: (branchId: string) => Promise<void>
  createChat: (title: string) => Promise<void>
  reset: () => void
}

const initialData: WorkspaceData = {
  chats: { status: 'idle' },
  branches: { status: 'idle' },
  timeline: { status: 'idle' },
  activeChatId: null,
  activeBranchId: null,
}

export const useWorkspace = create<WorkspaceState>()((set, get) => {
  let chatsRequest: AbortController | undefined
  let branchesRequest: AbortController | undefined
  let timelineRequest: AbortController | undefined
  let createRequest: AbortController | undefined

  return {
    ...initialData,

    async loadChats() {
      chatsRequest?.abort()
      const request = chatsRequest = new AbortController()
      set({ chats: { status: 'loading' } })
      try {
        const chats = await api.listChats(request.signal)
        if (request.signal.aborted) return
        set({ chats: { status: 'ready', data: chats } })
        if (chats[0]) void get().selectChat(chats[0].id)
      } catch (error) {
        if (request.signal.aborted) return
        set({ chats: { status: 'error', message: requestErrorMessage(error, 'Не удалось загрузить чаты.') } })
      }
    },

    async selectChat(chatId) {
      branchesRequest?.abort()
      timelineRequest?.abort()
      const request = branchesRequest = new AbortController()
      set({
        activeChatId: chatId,
        activeBranchId: null,
        branches: { status: 'loading' },
        timeline: { status: 'idle' },
      })
      try {
        const branches = await api.listBranches(chatId, request.signal)
        if (request.signal.aborted) return
        set({ branches: { status: 'ready', data: branches } })
        const branch = branches.find(({ name }) => name === 'main') ?? branches[0]
        if (branch) void get().selectBranch(branch.id)
      } catch (error) {
        if (request.signal.aborted) return
        set({ branches: { status: 'error', message: requestErrorMessage(error, 'Не удалось загрузить ветки.') } })
      }
    },

    async selectBranch(branchId) {
      timelineRequest?.abort()
      const request = timelineRequest = new AbortController()
      set({ activeBranchId: branchId, timeline: { status: 'loading' } })
      try {
        const messages = await api.getTimeline(branchId, request.signal)
        // The previous request may resolve after its branch has lost focus.
        if (request.signal.aborted) return
        set({ timeline: { status: 'ready', data: messages } })
      } catch (error) {
        if (request.signal.aborted) return
        set({ timeline: { status: 'error', message: requestErrorMessage(error, 'Не удалось загрузить историю.') } })
      }
    },

    async createChat(title) {
      const request = createRequest = new AbortController()
      const chat = await api.createChat(title, request.signal)
      if (request.signal.aborted) return
      const { chats } = get()
      if (chats.status === 'ready') set({ chats: { status: 'ready', data: [chat, ...chats.data] } })
      void get().selectChat(chat.id)
    },

    reset() {
      chatsRequest?.abort()
      branchesRequest?.abort()
      timelineRequest?.abort()
      createRequest?.abort()
      set(initialData)
    },
  }
})
