interface LoadErrorProps {
  message: string
  retry: () => void
}

export function LoadError({ message, retry }: LoadErrorProps) {
  return (
    <div className="space-y-3 rounded-xl border border-rose-900/70 bg-rose-950/25 p-4 text-sm">
      <p role="alert" className="text-rose-200">{message}</p>
      <button type="button" className="button-secondary" onClick={retry}>Повторить загрузку</button>
    </div>
  )
}
