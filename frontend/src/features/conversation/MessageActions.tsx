import { useId, useLayoutEffect, useRef, useState } from 'react'
import { requestErrorMessage } from '../../api/client'
import type { MessageResponse } from '../../api/types'
import { useWorkspace } from '../../store/workspace'
import { BranchNameDialog } from '../branches/BranchNameDialog'

interface MessageActionsProps {
  message: MessageResponse
  branchId: string
  onEdit: () => void
  regenerateHeadId?: string
}

export function MessageActions({ message, branchId, onEdit, regenerateHeadId }: MessageActionsProps) {
  const forkBranch = useWorkspace((state) => state.forkBranch)
  const regenerateBranch = useWorkspace((state) => state.regenerateBranch)
  const status = useWorkspace((state) => regenerateHeadId ? state.turns[branchId]?.status : undefined)
  const running = status === 'saving' || status === 'generating'
  const toggleMessage = useWorkspace((state) => state.toggleMessage)
  const pending = useWorkspace((state) => state.mutatingMessages.has(message.id))
  const [menuOpen, setMenuOpen] = useState(false)
  const [branchDialog, setBranchDialog] = useState<'fork' | 'regenerate' | null>(null)
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
        {regenerateHeadId && (
          <button
            type="button"
            className="message-action"
            aria-label="Перегенерировать ответ"
            title="Перегенерировать в новой ветке"
            disabled={running}
            onClick={() => setBranchDialog('regenerate')}
          >
            <span aria-hidden="true">✧</span>
          </button>
        )}
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
            <button type="button" onClick={() => { closeMenu(); setBranchDialog('fork') }}>Создать ветку отсюда</button>
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
      {branchDialog === 'fork' && (
        <BranchNameDialog
          title="Создать ветку"
          submitLabel="Создать ветку"
          pendingLabel="Создаём…"
          errorMessage="Не удалось создать ветку."
          onSubmit={(name) => forkBranch(branchId, message, name)}
          onClose={() => setBranchDialog(null)}
        />
      )}
      {branchDialog === 'regenerate' && regenerateHeadId && (
        <BranchNameDialog
          title="Перегенерировать в новой ветке"
          submitLabel="Перегенерировать"
          pendingLabel="Генерируем…"
          errorMessage="Не удалось перегенерировать ответ."
          onSubmit={(name) => regenerateBranch({ id: branchId, chat_id: message.chat_id }, regenerateHeadId, name)}
          onClose={() => setBranchDialog(null)}
        />
      )}
    </>
  )
}
