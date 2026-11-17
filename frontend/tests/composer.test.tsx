import { act, render, screen, waitFor, within } from '@testing-library/react'
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
  history?: MessageResponse[]
  send?: (request: Post) => Response | undefined | Promise<Response | undefined>
  loseSaveResponse?: () => boolean
  run?: (request: Post, history: MessageResponse[]) => Response | undefined | Promise<Response | undefined>
} = {}) {
  const histories: Record<string, MessageResponse[]> = { [main.id]: [...(overrides.history ?? [])], [side.id]: [] }
  const posts: Post[] = []
  const submissions = new Map<string, MessageResponse>()
  const saveKeys: (string | null)[] = []
  const fetchMock = vi.fn(async (url: string, options: RequestInit) => {
    const path = new URL(url, 'http://test').pathname
    if (path === '/api/v1/chats') return Response.json([chat])
    if (path === `/api/v1/chats/${chat.id}/branches`) return Response.json([main, side])
    const [, messageId] = path.match(/\/messages\/([^/]+)(?:\/toggle-active)?$/) ?? []
    if (messageId) {
      const message = histories[main.id].find(({ id }) => id === messageId)
      if (!message) throw new Error(`Unknown message: ${messageId}`)
      if (options.method === 'PATCH') {
        message.content = (JSON.parse(String(options.body)) as { content: string }).content
        message.is_edited = true
        return Response.json(message)
      }
      message.is_active = !message.is_active
      return Response.json({ message_id: messageId, is_active: message.is_active })
    }
    const [, branchId, action] = path.match(/\/branches\/([^/]+)\/(timeline|messages|run)$/) ?? []
    if (!branchId) throw new Error(`Unexpected request: ${url}`)

    const history = histories[branchId]
    if (action === 'timeline') return Response.json(history)
    const request = { branchId, action, body: JSON.parse(String(options.body)) as unknown }
    posts.push(request)
    const key = action === 'messages' ? new Headers(options.headers).get('Idempotency-Key') : null
    if (action === 'messages') saveKeys.push(key)
    if (key && submissions.has(key)) return Response.json(submissions.get(key), { status: 201 })
    const override = await (action === 'run' ? overrides.run?.(request, history) : overrides.send?.(request))
    if (override) return override

    const message: MessageResponse = {
      id: `${branchId}-${history.length + 1}`, chat_id: chat.id, parent_id: history.at(-1)?.id ?? null,
      role: action === 'run' ? 'model' : 'user',
      content: action === 'run' ? `Ответ для ${branchId}` : (request.body as { content: string }).content,
      is_active: true, is_edited: false, metadata: null, created_at: chat.created_at, updated_at: null,
    }
    history.push(message)
    if (key) submissions.set(key, message)
    if (action === 'messages' && overrides.loseSaveResponse?.()) throw new TypeError('Lost response after commit')
    return Response.json(message, { status: action === 'run' ? 200 : 201 })
  })
  vi.stubGlobal('fetch', fetchMock)
  return { posts, saveKeys }
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
    { branchId: main.id, action: 'run', body: {
      expected_head_id: 'branch-main-1', expected_question_id: 'branch-main-1',
    } },
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
  expect(posts.at(-1)).toEqual({ branchId: main.id, action: 'run', body: {
    expected_head_id: 'branch-main-2', expected_question_id: 'branch-main-2',
  } })
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

/** A reload between losing the save response and retrying must preserve the submission key. */
it('retries a saved message after losing its response without duplicating it', async () => {
  const loseSaveResponse = vi.fn().mockReturnValueOnce(true).mockReturnValue(false)
  const { saveKeys, posts } = mockConversation({ loseSaveResponse })
  const user = userEvent.setup()
  const app = render(<App />)
  await focusComposer()
  await user.keyboard('Сохранённый вопрос')
  await user.click(screen.getByRole('button', { name: 'Run' }))
  expect(await screen.findByRole('alert')).toBeVisible()
  expect(posts.map(({ action }) => action)).toEqual(['messages'])

  app.unmount()
  render(<App />)
  await screen.findByText('Сохранённый вопрос', { exact: true })
  expect(input()).toHaveValue('Сохранённый вопрос')
  await user.click(screen.getByRole('button', { name: 'Run' }))
  expect(await screen.findByText('Ответ для branch-main')).toBeVisible()
  expect(saveKeys[0]).toBeTruthy()
  expect(saveKeys[1]).toBe(saveKeys[0])
  expect(screen.getAllByText('Сохранённый вопрос', { exact: true })).toHaveLength(1)
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

function savedQuestion(id: string, content: string, isActive: boolean): MessageResponse {
  return {
    id, content, chat_id: chat.id, role: 'user', parent_id: null, is_active: isActive,
    is_edited: false, metadata: null, created_at: chat.created_at, updated_at: null,
  }
}

/** A mask can change the selected question without changing the branch head. */
it('runs past a summary and hidden head, then invalidates retry when the question changes', async () => {
  const history: MessageResponse[] = [
    savedQuestion('first', 'Первый вопрос', true),
    savedQuestion('second', 'Второй вопрос', true),
    { ...savedQuestion('summary', 'Выжимка', true), role: 'system_merge' },
    savedQuestion('hidden', 'Скрытая вершина', false),
  ]
  const run = vi.fn()
    .mockReturnValueOnce(Response.json({ detail: 'Ошибка Gemini.' }, { status: 502 }))
    .mockReturnValueOnce(undefined)
  const { posts } = mockConversation({ history, run })
  const user = userEvent.setup()
  render(<App />)

  expect(await screen.findByText('Run ответит на сохранённый вопрос')).toBeVisible()
  await user.click(screen.getByRole('button', { name: 'Run' }))
  expect(await screen.findByRole('button', { name: 'Повторить генерацию' })).toBeVisible()
  const row = within(screen.getByText('Второй вопрос').closest('li') as HTMLElement)
  await user.click(row.getByRole('button', { name: 'Действия с сообщением' }))
  await user.click(row.getByRole('button', { name: 'Скрыть из контекста' }))
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Повторить генерацию' })).not.toBeInTheDocument())
  expect(posts).toHaveLength(1)

  await user.click(screen.getByRole('button', { name: 'Run' }))
  expect(await screen.findByText('Ответ для branch-main')).toBeVisible()
  expect(posts.map(({ body }) => body)).toEqual([
    { expected_head_id: 'hidden', expected_question_id: 'second' },
    { expected_head_id: 'hidden', expected_question_id: 'first' },
  ])
  expect(screen.getByText('Введите новое сообщение')).toBeVisible()
  expect(screen.getByRole('button', { name: 'Run' })).toBeDisabled()
})

it('offers to edit a saved oversized question before running it again', async () => {
  const run = vi.fn()
    .mockReturnValueOnce(Response.json({ detail: 'Превышен лимит.' }, { status: 413 }))
    .mockReturnValueOnce(undefined)
  mockConversation({ history: [savedQuestion('question', 'Слишком длинный вопрос', true)], run })
  const user = userEvent.setup()
  render(<App />)

  await screen.findByText('Слишком длинный вопрос')
  const runButton = screen.getByRole('button', { name: 'Run' })
  await user.click(runButton)
  expect(await screen.findByRole('alert')).toHaveTextContent('Сократите его через «Редактировать»')
  expect(runButton).toBeDisabled()
  await user.click(screen.getByRole('button', { name: 'Редактировать сообщение' }))
  const editor = screen.getByRole('textbox', { name: 'Текст сообщения' })
  editor.focus()
  await user.keyboard('{Control>}a{/Control}Короткий вопрос')
  await user.click(screen.getByRole('button', { name: 'Сохранить' }))
  expect(await screen.findByText('Короткий вопрос')).toBeVisible()
  expect(runButton).toBeEnabled()
  await user.click(runButton)
  expect(await screen.findByText('Ответ для branch-main')).toBeVisible()
})
