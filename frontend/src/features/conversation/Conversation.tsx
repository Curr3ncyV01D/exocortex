import { useRef } from 'react'
import type { ReactNode } from 'react'
import { LoadError } from '../../components/LoadError'
import { useWorkspace } from '../../store/workspace'
import { BranchPicker } from '../branches/BranchPicker'
import { MergeAction } from '../branches/MergeAction'
import { ChatInput } from './ChatInput'
import { WindowTimeline } from './WindowTimeline'

export function Conversation({ navigation }: { navigation: ReactNode }) {
  const chats = useWorkspace((state) => state.chats)
  const branches = useWorkspace((state) => state.branches)
  const activeChatId = useWorkspace((state) => state.activeChatId)
  const activeBranchId = useWorkspace((state) => state.activeBranchId)
  const timeline = useWorkspace((state) => state.timeline)
  const selectChat = useWorkspace((state) => state.selectChat)
  const selectBranch = useWorkspace((state) => state.selectBranch)
  const chat = chats.status === 'ready' ? chats.data.find(({ id }) => id === activeChatId) : undefined
  const activeBranch = branches.status === 'ready' ? branches.data.find(({ id }) => id === activeBranchId) : undefined
  const mainId = branches.status === 'ready' ? branches.data.find(({ name }) => name === 'main')?.id : undefined
  const scrollArea = useRef<HTMLDivElement>(null)
  const branchStateReady = useWorkspace((state) => state.branchStateReady)
  const windowState = useWorkspace((state) => state.windowState)
  const pageError = useWorkspace((state) => state.pageError)
  const pageLoading = useWorkspace((state) => state.pageLoading)
  const navigationState = useWorkspace((state) => state.navigation)
  const hasUnread = useWorkspace((state) => state.hasUnread)
  const showLatest = useWorkspace((state) => state.showLatest)
  const loadPage = useWorkspace((state) => state.loadTimelinePage)
  const navigateToMessage = useWorkspace((state) => state.navigateToMessage)
  const headId = windowState?.branch.head_message_id
  const dialogue = windowState?.last_active_dialogue
  const target = windowState?.run_target ? {
    headId: windowState.run_target.expected_head_id, questionId: windowState.run_target.expected_question_id,
  } : undefined
  function pagingFeedback(direction: 'older' | 'newer') {
    return <>
      {pageLoading === direction && <p role="status" className="p-2 text-xs text-muted">Загружаем сообщения…</p>}
      {pageError?.direction === direction && <div role="alert" className="p-2 text-sm text-muted">
        {pageError.message} <button type="button" className="button-secondary" onClick={() => void loadPage(direction)}>Повторить догрузку</button>
      </div>}
    </>
  }

  return (
    <main className="workspace-island flex h-full min-h-0 min-w-0 flex-col overflow-hidden">
      <header className="shrink-0 border-b border-line/70">
        <div className="flex flex-wrap items-center justify-between gap-2.5 px-4 py-3 md:px-6">
          <h1 className="min-w-0 max-w-full text-base font-semibold tracking-tight wrap-anywhere md:text-lg">
            {chat?.title ?? 'История чата'}
          </h1>
          <div className="flex min-w-0 flex-wrap items-center gap-2.5">
            {branches.status === 'ready' && activeBranchId && (
              <BranchPicker branches={branches.data} activeBranchId={activeBranchId} onSelect={selectBranch} />
            )}
            {activeBranch && mainId && activeBranch.id !== mainId && (
              <MergeAction key={activeBranch.id} source={activeBranch} mainId={mainId} />
            )}
            {navigation}
          </div>
        </div>
      </header>
      <div className="relative min-h-0 flex-1">
      <div ref={scrollArea} data-timeline-scroll className="h-full overflow-y-auto overscroll-contain [overflow-anchor:none]">
        <div className="conversation-column py-4 md:py-6">
          {chats.status === 'ready' && !chat && (
            <p className="text-sm text-muted">Создайте чат, чтобы открыть его историю.</p>
          )}
          {branches.status === 'loading' && <p role="status" className="text-sm text-muted">Загружаем ветки…</p>}
          {branches.status === 'error' && activeChatId && (
            <LoadError message={branches.message} retry={() => { void selectChat(activeChatId, activeBranchId ?? undefined) }} />
          )}
          {branches.status === 'ready' && branches.data.length === 0 && (
            <p className="text-sm text-muted">В этом чате пока нет веток.</p>
          )}
          {timeline.status === 'loading' && <p role="status" className="text-sm text-muted">Загружаем историю…</p>}
          {timeline.status === 'error' && activeBranchId && (
            <LoadError message={timeline.message} retry={() => { void selectBranch(activeBranchId) }} />
          )}
          {timeline.status === 'ready' && (
            timeline.data.length > 0
              ? <WindowTimeline
                  key={activeBranchId}
                  messages={timeline.data}
                  scrollArea={scrollArea}
                  branchId={activeBranchId ?? undefined}
                  regeneration={headId && dialogue?.role === 'model' ? { messageId: dialogue.id, headId } : undefined}
                />
              : <p className="text-sm text-muted">В этой ветке пока нет сообщений.</p>
          )}
        </div>
      </div>
      <div className="pointer-events-none absolute top-1 right-3 left-3 z-20 flex flex-col items-center gap-1 [&>*]:pointer-events-auto [&>*]:rounded-lg [&>*]:bg-[#161718f5] [&>*]:p-2">
          {navigationState?.status === 'loading' && <p role="status" className="text-sm text-muted">Переходим к сообщению…</p>}
          {navigationState?.status === 'error' && <div role="alert" className="text-sm text-muted">{navigationState.message}
            <button type="button" className="button-secondary" onClick={() => void (navigationState.targetId
              ? navigateToMessage(navigationState.branchId, navigationState.targetId) : showLatest())}>Повторить переход</button>
          </div>}
        {pagingFeedback('older')}
      </div>
      <div className="pointer-events-none absolute right-3 bottom-1 left-3 z-20 flex justify-center [&>*]:pointer-events-auto [&>*]:rounded-lg [&>*]:bg-[#161718f5] [&>*]:p-2">{pagingFeedback('newer')}</div>
      </div>
      {(windowState?.has_newer || hasUnread) && <div className="flex justify-center py-1"><button type="button" className="button-secondary" onClick={() => void showLatest()}>К последним сообщениям</button></div>}
      {activeBranchId && (
        <ChatInput key={activeBranchId} branchId={activeBranchId} target={target} historyReady={timeline.status === 'ready' && branchStateReady} />
      )}
    </main>
  )
}
