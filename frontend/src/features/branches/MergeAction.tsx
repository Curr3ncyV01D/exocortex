import { useState } from 'react'
import { requestErrorMessage } from '../../api/client'
import type { BranchResponse } from '../../api/types'
import { useWorkspace } from '../../store/workspace'
import { MessageDialog } from '../conversation/MessageDialog'

export function MergeAction({ source, mainId }: { source: BranchResponse; mainId: string }) {
  const mergeBranch = useWorkspace((state) => state.mergeBranch)
  const status = useWorkspace((state) => state.turns[source.id]?.status)
  const [open, setOpen] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function merge() {
    if (pending) return
    setPending(true)
    setError(null)
    try {
      await mergeBranch(source, mainId)
      setOpen(false)
    } catch (failure) {
      setError(requestErrorMessage(failure, 'Не удалось слить ветку.'))
    } finally {
      setPending(false)
    }
  }

  return (
    <>
      <button
        type="button"
        className="button-secondary"
        disabled={pending || status === 'saving' || status === 'generating'}
        onClick={() => { setError(null); setOpen(true) }}
      >
        Слить в main
      </button>
      {open && (
        <MessageDialog title="Слить ветку в main" pending={pending} onCancel={() => setOpen(false)}>
          <p className="text-sm wrap-anywhere">
            Новые активные реплики из <strong>{source.name}</strong> будут сведены в выжимку в <strong>main</strong>.
          </p>
          <p className="mt-2 text-sm text-muted">
            Если вернуть в контекст реплику до границы предыдущего слияния, она снова будет доступна в исходной ветке.
            В следующую выжимку попадут только новые активные реплики после этой границы.
          </p>
          {error && <p role="alert" className="mt-2 text-sm text-rose-200">{error}</p>}
          <div className="message-dialog-actions">
            <button type="button" className="button-secondary" disabled={pending} onClick={() => setOpen(false)}>Отмена</button>
            <button type="button" className="button-primary" disabled={pending} onClick={() => void merge()}>
              {pending ? 'Готовим выжимку…' : 'Слить в main'}
            </button>
          </div>
        </MessageDialog>
      )}
    </>
  )
}
