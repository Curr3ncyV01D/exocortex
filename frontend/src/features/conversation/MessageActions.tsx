import { useId, useLayoutEffect, useRef, useState } from 'react'
import { requestErrorMessage } from '../../api/client'
import type { MessageResponse } from '../../api/types'
import { useWorkspace } from '../../store/workspace'
import { MessageDialog } from './MessageDialog'

interface MessageActionsProps {
  message: MessageResponse
  branchId: string
  onEdit: () => void
}

const branchNamePattern = /^[A-Za-z0-9_-]+(?:\/[A-Za-z0-9_-]+)*$/

function ForkDialog({ message, branchId, onClose }: Omit<MessageActionsProps, 'onEdit'> & { onClose: () => void }) {
  const forkBranch = useWorkspace((state) => state.forkBranch)
  const [name, setName] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const nameId = useId()

  async function create() {
    if (pending) return
    if (name.length > 64 || !branchNamePattern.test(name)) {
      setError('Введите имя до 64 символов. Используйте латиницу, цифры, дефис, подчёркивание и слэши между сегментами.')
      return
    }
    setPending(true)
    setError(null)
    try {
      await forkBranch(branchId, message, name)
      onClose()
    } catch (failure) {
      setError(requestErrorMessage(failure, 'Не удалось создать ветку.'))
    } finally {
      setPending(false)
    }
  }

  return (
    <MessageDialog title="Создать ветку" pending={pending} onCancel={onClose}>
      <form
        onSubmit={(event) => {
          event.preventDefault()
          void create()
        }}
      >
        <label htmlFor={nameId} className="mb-1.5 block text-sm text-muted">Имя ветки</label>
        <input
          id={nameId}
          className="field w-full"
          placeholder="feature/notes"
          value={name}
          readOnly={pending}
          autoComplete="off"
          onChange={(event) => setName(event.target.value)}
        />
        {error && <p role="alert" className="mt-2 text-sm text-rose-200">{error}</p>}
        <div className="message-dialog-actions">
          <button type="button" className="button-secondary" disabled={pending} onClick={onClose}>Отмена</button>
          <button type="submit" className="button-primary" disabled={pending}>
            {pending ? 'Создаём…' : 'Создать ветку'}
          </button>
        </div>
      </form>
    </MessageDialog>
  )
}

export function MessageActions({ message, branchId, onEdit }: MessageActionsProps) {
  const toggleMessage = useWorkspace((state) => state.toggleMessage)
  const pending = useWorkspace((state) => state.mutatingMessages.has(message.id))
  const [menuOpen, setMenuOpen] = useState(false)
  const [forkOpen, setForkOpen] = useState(false)
  const [notice, setNotice] = useState<{ error: boolean; text: string } | null>(null)
  const menuTrigger = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const actionsId = useId()

  useLayoutEffect(() => {
    const popup = menu.current
    const trigger = menuTrigger.current
    const scrollArea = trigger?.closest('[data-timeline-scroll]')
    if (!popup || !trigger || !scrollArea) return
    const bounds = scrollArea.getBoundingClientRect()
    const anchor = trigger.getBoundingClientRect()
    const below = bounds.bottom - anchor.bottom
    const above = anchor.top - bounds.top
    if (below < popup.offsetHeight + 4 && above > below) popup.dataset.placement = 'above'
  }, [menuOpen])

  function closeMenu() {
    menuTrigger.current?.focus()
    setMenuOpen(false)
  }

  async function toggle() {
    closeMenu()
    setNotice(null)
    try {
      await toggleMessage(message)
    } catch (failure) {
      setNotice({ error: true, text: requestErrorMessage(failure, 'Не удалось изменить маску сообщения.') })
    }
  }

  async function copy() {
    closeMenu()
    try {
      await navigator.clipboard.writeText(message.content)
      setNotice({ error: false, text: 'Markdown скопирован.' })
    } catch {
      setNotice({ error: true, text: 'Не удалось скопировать Markdown.' })
    }
  }

  return (
    <>
      <div
        className="message-toolbar"
        onBlur={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget)) setMenuOpen(false)
        }}
        onKeyDown={(event) => {
          if (event.key === 'Escape' && menuOpen) {
            event.preventDefault()
            closeMenu()
          }
        }}
      >
        <button type="button" className="message-action" aria-label="Редактировать сообщение" title="Редактировать" disabled={pending} onClick={onEdit}>
          <span aria-hidden="true">✎</span>
        </button>
        <button
          ref={menuTrigger}
          type="button"
          className="message-action"
          aria-label="Действия с сообщением"
          aria-expanded={menuOpen}
          aria-controls={menuOpen ? actionsId : undefined}
          title="Действия с сообщением"
          onClick={() => setMenuOpen((open) => !open)}
        >
          <span aria-hidden="true">⋮</span>
        </button>
        {menuOpen && (
          <div ref={menu} id={actionsId} className="message-action-menu">
            <button type="button" onClick={() => { closeMenu(); setForkOpen(true) }}>Создать ветку отсюда</button>
            <button type="button" disabled={pending} onClick={() => void toggle()}>
              {message.is_active ? 'Скрыть из контекста' : 'Вернуть в контекст'}
            </button>
            <button type="button" onClick={() => void copy()}>Скопировать Markdown</button>
          </div>
        )}
      </div>
      {notice && (
        <p role={notice.error ? 'alert' : 'status'} className={notice.error ? 'text-rose-200' : 'text-muted'}>{notice.text}</p>
      )}
      {forkOpen && <ForkDialog message={message} branchId={branchId} onClose={() => setForkOpen(false)} />}
    </>
  )
}
