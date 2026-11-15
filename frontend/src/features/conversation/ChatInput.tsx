import { useLayoutEffect, useState } from 'react'
import type { MessageResponse } from '../../api/types'
import { useWorkspace } from '../../store/workspace'
import { clearSentDraft, onDraftCleared, readDraft, writeDraft } from './drafts'

interface ChatInputProps {
  branchId: string
  head?: MessageResponse
  historyReady: boolean
}

export function ChatInput({ branchId, head, historyReady }: ChatInputProps) {
  const [draft, setDraft] = useState(() => readDraft(branchId))
  const turn = useWorkspace((state) => state.turns[branchId])
  const sendMessage = useWorkspace((state) => state.sendMessage)
  const runBranch = useWorkspace((state) => state.runBranch)
  const busy = turn?.status === 'saving' || turn?.status === 'generating'
  const hasDraft = draft.trim().length > 0
  const headId = head?.role === 'user' && head.is_active ? head.id : undefined
  const canAdd = historyReady && !busy && hasDraft
  const canRun = historyReady && !busy && (hasDraft || headId !== undefined)
  const retryHead = turn?.status === 'error' && turn.retryHeadId === headId ? headId : undefined

  useLayoutEffect(() => onDraftCleared(branchId, (sentText) => {
    setDraft((current) => current === sentText ? '' : current)
  }), [branchId])

  async function submit(generate: boolean) {
    if (generate ? !canRun : !canAdd) return
    if (!hasDraft && headId) {
      await runBranch(branchId, headId)
      return
    }
    if (await sendMessage(branchId, draft, generate)) clearSentDraft(branchId, draft)
  }

  return (
    <footer className="conversation-column shrink-0 pb-3 md:pb-5">
      <form
        className="chat-input rounded-2xl border border-line bg-surface p-2.5 shadow-lg shadow-black/25 md:p-3"
        aria-label="Ввод сообщения"
        onSubmit={(event) => {
          event.preventDefault()
          void submit(true)
        }}
      >
        <textarea
          aria-label="Сообщение"
          rows={2}
          value={draft}
          placeholder="Напишите сообщение"
          className="chat-input-textarea"
          onChange={(event) => {
            const text = event.target.value
            setDraft(text)
            writeDraft(branchId, text)
          }}
          onKeyDown={(event) => {
            if (event.ctrlKey && event.key === 'Enter' && !event.nativeEvent.isComposing) {
              event.preventDefault()
              void submit(true)
            }
          }}
        />
        {turn?.status === 'error' && (
          <div className="mb-2 flex flex-wrap items-center gap-2 px-1 text-sm">
            <p role="alert" className="text-rose-200">{turn.message}</p>
            {retryHead && historyReady && (
              <button type="button" className="button-secondary" onClick={() => void runBranch(branchId, retryHead)}>
                Повторить генерацию
              </button>
            )}
          </div>
        )}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="min-w-0 flex-1 px-1 text-xs text-muted" role="status">
            {turn?.status === 'saving' && 'Сохраняем сообщение…'}
            {turn?.status === 'generating' && 'Gemini отвечает…'}
            {!turn && !hasDraft && headId && 'Есть сохранённый вопрос'}
          </div>
          <div className="ml-auto flex items-center gap-2">
            <button type="button" className="button-secondary" disabled={!canAdd} onClick={() => void submit(false)}>
              <span aria-hidden="true">+ </span>Добавить
            </button>
            <button
              type="submit"
              className="button-primary"
              disabled={!canRun}
              aria-keyshortcuts="Control+Enter"
              title={!hasDraft && headId ? 'Ответить на сохранённый вопрос' : 'Отправить сообщение и получить ответ'}
            >
              Run <kbd aria-hidden="true" className="hidden text-xs opacity-75 sm:inline">Ctrl+Enter</kbd>
            </button>
          </div>
        </div>
      </form>
    </footer>
  )
}
