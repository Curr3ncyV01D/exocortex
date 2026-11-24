import { expect, it, vi } from 'vitest'
import { createResizeQueue } from '../src/features/layout/resizeQueue'

/** Writes from a ResizeObserver must be deferred and coalesced to avoid geometry feedback. */
it('applies only the latest resize on the next frame and cancels pending work on unmount', () => {
  let frame: (() => void) | undefined
  const apply = vi.fn()
  const cancel = vi.fn()
  const schedule = vi.fn((callback: () => void) => { frame = callback; return 7 })
  const queue = createResizeQueue<number>(apply, schedule, cancel)
  queue.push(30)
  queue.push(0)
  expect(apply).not.toHaveBeenCalled()
  expect(schedule).toHaveBeenCalledTimes(1)
  frame!()
  expect(apply).toHaveBeenCalledExactlyOnceWith(0)
  queue.push(60)
  queue.dispose()
  expect(cancel).toHaveBeenCalledWith(7)
})
