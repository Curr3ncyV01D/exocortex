import { expect, it } from 'vitest'
import { getLayoutPreferences, saveLayoutPreferences } from '../src/features/layout/preferences'

it('restores the saved sidebar width, explorer height, and visibility', () => {
  const resized = { sidebarWidth: 26, explorerHeight: 35, explorerOpen: true }
  saveLayoutPreferences(resized)
  expect(getLayoutPreferences()).toEqual(resized)
})
