import { useLayoutEffect, useRef, useState } from 'react'
import type { ComponentProps, ReactNode, RefObject } from 'react'
import { LoadError } from '../../components/LoadError'
import { useWorkspace } from '../../store/workspace'
import { BranchPicker } from '../branches/BranchPicker'
import { MergeAction } from '../branches/MergeAction'
import { ChatInput } from './ChatInput'
import { MessageTimeline } from './MessageTimeline'

const TIMELINE_PAGE_SIZE = 40
const TIMELINE_REVEAL_DISTANCE = 250

function PagedMessageTimeline({
  messages, scrollArea, ...timelineProps
}: ComponentProps<typeof MessageTimeline> & { scrollArea: RefObject<HTMLDivElement | null> }) {
  const [expandedStart, setExpandedStart] = useState<number | null>(null)
  const scrollBeforeReveal = useRef<{ height: number; top: number } | null>(null)
  const tailStart = Math.max(0, messages.length - TIMELINE_PAGE_SIZE)
  const start = Math.min(expandedStart ?? tailStart, tailStart)

  useLayoutEffect(() => {
    const area = scrollArea.current
    if (!area) return
    const previous = scrollBeforeReveal.current
    scrollBeforeReveal.current = null
    if (previous) area.scrollTop = previous.top + area.scrollHeight - previous.height
    if (start === 0) return

    let lastTop = Math.max(0, area.scrollTop)
    const revealOnScroll = () => {
      const top = Math.max(0, area.scrollTop)
      const movedUp = top < lastTop
      lastTop = top
      if (!movedUp || top > TIMELINE_REVEAL_DISTANCE || scrollBeforeReveal.current) return

      scrollBeforeReveal.current = { height: area.scrollHeight, top }
      setExpandedStart(Math.max(0, start - TIMELINE_PAGE_SIZE))
    }
    area.addEventListener('scroll', revealOnScroll, { passive: true })
    return () => area.removeEventListener('scroll', revealOnScroll)
  }, [start, scrollArea])

  return <MessageTimeline {...timelineProps} messages={messages.slice(start)} />
}

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
  const head = timeline.status === 'ready' ? timeline.data.at(-1) : undefined
  const headId = head?.id
  const dialogue = timeline.status === 'ready'
    ? timeline.data.findLast((message) => message.is_active && message.role !== 'system_merge')
    : undefined
  const target = headId && dialogue?.role === 'user' ? { headId, questionId: dialogue.id } : undefined

  useLayoutEffect(() => {
    const area = scrollArea.current
    if (area && headId) area.scrollTop = area.scrollHeight
  }, [activeBranchId, headId])

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
      <div ref={scrollArea} data-timeline-scroll className="min-h-0 flex-1 overflow-y-auto overscroll-contain [overflow-anchor:none]">
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
              ? <PagedMessageTimeline
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
      {activeBranchId && (
        <ChatInput key={activeBranchId} branchId={activeBranchId} target={target} historyReady={timeline.status === 'ready'} />
      )}
    </main>
  )
}
