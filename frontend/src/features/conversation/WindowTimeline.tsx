import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { ComponentProps, RefObject } from 'react'
import { useWorkspace } from '../../store/workspace'
import { MessageTimeline } from './MessageTimeline'

interface ScrollSnapshot { height: number; top: number; firstId: string | undefined; anchor: { id: string; offset: number } | null }

/** Anchor visible content while server pages are inserted or evicted from either edge. */
export function WindowTimeline({ scrollArea, ...props }: ComponentProps<typeof MessageTimeline> & { scrollArea: RefObject<HTMLDivElement | null> }) {
  const request = useWorkspace((state) => state.scrollRequest)
  const previous = useRef<ScrollSnapshot | null>(null)
  const handled = useRef<number | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const [highlightedId, setHighlighted] = useState<string | null>(null)
  useEffect(() => () => clearTimeout(timer.current), [])
  useLayoutEffect(() => {
    const area = scrollArea.current
    if (!area) return
    const rows = () => [...area.querySelectorAll<HTMLElement>('[data-message-id]')]
    const capture = (): ScrollSnapshot => {
      const bounds = area.getBoundingClientRect()
      const row = rows().find((node) => node.getBoundingClientRect().bottom > bounds.top)
      return { height: area.scrollHeight, top: area.scrollTop, firstId: props.messages[0]?.id,
        anchor: row ? { id: row.dataset.messageId!, offset: row.getBoundingClientRect().top - bounds.top } : null }
    }
    if (request && handled.current !== request.serial) {
      handled.current = request.serial
      clearTimeout(timer.current)
      if (request.mode === 'tail') { area.scrollTop = area.scrollHeight; setHighlighted(null) }
      else {
        const target = rows().find((row) => row.dataset.messageId === request.targetId)
        if (target) {
          const top = Math.max(0, area.scrollTop + target.getBoundingClientRect().top - area.getBoundingClientRect().top - (area.clientHeight - target.offsetHeight) / 2)
          const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
          if (area.scrollTo) area.scrollTo({ top, behavior: reduced ? 'auto' : 'smooth' })
          else area.scrollTop = top
          setHighlighted(request.targetId!)
          timer.current = setTimeout(() => setHighlighted(null), 3000)
        }
      }
    } else if (previous.current) {
      const before = previous.current
      const anchor = before.anchor && rows().find((row) => row.dataset.messageId === before.anchor!.id)
      if (anchor) area.scrollTop += anchor.getBoundingClientRect().top - area.getBoundingClientRect().top - before.anchor!.offset
      else if (before.firstId && props.messages.findIndex(({ id }) => id === before.firstId) > 0) {
        area.scrollTop = before.top + area.scrollHeight - before.height
      }
    }
    previous.current = capture()
    let lastTop = area.scrollTop
    const onScroll = () => {
      const top = Math.max(0, area.scrollTop), down = top > lastTop, up = top < lastTop
      lastTop = top
      previous.current = capture()
      const state = useWorkspace.getState()
      const bottom = area.scrollHeight - area.clientHeight - top
      state.setReadingTail(!state.windowState?.has_newer && bottom <= 32)
      if (up && top <= 250) void state.loadTimelinePage('older')
      else if (down && bottom <= 250) void state.loadTimelinePage('newer')
    }
    area.addEventListener('scroll', onScroll, { passive: true })
    return () => area.removeEventListener('scroll', onScroll)
  }, [props.messages, request, scrollArea])
  return <MessageTimeline {...props} highlightedId={highlightedId ?? undefined} />
}
