import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import App from '../src/App'
import { chat, main, side, timeline } from './fixtures'

const chatsUrl = '/api/v1/chats'
const branchesUrl = `${chatsUrl}/${chat.id}/branches`
const timelineUrl = (branchId: string) =>
  `/api/v1/branches/${branchId}/timeline?include_inactive=true`

/** Run the app against the fetch boundary and fail on unexpected routes. */
function mockReadApi(routes: Record<string, () => Response | Promise<Response>>) {
  const fetchMock = vi.fn(async (url: string) => {
    const handler = routes[url]
    if (!handler) throw new Error(`Unexpected request: ${url}`)
    return handler()
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

it('shows the main timeline, renders Markdown and keeps masked messages in order after remount', async () => {
  mockReadApi({
    [chatsUrl]: () => Response.json([chat]),
    [branchesUrl]: () => Response.json([main, side]),
    [timelineUrl(main.id)]: () => Response.json(timeline),
  })
  const firstRender = render(<App />)
  await screen.findByText('Итог ветки')
  firstRender.unmount()
  render(<App />)

  const history = within(await screen.findByRole('list', { name: 'История сообщений' }))
  const messages = history.getAllByRole('listitem')
  expect(messages).toHaveLength(3)
  expect(messages[0]).toHaveTextContent('Вопрос')
  expect(messages[1]).toHaveTextContent('Вне контекста')
  expect(messages[1].querySelector('strong')).toHaveTextContent('Скрытый ответ')
  expect(messages[1].querySelector('pre > code')).toHaveTextContent('print(1)')
  expect(messages[2]).toHaveTextContent('Слияние ветки')
  expect(messages[2]).toHaveTextContent('Итог ветки')
  expect(screen.getByRole('combobox', { name: 'Ветка' })).toHaveValue(main.id)
})

/** A slow reply from the previous branch must not replace the selected history. */
it('ignores a late timeline response after switching branches', async () => {
  let resolveMain: (response: Response) => void = () => {}
  const pendingMain = new Promise<Response>((resolve) => { resolveMain = resolve })
  const fetchMock = mockReadApi({
    [chatsUrl]: () => Response.json([chat]),
    [branchesUrl]: () => Response.json([main, side]),
    [timelineUrl(main.id)]: () => pendingMain,
    [timelineUrl(side.id)]: () => Response.json([]),
  })
  render(<App />)

  const select = await screen.findByRole('combobox', { name: 'Ветка' })
  expect(fetchMock).toHaveBeenCalledWith(timelineUrl(main.id), expect.anything())
  await userEvent.selectOptions(select, side.id)
  expect(await screen.findByText('В этой ветке пока нет сообщений.')).toBeVisible()
  await act(async () => { resolveMain(Response.json(timeline)) })
  expect(select).toHaveValue(side.id)
  expect(screen.queryByText('Итог ветки')).not.toBeInTheDocument()
})

it('retries a failed timeline request on the same branch', async () => {
  const loadHistory = vi.fn()
    .mockReturnValueOnce(Response.json({ detail: 'История недоступна.' }, { status: 502 }))
    .mockReturnValueOnce(Response.json(timeline))
  mockReadApi({
    [chatsUrl]: () => Response.json([chat]),
    [branchesUrl]: () => Response.json([main]),
    [timelineUrl(main.id)]: loadHistory,
  })
  render(<App />)

  expect(await screen.findByRole('alert')).toHaveTextContent('История недоступна.')
  await userEvent.click(screen.getByRole('button', { name: 'Повторить загрузку' }))
  expect(await screen.findByText('Итог ветки')).toBeVisible()
  expect(loadHistory).toHaveBeenCalledTimes(2)
})

it('switches chats and scopes branch loading to the selected chat', async () => {
  const otherChat = { ...chat, id: 'chat-2', title: 'Второй чат' }
  const otherBranch = { ...main, id: 'branch-2', chat_id: otherChat.id, name: 'other' }
  mockReadApi({
    [chatsUrl]: () => Response.json([chat, otherChat]),
    [branchesUrl]: () => Response.json([main]),
    [timelineUrl(main.id)]: () => Response.json(timeline),
    [`${chatsUrl}/${otherChat.id}/branches`]: () => Response.json([otherBranch]),
    [timelineUrl(otherBranch.id)]: () => Response.json([]),
  })
  render(<App />)

  await screen.findByText('Итог ветки')
  await userEvent.click(screen.getByRole('button', { name: otherChat.title }))
  expect(await screen.findByText('В этой ветке пока нет сообщений.')).toBeVisible()
  expect(screen.getByRole('combobox', { name: 'Ветка' })).toHaveValue(otherBranch.id)
  expect(screen.queryByText('Итог ветки')).not.toBeInTheDocument()
})

it('creates a chat from an empty workspace and opens its main branch', async () => {
  const fetchMock = vi.fn(async (url: string, options: RequestInit) => {
    if (url === chatsUrl) {
      return options.method === 'POST' ? Response.json(chat, { status: 201 }) : Response.json([])
    }
    if (url === branchesUrl) return Response.json([main])
    if (url === timelineUrl(main.id)) return Response.json([])
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  render(<App />)

  await screen.findByText('Чатов пока нет.')
  await userEvent.click(screen.getByRole('button', { name: 'Новый чат' }))
  await userEvent.type(screen.getByRole('textbox', { name: 'Название чата' }), chat.title)
  await userEvent.click(screen.getByRole('button', { name: 'Создать' }))

  expect(await screen.findByText('В этой ветке пока нет сообщений.')).toBeVisible()
  expect(screen.getByRole('button', { name: chat.title })).toHaveAttribute('aria-current', 'true')
  expect(screen.getByRole('combobox', { name: 'Ветка' })).toHaveValue(main.id)
})
