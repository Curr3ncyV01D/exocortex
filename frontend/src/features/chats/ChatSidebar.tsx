import { useState } from 'react'
import type { SubmitEvent } from 'react'
import { requestErrorMessage } from '../../api/client'
import { LoadError } from '../../components/LoadError'
import { useWorkspace } from '../../store/workspace'

export function ChatSidebar() {
  const chats = useWorkspace((state) => state.chats)
  const activeChatId = useWorkspace((state) => state.activeChatId)
  const loadChats = useWorkspace((state) => state.loadChats)
  const selectChat = useWorkspace((state) => state.selectChat)
  const createChat = useWorkspace((state) => state.createChat)
  const [showForm, setShowForm] = useState(false)
  const [title, setTitle] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  async function submitChat(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmedTitle = title.trim()
    if (!trimmedTitle || submitting) return

    setSubmitting(true)
    setFormError(null)
    try {
      await createChat(trimmedTitle)
      setShowForm(false)
      setTitle('')
    } catch (error) {
      setFormError(requestErrorMessage(error, 'Не удалось создать чат.'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <aside className="flex min-h-0 flex-col border-b border-line bg-panel md:border-r md:border-b-0">
      <header className="flex items-center gap-3 px-5 py-5 md:py-7">
        <span aria-hidden="true" className="grid size-8 place-items-center rounded-lg bg-accent text-sm font-bold text-accent-ink">E</span>
        <p className="text-base font-semibold tracking-tight text-foreground">ExoCortex</p>
      </header>
      <section className="flex min-h-0 flex-col gap-3 px-4 pb-5" aria-labelledby="chats-heading">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="chats-heading" className="px-1 text-sm font-semibold text-muted">Чаты</h2>
          <button
            type="button"
            className="button-secondary"
            aria-expanded={showForm}
            disabled={chats.status !== 'ready' || submitting}
            onClick={() => {
              setShowForm(!showForm)
              setFormError(null)
            }}
          >
            <span aria-hidden="true" className="mr-1 text-accent">+</span> Новый чат
          </button>
        </div>
        {showForm && (
          <form className="space-y-2 rounded-xl border border-line bg-canvas p-3" onSubmit={submitChat}>
            <label className="block text-sm text-muted" htmlFor="chat-title">Название чата</label>
            <input
              id="chat-title"
              className="field w-full"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              disabled={submitting}
              required
            />
            {formError && <p role="alert" className="text-sm text-rose-200">{formError}</p>}
            <button type="submit" className="button-secondary" disabled={submitting || !title.trim()}>
              {submitting ? 'Создаём…' : 'Создать'}
            </button>
          </form>
        )}
        {(chats.status === 'loading' || chats.status === 'idle') && (
          <p role="status" className="px-1 text-sm text-muted">Загружаем чаты…</p>
        )}
        {chats.status === 'error' && <LoadError message={chats.message} retry={loadChats} />}
        {chats.status === 'ready' && chats.data.length === 0 && (
          <p className="px-1 text-sm text-muted">Чатов пока нет.</p>
        )}
        {chats.status === 'ready' && chats.data.length > 0 && (
          <nav aria-label="Чаты" className="max-h-42 overflow-y-auto md:max-h-none">
            <ul className="space-y-1">
              {chats.data.map((chat) => (
                <li key={chat.id}>
                  <button
                    type="button"
                    aria-current={chat.id === activeChatId ? 'true' : undefined}
                    className="block w-full truncate rounded-lg border border-transparent px-3 py-2.5 text-left text-sm text-muted hover:bg-surface hover:text-foreground aria-[current=true]:border-line aria-[current=true]:bg-surface aria-[current=true]:text-foreground"
                    onClick={() => {
                      if (chat.id !== activeChatId) void selectChat(chat.id)
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
