import { NewChatButton } from './NewChatButton'
import { LoadError } from '../../components/LoadError'
import { useWorkspace } from '../../store/workspace'

interface ChatSidebarProps {
  listOnly?: boolean
  onClose?: () => void
  onChatOpened?: () => void
}

export function ChatSidebar({ onClose, onChatOpened, listOnly = false }: ChatSidebarProps) {
  const chats = useWorkspace((state) => state.chats)
  const activeChatId = useWorkspace((state) => state.activeChatId)
  const loadChats = useWorkspace((state) => state.loadChats)
  const selectChat = useWorkspace((state) => state.selectChat)
  return (
    <aside className={`${listOnly ? "" : "workspace-island"} flex h-full min-h-0 flex-col overflow-hidden`}>
      {!listOnly && <header className="flex shrink-0 items-center justify-between gap-2 px-4 py-4">
        <p className="text-base font-semibold tracking-tight text-foreground">ExoCortex</p>
        {onClose && (
          <button type="button" className="button-secondary" aria-label="Закрыть чаты" onClick={onClose}>
            <span aria-hidden="true">×</span>
          </button>
        )}
      </header>}
      <section className="min-h-0 space-y-3 overflow-y-auto px-3 pb-4" aria-label="Чаты">
        {!listOnly && <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="chats-heading" className="px-1 text-sm font-semibold text-muted">Чаты</h2>
          <NewChatButton onCreated={onChatOpened} />
        </div>}
        {(chats.status === 'loading' || chats.status === 'idle') && (
          <p role="status" className="px-1 text-sm text-muted">Загружаем чаты…</p>
        )}
        {chats.status === 'error' && <LoadError message={chats.message} retry={loadChats} />}
        {chats.status === 'ready' && chats.data.length === 0 && (
          <p className="px-1 text-sm text-muted">Чатов пока нет.</p>
        )}
        {chats.status === 'ready' && chats.data.length > 0 && (
          <nav aria-label="Чаты">
            <ul className="space-y-1">
              {chats.data.map((chat) => (
                <li key={chat.id}>
                  <button
                    type="button"
                    aria-current={chat.id === activeChatId ? 'true' : undefined}
                    className="block w-full truncate rounded-lg border border-transparent px-3 py-2.5 text-left text-sm text-muted hover:bg-surface hover:text-foreground aria-[current=true]:border-line aria-[current=true]:bg-surface aria-[current=true]:text-foreground"
                    onClick={() => {
                      if (chat.id !== activeChatId) void selectChat(chat.id)
                      onChatOpened?.()
                    }}
                  >
                    {chat.title}
                  </button>
                </li>
              ))}
            </ul>
          </nav>
        )}
      </section>
    </aside>
  )
}
