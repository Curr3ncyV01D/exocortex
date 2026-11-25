import { expect, it, vi } from 'vitest'
import { api } from '../src/api/client'

/** Keep cursor encoding and cancellation explicit at the HTTP boundary. */
it('requests an additive window endpoint without changing the legacy route', async () => {
  const fetch = vi.fn().mockResolvedValue(Response.json({ messages: [] }))
  vi.stubGlobal('fetch', fetch)
  const signal = new AbortController().signal
  await api.getTimelineWindow('branch/id', { mode: 'around', anchor_id: 'message/id', radius: 20 }, signal)
  expect(fetch).toHaveBeenCalledWith('/api/v1/branches/branch%2Fid/timeline/window?mode=around&anchor_id=message%2Fid&radius=20', { signal })
})
