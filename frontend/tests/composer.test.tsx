import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import App from '../src/App'
import type { MessageResponse } from '../src/api/types'
import { chat, main, side } from './fixtures'

interface Post {
  branchId: string
  action: string
  body: unknown
}

function mockConversation(overrides: {
  send?: (request: Post) => Response | undefined | Promise<Response | undefined>
  run?: (request: Post, history: MessageResponse[]) => Response | undefined | Promise<Response | undefined>
} = {}) {
  const histories: Record<string, MessageResponse[]> = { [main.id]: [], [side.id]: [] }
  const posts: Post[] = []
  const fetchMock = vi.fn(async (url: string, options: RequestInit) => {
    const path = new URL(url, 'http://test').pathname
    if (path === '/api/v1/chats') return Response.json([chat])
    if (path === `/api/v1/chats/${chat.id}/branches`) return Response.json([main, side])
    const [, branchId, action] = path.match(/\/branches\/([^/]+)\/(timeline|messages|run)$/) ?? []
    if (!branchId) throw new Error(`Unexpected request: ${url}`)

    const history = histories[branchId]
    if (action === 'timeline') return Response.json(history)
    const request = { branchId, action, body: JSON.parse(String(options.body)) as unknown }
    posts.push(request)
    const override = await (action === 'run' ? overrides.run?.(request, history) : overrides.send?.(request))
    if (override) return override

    const message: MessageResponse = {
      id: `${branchId}-${history.length + 1}`, chat_id: chat.id, parent_id: history.at(-1)?.id ?? null,
      role: action === 'run' ? 'model' : 'user',
      content: action === 'run' ? `Ответ для ${branchId}` : (request.body as { content: string }).content,
      is_active: true, is_edited: false, metadata: null, created_at: chat.created_at, updated_at: null,
    }
    history.push(message)
    return Response.json(message, { status: action === 'run' ? 200 : 201 })
  })
  vi.stubGlobal('fetch', fetchMock)
  return { posts }
}

const input = () => screen.getByRole('textbox', { name: 'Сообщение' })

async function ready() {
  await screen.findByText('В этой ветке пока нет сообщений.')
  return input()
}

async function focusComposer() {
  const composer = await ready()
  composer.focus()
  return composer
}

it('resizes the composer with its keyboard-accessible top edge', async () => {
  mockConversation()
  const user = userEvent.setup()
  render(<App />)
  const composer = await ready()
  const edge = screen.getByRole('separator', { name: 'Высота поля ввода' })
  expect(edge).toHaveAttribute('aria-controls', composer.id)
  expect(edge).toHaveAttribute('aria-valuetext', 'Автоматическая высота')

  edge.focus()
  await user.keyboard('{ArrowUp}')
  expect(composer).toHaveStyle({ height: '64px' })
  expect(edge).toHaveAttribute('aria-valuenow', '64')
  await user.keyboard('{Home}')
  expect(composer).toHaveStyle({ height: '48px' })
})

it('saves the full prompt and uses its id for Ctrl+Enter generation', async () => {
  const { posts } = mockConversation()
  const user = userEvent.setup()
  render(<App />)
  const composer = await focusComposer()
  await user.keyboard('  Сохрани отступы  {Control>}{Enter}{/Control}')

  expect(await screen.findByText('Ответ для branch-main')).toBeVisible()
  expect(composer).toHaveValue('')
  expect(posts).toEqual([
    { branchId: main.id, action: 'messages', body: { content: '  Сохрани отступы  ' } },
    { branchId: main.id, action: 'run', body: { expected_head_id: 'branch-main-1' } },
  ])
})

/** Adding multiple prompts builds a chain. An empty Run answers the saved head. */
it('adds user messages without generation and runs the newest one on demand', async () => {
  const { posts } = mockConversation()
  const user = userEvent.setup()
  render(<App />)
  const composer = await ready()
  for (const prompt of ['Первый вопрос', 'Уточнение']) {
    composer.focus()
    await user.keyboard(prompt)
    await user.click(screen.getByRole('button', { name: 'Добавить' }))
    expect(await screen.findByText(prompt)).toBeVisible()
  }
  expect(posts.map(({ action }) => action)).toEqual(['messages', 'messages'])
  await user.click(screen.getByRole('button', { name: 'Run' }))
  expect(await screen.findByText('Ответ для branch-main')).toBeVisible()
  expect(posts.at(-1)).toEqual({ branchId: main.id, action: 'run', body: { expected_head_id: 'branch-main-2' } })
})

it('restores individual drafts when switching branches and reloading', async () => {
  mockConversation()
  const user = userEvent.setup()
  const app = render(<App />)
  await focusComposer()
  await user.keyboard('Черновик main')
  await user.selectOptions(screen.getByRole('combobox', { name: 'Ветка' }), side.id)
  await focusComposer()
  await user.keyboard('Черновик боковой ветки')
  app.unmount()

  render(<App />)
  expect(await ready()).toHaveValue('Черновик main')
  await user.selectOptions(screen.getByRole('combobox', { name: 'Ветка' }), side.id)
  expect(await ready()).toHaveValue('Черновик боковой ветки')
})

/** A 502 comes after persistence. Retry the model call on the same saved head. */
it('retries a failed generation without posting the saved question again', async () => {
  const run = vi.fn()
    .mockReturnValueOnce(Response.json({ detail: 'Сбой вызова Gemini.' }, { status: 502 }))
    .mockReturnValueOnce(undefined)
  const { posts } = mockConversation({ run })
  const user = userEvent.setup()
  render(<App />)
  const composer = await focusComposer()
  await user.keyboard('Вопрос для повтора')
  await user.click(screen.getByRole('button', { name: 'Run' }))

  expect(await screen.findByRole('alert')).toHaveTextContent('Сбой вызова Gemini.')
  expect(screen.getByText('Вопрос для повтора')).toBeVisible()
  expect(composer).toHaveValue('')
  await user.click(screen.getByRole('button', { name: 'Повторить генерацию' }))
  expect(await screen.findByText('Ответ для branch-main')).toBeVisible()
  expect(posts.map(({ action }) => action)).toEqual(['messages', 'run', 'run'])
  expect(posts[2].body).toEqual(posts[1].body)
})

it('retains the draft and skips generation when saving fails', async () => {
  const { posts } = mockConversation({
    send: () => Response.json({ detail: 'Не удалось сохранить реплику.' }, { status: 409 }),
  })
  const user = userEvent.setup()
  const app = render(<App />)
  await focusComposer()
  await user.keyboard('Сохранить черновик')
  await user.click(screen.getByRole('button', { name: 'Run' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось сохранить реплику.')
  expect(posts.map(({ action }) => action)).toEqual(['messages'])
  app.unmount()
  render(<App />)
  expect(await ready()).toHaveValue('Сохранить черновик')
})

it('refreshes the saved history on a generation conflict without running a different head', async () => {
  const { posts } = mockConversation({
    run: (_request, history) => {
      const saved = history[0]
      history.push({ ...saved, id: 'concurrent', parent_id: saved.id, content: 'Реплика из другого окна' })
      return Response.json({ detail: 'Состояние ветки изменилось.' }, { status: 409 })
    },
  })
  const user = userEvent.setup()
  render(<App />)
  await focusComposer()
  await user.keyboard('Исходный вопрос')
  await user.click(screen.getByRole('button', { name: 'Run' }))

  expect(await screen.findByRole('alert')).toHaveTextContent('Состояние ветки изменилось.')
  expect(await screen.findByText('Реплика из другого окна')).toBeVisible()
  expect(screen.queryByRole('button', { name: 'Повторить генерацию' })).not.toBeInTheDocument()
  expect(posts.map(({ action }) => action)).toEqual(['messages', 'run'])
})

/** A pending provider reply stays attached to its branch after the user navigates away. */
it('keeps generation on the original branch while another branch is active', async () => {
  let finish = () => {}
  const pending = new Promise<undefined>((resolve) => { finish = () => resolve(undefined) })
  const { posts } = mockConversation({ run: () => pending })
  const user = userEvent.setup()
  render(<App />)
  await focusComposer()
  await user.keyboard('Вопрос в main')
  await user.click(screen.getByRole('button', { name: 'Run' }))
  await waitFor(() => expect(posts).toHaveLength(2))
  await user.selectOptions(screen.getByRole('combobox', { name: 'Ветка' }), side.id)
  await focusComposer()
  await user.keyboard('Текст боковой ветки')
  await act(async () => { finish() })

  expect(screen.queryByText('Ответ для branch-main')).not.toBeInTheDocument()
  expect(input()).toHaveValue('Текст боковой ветки')
  await user.selectOptions(screen.getByRole('combobox', { name: 'Ветка' }), main.id)
  expect(await screen.findByText('Ответ для branch-main')).toBeVisible()
})

/** A completed save clears only the submitted snapshot, even across remounts. */
it('preserves a newer draft when a previous save finishes in the same branch', async () => {
  let finish = () => {}
  const pending = new Promise<undefined>((resolve) => { finish = () => resolve(undefined) })
  mockConversation({ send: () => pending })
  const user = userEvent.setup()
  const app = render(<App />)
  await focusComposer()
  await user.keyboard('Первый текст')
  await user.click(screen.getByRole('button', { name: 'Добавить' }))
  expect(await screen.findByText('Сохраняем сообщение…')).toBeVisible()

  await user.selectOptions(screen.getByRole('combobox', { name: 'Ветка' }), side.id)
  await ready()
  await user.selectOptions(screen.getByRole('combobox', { name: 'Ветка' }), main.id)
  const composer = await focusComposer()
  expect(composer).toHaveValue('Первый текст')
  await user.keyboard('{Control>}a{/Control}Новый черновик')
  await act(async () => { finish() })

  expect(await screen.findByText('Первый текст')).toBeVisible()
  expect(composer).toHaveValue('Новый черновик')
  app.unmount()
  render(<App />)
  await screen.findByText('Первый текст')
  expect(input()).toHaveValue('Новый черновик')
})
