import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import App from '../src/App'

function setDesktop(matches: boolean) {
  vi.stubGlobal('matchMedia', vi.fn(() => ({
    matches, addEventListener: vi.fn(), removeEventListener: vi.fn(),
  })))
  vi.stubGlobal('fetch', vi.fn(async () => Response.json([])))
}

/** Check the default and the restoration path by remounting the workspace. */
it('starts with the explorer closed and remembers when it is opened', async () => {
  setDesktop(true)
  const user = userEvent.setup()
  const app = render(<App />)
  const toggle = screen.getByRole('button', { name: 'Проводник' })
  expect(toggle).toHaveAttribute('aria-expanded', 'false')
  expect(screen.queryByRole('region', { name: 'Панель проводника' })).not.toBeInTheDocument()

  await user.click(toggle)
  expect(toggle).toHaveAttribute('aria-expanded', 'true')
  expect(screen.getByRole('region', { name: 'Панель проводника' })).toBeVisible()
  app.unmount()

  render(<App />)
  expect(screen.getByRole('button', { name: 'Проводник' })).toHaveAttribute('aria-expanded', 'true')
  expect(screen.getByRole('region', { name: 'Панель проводника' })).toBeVisible()
})

it('opens the chat list on a narrow screen and returns focus to its trigger', async () => {
  setDesktop(false)
  const user = userEvent.setup()
  render(<App />)
  const trigger = screen.getByRole('button', { name: 'Чаты' })
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

  await user.click(trigger)
  const drawer = screen.getByRole('dialog', { name: 'Чаты' })
  expect(await within(drawer).findByText('Чатов пока нет.')).toBeVisible()
  await user.click(within(drawer).getByRole('button', { name: 'Закрыть чаты' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(trigger).toHaveFocus()
})
