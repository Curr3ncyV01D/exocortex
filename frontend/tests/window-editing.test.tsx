import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it } from 'vitest'
import { MessageTimeline } from '../src/features/conversation/MessageTimeline'
import { timeline, main } from './fixtures'
import type { MessageResponse } from '../src/api/types'

/** A message leaving the bounded DOM must retain its unfinished source edit. */
it('restores an unsaved edit when the same message returns to the window', async () => {
  const message: MessageResponse = { ...timeline[0], id: 'window-edit', role: 'user' }
  const view = render(<MessageTimeline messages={[message]} branchId={main.id} />)
  await userEvent.click(within(screen.getByRole('listitem')).getByRole('button', { name: 'Редактировать сообщение' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Текст сообщения' }), { target: { value: 'Сохранить черновик редактора' } })
  view.rerender(<MessageTimeline messages={[]} branchId={main.id} />)
  view.rerender(<MessageTimeline messages={[message]} branchId={main.id} />)
  expect(screen.getByRole('textbox', { name: 'Текст сообщения' })).toHaveValue('Сохранить черновик редактора')
  await userEvent.click(screen.getByRole('button', { name: 'Отмена' }))
})
