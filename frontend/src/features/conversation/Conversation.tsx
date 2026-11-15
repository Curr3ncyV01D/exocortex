import { LoadError } from '../../components/LoadError'
import { useWorkspace } from '../../store/workspace'
import { BranchPicker } from '../branches/BranchPicker'
import { MessageTimeline } from './MessageTimeline'

export function Conversation() {
  const chats = useWorkspace((state) => state.chats)
  const branches = useWorkspace((state) => state.branches)
  const activeChatId = useWorkspace((state) => state.activeChatId)
  const activeBranchId = useWorkspace((state) => state.activeBranchId)
  const timeline = useWorkspace((state) => state.timeline)
  const selectChat = useWorkspace((state) => state.selectChat)
  const selectBranch = useWorkspace((state) => state.selectBranch)
  const chat = chats.status === 'ready' ? chats.data.find(({ id }) => id === activeChatId) : undefined

  return (
    <main className="flex min-h-0 min-w-0 flex-col">
      <header className="border-b border-line bg-canvas">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-3 px-5 py-5 md:px-9 md:py-6">
          <h1 className="min-w-0 max-w-full text-xl font-semibold tracking-tight wrap-anywhere md:text-2xl">
            {chat?.title ?? 'История чата'}
          </h1>
          {branches.status === 'ready' && activeBranchId && (
            <BranchPicker branches={branches.data} activeBranchId={activeBranchId} onSelect={selectBranch} />
          )}
        </div>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-5xl px-5 py-7 md:px-9 md:py-9">
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
