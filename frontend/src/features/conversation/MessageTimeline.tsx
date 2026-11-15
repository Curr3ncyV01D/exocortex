import type { ReactNode } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { MessageResponse } from '../../api/types'
import { asRecord, formatDuration, readExecutionTrace } from './executionTrace'

const clock = new Intl.DateTimeFormat('ru', { hour: '2-digit', minute: '2-digit' })
const timestamp = new Intl.DateTimeFormat('ru', { dateStyle: 'medium', timeStyle: 'short' })

function TechnicalDetails({ label, children }: { label: string; children: ReactNode }) {
  return (
    <details className="technical-details">
      <summary>{label}</summary>
      <div className="technical-body">{children}</div>
    </details>
  )
}

function MessageMarkdown({ content }: { content: string }) {
  return (
    <div className="message-markdown">
      <Markdown remarkPlugins={[remarkGfm]} skipHtml>{content}</Markdown>
    </div>
  )
}

function MessageCard({ message }: { message: MessageResponse }) {
  const created = new Date(message.created_at)
  const trace = message.role === 'model' ? readExecutionTrace(message.metadata) : null
  const duration = formatDuration(trace?.total_duration_ms)

  return (
    <li
      className="message-card"
      data-role={message.role}
      data-active={message.is_active}
      tabIndex={duration ? 0 : undefined}
    >
      <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
        {message.role !== 'system_merge' && (
          <span className="message-role font-medium">{message.role === 'user' ? 'Вы' : 'Gemini'}</span>
        )}
        <time dateTime={message.created_at} title={timestamp.format(created)}>{clock.format(created)}</time>
        {!message.is_active && (
          <span className="rounded-full border border-line px-2 py-0.5 text-xs">Вне контекста</span>
        )}
        {duration && <span className="message-duration ml-auto tabular-nums" title="Длительность генерации">{duration}</span>}
      </div>
      {trace && trace.steps.length > 0 && (
        <TechnicalDetails label="Вызовы инструментов">
          <ol className="space-y-3">
            {trace.steps.map((step, index) => {
              const elapsed = formatDuration(step.execution_time_ms)
              const args = asRecord(step.arguments)
              return (
                <li key={index} className="min-w-0 space-y-1">
                  <div className="flex flex-wrap items-baseline gap-2">
                    <code className="text-accent">{step.tool_called}</code>
                    {elapsed && <span className="text-xs text-muted">{elapsed}</span>}
                  </div>
                  {args && Object.keys(args).length > 0 && (
                    <pre className="overflow-x-auto rounded-lg bg-canvas p-2 text-xs">{JSON.stringify(args, null, 2)}</pre>
                  )}
                  {typeof step.output_summary === 'string' && (
                    <p className="whitespace-pre-wrap wrap-anywhere text-muted">{step.output_summary}</p>
                  )}
                </li>
              )
            })}
          </ol>
        </TechnicalDetails>
      )}
      {message.role === 'system_merge'
        ? <TechnicalDetails label="Слияние ветки"><MessageMarkdown content={message.content} /></TechnicalDetails>
        : <MessageMarkdown content={message.content} />}
    </li>
  )
}

export function MessageTimeline({ messages }: { messages: MessageResponse[] }) {
  return (
    <ol aria-label="История сообщений" className="space-y-1.5">
      {messages.map((message) => <MessageCard key={message.id} message={message} />)}
    </ol>
  )
}
