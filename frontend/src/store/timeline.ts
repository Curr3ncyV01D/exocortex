import type { StoreApi } from 'zustand'
import { api, requestErrorMessage } from '../api/client'
import type { MessageResponse, TimelineWindowQuery, TimelineWindowResponse } from '../api/types'
import { mergeTimelinePage, WINDOW_CARD_LIMIT, type PageDirection } from '../features/conversation/timelineWindow'
import type { WorkspaceState } from './workspace'
import { notifyChatChanged } from './chatEvents'

type Metadata = Omit<TimelineWindowResponse, 'messages'>
type Resource = { status: 'idle' | 'loading' } | { status: 'ready'; data: MessageResponse[] } | { status: 'error'; message: string }
interface Navigation { branchId: string; targetId: string | null; status: 'loading' | 'error'; message?: string }
export interface TimelineData {
  timeline: Resource
  windowState: Metadata | null
  branchStateReady: boolean
  navigation: Navigation | null
  pageLoading: PageDirection | null
  pageError: { direction: PageDirection; message: string } | null
  readingTail: boolean
  hasUnread: boolean
  scrollRequest: { serial: number; mode: 'tail' | 'target'; targetId?: string } | null
}
export interface TimelineActions {
  selectBranch: (branchId: string) => Promise<void>
  navigateToMessage: (branchId: string, messageId: string) => Promise<boolean>
  showLatest: () => Promise<boolean>
  loadTimelinePage: (direction: PageDirection) => Promise<void>
  setReadingTail: (atTail: boolean) => void
  cancelNavigation: () => void
}
export const timelineDefaults: TimelineData = {
  timeline: { status: 'idle' }, windowState: null, branchStateReady: false, navigation: null, pageLoading: null,
  pageError: null, readingTail: true, hasUnread: false, scrollRequest: null,
}

/** Own viewport reads separately from generation jobs and confirmed chat mutations. */
export function createTimelineController(set: StoreApi<WorkspaceState>['setState'], get: StoreApi<WorkspaceState>['getState']) {
  let generation = 0, scrollSerial = 0, readingSerial = 0
  let queuedDirection: PageDirection | null = null
  let request: AbortController | undefined, paging: AbortController | undefined, metadata: AbortController | undefined
  function cancel() {
    generation++
    queuedDirection = null
    request?.abort(); paging?.abort(); metadata?.abort()
    set({ pageLoading: null })
  }
  function current(): TimelineWindowResponse | null {
    const { timeline, windowState } = get()
    return timeline.status === 'ready' && windowState ? { ...windowState, messages: timeline.data } : null
  }
  function branchUpdate(branch: TimelineWindowResponse['branch']) {
    const branches = get().branches
    if (branches.status !== 'ready') return branches
    const present = branches.data.some(({ id }) => id === branch.id)
    return { ...branches, data: present ? branches.data.map((item) => item.id === branch.id ? branch : item) : [...branches.data, branch] }
  }
  function apply(page: TimelineWindowResponse) {
    const { messages, ...windowState } = page
    set({ timeline: { status: 'ready', data: messages }, windowState, branchStateReady: true, branches: branchUpdate(page.branch) })
  }
  async function open(branchId: string, query: TimelineWindowQuery, preserve: boolean, targetId: string | null = null): Promise<boolean> {
    cancel()
    const token = generation, chatId = get().activeChatId
    const controller = request = new AbortController()
    if (preserve) set({ navigation: { branchId, targetId, status: 'loading' }, pageError: null })
    else set({ ...timelineDefaults, activeBranchId: branchId, timeline: { status: 'loading' } })
    try {
      const page = await api.getTimelineWindow(branchId, query, controller.signal)
      if (controller.signal.aborted || generation !== token || get().activeChatId !== chatId) return false
      const existing = current()
      const canRetain = preserve && targetId === null && existing?.branch.id === branchId && !existing.has_newer
        && page.messages.some((row) => existing.messages.some(({ id }) => id === row.id))
      apply(canRetain ? mergeTimelinePage(existing!, page, 'newer') : page)
      set({ activeBranchId: branchId, navigation: null, hasUnread: false,
        readingTail: targetId === null,
        scrollRequest: { serial: ++scrollSerial, mode: targetId ? 'target' : 'tail', ...(targetId ? { targetId } : {}) } })
      return true
    } catch (error) {
      if (controller.signal.aborted || generation !== token) return false
      const message = requestErrorMessage(error, 'Не удалось загрузить историю.')
      if (preserve) set({ navigation: { branchId, targetId, status: 'error', message } })
      else set({ timeline: { status: 'error', message } })
      return false
    }
  }
  const actions: TimelineActions = {
    async selectBranch(branchId) { await open(branchId, {}, false) },
    navigateToMessage(branchId, messageId) { return open(branchId, { mode: 'around', anchor_id: messageId, radius: 20 }, true, messageId) },
    async showLatest() { const id = get().activeBranchId; return id ? open(id, {}, true) : false },
    cancelNavigation() { cancel(); set({ navigation: null }) },
    setReadingTail(atTail) {
      readingSerial++
      if (get().readingTail !== atTail || (atTail && get().hasUnread)) set({ readingTail: atTail, ...(atTail ? { hasUnread: false } : {}) })
    },
    async loadTimelinePage(direction) {
      const before = current(), branchId = get().activeBranchId
      if (!before || !branchId) return
      if (get().pageLoading) { queuedDirection = direction; return }
      if (!before[direction === 'older' ? 'has_older' : 'has_newer']) return
      const cursor = before[direction === 'older' ? 'older_cursor' : 'newer_cursor']
      if (!cursor) return
      const token = generation, controller = paging = new AbortController()
      set({ pageLoading: direction, pageError: null })
      let succeeded = false
      try {
        const page = await api.getTimelineWindow(branchId, { mode: direction, anchor_id: cursor, limit: 40 }, controller.signal)
        if (controller.signal.aborted || token !== generation || get().activeBranchId !== branchId) return
        const latest = current()
        if (latest) { apply(mergeTimelinePage(latest, page, direction)); succeeded = true }
      } catch (error) {
        if (!controller.signal.aborted && token === generation) set({ pageError: { direction, message: requestErrorMessage(error, 'Не удалось загрузить сообщения.') } })
      } finally {
        if (token === generation) {
          set({ pageLoading: null })
          const next = queuedDirection
          queuedDirection = null
          if (succeeded && next) await actions.loadTimelinePage(next)
        }
      }
    },
  }
  async function refresh() {
    const branchId = get().activeBranchId, before = current()
    if (!branchId || !before) return
    metadata?.abort()
    const controller = metadata = new AbortController(), token = generation
    set({ branchStateReady: false })
    try {
      const page = await api.getTimelineWindow(branchId, { limit: 1 }, controller.signal)
      if (controller.signal.aborted || generation !== token || get().activeBranchId !== branchId) return
      const latest = current()
      if (!latest) return
      const changed = page.branch.head_message_id !== latest.branch.head_message_id
      if (changed) notifyChatChanged(page.branch.chat_id)
      if (changed && get().readingTail) { await actions.showLatest(); return }
      const newer = latest.has_newer || (changed && page.branch.head_message_id !== latest.messages.at(-1)?.id)
      const { messages: _messages, ...state } = latest
      set({ branchStateReady: true, windowState: { ...state, branch: page.branch, last_active_dialogue: page.last_active_dialogue, run_target: page.run_target,
        has_newer: newer, newer_cursor: newer ? latest.messages.at(-1)?.id ?? null : null },
        branches: branchUpdate(page.branch), hasUnread: get().hasUnread || changed })
    } catch (error) {
      if (!controller.signal.aborted && token === generation) set({ branchStateReady: false,
        navigation: { branchId, targetId: null, status: 'error', message: requestErrorMessage(error, 'Не удалось обновить состояние ветки.') } })
    }
  }
  async function recordMessage(branchId: string, message: MessageResponse, explicit = false) {
    const branches = get().branches
    if (branches.status === 'ready') set({ branches: { ...branches, data: branches.data.map((branch) => branch.id === branchId ? { ...branch, head_message_id: message.id } : branch) } })
    if (get().activeBranchId !== branchId) return
    const before = current()
    if (!before) { await actions.showLatest(); return }
    const duplicate = before.messages.some(({ id }) => id === message.id)
    if (!duplicate && !before.has_newer && (before.messages.at(-1)?.id ?? null) === message.parent_id) {
      const rows = [...before.messages, message], overflow = rows.length > WINDOW_CARD_LIMIT
      const messages = rows.slice(-WINDOW_CARD_LIMIT)
      const dialogue = message.is_active && message.role !== 'system_merge' ? { id: message.id, role: message.role } : before.last_active_dialogue
      apply({ ...before, messages, branch: { ...before.branch, head_message_id: message.id },
        last_active_dialogue: dialogue, run_target: dialogue?.role === 'user' ? { expected_head_id: message.id, expected_question_id: dialogue.id } : null,
        has_older: before.has_older || overflow, older_cursor: before.has_older || overflow ? messages[0].id : null })
      if (explicit || get().readingTail) set({ hasUnread: false, scrollRequest: { serial: ++scrollSerial, mode: 'tail' }, readingTail: true })
      else set({ hasUnread: true })
    } else if (explicit || get().readingTail) {
      await actions.showLatest()
    } else {
      const { messages: _messages, ...state } = before
      set({ windowState: { ...state, branch: { ...before.branch, head_message_id: message.id }, has_newer: true,
        newer_cursor: before.messages.at(-1)?.id ?? null }, hasUnread: true })
      await refresh()
    }
  }
  function invalidate(chatId: string) {
    if (get().activeChatId !== chatId) return
    cancel()
    const navigation = get().navigation
    if (navigation?.status === 'loading') set({ navigation: { ...navigation, status: 'error', message: 'История изменилась. Повторите переход.' } })
    if (get().timeline.status === 'loading' && get().activeBranchId) void actions.selectBranch(get().activeBranchId!)
  }
  return { actions, cancel, refresh, recordMessage, invalidate, positionVersion: () => `${generation}:${readingSerial}` }
}
