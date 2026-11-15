import { expect, test } from '@playwright/test'

test('recovers a lost save and completes a conversation, fork and merge', async ({ page }) => {
  const keys: (string | undefined)[] = []
  await page.route('**/api/v1/branches/*/messages', async (route) => {
    keys.push(route.request().headers()['idempotency-key'])
    if (keys.length === 1) {
      // Drop the reply only after FastAPI has committed the message.
      const saved = await route.fetch()
      expect(saved.status()).toBe(201)
      await route.abort('failed')
    } else {
      await route.continue()
    }
  })

  await page.goto('/')
  await page.getByRole('button', { name: 'Новый чат' }).click()
  await page.getByRole('textbox', { name: 'Название чата' }).fill('Приёмка MVP')
  await page.getByRole('button', { name: 'Создать', exact: true }).click()
  const input = page.getByRole('textbox', { name: 'Сообщение' })
  const run = page.getByRole('button', { name: 'Run', exact: true })
  const firstQuestion = 'Первый вопрос для проверки'
  await input.fill(firstQuestion)
  await run.click()
  await expect(page.getByRole('alert')).toContainText('Не удалось подтвердить сохранение')
  await page.reload()
  await expect(input).toHaveValue(firstQuestion)
  await run.click()
  const answer = page.getByText('Ответ тестовой модели', { exact: true })
  await expect(answer).toBeVisible()
  await expect(page.getByText(firstQuestion, { exact: true })).toHaveCount(1)
  expect(keys[0]).toMatch(/^[0-9a-f-]{36}$/)
  expect(keys[1]).toBe(keys[0])

  await answer.hover()
  const reply = page.getByRole('listitem').filter({ hasText: 'Ответ тестовой модели' })
  await reply.getByRole('button', { name: 'Действия с сообщением' }).click()
  await reply.getByRole('button', { name: 'Создать ветку отсюда' }).click()
  const fork = page.getByRole('dialog', { name: 'Создать ветку' })
  await fork.getByRole('textbox', { name: 'Имя ветки' }).fill('feature/acceptance')
  await fork.getByRole('button', { name: 'Создать ветку' }).click()
  const picker = page.getByRole('combobox', { name: 'Ветка' })
  await expect(picker.locator('option:checked')).toHaveText('feature/acceptance')
  await input.fill('Дополнение в боковой ветке')
  await run.click()
  await expect(answer).toHaveCount(2)
  expect(keys[2]).not.toBe(keys[0])

  await page.getByRole('button', { name: 'Слить в main', exact: true }).click()
  const merge = page.getByRole('dialog', { name: 'Слить ветку в main' })
  await merge.getByRole('button', { name: 'Слить в main' }).click()
  await expect(picker.locator('option:checked')).toHaveText('main')
  await page.getByText('Слияние ветки', { exact: true }).click()
  await expect(page.getByText('Выжимка тестовой ветки', { exact: true })).toBeVisible()
  await expect(page.getByText('Дополнение в боковой ветке', { exact: true })).toHaveCount(0)

  await page.setViewportSize({ width: 390, height: 780 })
  const chats = page.getByRole('button', { name: 'Чаты', exact: true })
  await chats.click()
  await expect(page.getByRole('dialog', { name: 'Чаты' }).getByRole('button', { name: 'Приёмка MVP' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(chats).toBeFocused()
})
