import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import App from '../src/App'
import type { BranchResponse, MessageResponse } from '../src/api/types'
import { chat, main, side, timeline } from './fixtures'

const question: MessageResponse = { ...timeline[0], role: 'user' }
const answer: MessageResponse = { ...timeline[1], role: 'model', content: 'Исходный ответ', is_active: true }
const hidden: MessageResponse = { ...question, id: 'hidden', parent_id: answer.id, is_active: false }
const summary: MessageResponse = { ...timeline[2], role: 'system_merge', content: 'Выжимка боковой ветки' }
const fork: BranchResponse = {
  ...side, status: 'active', id: 'fork', name: 'retry/new', forked_from_id: question.id, head_message_id: question.id,
}
const rerun: MessageResponse = { ...answer, id: 'rerun', content: 'Новый ответ', parent_id: question.id }

function mockBranches(status = 201) {
  const branches: BranchResponse[] = [{ ...main, status: 'active' }, { ...side, status: 'active' }]
  const histories: Record<string, MessageResponse[]> = {
    [main.id]: [question, answer, hidden], [side.id]: [question],
  }
  const requests: { path: string; body: unknown }[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
    const path = new URL(url, 'http://test').pathname
    if (path === '/api/v1/chats') return Response.json([chat])
    if (path === `/api/v1/chats/${chat.id}/branches`) return Response.json(branches)
    const [, id, action] = path.match(/\/branches\/([^/]+)\/(timeline|merge|regenerate|run)$/) ?? []
    if (action === 'timeline') return Response.json(histories[id])
    requests.push({ path, body: JSON.parse(String(options.body)) as unknown })

    if (action === 'merge') {
      if (status === 400) return Response.json({ detail: 'Отсутствуют активные сообщения для слияния.' }, { status })
      histories[main.id].push(summary)
      return Response.json({
        status: 'success', source_branch: side.name, target_branch: 'main', merge_message_id: summary.id,
        compressed_messages_count: 1, summary: summary.content,
      })
    }
    if (action === 'regenerate') {
      if (status === 409) return Response.json({ detail: 'Исходный вопрос скрыт.' }, { status })
      branches.push(fork)
      histories[fork.id] = status === 201 ? [question, rerun] : [question]
      if (status === 201) return Response.json({ branch: fork, message: rerun }, { status })
      return Response.json({
        detail: 'Ошибка генерации.', branch_id: fork.id, head_message_id: question.id,
      }, { status })
    }
    if (action === 'run') {
      histories[id].push(rerun)
      return Response.json(rerun)
    }
    throw new Error(`Unexpected request: ${path}`)
  }))
  return requests
}

it('merges only into main after confirmation and opens the new summary', async () => {
  const requests = mockBranches()
  const user = userEvent.setup()
  render(<App />)
  const picker = await screen.findByRole('combobox', { name: 'Ветка' })
  expect(screen.queryByRole('button', { name: 'Слить в main' })).not.toBeInTheDocument()
  await user.selectOptions(picker, side.id)
  await user.click(await screen.findByRole('button', { name: 'Слить в main' }))
  const dialog = await screen.findByRole('dialog', { name: 'Слить ветку в main' })
  expect(dialog).toHaveTextContent('до границы предыдущего слияния')
  expect(requests).toHaveLength(0)
  await user.click(within(dialog).getByRole('button', { name: 'Слить в main' }))

  await screen.findByText('Слияние ветки')
  expect(screen.getByRole('combobox', { name: 'Ветка' })).toHaveValue(main.id)
  await user.click(screen.getByText('Слияние ветки', { exact: true }))
  expect(screen.getByText(summary.content)).toBeVisible()
  expect(requests).toEqual([{ path: `/api/v1/branches/${side.id}/merge`, body: { target_branch: 'main' } }])
})

it('shows an empty merge delta and keeps the source selected', async () => {
  mockBranches(400)
  const user = userEvent.setup()
  render(<App />)
  await user.selectOptions(await screen.findByRole('combobox', { name: 'Ветка' }), side.id)
  await user.click(await screen.findByRole('button', { name: 'Слить в main' }))
  const dialog = await screen.findByRole('dialog')
  await user.click(within(dialog).getByRole('button', { name: 'Слить в main' }))
  expect(await within(dialog).findByRole('alert')).toHaveTextContent('Отсутствуют активные сообщения')
  expect(screen.getByRole('combobox', { name: 'Ветка' })).toHaveValue(side.id)
})

async function regenerate(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: 'Перегенерировать ответ' }))
  const dialog = await screen.findByRole('dialog', { name: 'Перегенерировать в новой ветке' })
  within(dialog).getByRole('textbox', { name: 'Имя ветки' }).focus()
  await user.keyboard(fork.name)
  await user.click(within(dialog).getByRole('button', { name: 'Перегенерировать' }))
  return dialog
}

it('regenerates the last active answer using the actual head and opens the fork', async () => {
  const requests = mockBranches()
  const user = userEvent.setup()
  render(<App />)
  await regenerate(user)
  expect(await screen.findByText(rerun.content)).toBeVisible()
  expect(screen.getByRole('combobox', { name: 'Ветка' })).toHaveValue(fork.id)
  expect(requests).toEqual([{
    path: `/api/v1/branches/${main.id}/regenerate`,
    body: { expected_head_id: hidden.id, new_branch_name: fork.name },
  }])
  await user.selectOptions(screen.getByRole('combobox', { name: 'Ветка' }), main.id)
  expect(await screen.findByText(answer.content)).toBeVisible()
})

/** The fork already exists after a structured provider failure, so retry must only call /run. */
it.each([413, 502])('opens the saved fork after %i and offers the corresponding recovery', async (status) => {
  const requests = mockBranches(status)
  const user = userEvent.setup()
  render(<App />)
  await regenerate(user)
  const alert = await screen.findByRole('alert')
  expect(alert).toHaveTextContent('Ветка создана')
  expect(screen.getByRole('combobox', { name: 'Ветка' })).toHaveValue(fork.id)
  if (status === 413) {
    expect(alert).toHaveTextContent('Сократите')
    expect(screen.getByRole('button', { name: 'Run' })).toBeDisabled()
  } else {
    await user.click(await screen.findByRole('button', { name: 'Повторить генерацию' }))
    expect(await screen.findByText(rerun.content)).toBeVisible()
    expect(requests.at(-1)).toEqual({
      path: `/api/v1/branches/${fork.id}/run`,
      body: { expected_head_id: question.id, expected_question_id: question.id },
    })
  }
})

it('keeps the regeneration dialog open when there is no recoverable fork', async () => {
  mockBranches(409)
  const user = userEvent.setup()
  render(<App />)
  const dialog = await regenerate(user)
  expect(await within(dialog).findByRole('alert')).toHaveTextContent('Исходный вопрос скрыт.')
  expect(within(dialog).getByRole('textbox', { name: 'Имя ветки' })).toHaveValue(fork.name)
  expect(screen.getByRole('combobox', { name: 'Ветка' })).toHaveValue(main.id)
})
