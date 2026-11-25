import { windowFixture } from '../tests/window-fixture.js'
import { expect, test } from '@playwright/test'
import type { ChatGraphResponse } from '../src/api/types.js'
import { chat, main, timeline } from '../tests/fixtures.js'

/** Validate actual browser label rectangles against neighboring nodes, including zoomed-out fallback. */
test('keeps branch labels clear of nodes and exposes names in the preview at low zoom', async ({ page }) => {
  const graph: ChatGraphResponse = {
    chat_id: chat.id,
    nodes: Array.from({ length: 14 }, (_, index) => ({
      id: `main-${index}`, parent_id: index ? `main-${index - 1}` : null,
      role: index === 13 ? 'system_merge' : index % 2 ? 'model' : 'user',
      preview: `Main ${index}`, preview_truncated: false, is_active: true, is_edited: false,
      created_at: chat.created_at, updated_at: null,
    })),
    branches: [{ ...main, status: 'active', head_message_id: 'main-13' }],
    merges: [{ message_id: 'main-13', source_branch_name: 'Правая ветка', source_head_message_id: 'right-3', compressed_messages_count: 4 }],
  }
  for (const [prefix, fork, name] of [['right', 3, 'Правая ветка'], ['left', 7, 'Левая ветка']] as const) {
    for (let index = 0; index < 4; index++) graph.nodes.push({
      ...graph.nodes[0], id: `${prefix}-${index}`, parent_id: index ? `${prefix}-${index - 1}` : `main-${fork}`,
      role: 'model', preview: `${name} ${index}`,
    })
    graph.branches.push({ ...main, id: prefix, name, status: 'active', head_message_id: `${prefix}-3` })
  }
  await page.route('**/api/v1/chats', (route) => route.fulfill({ json: [chat] }))
  await page.route('**/api/v1/chats/*/branches', (route) => route.fulfill({ json: graph.branches }))
  await page.route('**/api/v1/branches/*/timeline/window**', (route) => route.fulfill({ json: windowFixture(main, timeline, new URL(route.request().url()).searchParams) }))
  await page.route('**/api/v1/chats/*/graph', (route) => route.fulfill({ json: graph }))
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/')
  await page.getByRole('button', { name: 'Проводник', exact: true }).click()
  const panel = page.getByRole('region', { name: 'Панель проводника' })
  await expect(panel.locator('.explorer-point')).toHaveCount(22)
  await page.getByRole('button', { name: 'Чаты', exact: true }).click()
  await panel.getByRole('button', { name: 'Показать весь граф' }).click()
  await expect(panel.locator('.explorer-branch-label')).toHaveCount(3)
  const collisions = await panel.evaluate((element) => {
    const points = [...element.querySelectorAll('.explorer-point')]
    return [...element.querySelectorAll('.explorer-branch-label')].some((label) => {
      const a = label.getBoundingClientRect()
      return points.some((point) => {
        if (point.parentElement === label.parentElement) return false
        const b = point.getBoundingClientRect()
        return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top
      })
    })
  })
  expect(collisions).toBe(false)
  await page.screenshot({ path: test.info().outputPath('refined-minimap.png') })
  for (let index = 0; index < 8; index++) await panel.getByRole('button', { name: 'Уменьшить граф' }).click()
  await expect(panel.locator('.explorer-branch-label')).toHaveCount(0)
  await panel.getByRole('button', { name: 'Модель: Левая ветка 3', exact: true }).focus()
  await expect(panel.getByLabel('Превью сообщения')).toContainText('Левая ветка')
})
