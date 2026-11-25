import { afterEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '../src/api/client'
import { useWorkspace } from '../src/store/workspace'
import { onChatChanged } from '../src/store/chatEvents'
import { chat, main, side, timeline } from './fixtures'
import { windowFixture } from './window-fixture'

afterEach(() => { useWorkspace.getState().reset(); vi.restoreAllMocks() })

/** A freshly refreshed graph may know a branch that the dropdown has not loaded yet. */
it('adds a successfully opened graph branch to the selector without changing its status', async () => {
  const branch = { ...side, id: 'new-graph-branch', status: 'archived' as const }
  const page = windowFixture(branch, timeline)
  vi.spyOn(api, 'getTimelineWindow').mockResolvedValue(page)
  useWorkspace.setState({ activeChatId: chat.id, branches: { status: 'ready', data: [{ ...main, status: 'active' }] } })
  await useWorkspace.getState().navigateToMessage(branch.id, 'question')
  const branches = useWorkspace.getState().branches
  expect(branches.status).toBe('ready')
  if (branches.status === 'ready') expect(branches.data.find(({ id }) => id === branch.id)?.status).toBe('archived')
  expect(useWorkspace.getState().activeBranchId).toBe(branch.id)
})

it('notifies the graph when regeneration saved its fork before the provider failed', async () => {
  const changed = vi.fn()
  const unsubscribe = onChatChanged(changed)
  vi.spyOn(api, 'regenerate').mockRejectedValue(new ApiError(502, {
    detail: 'Provider unavailable', branch_id: 'saved-fork', head_message_id: 'question',
  }))
  try {
    await useWorkspace.getState().regenerateBranch({ id: main.id, chat_id: chat.id }, 'merge', 'Saved fork')
    expect(changed).toHaveBeenCalledExactlyOnceWith(chat.id)
  } finally { unsubscribe() }
})
