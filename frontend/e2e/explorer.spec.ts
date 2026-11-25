import { windowFixture } from '../tests/window-fixture.js'
import { expect, test } from '@playwright/test'
import type { ChatGraphResponse } from '../src/api/types.js'
import { chat, main, timeline } from '../tests/fixtures.js'

test('loads the minimap lazily, selects nodes and preserves a chat camera across panel reopening', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  const graph: ChatGraphResponse = {
    chat_id: chat.id,
    nodes: timeline.map(({ content, ...message }) => ({
      ...message, role: message.role as 'user' | 'model' | 'system_merge', preview_truncated: false, preview: content,
    })),
    branches: [{ ...main, status: 'active' }],
    merges: [{ message_id: 'merge', source_branch_name: 'side', source_head_message_id: 'masked', compressed_messages_count: 1 }],
  }
  let requests = 0
  await page.route('**/api/v1/chats', (route) => route.fulfill({ json: [chat] }))
  await page.route('**/api/v1/chats/*/branches', (route) => route.fulfill({ json: [main] }))
  await page.route('**/api/v1/branches/*/timeline/window**', (route) => route.fulfill({ json: windowFixture(main, timeline, new URL(route.request().url()).searchParams) }))
  await page.route('**/api/v1/chats/*/graph', (route) => {
    requests++
    return requests === 1
      ? route.fulfill({ status: 503, json: { detail: 'Граф временно недоступен.' } })
      : route.fulfill({ json: graph })
  })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/')
  await expect(page.getByRole('combobox', { name: 'Ветка' })).toBeVisible()
  expect(requests).toBe(0)
  const toggle = page.getByRole('button', { name: 'Проводник', exact: true })
  await toggle.click()
  const panel = page.getByRole('region', { name: 'Панель проводника' })
  await expect(panel.getByRole('alert')).toContainText('Граф временно недоступен.')
  await panel.getByRole('button', { name: 'Повторить' }).click()
  await expect(panel.locator('.explorer-point')).toHaveCount(3)
  await expect(panel.locator('.react-flow__edge')).toHaveCount(3)
  await expect(panel.locator('[data-role="user"]')).toHaveCount(1)
  await expect(panel.locator('[data-role="model"]')).toHaveCount(1)
  await expect(panel.locator('[data-role="system_merge"]')).toHaveCount(1)
  await expect(panel.locator('[data-masked="true"]')).toHaveCount(1)
  const node = panel.getByRole('button', { name: 'Слияние: Итог ветки', exact: true })
  await node.click()
  await expect(node).toHaveAttribute('aria-pressed', 'true')
  await expect(panel.getByLabel('Превью сообщения')).toContainText('Источник: side')
  const viewport = panel.locator('.react-flow__viewport')
  const beforeZoom = await viewport.getAttribute('style')
  await panel.getByRole('button', { name: 'Увеличить граф' }).click()
  await expect.poll(() => viewport.getAttribute('style')).not.toBe(beforeZoom)
  const saved = await viewport.getAttribute('style')
  await toggle.click()
  await expect(panel).toHaveCount(0)
  await toggle.click()
  await expect(panel.locator('.explorer-point')).toHaveCount(3)
  await expect(viewport).toHaveAttribute('style', saved!)
  await panel.getByRole('button', { name: 'Показать весь граф' }).click()
  await node.focus()
  await expect(panel.getByLabel('Превью сообщения')).toContainText('Итог ветки')
  const beforeDoubleClick = await viewport.getAttribute('style')
  await node.dblclick()
  await expect(viewport).toHaveAttribute('style', beforeDoubleClick!)
  const canvas = await panel.locator('.react-flow__pane').boundingBox()
  const panStart = await viewport.getAttribute('style')
  await page.mouse.move(canvas!.x + 20, canvas!.y + 40)
  await page.mouse.down({ button: 'middle' })
  await page.mouse.move(canvas!.x + 50, canvas!.y + 70, { steps: 5 })
  await page.mouse.up({ button: 'middle' })
  await expect.poll(() => viewport.getAttribute('style')).not.toBe(panStart)
  const spaceStart = await viewport.getAttribute('style')
  await page.mouse.move(canvas!.x + 20, canvas!.y + 40)
  await page.keyboard.down('Space')
  await page.mouse.down()
  await page.mouse.move(canvas!.x + 60, canvas!.y + 80, { steps: 5 })
  await page.mouse.up()
  await page.keyboard.up('Space')
  await expect.poll(() => viewport.getAttribute('style')).not.toBe(spaceStart)
  await page.screenshot({ path: test.info().outputPath('minimap.png') })
  expect(errors).toEqual([])
})

test('fits a long branched history inside the minimap after the panel is resized', async ({ page }) => {
  const graph: ChatGraphResponse = {
    chat_id: chat.id,
    nodes: Array.from({ length: 605 }, (_, index) => ({
      id: `node-${index}`, parent_id: index ? `node-${index - 1}` : null,
      role: index % 2 ? 'model' : 'user', preview_truncated: false, preview: `Узел ${index}`, is_active: true,
      is_edited: false, created_at: chat.created_at, updated_at: null,
    })),
    branches: [{ ...main, status: 'active', head_message_id: 'node-604' }, {
      ...main, id: 'side', name: 'Боковая', status: 'archived', head_message_id: 'side-head', forked_from_id: 'node-300',
    }], merges: [],
  }
  graph.nodes.push({ ...graph.nodes[0], id: 'side-head', parent_id: 'node-300', role: 'model', is_active: false })
  await page.route('**/api/v1/chats', (route) => route.fulfill({ json: [chat] }))
  await page.route('**/api/v1/chats/*/branches', (route) => route.fulfill({ json: graph.branches }))
  await page.route('**/api/v1/branches/*/timeline/window**', (route) => route.fulfill({ json: windowFixture(main, timeline, new URL(route.request().url()).searchParams) }))
  await page.route('**/api/v1/chats/*/graph', (route) => route.fulfill({ json: graph }))
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/')
  const loadStarted = Date.now()
  await page.getByRole('button', { name: 'Проводник', exact: true }).click()
  const panel = page.getByRole('region', { name: 'Панель проводника' })
  await expect(panel.locator('.explorer-point')).toHaveCount(606, { timeout: 30_000 })
  await test.info().attach('long-graph-load-time', {
    body: JSON.stringify({ nodes: 606, elapsedMilliseconds: Date.now() - loadStarted }),
    contentType: 'application/json',
  })
  const separator = await page.getByRole('separator', { name: 'Высота проводника' }).boundingBox()
  await page.mouse.move(separator!.x + separator!.width / 2, separator!.y + separator!.height / 2)
  await page.mouse.down()
  await page.mouse.move(separator!.x + separator!.width / 2, separator!.y + 45, { steps: 5 })
  await page.mouse.up()
  await panel.getByRole('button', { name: 'Показать весь граф' }).click()
  await expect.poll(async () => {
    const area = (await panel.locator('.react-flow__pane').boundingBox())!
    const root = (await panel.getByRole('button', { name: 'Вы: Узел 0', exact: true }).boundingBox())!
    const head = (await panel.getByRole('button', { name: 'Вы: Узел 604', exact: true }).boundingBox())!
    return root.y >= area.y && head.y + head.height <= area.y + area.height
  }).toBe(true)
  const colors = await panel.locator('.explorer-point').evaluateAll((nodes) => [...new Set(nodes.map((node) => (node as { style: { color: string } }).style.color))])
  expect(colors).toHaveLength(2)
})
