import { afterEach, expect, it, vi } from 'vitest'
import { mergeTimelinePage } from '../src/features/conversation/timelineWindow'
import { useWorkspace } from '../src/store/workspace'
import { api } from '../src/api/client'
import { chat, main, side, timeline } from './fixtures'
import { windowFixture } from './window-fixture'

const history = Array.from({ length: 240 }, (_, index) => ({ ...timeline[0], id: `m${index}`, parent_id: index ? `m${index - 1}` : null, role: 'user', content: `Message ${index}` }))
afterEach(() => { useWorkspace.getState().reset(); vi.restoreAllMocks() })

/** Eviction makes the opposite edge pageable again without duplicate cards or unbounded text. */
it('merges cursor pages in both directions and caps the complete message window at 160', () => {
  let state = windowFixture(main, history)
  for (let i = 0; i < 5; i++) {
    const page = windowFixture(main, history, new URLSearchParams({ mode: 'older', anchor_id: state.older_cursor!, limit: '40' }))
    state = mergeTimelinePage(state, page, 'older')
    expect(state.messages.length).toBeLessThanOrEqual(160)
    expect(new Set(state.messages.map(({ id }) => id)).size).toBe(state.messages.length)
  }
  expect(state.messages[0].id).toBe('m0')
  expect(state.messages.at(-1)!.id).toBe('m159')
  expect(state.has_newer).toBe(true)
  const next = windowFixture(main, history, new URLSearchParams({ mode: 'newer', anchor_id: state.newer_cursor!, limit: '40' }))
  state = mergeTimelinePage(state, next, 'newer')
  expect(state.messages[0].id).toBe('m40')
  expect(state.messages.at(-1)!.id).toBe('m199')
  expect(state.older_cursor).toBe('m40')
})

it('starts with a bounded server window and keeps the real pending question outside the displayed slice', async () => {
  const page = windowFixture(main, history, new URLSearchParams({ mode: 'around', anchor_id: 'm40', radius: '20' }))
  vi.spyOn(api, 'getTimelineWindow').mockResolvedValue(page)
  const legacy = vi.spyOn(api, 'getTimeline')
  useWorkspace.setState({ activeChatId: chat.id, branches: { status: 'ready', data: [{ ...main, status: 'active' }] } })
  await useWorkspace.getState().navigateToMessage(main.id, 'm40')
  const state = useWorkspace.getState()
  expect(state.timeline.status).toBe('ready')
  if (state.timeline.status === 'ready') expect(state.timeline.data).toHaveLength(41)
  expect(state.windowState?.branch.head_message_id).toBe('m239')
  expect(state.windowState?.run_target?.expected_question_id).toBe('m239')
  legacy.mockRestore()
})

it('keeps the confirmed branch and visible messages when a graph transition fails', async () => {
  vi.spyOn(api, 'getTimelineWindow').mockResolvedValueOnce(windowFixture(main, history)).mockRejectedValueOnce(new Error('offline'))
  useWorkspace.setState({ activeChatId: chat.id, branches: { status: 'ready', data: [{ ...main, status: 'active' }, { ...side, status: 'active' }] } })
  await useWorkspace.getState().selectBranch(main.id)
  const before = useWorkspace.getState().timeline
  await useWorkspace.getState().navigateToMessage(side.id, 'other-message')
  expect(useWorkspace.getState().activeBranchId).toBe(main.id)
  expect(useWorkspace.getState().timeline).toBe(before)
  expect(useWorkspace.getState().navigation?.status).toBe('error')
})

it('coalesces an in-flight page and retains the current window on a paging failure', async () => {
  let reject!: (reason: Error) => void
  vi.spyOn(api, 'getTimelineWindow').mockResolvedValueOnce(windowFixture(main, history)).mockImplementation(() => new Promise((_resolve, fail) => { reject = fail }))
  useWorkspace.setState({ activeChatId: chat.id, branches: { status: 'ready', data: [{ ...main, status: 'active' }] } })
  await useWorkspace.getState().selectBranch(main.id)
  const before = useWorkspace.getState().timeline
  const first = useWorkspace.getState().loadTimelinePage('older')
  await useWorkspace.getState().loadTimelinePage('older')
  expect(api.getTimelineWindow).toHaveBeenCalledTimes(2)
  reject(new Error('offline'))
  await first
  expect(useWorkspace.getState().timeline).toBe(before)
  expect(useWorkspace.getState().pageError?.direction).toBe('older')
})
