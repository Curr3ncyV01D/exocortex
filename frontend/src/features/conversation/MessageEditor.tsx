import { useLayoutEffect, useRef, useState } from 'react'
import type { MessageResponse } from '../../api/types'
import { isAncestorWarning, requestErrorMessage } from '../../api/client'
import { useWorkspace } from '../../store/workspace'
import { MessageDialog } from './MessageDialog'

interface MessageEditorProps {
  message: MessageResponse
  onClose: () => void
}

export function MessageEditor({ message, onClose }: MessageEditorProps) {
  const editMessage = useWorkspace((state) => state.editMessage)
  const pending = useWorkspace((state) => state.mutatingMessages.has(message.id))
  const [content, setContent] = useState(message.content)
  const [warning, setWarning] = useState<{ count: number; content: string } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const editor = useRef<HTMLTextAreaElement>(null)

  useLayoutEffect(() => { editor.current?.focus() }, [])

  async function save(text: string, force: boolean) {
    if (!text.trim() || pending) return
    setError(null)
    try {
      if (await editMessage(message, text, force)) onClose()
    } catch (failure) {
      if (!force && isAncestorWarning(failure)) {
        setWarning({ count: failure.body.affected_branch_count, content: text })
      } else {
        setWarning(null)
        setError(requestErrorMessage(failure, 'Не удалось сохранить изменения.'))
      }
    }
  }

  return (
    <>
      <form
        className="space-y-2"
        onSubmit={(event) => {
          event.preventDefault()
          void save(content, false)
        }}
      >
        <textarea
          ref={editor}
          className="field block max-h-64 w-full"
          aria-label="Текст сообщения"
          rows={4}
          value={content}
          readOnly={pending}
          onChange={(event) => setContent(event.target.value)}
        />
        {error && <p role="alert" className="text-sm text-rose-200">{error}</p>}
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" className="button-secondary" disabled={pending} onClick={onClose}>Отмена</button>
          <button type="submit" className="button-primary" disabled={pending || !content.trim()}>
            {pending && !warning ? 'Сохраняем…' : 'Сохранить'}
          </button>
        </div>
      </form>
      {warning && (
        <MessageDialog title="Подтвердить общую правку" pending={pending} onCancel={() => setWarning(null)}>
          <p className="text-sm text-muted">В {warning.count} ветках изменится эта реплика.</p>
          <div className="message-dialog-actions">
            <button type="button" className="button-secondary" disabled={pending} onClick={() => setWarning(null)}>Отмена</button>
            <button
              type="button"
              className="button-primary"
              disabled={pending}
              onClick={() => void save(warning.content, true)}
            >
              {pending ? 'Сохраняем…' : 'Применить правку'}
            </button>
          </div>
        </MessageDialog>
      )}
    </>
  )
}
