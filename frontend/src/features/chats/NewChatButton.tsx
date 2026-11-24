import { useRef, useState } from 'react'
import type { SubmitEvent } from 'react'
import { requestErrorMessage } from '../../api/client'
import { useWorkspace } from '../../store/workspace'
import { MessageDialog } from '../conversation/MessageDialog'

export function NewChatButton({ onCreated }: { onCreated?: () => void }) {
  const createChat = useWorkspace((state) => state.createChat)
  const ready = useWorkspace((state) => state.chats.status === 'ready')
  const trigger = useRef<HTMLButtonElement>(null)
  const [open, setOpen] = useState(false)
  const [title, setTitle] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  function close() { setOpen(false); trigger.current?.focus() }
  async function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault()
    if (pending || !title.trim()) return
    setPending(true)
    setError(null)
    try {
      await createChat(title.trim())
      close()
      setTitle('')
      onCreated?.()
    } catch (failure) { setError(requestErrorMessage(failure, 'Не удалось создать чат.')) }
    finally { setPending(false) }
  }
  return <>
    <button ref={trigger} type="button" className="button-secondary" aria-haspopup="dialog" disabled={!ready || pending}
      onClick={() => { setError(null); setOpen(true) }}><span aria-hidden="true">+ </span>Новый чат</button>
    {open && <MessageDialog title="Новый чат" pending={pending} onCancel={close}>
      <form onSubmit={submit} className="space-y-3">
        <label className="block text-sm text-muted" htmlFor="new-chat-title">Название чата</label>
        <input autoFocus id="new-chat-title" className="field w-full" value={title} onChange={(event) => setTitle(event.target.value)} disabled={pending} required />
        {error && <p role="alert" className="text-sm text-rose-200">{error}</p>}
        <div className="flex justify-end gap-2">
          <button type="button" className="button-secondary" disabled={pending} onClick={close}>Отмена</button>
          <button type="submit" className="button-secondary" disabled={pending || !title.trim()}>{pending ? 'Создаём…' : 'Создать'}</button>
        </div>
      </form>
    </MessageDialog>}
  </>
}
