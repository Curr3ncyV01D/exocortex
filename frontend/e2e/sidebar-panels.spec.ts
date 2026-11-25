import { expect, test } from '@playwright/test'

/** Button-based hiding must leave the other island closed when the explorer opens. */
test('opens only the requested island after both were closed using buttons', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/')
  const chats = () => page.getByRole('button', { name: 'Чаты', exact: true })
  const explorer = () => page.getByRole('button', { name: 'Проводник', exact: true })
  await chats().click()
  await expect(chats()).toHaveAttribute('aria-expanded', 'false')
  await chats().click()
  await expect(chats()).toHaveAttribute('aria-expanded', 'true')
  await chats().click()
  await explorer().click()
  await expect(explorer()).toHaveAttribute('aria-expanded', 'true')
  await expect(chats()).toHaveAttribute('aria-expanded', 'false')
  const panel = page.getByRole('region', { name: 'Панель проводника' })
  await expect(panel).toBeVisible()
  await expect.poll(async () => (await panel.boundingBox())!.height).toBeGreaterThan(650)
  await expect(page.getByRole('navigation', { name: 'Чаты' })).toHaveCount(0)
  await page.reload()
  await expect(chats()).toHaveAttribute('aria-expanded', 'false')
  await expect(explorer()).toHaveAttribute('aria-expanded', 'true')
  await explorer().click()
  await chats().click()
  await expect(chats()).toHaveAttribute('aria-expanded', 'true')
  await expect(explorer()).toHaveAttribute('aria-expanded', 'false')
  await expect(panel).toHaveCount(0)
})

/** Both endpoints of the divider must dispatch the same visibility changes as buttons. */
test('collapses either island with the divider and restores the previous split with buttons', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/')
  const chats = () => page.getByRole('button', { name: 'Чаты', exact: true })
  const explorer = () => page.getByRole('button', { name: 'Проводник', exact: true })
  await explorer().click()
  const panel = page.getByRole('region', { name: 'Панель проводника' })
  const height = (await panel.boundingBox())!.height
  async function dragTo(y: number) {
    const divider = (await page.getByRole('separator', { name: 'Высота проводника' }).boundingBox())!
    await page.mouse.move(divider.x + divider.width / 2, divider.y + divider.height / 2)
    await page.mouse.down()
    await page.mouse.move(divider.x + divider.width / 2, y, { steps: 12 })
    await page.mouse.up()
  }
  await dragTo(885)
  await expect(explorer()).toHaveAttribute('aria-expanded', 'false')
  await expect(chats()).toHaveAttribute('aria-expanded', 'true')
  await expect(panel).toHaveCount(0)
  await explorer().click()
  await expect.poll(async () => Math.abs((await panel.boundingBox())!.height - height)).toBeLessThan(8)
  await dragTo(110)
  await expect(chats()).toHaveAttribute('aria-expanded', 'false')
  await expect(explorer()).toHaveAttribute('aria-expanded', 'true')
  await dragTo(440)
  await expect(chats()).toHaveAttribute('aria-expanded', 'true')
  await expect(explorer()).toHaveAttribute('aria-expanded', 'true')
  await dragTo(885)
  await expect(explorer()).toHaveAttribute('aria-expanded', 'false')
  await page.reload()
  await expect(explorer()).toHaveAttribute('aria-expanded', 'false')
  await expect(chats()).toHaveAttribute('aria-expanded', 'true')
  await dragTo(440)
  await expect(explorer()).toHaveAttribute('aria-expanded', 'true')
  expect(errors).toEqual([])
})

/** Collapsing the island must not expose a zero-sized viewport to React Flow. */
test('collapses a loaded graph without React Flow container warnings', async ({ page }) => {
  const warnings: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'warning' && message.text().includes('[React Flow]')) warnings.push(message.text())
  })
  const response = await page.request.post('/api/v1/chats', { data: { title: `Контейнер ${Date.now()}` } })
  const chat = await response.json()
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/')
  await page.getByRole('button', { name: chat.title, exact: true }).click()
  const explorer = () => page.getByRole('button', { name: 'Проводник', exact: true })
  await explorer().click()
  await expect(page.locator('.react-flow')).toBeVisible()
  for (let attempt = 0; attempt < 2; attempt++) {
    const divider = (await page.getByRole('separator', { name: 'Высота проводника' }).boundingBox())!
    await page.mouse.move(divider.x + divider.width / 2, divider.y + divider.height / 2)
    await page.mouse.down()
    await page.mouse.move(divider.x + divider.width / 2, 885, { steps: 12 })
    await page.mouse.up()
    await expect(explorer()).toHaveAttribute('aria-expanded', 'false')
    await explorer().click()
    await expect(page.locator('.react-flow')).toBeVisible()
  }
  expect(warnings).toEqual([])
})
