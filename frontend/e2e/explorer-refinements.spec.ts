import { expect, test } from '@playwright/test'

// The callback executes in the browser; this test project otherwise uses Node types.
declare function requestAnimationFrame(callback: () => void): number

/** Exercise the same collapsed state through both the button and the physical divider. */
test('keeps chat creation available while the explorer occupies the sidebar', async ({ page }) => {
  const title = `Панели ${Date.now()}`
  const created = await page.request.post('/api/v1/chats', { data: { title } })
  expect(created.status()).toBe(201)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/')
  await page.getByRole('button', { name: title, exact: true }).click()
  await page.getByRole('button', { name: 'Проводник', exact: true }).click()
  const panel = page.getByRole('region', { name: 'Панель проводника' })
  await expect(panel).toBeVisible()
  const chats = page.getByRole('button', { name: 'Чаты', exact: true })
  await expect(chats).toHaveAttribute('aria-expanded', 'true')
  await chats.click()
  await expect(page.getByRole('navigation', { name: 'Чаты' })).toHaveCount(0)
  const fullHeight = (await panel.boundingBox())!.height
  expect(fullHeight).toBeGreaterThan(650)
  await page.getByRole('button', { name: 'Новый чат', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Новый чат', exact: true })
  await expect(dialog).toBeVisible()
  const input = dialog.getByRole('textbox', { name: 'Название чата' })
  await input.fill('Не потерять название')
  let fail = true
  await page.route('**/api/v1/chats', async (route) => {
    if (route.request().method() === 'POST' && fail) { fail = false; await route.abort('failed') }
    else await route.continue()
  })
  await dialog.getByRole('button', { name: 'Создать', exact: true }).click()
  await expect(dialog.getByRole('alert')).toBeVisible()
  await expect(input).toHaveValue('Не потерять название')
  await dialog.getByRole('button', { name: 'Создать', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Не потерять название' })).toBeVisible()
  await expect(chats).toHaveAttribute('aria-expanded', 'false')
  await chats.click()
  await expect(chats).toHaveAttribute('aria-expanded', 'true')
  expect((await panel.boundingBox())!.height).toBeLessThan(fullHeight - 100)
  const divider = await page.getByRole('separator', { name: 'Высота проводника' }).boundingBox()
  await page.mouse.move(divider!.x + divider!.width / 2, divider!.y + divider!.height / 2)
  await page.mouse.down()
  await page.mouse.move(divider!.x + divider!.width / 2, 95, { steps: 12 })
  await page.mouse.up()
  await expect(chats).toHaveAttribute('aria-expanded', 'false')
  await panel.getByRole('button', { name: 'Проводник', exact: true }).click()
  await expect(panel).toHaveCount(0)
  await page.getByRole('button', { name: 'Проводник', exact: true }).click()
  await expect(panel).toBeVisible()
  await page.reload()
  await expect(chats).toHaveAttribute('aria-expanded', 'false')
  await expect(panel).toBeVisible()
})

/** Observer notifications must not feed back synchronously into panel geometry. */
test('changes panel geometry without ResizeObserver runtime errors', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/')
  await page.getByRole('button', { name: 'Проводник', exact: true }).click()
  const chats = page.getByRole('button', { name: 'Чаты', exact: true })
  await chats.click()
  await expect(chats).toHaveAttribute('aria-expanded', 'false')
  await chats.click()
  await expect(chats).toHaveAttribute('aria-expanded', 'true')
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))))
  expect(errors).toEqual([])
})

/** Preview decoration follows the server flag and shares the timeline edit timestamp format. */
test('shows an ellipsis and the same edit timestamp as the conversation', async ({ page }) => {
  const response = await page.request.post('/api/v1/chats', { data: { title: `Превью ${Date.now()}` } })
  const chat = await response.json()
  const branches = await (await page.request.get(`/api/v1/chats/${chat.id}/branches`)).json()
  const saved = await (await page.request.post(`/api/v1/branches/${branches[0].id}/messages`, { data: { content: 'original' } })).json()
  const edited = await page.request.patch(`/api/v1/messages/${saved.id}`, { data: { content: 'я'.repeat(201), force: true } })
  expect(edited.status()).toBe(200)
  await page.goto('/')
  await page.getByRole('button', { name: chat.title, exact: true }).click()
  const conversationTitle = await page.getByRole('main').getByText('Изменено', { exact: true }).getAttribute('title')
  await page.getByRole('button', { name: 'Проводник', exact: true }).click()
  const panel = page.getByRole('region', { name: 'Панель проводника' })
  await panel.getByRole('button', { name: `Вы: ${'я'.repeat(200)}`, exact: true }).click()
  const preview = panel.getByLabel('Превью сообщения')
  await expect(preview.getByText('я'.repeat(200) + '…', { exact: true })).toBeVisible()
  await expect(preview.getByText('Изменено', { exact: true })).toHaveAttribute('title', conversationTitle!)
})

/** ResizeObserver reports via window ErrorEvent even when pageerror does not receive it. */
test('does not dispatch window resize errors while the splitter collapses chats', async ({ page }) => {
  await page.addInitScript(() => {
    const browser = globalThis as unknown as { resizeFailures: string[]; addEventListener: (name: string, callback: (event: { message: string }) => void) => void }
    browser.resizeFailures = []
    browser.addEventListener('error', (event) => { if (event.message.includes('ResizeObserver')) browser.resizeFailures.push(event.message) })
  })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/')
  await page.getByRole('button', { name: 'Проводник', exact: true }).click()
  const chats = page.getByRole('button', { name: 'Чаты', exact: true })
  await chats.click()
  await expect(chats).toHaveAttribute('aria-expanded', 'false')
  await chats.click()
  await expect(chats).toHaveAttribute('aria-expanded', 'true')
  const divider = await page.getByRole('separator', { name: 'Высота проводника' }).boundingBox()
  await page.mouse.move(divider!.x + divider!.width / 2, divider!.y + divider!.height / 2)
  await page.mouse.down()
  await page.mouse.move(divider!.x + divider!.width / 2, 95, { steps: 12 })
  await page.mouse.up()
  await expect(chats).toHaveAttribute('aria-expanded', 'false')
  const errors = await page.evaluate(() => (globalThis as unknown as { resizeFailures: string[] }).resizeFailures)
  expect(errors).toEqual([])
})
