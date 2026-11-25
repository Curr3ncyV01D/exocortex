import { windowFixture } from '../tests/window-fixture.js'
import { expect, test } from '@playwright/test'
import type { MessageResponse } from '../src/api/types.js'
import { chat, main, timeline } from '../tests/fixtures.js'

test('renders a long timeline in batches and anchors scroll when revealing older messages', async ({ page }) => {
  const history: MessageResponse[] = Array.from({ length: 605 }, (_, index) => ({
    ...timeline[0],
    id: String(index + 1),
    parent_id: index === 0 ? null : String(index),
    role: 'user',
    content: 'Реплика ' + (index + 1) + '\n\n**Подробности сообщения**',
  }))
  await page.route('**/api/v1/chats', (route) => route.fulfill({ json: [chat] }))
  await page.route('**/api/v1/chats/*/branches', (route) => route.fulfill({ json: [main] }))
  await page.route('**/api/v1/branches/*/timeline/window**', (route) => route.fulfill({ json: windowFixture(main, history, new URL(route.request().url()).searchParams) }))
  await page.goto('/')

  const list = page.getByRole('list', { name: 'История сообщений' })
  await expect(list.locator(':scope > li')).toHaveCount(40)
  await expect(list.getByText('Реплика 605', { exact: true })).toBeInViewport()

  const scrollArea = page.locator('[data-timeline-scroll]')
  await scrollArea.evaluate((element) => new Promise<void>((resolve) => {
    element.addEventListener('scroll', () => resolve(), { once: true })
    element.scrollTop = 330
  }))
  await expect(list.locator(':scope > li')).toHaveCount(40)
  const anchor = list.getByText('Реплика 568', { exact: true })
  const originalY = await anchor.evaluate((node) => {
    const area = node.closest('[data-timeline-scroll]')!
    area.scrollTop = 190
    return node.getBoundingClientRect().y
  })

  await expect(list.locator(':scope > li')).toHaveCount(80)
  await expect(list.getByText('Реплика 526', { exact: true })).toHaveCount(1)
  await expect.poll(async () => Math.abs((await anchor.boundingBox())!.y - originalY)).toBeLessThan(2)

  await scrollArea.evaluate((element) => { element.scrollTop = 100 })
  await expect(list.locator(':scope > li')).toHaveCount(120)
  await expect(list.getByText('Реплика 486', { exact: true })).toHaveCount(1)
  await expect(page.getByRole('button', { name: /Показать предыдущие/ })).toHaveCount(0)
})
