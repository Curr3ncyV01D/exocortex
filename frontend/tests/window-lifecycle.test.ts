import { afterEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '../src/api/client'
import { useWorkspace } from '../src/store/workspace'
import { chat, main, timeline } from './fixtures'
import { windowFixture } from './window-fixture'

const history = Array.from({ length: 100 }, (_, index) => ({ ...timeline[0], id: `life-${index}`, parent_id: index ? `life-${index - 1}` : null, role: 'user', content: `History ${index}` }))
afterEach(() => { useWorkspace.getState().reset(); vi.restoreAllMocks() })
function initialize() {
  useWorkspace.setState({ activeChatId: chat.id, branches: { status: 'ready', data: [{ ...main, status: 'active' }] } })
  vi.spyOn(api, 'getTimelineWindow').mockImplementation(async (_id, query = {}) => windowFixture(main, history, new URLSearchParams(Object.entries(query).map(([key, value]) => [key, String(value)]))))
}

it('does not move the reading window when an empty Run fails', async () => {
  initialize()
  await useWorkspace.getState().navigateToMessage(main.id, 'life-30')
  const before = useWorkspace.getState().scrollRequest
  const rows = useWorkspace.getState().timeline
  vi.spyOn(api, 'runTurn').mockRejectedValue(new ApiError(502, { detail: 'Unavailable' }))
  await useWorkspace.getState().runBranch(main.id, { headId: 'life-99', questionId: 'life-99' })
  expect(useWorkspace.getState().scrollRequest).toBe(before)
  expect(useWorkspace.getState().timeline).toBe(rows)
})

it('ignores a navigation response after a newer transition has been confirmed', async () => {
  initialize()
  let finish!: (data: ReturnType<typeof windowFixture>) => void
  vi.mocked(api.getTimelineWindow).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
  const old = useWorkspace.getState().navigateToMessage(main.id, 'life-30')
  await useWorkspace.getState().navigateToMessage(main.id, 'life-70')
  const confirmed = useWorkspace.getState().timeline
  finish(windowFixture(main, history, new URLSearchParams({ mode: 'around', anchor_id: 'life-30' })))
  await old
  expect(useWorkspace.getState().timeline).toBe(confirmed)
  expect(useWorkspace.getState().scrollRequest?.targetId).toBe('life-70')
})

it('blocks a stale Run target when state refresh after masking fails and keeps visible messages', async () => {
  initialize()
  await useWorkspace.getState().selectBranch(main.id)
  const rows = useWorkspace.getState().timeline
  vi.spyOn(api, 'setMessageActive').mockResolvedValue({ message_id: 'life-99', is_active: false })
  vi.mocked(api.getTimelineWindow).mockRejectedValueOnce(new Error('offline'))
  await useWorkspace.getState().toggleMessage({ ...history[99], role: 'user' })
  await vi.waitFor(() => expect(useWorkspace.getState().navigation?.status).toBe('error'))
  expect(useWorkspace.getState().timeline.status).toBe('ready')
  expect(useWorkspace.getState().branchStateReady).toBe(false)
  if (rows.status === 'ready' && useWorkspace.getState().timeline.status === 'ready') expect(rows.data).toHaveLength(40)
})
