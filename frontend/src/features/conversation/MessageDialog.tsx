import { useId, useLayoutEffect, useRef } from 'react'
import type { ReactNode } from 'react'

interface MessageDialogProps {
  title: string
  pending: boolean
  onCancel: () => void
  children: ReactNode
}

export function MessageDialog({ title, pending, onCancel, children }: MessageDialogProps) {
  const ref = useRef<HTMLDialogElement>(null)
  const titleId = useId()

  useLayoutEffect(() => {
    const dialog = ref.current
    dialog?.showModal()
    return () => { dialog?.close() }
  }, [])

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      className="message-dialog"
      onCancel={(event) => {
        event.preventDefault()
        if (!pending) onCancel()
      }}
    >
      <h2 id={titleId} className="mb-3 text-base font-semibold">{title}</h2>
      {children}
    </dialog>
  )
}
