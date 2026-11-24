import { expect, it } from 'vitest'
import { getLayoutPreferences, saveLayoutPreferences } from '../src/features/layout/preferences'
import { routeConnection, placeLabels } from '../src/features/explorer/routing'

/** Legacy preferences retain their meaning while expanded layouts can exceed half the column. */
it('restores expanded explorer sizes and collapsed chat state without losing the restore height', () => {
  const preferences = { sidebarWidth: 25, explorerHeight: 82, explorerOpen: true, chatsCollapsed: true }
  saveLayoutPreferences(preferences)
  expect(getLayoutPreferences()).toEqual(preferences)
})

it('routes long merge connections away from both history lanes', () => {
  const points = routeConnection({ x: 90, y: 20 }, { x: 0, y: 800 }, 'merge')
  expect(points[0]).toEqual({ x: 90, y: 20 })
  expect(points.at(-1)).toEqual({ x: 0, y: 800 })
  const vertical = points.slice(1).map((point, i) => [points[i], point]).filter(([a, b]) => a.x === b.x && Math.abs(a.y - b.y) > 100)
  expect(vertical.length).toBeGreaterThan(0)
  expect(vertical.every(([a]) => a.x !== 0 && a.x !== 90)).toBe(true)
})

it('moves a label to the free side rather than covering an adjacent node', () => {
  const labels = placeLabels([
    { id: 'main', x: 0, y: 0, names: ['main'] },
    { id: 'side', x: 90, y: 0, names: ['side'] },
  ])
  expect(labels.main.side).toBe('left')
  expect(labels.side.side).toBe('right')
})
