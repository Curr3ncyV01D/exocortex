import { describe, expect, it } from 'vitest'
import { layoutReducer, sidebarLayout, sidebarVisibility } from '../src/features/layout/sidebarState'
import type { LayoutPreferences } from '../src/features/layout/preferences'

const base: LayoutPreferences = { sidebarWidth: 18, explorerHeight: 45, explorerOpen: false }

/** Every combination has independent visibility; dimensions never imply an open island. */
describe('sidebar visibility', () => {
  it.each([false, true])('keeps chats independent when explorerOpen=%s', (explorerOpen) => {
    const state = { ...base, explorerOpen }
    const hidden = layoutReducer(state, { type: 'toggle', panel: 'chats' })
    expect(hidden.chatsCollapsed).toBe(true)
    expect(hidden.explorerOpen).toBe(explorerOpen)
    const restored = layoutReducer(hidden, { type: 'toggle', panel: 'chats' })
    expect(Boolean(restored.chatsCollapsed)).toBe(false)
    expect(restored.explorerOpen).toBe(explorerOpen)
  })
  it.each([false, true])('keeps explorer independent when chatsCollapsed=%s', (chatsCollapsed) => {
    const state = { ...base, chatsCollapsed }
    const opened = layoutReducer(state, { type: 'toggle', panel: 'explorer' })
    expect(opened.explorerOpen).toBe(true)
    expect(Boolean(opened.chatsCollapsed)).toBe(chatsCollapsed)
    expect(sidebarLayout(opened)).toEqual(chatsCollapsed ? { chats: 0, explorer: 100 } : { chats: 55, explorer: 45 })
    const closed = layoutReducer(opened, { type: 'toggle', panel: 'explorer' })
    expect(closed.explorerOpen).toBe(false)
    expect(Boolean(closed.chatsCollapsed)).toBe(chatsCollapsed)
  })
})

/** Endpoint collapse and button close preserve the last useful two-island proportion. */
it.each([
  { chats: 0, explorer: 100, collapsed: true, open: true },
  { chats: 100, explorer: 0, collapsed: false, open: false },
])('preserves the split at the endpoint $chats/$explorer', ({ chats, explorer, collapsed, open }) => {
  const state = { ...base, explorerOpen: true, explorerHeight: 62 }
  const resized = layoutReducer(state, { type: 'resize', layout: { chats, explorer }, source: sidebarVisibility(state) })
  expect(Boolean(resized.chatsCollapsed)).toBe(collapsed)
  expect(resized.explorerOpen).toBe(open)
  expect(resized.explorerHeight).toBe(62)
  const restored = layoutReducer(resized, { type: 'toggle', panel: collapsed ? 'chats' : 'explorer' })
  expect(sidebarLayout(restored)).toEqual({ chats: 38, explorer: 62 })
})

it('restores a collapsed island by dragging back and remembers only a shared split', () => {
  const state = { ...base, chatsCollapsed: true, explorerOpen: true }
  const resized = layoutReducer(state, { type: 'resize', layout: { chats: 40, explorer: 60 }, source: sidebarVisibility(state) })
  expect(Boolean(resized.chatsCollapsed)).toBe(false)
  expect(resized.explorerOpen).toBe(true)
  expect(resized.explorerHeight).toBe(60)
})

it('ignores an obsolete geometry event after a visibility command', () => {
  const state = { ...base, chatsCollapsed: true, explorerOpen: true }
  const closed = layoutReducer(state, { type: 'toggle', panel: 'explorer' })
  const late = layoutReducer(closed, { type: 'resize', layout: { chats: 55, explorer: 45 }, source: sidebarVisibility(state) })
  expect(late).toBe(closed)
  expect(sidebarLayout(closed)).toEqual({ chats: 0, explorer: 0 })
})
