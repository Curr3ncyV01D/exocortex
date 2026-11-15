import { useId, useState } from 'react'
import { requestErrorMessage } from '../../api/client'
import { MessageDialog } from '../conversation/MessageDialog'

const branchNamePattern = /^[A-Za-z0-9_-]+(?:\/[A-Za-z0-9_-]+)*$/

interface BranchNameDialogProps {
  title: string
  submitLabel: string
  pendingLabel: string
  errorMessage: string
  onSubmit: (name: string) => Promise<void>
  onClose: () => void
}

export function BranchNameDialog({
  title, submitLabel, pendingLabel, errorMessage, onSubmit, onClose,
}: BranchNameDialogProps) {
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
      await onSubmit(name)
      onClose()
    } catch (failure) {
      setError(requestErrorMessage(failure, errorMessage))
    } finally {
      setPending(false)
    }
  }

  return (
    <MessageDialog title={title} pending={pending} onCancel={onClose}>
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
            {pending ? pendingLabel : submitLabel}
          </button>
        </div>
      </form>
    </MessageDialog>
  )
}
