import { create } from 'zustand'
import { api, ApiError, requestErrorMessage } from '../api/client'
import type { BranchResponse, ChatResponse, MessageResponse } from '../api/types'

type Resource<T> =
  | { status: 'idle' | 'loading' }
  | { status: 'ready'; data: T }
  | { status: 'error'; message: string }

type TurnState =
  | { status: 'saving' | 'generating' }
  | { status: 'error'; message: string; retryHeadId?: string }

interface WorkspaceData {
  chats: Resource<ChatResponse[]>
  branches: Resource<BranchResponse[]>
  timeline: Resource<MessageResponse[]>
  activeChatId: string | null
  activeBranchId: string | null
  turns: Partial<Record<string, TurnState>>
  mutatingMessages: ReadonlySet<string>
}

interface WorkspaceState extends WorkspaceData {
  loadChats: () => Promise<void>
  selectChat: (chatId: string) => Promise<void>
  selectBranch: (branchId: string) => Promise<void>
  createChat: (title: string) => Promise<void>
  sendMessage: (branchId: string, content: string, generate: boolean) => Promise<boolean>
  runBranch: (branchId: string, headId: string) => Promise<void>
  editMessage: (message: MessageResponse, content: string, force: boolean) => Promise<boolean>
  toggleMessage: (message: MessageResponse) => Promise<boolean>
  forkBranch: (sourceBranchId: string, message: MessageResponse, name: string) => Promise<void>
  reset: () => void
}

const initialData: WorkspaceData = {
  chats: { status: 'idle' },
  branches: { status: 'idle' },
  timeline: { status: 'idle' },
  activeChatId: null,
  activeBranchId: null,
  turns: {},
  mutatingMessages: new Set(),
}

function updateBranchHead(branches: WorkspaceData['branches'], branchId: string, headId: string | null) {
  if (branches.status !== 'ready') return branches
  return {
    ...branches,
    data: branches.data.map((branch) =>
      branch.id === branchId ? { ...branch, head_message_id: headId } : branch,
    ),
  }
}

export const useWorkspace = create<WorkspaceState>()((set, get) => {
  let chatsRequest: AbortController | undefined
  let branchesRequest: AbortController | undefined
  let timelineRequest: AbortController | undefined
  let createRequest: AbortController | undefined
  let revision = 0

  function setTurn(branchId: string, turn?: TurnState) {
    const turns = { ...get().turns }
    if (turn) turns[branchId] = turn
    else delete turns[branchId]
    set({ turns })
  }

  function branchIsBusy(branchId: string) {
    const status = get().turns[branchId]?.status
    return status === 'saving' || status === 'generating'
  }

  function refreshIfSelected(branchId: string) {
    if (get().activeBranchId === branchId) void get().selectBranch(branchId)
  }

  async function mutateMessage(
    message: MessageResponse,
    requestUpdate: () => Promise<(current: MessageResponse) => MessageResponse>,
  ) {
    const { id, chat_id: chatId } = message
    if (get().mutatingMessages.has(id)) return false
    const startedAt = revision
    set({ mutatingMessages: new Set([...get().mutatingMessages, id]) })

    try {
      const update = await requestUpdate()
      if (revision !== startedAt) return false
      const { activeChatId, activeBranchId, timeline } = get()
      if (activeChatId === chatId && activeBranchId) {
        if (timeline.status === 'ready') {
          // Shared messages may still be visible after changing branches.
          set({ timeline: { status: 'ready', data: timeline.data.map((row) => row.id === id ? update(row) : row) } })
        } else if (timeline.status === 'loading') {
          refreshIfSelected(activeBranchId)
        }
      }
      return true
    } finally {
      if (revision === startedAt) {
        const pending = new Set(get().mutatingMessages)
        pending.delete(id)
        set({ mutatingMessages: pending })
      }
    }
  }

  function recordMessage(branchId: string, message: MessageResponse) {
    const { activeBranchId, timeline, branches } = get()
    if (activeBranchId !== branchId) return

    if (timeline.status === 'ready') {
      if (timeline.data.some(({ id }) => id === message.id)) return
      const headId = timeline.data.at(-1)?.id ?? null
      if (headId === message.parent_id) {
        set({
          timeline: { status: 'ready', data: [...timeline.data, message] },
          branches: updateBranchHead(branches, branchId, message.id),
        })
        return
      }
    }
    refreshIfSelected(branchId)
  }

  async function generateReply(branchId: string, headId: string, startedAt: number) {
    setTurn(branchId, { status: 'generating' })
    try {
      const reply = await api.runTurn(branchId, headId)
      if (revision !== startedAt) return
      recordMessage(branchId, reply)
      setTurn(branchId)
    } catch (error) {
      if (revision !== startedAt) return
      const status = error instanceof ApiError ? error.status : undefined
      setTurn(branchId, {
        status: 'error',
        message: requestErrorMessage(error, 'Не удалось получить ответ. Вопрос сохранён.'),
        retryHeadId: status === 502 || status === undefined ? headId : undefined,
      })
      // Re-read the head if a conflict or an interrupted response left it uncertain.
      if (status === 409 || status === undefined) refreshIfSelected(branchId)
    }
  }

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
        set({
          timeline: { status: 'ready', data: messages },
          branches: updateBranchHead(get().branches, branchId, messages.at(-1)?.id ?? null),
        })
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

    async sendMessage(branchId, content, generate) {
      if (branchIsBusy(branchId) || !content.trim()) return false
      const startedAt = revision
      setTurn(branchId, { status: 'saving' })
      let saved: MessageResponse
      try {
        saved = await api.sendMessage(branchId, content)
      } catch (error) {
        if (revision !== startedAt) return false
        setTurn(branchId, {
          status: 'error',
          message: requestErrorMessage(error, 'Не удалось сохранить реплику. Черновик сохранён.'),
        })
        if (error instanceof ApiError && error.status === 409) refreshIfSelected(branchId)
        return false
      }
      if (revision !== startedAt) return true

      recordMessage(branchId, saved)
      if (generate) void generateReply(branchId, saved.id, startedAt)
      else setTurn(branchId)
      return true
    },

    async runBranch(branchId, headId) {
      if (branchIsBusy(branchId)) return
      await generateReply(branchId, headId, revision)
    },

    editMessage(message, content, force) {
      return mutateMessage(message, async () => {
        const edited = await api.editMessage(message.id, content, force)
        return (current) => ({
          ...current, content: edited.content, is_edited: edited.is_edited, updated_at: edited.updated_at,
        })
      })
    },

    toggleMessage(message) {
      return mutateMessage(message, async () => {
        const result = await api.toggleMessage(message.id)
        return (current) => ({ ...current, is_active: result.is_active })
      })
    },

    async forkBranch(sourceBranchId, message, name) {
      const startedAt = revision
      const branch = await api.createBranch(message.chat_id, message.id, name)
      const { activeChatId, activeBranchId, branches } = get()
      if (revision !== startedAt || activeChatId !== message.chat_id || branches.status !== 'ready') return
      if (!branches.data.some(({ id }) => id === branch.id)) {
        set({ branches: { status: 'ready', data: [...branches.data, branch] } })
      }
      if (activeBranchId === sourceBranchId) void get().selectBranch(branch.id)
    },

    reset() {
      revision += 1
      chatsRequest?.abort()
      branchesRequest?.abort()
      timelineRequest?.abort()
      createRequest?.abort()
      set(initialData)
    },
  }
})
