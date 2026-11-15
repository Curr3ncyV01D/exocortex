import type { ReactNode } from 'react'
import { LoadError } from '../../components/LoadError'
import { useWorkspace } from '../../store/workspace'
import { BranchPicker } from '../branches/BranchPicker'
import { MessageTimeline } from './MessageTimeline'

export function Conversation({ navigation }: { navigation: ReactNode }) {
  const chats = useWorkspace((state) => state.chats)
  const branches = useWorkspace((state) => state.branches)
  const activeChatId = useWorkspace((state) => state.activeChatId)
  const activeBranchId = useWorkspace((state) => state.activeBranchId)
  const timeline = useWorkspace((state) => state.timeline)
  const selectChat = useWorkspace((state) => state.selectChat)
  const selectBranch = useWorkspace((state) => state.selectBranch)
  const chat = chats.status === 'ready' ? chats.data.find(({ id }) => id === activeChatId) : undefined

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
            {navigation}
          </div>
        </div>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
        <div className="conversation-column py-4 md:py-6">
          {chats.status === 'ready' && !chat && (
            <p className="text-sm text-muted">Создайте чат, чтобы открыть его историю.</p>
          )}
          {branches.status === 'loading' && <p role="status" className="text-sm text-muted">Загружаем ветки…</p>}
          {branches.status === 'error' && activeChatId && (
            <LoadError message={branches.message} retry={() => { void selectChat(activeChatId) }} />
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
              ? <MessageTimeline messages={timeline.data} />
              : <p className="text-sm text-muted">В этой ветке пока нет сообщений.</p>
          )}
        </div>
      </div>
    </main>
  )
}
