import { windowFixture } from './window-fixture'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import App from '../src/App'
import type { MessageResponse } from '../src/api/types'
import { chat, main, side, timeline } from './fixtures'

const original: MessageResponse = { ...timeline[0], role: 'user', content: '**Исходная реплика**' }
const branchName = 'исследование/Разбор ёлки'
const editTimestamp = '2026-11-16T14:35:00Z'

function mockMessageApi({ shared = false, forkConflict = false, editConflict = false, loseMaskResponse = false } = {}) {
  let message = original
  const fork = { ...side, id: 'new-branch', name: branchName, head_message_id: original.id }
  const branches = shared ? [main, side] : [main]
  const requests: { method: string; path: string; body?: unknown }[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
    const path = new URL(url, 'http://test').pathname
    if (path === '/api/v1/chats') return Response.json([chat])
    if (path.endsWith('/timeline/window')) {
      const id = path.split('/')[4]
      return Response.json(windowFixture(branches.find((branch) => branch.id === id)!, [message], new URL(url, 'http://test').searchParams))
    }
    if (path.endsWith('/timeline')) return Response.json([message])
    if (path.endsWith('/branches') && options.method !== 'POST') return Response.json(branches)
    const body = options.body ? JSON.parse(String(options.body)) as { content?: string; force?: boolean; is_active?: boolean } : undefined
    requests.push({ method: options.method ?? 'GET', path, body })
    if (path === '/api/v1/messages/question' && options.method === 'PATCH' && body?.content) {
      if (editConflict) return Response.json({ detail: 'Реплика изменилась.' }, { status: 409 })
      if (shared && !body.force) return Response.json({
        detail: 'ANCESTOR_MUTATION_WARNING', affected_branch_count: 2, message: 'Правка затронет 2 веток.',
      }, { status: 409 })
      message = { ...message, content: body.content, is_edited: true, updated_at: editTimestamp }
      return Response.json(message)
    }
    if (path === '/api/v1/messages/question/active' && options.method === 'PUT' && typeof body?.is_active === 'boolean') {
      message = { ...message, is_active: body.is_active }
      if (loseMaskResponse) {
        loseMaskResponse = false
        throw new TypeError('Response lost after saving the mask')
      }
      return Response.json({ message_id: message.id, is_active: message.is_active })
    }
    if (path === `/api/v1/chats/${chat.id}/branches` && options.method === 'POST') {
      if (forkConflict) return Response.json({ detail: 'Ветка уже существует.' }, { status: 409 })
      branches.push(fork)
      return Response.json(fork, { status: 201 })
    }
    throw new Error(`Unexpected request: ${path}`)
  }))
  return requests
}

async function messageRow() {
  const timelineList = await screen.findByRole('list', { name: 'История сообщений' })
  return within(timelineList).getByRole('listitem')
}

async function openEdit(user: ReturnType<typeof userEvent.setup>) {
  await user.click(within(await messageRow()).getByRole('button', { name: 'Редактировать сообщение' }))
  const editor = screen.getByRole('textbox', { name: 'Текст сообщения' })
  expect(editor).toHaveValue(original.content)
  editor.focus()
  await user.keyboard('{Control>}a{/Control}Новая версия')
}

async function menuAction(user: ReturnType<typeof userEvent.setup>, name: string) {
  const row = within(await messageRow())
  await user.click(row.getByRole('button', { name: 'Действия с сообщением' }))
  await user.click(row.getByRole('button', { name }))
}

it('edits the Markdown source in place and marks the saved message edited', async () => {
  const requests = mockMessageApi()
  const user = userEvent.setup()
  render(<App />)
  await openEdit(user)
  await user.click(screen.getByRole('button', { name: 'Сохранить' }))

  expect(await screen.findByText('Новая версия')).toBeVisible()
  const editedBadge = screen.getByText('Изменено')
  const editedAt = new Date(editTimestamp)
  const localEditTime = new Intl.DateTimeFormat('ru', { hour: '2-digit', minute: '2-digit' }).format(editedAt)
  const localEditDate = new Intl.DateTimeFormat('ru', { dateStyle: 'medium' }).format(editedAt)
  expect(editedBadge).toBeVisible()
  expect(editedBadge).toHaveAttribute('title', `Реплика отредактирована в ${localEditTime} (${localEditDate})`)
  expect(requests.map(({ body }) => body)).toEqual([{ content: 'Новая версия', force: false }])
})

/** The initial PATCH may return a shared-ancestor warning. Only an explicit confirmation may force it. */
it('lets the user cancel a shared edit or confirm it without losing the draft', async () => {
  const requests = mockMessageApi({ shared: true })
  const user = userEvent.setup()
  render(<App />)
  await openEdit(user)
  await user.click(screen.getByRole('button', { name: 'Сохранить' }))
  const dialog = await screen.findByRole('dialog', { name: 'Подтвердить общую правку' })
  expect(dialog).toHaveTextContent(/2.*вет/)
  expect(requests).toHaveLength(1)
  await user.click(within(dialog).getByRole('button', { name: 'Отмена' }))
  expect(screen.getByRole('textbox', { name: 'Текст сообщения' })).toHaveValue('Новая версия')
  expect(requests).toHaveLength(1)
  await user.click(screen.getByRole('button', { name: 'Сохранить' }))
  await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Применить правку' }))
  expect(await screen.findByText('Новая версия')).toBeVisible()
  expect(requests.map(({ body }) => body)).toEqual([
    { content: 'Новая версия', force: false },
    { content: 'Новая версия', force: false },
    { content: 'Новая версия', force: true },
  ])
})

it('keeps the edit open and does not force an unrelated 409', async () => {
  const requests = mockMessageApi({ editConflict: true })
  const user = userEvent.setup()
  render(<App />)
  await openEdit(user)
  await user.click(screen.getByRole('button', { name: 'Сохранить' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Реплика изменилась.')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(screen.getByRole('textbox', { name: 'Текст сообщения' })).toHaveValue('Новая версия')
  expect(requests).toHaveLength(1)
})

/** A mask belongs to the message and is visible through both branches sharing it. */
it('masks and restores the same message across branches without removing it from the timeline', async () => {
  const requests = mockMessageApi({ shared: true })
  const user = userEvent.setup()
  render(<App />)
  await menuAction(user, 'Скрыть из контекста')
  expect(await screen.findByText('Вне контекста')).toBeVisible()
  expect(await messageRow()).toHaveTextContent('Исходная реплика')
  await user.selectOptions(screen.getByRole('combobox', { name: 'Ветка' }), side.id)
  expect(await screen.findByText('Вне контекста')).toBeVisible()
  await menuAction(user, 'Вернуть в контекст')
  await within(await messageRow()).findByText('Исходная реплика')
  await user.selectOptions(screen.getByRole('combobox', { name: 'Ветка' }), main.id)
  expect(await messageRow()).toHaveTextContent('Исходная реплика')
  expect(screen.queryByText('Вне контекста')).not.toBeInTheDocument()
  expect(requests.map(({ method, path, body }) => ({ method, path, body }))).toEqual([
    { method: 'PUT', path: '/api/v1/messages/question/active', body: { is_active: false } },
    { method: 'PUT', path: '/api/v1/messages/question/active', body: { is_active: true } },
  ])
})

it('creates a named branch from the selected message and switches to it', async () => {
  const requests = mockMessageApi()
  const user = userEvent.setup()
  render(<App />)
  await menuAction(user, 'Создать ветку отсюда')
  const dialog = await screen.findByRole('dialog', { name: 'Создать ветку' })
  within(dialog).getByRole('textbox', { name: 'Имя ветки' }).focus()
  await user.keyboard(branchName)
  await user.click(within(dialog).getByRole('button', { name: 'Создать ветку' }))
  await screen.findByRole('option', { name: branchName })
  expect(screen.getByRole('combobox', { name: 'Ветка' })).toHaveValue('new-branch')
  expect(requests).toEqual([{
    path: '/api/v1/chats/chat-1/branches', method: 'POST',
    body: { name: branchName, target_message_id: original.id },
  }])
})

it('validates branch segments and retains the name when the server rejects a duplicate', async () => {
  const requests = mockMessageApi({ forkConflict: true })
  const user = userEvent.setup()
  render(<App />)
  await menuAction(user, 'Создать ветку отсюда')
  const dialog = await screen.findByRole('dialog', { name: 'Создать ветку' })
  const name = within(dialog).getByRole('textbox', { name: 'Имя ветки' })
  name.focus()
  await user.keyboard('разбор/ заметки')
  await user.click(within(dialog).getByRole('button', { name: 'Создать ветку' }))
  expect(await within(dialog).findByRole('alert')).toBeVisible()
  expect(requests).toHaveLength(0)
  name.focus()
  await user.keyboard('{Control>}a{/Control}feature/notes')
  await user.click(within(dialog).getByRole('button', { name: 'Создать ветку' }))
  expect(await within(dialog).findByRole('alert')).toHaveTextContent('Ветка уже существует.')
  expect(name).toHaveValue('feature/notes')
})

it('copies the original Markdown source', async () => {
  mockMessageApi()
  const user = userEvent.setup()
  render(<App />)
  await menuAction(user, 'Скопировать Markdown')
  expect(await navigator.clipboard.readText()).toBe(original.content)
})


/** A lost response leaves the UI stale; retry must resend the desired state. */
it('keeps a message hidden when the first successful mask response is lost', async () => {
  const requests = mockMessageApi({ loseMaskResponse: true })
  const user = userEvent.setup()
  render(<App />)
  await menuAction(user, 'Скрыть из контекста')
  expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось изменить маску сообщения.')
  await menuAction(user, 'Скрыть из контекста')
  expect(await screen.findByText('Вне контекста')).toBeVisible()
  expect(requests.map(({ body }) => body)).toEqual([{ is_active: false }, { is_active: false }])
})
