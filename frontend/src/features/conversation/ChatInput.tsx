import { useId, useLayoutEffect, useRef, useState } from 'react'
import type { RunTarget } from '../../api/types'
import { useWorkspace } from '../../store/workspace'
import { clearSentDraft, finishSubmission, onDraftCleared, readDraft, startSubmission, writeDraft } from './drafts'

interface ChatInputProps {
  branchId: string
  target?: RunTarget
  historyReady: boolean
}

const minInputHeight = 48
const maxInputHeight = () => Math.max(minInputHeight, Math.floor(window.innerHeight * 0.4))

export function ChatInput({ branchId, target, historyReady }: ChatInputProps) {
  const [draft, setDraft] = useState(() => readDraft(branchId))
  const [height, setHeight] = useState<number>()
  const [dragging, setDragging] = useState(false)
  const textarea = useRef<HTMLTextAreaElement>(null)
  const drag = useRef<{ y: number; height: number } | null>(null)
  const inputId = useId()
  const turn = useWorkspace((state) => state.turns[branchId])
  const sendMessage = useWorkspace((state) => state.sendMessage)
  const runBranch = useWorkspace((state) => state.runBranch)
  const busy = turn?.status === 'saving' || turn?.status === 'generating'
  const hasDraft = draft.trim().length > 0
  const canAdd = historyReady && !busy && hasDraft
  const oversized = turn?.status === 'error' && turn.oversizedQuestionId === target?.questionId && !!target
  const canRun = historyReady && !busy && (hasDraft || (target !== undefined && !oversized))
  const retryTarget = turn?.status === 'error' ? turn.retryTarget : undefined
  const canRetry = historyReady && retryTarget
    && retryTarget.headId === target?.headId && retryTarget.questionId === target?.questionId

  useLayoutEffect(() => onDraftCleared(branchId, (sentText) => {
    setDraft((current) => current === sentText ? '' : current)
  }), [branchId])

  async function submit(generate: boolean) {
    if (generate ? !canRun : !canAdd) return
    if (!hasDraft && target) {
      await runBranch(branchId, target)
      return
    }
    const key = startSubmission(branchId, draft)
    if (await sendMessage(branchId, draft, generate, key)) {
      clearSentDraft(branchId, draft)
      finishSubmission(branchId, key)
    }
  }

  function currentHeight() {
    return textarea.current?.offsetHeight || minInputHeight
  }

  function resizeTo(pixels: number) {
    setHeight(Math.min(maxInputHeight(), Math.max(minInputHeight, pixels)))
  }

  return (
    <footer className="conversation-column shrink-0 pb-3 md:pb-5">
      <form
        className="chat-input relative rounded-2xl border border-line bg-surface p-2.5 shadow-lg shadow-black/25 md:p-3"
        aria-label="Ввод сообщения"
        onSubmit={(event) => {
          event.preventDefault()
          void submit(true)
        }}
      >
        <div
          role="separator"
          tabIndex={0}
          aria-label="Высота поля ввода"
          aria-controls={inputId}
          aria-orientation="horizontal"
          aria-valuemin={minInputHeight}
          aria-valuemax={maxInputHeight()}
          aria-valuenow={Math.min(height ?? minInputHeight, maxInputHeight())}
          aria-valuetext={height === undefined ? 'Автоматическая высота' : undefined}
          className="workspace-separator absolute inset-x-2 -top-1.5 z-10 h-3 cursor-ns-resize touch-none"
          data-resizing={dragging || undefined}
          onPointerDown={(event) => {
            if (event.button !== 0 || !event.isPrimary) return
            event.preventDefault()
            event.currentTarget.focus()
            event.currentTarget.setPointerCapture(event.pointerId)
            drag.current = { y: event.clientY, height: currentHeight() }
            setDragging(true)
          }}
          onPointerMove={(event) => {
            if (event.isPrimary && drag.current) resizeTo(drag.current.height + drag.current.y - event.clientY)
          }}
          onLostPointerCapture={() => {
            drag.current = null
            setDragging(false)
          }}
          onKeyDown={(event) => {
            switch (event.key) {
              case 'ArrowUp': resizeTo(currentHeight() + 16); break
              case 'ArrowDown': resizeTo(currentHeight() - 16); break
              case 'Home': resizeTo(minInputHeight); break
              case 'End': resizeTo(maxInputHeight()); break
              default: return
            }
            event.preventDefault()
          }}
        />
        <textarea
          ref={textarea}
          id={inputId}
          aria-label="Сообщение"
          rows={2}
          value={draft}
          placeholder="Напишите сообщение"
          className="chat-input-textarea"
          data-manual-size={height !== undefined || undefined}
          style={height !== undefined ? { height } : undefined}
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
            {canRetry && (
              <button type="button" className="button-secondary" onClick={() => void runBranch(branchId, retryTarget)}>
                Повторить генерацию
              </button>
            )}
          </div>
        )}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="min-w-0 flex-1 px-1 text-xs text-muted" role="status">
            {turn?.status === 'saving' && 'Сохраняем сообщение…'}
            {turn?.status === 'generating' && 'Gemini отвечает…'}
            {historyReady && !busy && !hasDraft && !oversized && (
              target ? 'Run ответит на сохранённый вопрос' : 'Введите новое сообщение'
            )}
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
              title={!hasDraft && target ? 'Ответить на сохранённый вопрос' : 'Отправить сообщение и получить ответ'}
            >
              Run <kbd aria-hidden="true" className="hidden text-xs opacity-75 sm:inline">Ctrl+Enter</kbd>
            </button>
          </div>
        </div>
      </form>
    </footer>
  )
}
