import { act, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import App from '../src/App'
import type { MessageResponse } from '../src/api/types'
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
  await screen.findByText('Слияние ветки')
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
  expect(within(messages[2]).getByText('Итог ветки')).not.toBeVisible()
  await userEvent.click(within(messages[2]).getByText('Слияние ветки'))
  expect(within(messages[2]).getByText('Итог ветки')).toBeVisible()
  expect(screen.getByRole('combobox', { name: 'Ветка' })).toHaveValue(main.id)
})

it('reveals older pages on upward scroll and resets when selecting a branch', async () => {
  const history: MessageResponse[] = Array.from({ length: 85 }, (_, index) => ({
    ...timeline[0],
    id: String(index + 1),
    parent_id: index === 0 ? null : String(index),
    role: 'user',
    content: 'Реплика ' + (index + 1),
  }))
  const reply: MessageResponse = {
    ...history[84], id: '86', parent_id: '85', role: 'model', content: 'Новый ответ',
  }
  const mock = mockReadApi({
    [chatsUrl]: () => Response.json([chat]),
    [branchesUrl]: () => Response.json([main, side]),
    [timelineUrl(main.id)]: () => Response.json(history),
    [timelineUrl(side.id)]: () => Response.json(history.slice(0, 45)),
    ['/api/v1/branches/' + main.id + '/run']: () => Response.json(reply),
  })
  render(<App />)

  const list = () => screen.getByRole('list', { name: 'История сообщений' })
  const cards = () => list().children
  await screen.findByText('Реплика 85')
  expect(cards()).toHaveLength(40)
  expect(cards()[0]).toHaveTextContent('Реплика 46')
  expect(within(list()).queryByText('Реплика 45')).not.toBeInTheDocument()

  const scrollArea = list().closest<HTMLDivElement>('[data-timeline-scroll]')!
  Object.defineProperty(scrollArea, 'scrollHeight', {
    get: () => (scrollArea.querySelector('ol[aria-label="История сообщений"]')?.childElementCount ?? 0) * 70,
  })
  const scrollTo = (top: number) => {
    scrollArea.scrollTop = top
    fireEvent.scroll(scrollArea)
  }
  const requestsBeforeReveal = mock.mock.calls.length
  scrollTo(100)
  expect(cards()).toHaveLength(40)
  scrollTo(800)
  scrollTo(251)
  expect(cards()).toHaveLength(40)
  scrollTo(250)
  expect(cards()).toHaveLength(80)
  expect(cards()[0]).toHaveTextContent('Реплика 6')
  expect(scrollArea.scrollTop).toBe(250 + 40 * 70)
  fireEvent.scroll(scrollArea)
  expect(cards()).toHaveLength(80)
  scrollTo(120)
  expect(cards()).toHaveLength(85)
  expect(within(scrollArea).queryByRole('button', { name: /Показать предыдущие/ })).not.toBeInTheDocument()
  expect(mock).toHaveBeenCalledTimes(requestsBeforeReveal)

  const composer = screen.getByRole('form', { name: 'Ввод сообщения' })
  await userEvent.click(within(composer).getByRole('button', { name: /^Run$/ }))
  expect(await screen.findByText('Новый ответ')).toBeVisible()
  expect(cards()).toHaveLength(86)

  const branchPicker = screen.getByRole('combobox', { name: 'Ветка' })
  await userEvent.selectOptions(branchPicker, side.id)
  await screen.findByText('Реплика 45')
  expect(cards()).toHaveLength(40)
  expect(within(list()).queryByText('Реплика 5')).not.toBeInTheDocument()
  await userEvent.selectOptions(branchPicker, main.id)
  await screen.findByText('Реплика 85')
  expect(cards()).toHaveLength(40)
  expect(within(list()).queryByText('Реплика 45')).not.toBeInTheDocument()
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
  expect(screen.queryByText('Слияние ветки')).not.toBeInTheDocument()
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
  expect(await screen.findByText('Слияние ветки')).toBeVisible()
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

  await screen.findByText('Слияние ветки')
  await userEvent.click(screen.getByRole('button', { name: otherChat.title }))
  expect(await screen.findByText('В этой ветке пока нет сообщений.')).toBeVisible()
  expect(screen.getByRole('combobox', { name: 'Ветка' })).toHaveValue(otherBranch.id)
  expect(screen.queryByText('Слияние ветки')).not.toBeInTheDocument()
})

it('creates a chat from an empty workspace and opens its main branch', async () => {
  const user = userEvent.setup()
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
  await user.click(screen.getByRole('button', { name: 'Новый чат' }))
  screen.getByRole('textbox', { name: 'Название чата' }).focus()
  await user.keyboard(`${chat.title}{Enter}`)

  expect(await screen.findByText('В этой ветке пока нет сообщений.')).toBeVisible()
  expect(screen.getByRole('button', { name: chat.title })).toHaveAttribute('aria-current', 'true')
  expect(screen.getByRole('combobox', { name: 'Ветка' })).toHaveValue(main.id)
})
