import type { TimelineWindowResponse } from '../../api/types'

export const WINDOW_PAGE_SIZE = 40
export const WINDOW_CARD_LIMIT = 160
export type PageDirection = 'older' | 'newer'

/** Merge adjacent pages while preserving the opposite boundary and bounding full payloads. */
export function mergeTimelinePage(
  current: TimelineWindowResponse, page: TimelineWindowResponse, direction: PageDirection,
): TimelineWindowResponse {
  const ordered = direction === 'older' ? [...page.messages, ...current.messages] : [...current.messages, ...page.messages]
  const latest = new Map([...current.messages, ...page.messages].map((message) => [message.id, message]))
  const seen = new Set<string>()
  let messages = ordered.filter(({ id }) => !seen.has(id) && Boolean(seen.add(id))).map(({ id }) => latest.get(id)!)
  const overflow = messages.length > WINDOW_CARD_LIMIT
  if (overflow) messages = direction === 'older' ? messages.slice(0, WINDOW_CARD_LIMIT) : messages.slice(-WINDOW_CARD_LIMIT)
  const older = direction === 'older' ? page.has_older : current.has_older || overflow
  const newer = direction === 'newer' ? page.has_newer : current.has_newer || overflow
    || (messages.length > 0 && messages.at(-1)!.id !== page.branch.head_message_id)
  return { ...page, messages, has_older: older, has_newer: newer,
    older_cursor: older ? messages[0]?.id ?? null : null,
    newer_cursor: newer ? messages.at(-1)?.id ?? null : null }
}
