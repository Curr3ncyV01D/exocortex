import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { MessageResponse } from '../../api/types'

const roles = {
  user: 'Вы',
  model: 'Gemini',
  system_merge: 'Слияние ветки',
} satisfies Record<MessageResponse['role'], string>

export function MessageTimeline({ messages }: { messages: MessageResponse[] }) {
  return (
    <ol aria-label="История сообщений" className="space-y-4">
      {messages.map((message) => (
        <li
          key={message.id}
          className="message-card"
          data-role={message.role}
          data-active={message.is_active}
        >
          <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
            <span className="message-role font-semibold">{roles[message.role]}</span>
            {!message.is_active && (
              <span className="rounded-full border border-line px-2 py-0.5 text-xs text-muted">Вне контекста</span>
            )}
          </div>
          <div className="message-markdown">
            <Markdown remarkPlugins={[remarkGfm]} skipHtml>{message.content}</Markdown>
          </div>
        </li>
      ))}
    </ol>
  )
}
