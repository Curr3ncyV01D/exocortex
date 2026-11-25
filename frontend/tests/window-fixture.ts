import type { BranchResponse, MessageResponse, TimelineWindowResponse } from '../src/api/types.js'

/** Serve the public window shape from deterministic test histories. */
export function windowFixture(
  branch: Omit<BranchResponse, 'status'> & { status: string },
  history: readonly (Omit<MessageResponse, 'role'> & { role: string })[],
  search: URLSearchParams = new URLSearchParams(),
): TimelineWindowResponse {
  const mode = search.get('mode') ?? 'tail'
  const limit = Number(search.get('limit') ?? 40)
  const radius = Number(search.get('radius') ?? 20)
  const anchor = history.findIndex(({ id }) => id === search.get('anchor_id'))
  let start = Math.max(0, history.length - limit), end = history.length
  if (mode === 'around') { start = Math.max(0, anchor - radius); end = Math.min(history.length, anchor + radius + 1) }
  if (mode === 'older') { start = Math.max(0, anchor - limit); end = anchor }
  if (mode === 'newer') { start = anchor + 1; end = Math.min(history.length, anchor + limit + 1) }
  const messages = history.slice(start, end) as MessageResponse[]
  const head = history.at(-1)?.id ?? null
  const dialogue = history.findLast((row) => row.is_active && row.role !== 'system_merge')
  const older = messages.length > 0 && start > 0, newer = messages.length > 0 && end < history.length
  return {
    branch: { ...branch, status: branch.status as BranchResponse['status'], head_message_id: head }, messages,
    has_older: older, has_newer: newer, older_cursor: older ? messages[0].id : null,
    newer_cursor: newer ? messages.at(-1)!.id : null,
    last_active_dialogue: dialogue ? { id: dialogue.id, role: dialogue.role as 'user' | 'model' } : null,
    run_target: head && dialogue?.role === 'user' ? { expected_head_id: head, expected_question_id: dialogue.id } : null,
  }
}
