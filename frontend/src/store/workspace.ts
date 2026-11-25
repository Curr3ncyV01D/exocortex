import { create } from 'zustand'
import { createTimelineController, timelineDefaults, type TimelineData, type TimelineActions } from './timeline'
import { notifyChatChanged } from './chatEvents'
import { useMessageEdits } from './messageEdits'
import { api, ApiError, requestErrorMessage, savedRegenerationFork } from '../api/client'
import type { BranchResponse, ChatResponse, MessageResponse, RunTarget } from '../api/types'

type Resource<T> =
  | { status: 'idle' | 'loading' }
  | { status: 'ready'; data: T }
  | { status: 'error'; message: string }

type TurnState =
  | { status: 'saving' | 'generating' }
  | { status: 'error'; message: string; retryTarget?: RunTarget; oversizedQuestionId?: string }

interface WorkspaceData extends TimelineData {
  chats: Resource<ChatResponse[]>
  branches: Resource<BranchResponse[]>
  activeChatId: string | null
  activeBranchId: string | null
  turns: Partial<Record<string, TurnState>>
  mutatingMessages: ReadonlySet<string>
}

export interface WorkspaceState extends WorkspaceData, TimelineActions {
  loadChats: () => Promise<void>
  selectChat: (chatId: string, preferredBranchId?: string) => Promise<void>
  createChat: (title: string) => Promise<void>
  sendMessage: (branchId: string, content: string, generate: boolean, submissionKey: string) => Promise<boolean>
  runBranch: (branchId: string, target: RunTarget) => Promise<void>
  editMessage: (message: MessageResponse, content: string, force: boolean) => Promise<boolean>
  toggleMessage: (message: MessageResponse) => Promise<boolean>
  forkBranch: (sourceBranchId: string, message: MessageResponse, name: string) => Promise<void>
  mergeBranch: (source: BranchResponse, mainId: string) => Promise<void>
  regenerateBranch: (source: Pick<BranchResponse, 'id' | 'chat_id'>, headId: string, name: string) => Promise<void>
  reset: () => void
}

const initialData: WorkspaceData = {
  chats: { status: 'idle' },
  branches: { status: 'idle' },
  ...timelineDefaults,
  activeChatId: null,
  activeBranchId: null,
  turns: {},
  mutatingMessages: new Set(),
}

function generationFailure(error: unknown, target: RunTarget, prefix = ''): TurnState {
  const status = error instanceof ApiError ? error.status : undefined
  const message = status === 413
    ? 'Вопрос сохранён. Сократите его через «Редактировать».'
    : requestErrorMessage(error, 'Не удалось получить ответ. Вопрос сохранён.')
  return {
    status: 'error',
    message: prefix + message,
    retryTarget: status === 502 || status === undefined ? target : undefined,
    oversizedQuestionId: status === 413 ? target.questionId : undefined,
  }
}

export const useWorkspace = create<WorkspaceState>()((set, get) => {
  let chatsRequest: AbortController | undefined
  let branchesRequest: AbortController | undefined
  let createRequest: AbortController | undefined
  let revision = 0
  const timelineController = createTimelineController(set, get)

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
    if (get().activeBranchId === branchId) void timelineController.refresh()
  }

  function openNewBranch(sourceBranchId: string, branch: BranchResponse) {
    const { activeChatId, activeBranchId, branches } = get()
    if (activeChatId !== branch.chat_id || branches.status !== 'ready') return
    if (!branches.data.some(({ id }) => id === branch.id)) {
      set({ branches: { status: 'ready', data: [...branches.data, branch] } })
    }
    if (activeBranchId === sourceBranchId) void get().selectBranch(branch.id)
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
      timelineController.invalidate(chatId)
      const { activeChatId, activeBranchId, timeline } = get()
      if (activeChatId === chatId && activeBranchId) {
        if (timeline.status === 'ready') {
          // Shared messages may still be visible after changing branches.
          set({ timeline: { status: 'ready', data: timeline.data.map((row) => row.id === id ? update(row) : row) } })
        } else if (timeline.status === 'loading') {
          refreshIfSelected(activeBranchId)
        }
      }
      notifyChatChanged(chatId)
      if (get().activeChatId === chatId) void timelineController.refresh()
      return true
    } finally {
      if (revision === startedAt) {
        const pending = new Set(get().mutatingMessages)
        pending.delete(id)
        set({ mutatingMessages: pending })
      }
    }
  }

  async function recordMessage(branchId: string, message: MessageResponse, explicit = false) {
    timelineController.invalidate(message.chat_id)
    notifyChatChanged(message.chat_id)
    await timelineController.recordMessage(branchId, message, explicit)
  }

  async function generateReply(branchId: string, target: RunTarget, startedAt: number, explicit = false) {
    const position = timelineController.positionVersion()
    setTurn(branchId, { status: 'generating' })
    try {
      const reply = await api.runTurn(branchId, target)
      if (revision !== startedAt) return
      await recordMessage(branchId, reply, explicit && position === timelineController.positionVersion())
      setTurn(branchId)
    } catch (error) {
      if (revision !== startedAt) return
      const status = error instanceof ApiError ? error.status : undefined
      setTurn(branchId, generationFailure(error, target))
      // Re-read the head if a conflict or an interrupted response left it uncertain.
      if (status === 409 || status === undefined) refreshIfSelected(branchId)
    }
  }

  return {
    ...initialData,
    ...timelineController.actions,

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

    async selectChat(chatId, preferredBranchId) {
      branchesRequest?.abort()
      timelineController.cancel()
      const request = branchesRequest = new AbortController()
      set({
        activeChatId: chatId,
        activeBranchId: preferredBranchId ?? null,
        branches: { status: 'loading' },
        ...timelineDefaults,
      })
      try {
        const branches = await api.listBranches(chatId, request.signal)
        if (request.signal.aborted) return
        set({ branches: { status: 'ready', data: branches } })
        const branch = branches.find(({ id }) => id === preferredBranchId)
          ?? branches.find(({ name }) => name === 'main') ?? branches[0]
        if (branch) void get().selectBranch(branch.id)
      } catch (error) {
        if (request.signal.aborted) return
        set({ branches: { status: 'error', message: requestErrorMessage(error, 'Не удалось загрузить ветки.') } })
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

    async sendMessage(branchId, content, generate, submissionKey) {
      if (branchIsBusy(branchId) || !content.trim()) return false
      const startedAt = revision
      setTurn(branchId, { status: 'saving' })
      let saved: MessageResponse
      try {
        saved = await api.sendMessage(branchId, content, submissionKey)
      } catch (error) {
        if (revision !== startedAt) return false
        setTurn(branchId, {
          status: 'error',
          message: requestErrorMessage(error, 'Не удалось подтвердить сохранение. Повторите отправку того же текста.'),
        })
        if (error instanceof ApiError && error.status === 409) refreshIfSelected(branchId)
        return false
      }
      if (revision !== startedAt) return true

      await recordMessage(branchId, saved, true)
      if (generate) void generateReply(branchId, { headId: saved.id, questionId: saved.id }, startedAt)
      else setTurn(branchId)
      return true
    },

    async runBranch(branchId, target) {
      if (branchIsBusy(branchId)) return
      await generateReply(branchId, target, revision, true)
    },

    async editMessage(message, content, force) {
      const saved = await mutateMessage(message, async () => {
        const edited = await api.editMessage(message.id, content, force)
        return (current) => ({
          ...current, content: edited.content, is_edited: edited.is_edited, updated_at: edited.updated_at,
        })
      })
      if (saved && message.content !== content) {
        for (const [branchId, turn] of Object.entries(get().turns)) {
          if (turn?.status === 'error' && turn.oversizedQuestionId === message.id) setTurn(branchId)
        }
      }
      return saved
    },

    toggleMessage(message) {
      return mutateMessage(message, async () => {
        const result = await api.setMessageActive(message.id, !message.is_active)
        return (current) => ({ ...current, is_active: result.is_active })
      })
    },

    async forkBranch(sourceBranchId, message, name) {
      const startedAt = revision
      const branch = await api.createBranch(message.chat_id, message.id, name)
      if (revision === startedAt) {
        timelineController.invalidate(message.chat_id)
        notifyChatChanged(message.chat_id)
        openNewBranch(sourceBranchId, branch)
      }
    },

    async mergeBranch(source, mainId) {
      const startedAt = revision
      await api.mergeIntoMain(source.id)
      timelineController.invalidate(source.chat_id)
      notifyChatChanged(source.chat_id)
      const { activeChatId, activeBranchId } = get()
      if (revision === startedAt && activeChatId === source.chat_id) {
        const preferredBranchId = activeBranchId === source.id ? mainId : activeBranchId ?? undefined
        await get().selectChat(source.chat_id, preferredBranchId)
      }
    },

    async regenerateBranch(source, headId, name) {
      const startedAt = revision
      try {
        const { branch } = await api.regenerate(source.id, headId, name)
        if (revision === startedAt) {
          timelineController.invalidate(source.chat_id)
          notifyChatChanged(source.chat_id)
          openNewBranch(source.id, branch)
        }
      } catch (error) {
        const fork = savedRegenerationFork(error)
        if (!fork) throw error
        if (revision !== startedAt) return
        timelineController.invalidate(source.chat_id)
        notifyChatChanged(source.chat_id)
        const target = { headId: fork.head_message_id, questionId: fork.head_message_id }
        setTurn(fork.branch_id, generationFailure(error, target, 'Ветка создана. '))
        const { activeChatId, activeBranchId } = get()
        if (activeChatId === source.chat_id) {
          const preferred = activeBranchId === source.id ? fork.branch_id : activeBranchId ?? undefined
          await get().selectChat(source.chat_id, preferred)
        }
      }
    },

    reset() {
      revision += 1
      chatsRequest?.abort()
      branchesRequest?.abort()
      timelineController.cancel()
      createRequest?.abort()
      useMessageEdits.getState().reset()
      set(initialData)
    },
  }
})
