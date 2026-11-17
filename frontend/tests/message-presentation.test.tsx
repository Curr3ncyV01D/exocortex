import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it } from 'vitest'
import type { MessageResponse } from '../src/api/types'
import { MessageTimeline } from '../src/features/conversation/MessageTimeline'
import { timeline } from './fixtures'

/** The tool trace and generation duration are independent pieces of metadata. */
it('shows a collapsed trace only when tools ran and keeps duration available for direct replies', async () => {
  const user = userEvent.setup()
  const direct: MessageResponse = {
    ...timeline[1], role: 'model', content: 'Прямой ответ',
    metadata: { execution_trace: { steps: [], total_duration_ms: 247 } },
  }
  const withTools: MessageResponse = {
    ...direct, id: 'with-tools', content: 'Ответ с поиском',
    metadata: { execution_trace: {
      steps: [{
        step: 1, tool_called: 'search_history', arguments: { query: 'FastAPI' },
        output_summary: 'Найден один результат', execution_time_ms: 10,
      }],
      total_duration_ms: 1240,
    } },
  }

  render(<MessageTimeline messages={[direct, withTools]} />)
  const [directRow, toolsRow] = screen.getAllByRole('listitem')
  expect(within(directRow).queryByText('Вызовы инструментов')).not.toBeInTheDocument()
  expect(within(directRow).getByText('247 мс')).toBeInTheDocument()
  const toggle = within(toolsRow).getByText('Вызовы инструментов')
  expect(within(toolsRow).getByText('search_history')).not.toBeVisible()
  await user.click(toggle)
  expect(within(toolsRow).getByText('search_history')).toBeVisible()
  expect(within(toolsRow).getByText('Найден один результат')).toBeVisible()
})

it('renders each ordinary message creation timestamp as local time', () => {
  const question: MessageResponse = { ...timeline[0], role: 'user' }
  const answer: MessageResponse = { ...timeline[1], role: 'model' }
  render(<MessageTimeline messages={[question, answer]} />)

  for (const row of screen.getAllByRole('listitem')) {
    const time = row.querySelector('time')
    expect(time).toHaveAttribute('datetime', question.created_at)
    expect(time).toHaveTextContent(new Intl.DateTimeFormat('ru', {
      hour: '2-digit', minute: '2-digit',
    }).format(new Date(question.created_at)))
  }
})
