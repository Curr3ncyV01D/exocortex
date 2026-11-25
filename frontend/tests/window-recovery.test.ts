import { afterEach, expect, it, vi } from 'vitest'
import { api } from '../src/api/client'
import { useWorkspace } from '../src/store/workspace'
import { onChatChanged } from '../src/store/chatEvents'
import { chat, main, timeline } from './fixtures'
import { windowFixture } from './window-fixture'
import type { MessageResponse } from '../src/api/types'

afterEach(() => { useWorkspace.getState().reset(); vi.restoreAllMocks() })

/** A committed answer recovered after a lost HTTP response is also a confirmed graph change. */
it('notifies the graph when a state refresh recovers an answer after the run response was lost', async () => {
  const rows: MessageResponse[] = [{ ...timeline[0], role: 'user' }]
  vi.spyOn(api, 'getTimelineWindow').mockImplementation(async (_id, query = {}) => windowFixture(main, rows, new URLSearchParams(Object.entries(query).map(([key, value]) => [key, String(value)]))))
  useWorkspace.setState({ activeChatId: chat.id, branches: { status: 'ready', data: [{ ...main, status: 'active' }] } })
  await useWorkspace.getState().selectBranch(main.id)
  const changed = vi.fn(), unsubscribe = onChatChanged(changed)
  vi.spyOn(api, 'runTurn').mockImplementation(async () => {
    rows.push({ ...rows[0], id: 'recovered', parent_id: rows[0].id, role: 'model', content: 'Recovered answer' })
    throw new TypeError('Response lost after commit')
  })
  try {
    await useWorkspace.getState().runBranch(main.id, { headId: rows[0].id, questionId: rows[0].id })
    await vi.waitFor(() => expect(changed).toHaveBeenCalledExactlyOnceWith(chat.id))
    const visible = useWorkspace.getState().timeline
    expect(visible.status).toBe('ready')
    if (visible.status === 'ready') expect(visible.data.at(-1)?.id).toBe('recovered')
  } finally { unsubscribe() }
})
