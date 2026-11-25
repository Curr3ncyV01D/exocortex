import { expect, test } from '@playwright/test'
import type { MessageResponse } from '../src/api/types.js'
import { chat, main, side, timeline } from '../tests/fixtures.js'
import { windowFixture } from '../tests/window-fixture.js'

/** Use HTTP fixtures for long histories and a deferred model reply; no live provider is involved. */
test('opens a far graph target, bounds both scroll directions and keeps a later reply from moving the reader', async ({ page }) => {
  test.setTimeout(90_000)
  const history: MessageResponse[] = Array.from({ length: 240 }, (_, index) => ({ ...timeline[0], id: `nav-${index}`, parent_id: index ? `nav-${index - 1}` : null, role: 'user', content: `Навигация ${index}` }))
  const requests: string[] = []
  let release!: () => void
  const pending = new Promise<void>((resolve) => { release = resolve })
  const branch = () => ({ ...main, status: 'active' as const, head_message_id: history.at(-1)!.id })
  const graph = () => ({ chat_id: chat.id, nodes: history.map((row) => ({
    id: row.id, parent_id: row.parent_id, role: row.role, preview: row.content, preview_truncated: false,
    is_active: row.is_active, is_edited: row.is_edited, created_at: row.created_at, updated_at: row.updated_at,
  })), branches: [branch()], merges: [] })
  await page.route('**/api/v1/chats', (route) => route.fulfill({ json: [chat] }))
  await page.route('**/api/v1/chats/*/branches', (route) => route.fulfill({ json: [branch()] }))
  await page.route('**/api/v1/chats/*/graph', (route) => route.fulfill({ json: graph() }))
  await page.route('**/api/v1/branches/*/timeline/window**', (route) => {
    requests.push(route.request().url())
    return route.fulfill({ json: windowFixture(branch(), history, new URL(route.request().url()).searchParams) })
  })
  await page.route('**/api/v1/branches/*/run', async (route) => {
    expect(route.request().postDataJSON()).toEqual({ expected_head_id: 'nav-239', expected_question_id: 'nav-239' })
    await pending
    const reply: MessageResponse = { ...history[0], id: 'nav-reply', parent_id: 'nav-239', role: 'model', content: 'Ответ после перехода' }
    history.push(reply)
    await route.fulfill({ json: reply })
  })
  await page.goto('/')
  const list = page.getByRole('list', { name: 'История сообщений' })
  await expect(list.locator(':scope > li')).toHaveCount(40)
  await page.getByRole('button', { name: 'Проводник', exact: true }).click()
  const panel = page.getByRole('region', { name: 'Панель проводника' })
  await expect(panel.locator('.explorer-point')).toHaveCount(240, { timeout: 30_000 })
  const viewport = panel.locator('.react-flow__viewport')
  const camera = await viewport.getAttribute('style')
  const far = panel.getByRole('button', { name: 'Вы: Навигация 160', exact: true })
  await far.focus()
  await far.press('Enter')
  await expect(list.getByText('Навигация 160', { exact: true })).toBeInViewport()
  await expect(list.locator(':scope > li')).toHaveCount(41)
  await expect(viewport).toHaveAttribute('style', camera!)
  await expect(page.getByRole('button', { name: 'К последним сообщениям' })).toBeVisible()
  const area = page.locator('[data-timeline-scroll]')
  for (let i = 0; i < 4; i++) {
    await area.evaluate((node) => { node.scrollTop = Math.min(node.scrollHeight, 500) })
    await area.evaluate((node) => { node.scrollTop = 1 })
    const first = Math.max(0, 140 - (i + 1) * 40)
    await expect(list.getByText(`Навигация ${first}`, { exact: true })).toHaveCount(1)
    expect(await list.locator(':scope > li').count()).toBeLessThanOrEqual(160)
  }
  await expect(list.getByText('Навигация 0', { exact: true })).toHaveCount(1)
  await area.evaluate((node) => { node.scrollTop = node.scrollHeight - 1 })
  await expect(list.getByText('Навигация 199', { exact: true })).toHaveCount(1)
  await expect(list.getByText('Навигация 0', { exact: true })).toHaveCount(0)
  expect(requests.some((url) => url.includes('mode=newer'))).toBe(true)
  await page.getByRole('button', { name: 'К последним сообщениям' }).click()
  await expect(list.getByText('Навигация 239', { exact: true })).toBeInViewport()
  const sent = page.waitForRequest((request) => request.url().endsWith('/run'))
  await page.getByRole('button', { name: 'Run', exact: true }).click()
  await sent
  await far.focus()
  await far.press('Enter')
  await expect(list.getByText('Навигация 160', { exact: true })).toBeInViewport()
  release()
  await expect(page.getByRole('button', { name: 'К последним сообщениям' })).toBeVisible()
  await expect(panel.locator('.explorer-point')).toHaveCount(241, { timeout: 30_000 })
  await expect(list.getByText('Навигация 160', { exact: true })).toBeInViewport()
  await page.getByRole('button', { name: 'К последним сообщениям' }).click()
  await expect(list.getByText('Ответ после перехода', { exact: true })).toBeInViewport()
})


/** Choosing a branch is explicit, and cancellation invalidates even an already-started read. */
test('chooses among shared branches, preserves archived status and cancels an in-flight transition', async ({ page }) => {
  const rows: MessageResponse[] = Array.from({ length: 6 }, (_, index) => ({ ...timeline[0], id: `choice-${index}`, parent_id: index ? `choice-${index - 1}` : null, role: 'user', content: index ? `Общая цепочка ${index}` : 'Общая реплика' }))
  const separate: MessageResponse = { ...rows[0], id: 'separate', parent_id: null, content: 'Отдельная цепочка' }
  const branches = [{ ...main, status: 'active' as const, head_message_id: rows[5].id },
    { ...side, status: 'archived' as const, head_message_id: rows[4].id },
    { ...main, status: 'active' as const, id: 'unrelated', name: 'Отдельная', head_message_id: separate.id }]
  const histories: Record<string, MessageResponse[]> = { [main.id]: rows, [side.id]: rows.slice(0, 5), unrelated: [separate] }
  let release!: () => void
  let hold = true
  const pending = new Promise<void>((resolve) => { release = resolve })
  await page.route('**/api/v1/chats', (route) => route.fulfill({ json: [chat] }))
  await page.route('**/api/v1/chats/*/branches', (route) => route.fulfill({ json: branches }))
  await page.route('**/api/v1/chats/*/graph', (route) => route.fulfill({ json: {
    chat_id: chat.id, branches, merges: [], nodes: [...rows, separate].map((row) => ({ id: row.id,
      parent_id: row.parent_id, role: row.role, preview: row.content, preview_truncated: false,
      is_active: row.is_active, is_edited: row.is_edited, created_at: row.created_at, updated_at: null })),
  } }))
  await page.route('**/api/v1/branches/*/timeline/window**', async (route) => {
    const url = new URL(route.request().url()), id = url.pathname.split('/')[4]
    if (id === side.id && hold) await pending
    await route.fulfill({ json: windowFixture(branches.find((branch) => branch.id === id)!, histories[id], url.searchParams) })
  })
  await page.goto('/')
  const picker = page.getByRole('combobox', { name: 'Ветка' })
  await picker.selectOption('unrelated')
  await expect(page.getByText('Отдельная цепочка', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Проводник', exact: true }).click()
  const root = page.getByRole('region', { name: 'Панель проводника' }).getByRole('button', { name: 'Вы: Общая реплика', exact: true })
  await root.focus(); await root.press('Enter')
  const dialog = page.getByRole('dialog', { name: 'Выберите ветку' })
  const archive = dialog.getByRole('button', { name: /feature\/refactor/ })
  await expect(archive).toContainText('Архивная')
  const started = page.waitForRequest((request) => request.url().includes(`/branches/${side.id}/timeline/window`))
  await archive.click(); await started
  await dialog.getByRole('button', { name: 'Отмена', exact: true }).click()
  hold = false; release()
  await expect(dialog).toHaveCount(0)
  await expect(picker).toHaveValue('unrelated')
  await expect(page.getByText('Отдельная цепочка', { exact: true })).toBeVisible()
  await root.focus(); await root.press('Enter')
  await archive.click()
  await expect(picker).toHaveValue(side.id)
  await expect(page.getByRole('list', { name: 'История сообщений' }).getByText('Общая реплика', { exact: true })).toBeInViewport()
})
