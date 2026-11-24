import type { LayoutPreferences } from './preferences'

type Island = 'chats' | 'explorer'
type SidebarLayout = { chats: number; explorer: number }
export type LayoutAction =
  | { type: 'toggle'; panel: Island }
  | { type: 'resize'; layout: SidebarLayout; source: string }
  | { type: 'width'; value: number }

/** Visibility identifies which topology produced a deferred user resize. */
export function sidebarVisibility(state: LayoutPreferences): string {
  return `${!state.chatsCollapsed}:${state.explorerOpen}`
}

/** Keep visibility independent from the remembered proportion of two open islands. */
export function sidebarLayout(state: LayoutPreferences): SidebarLayout {
  const chats = !state.chatsCollapsed
  if (chats && state.explorerOpen) return { chats: 100 - state.explorerHeight, explorer: state.explorerHeight }
  return { chats: chats ? 100 : 0, explorer: state.explorerOpen ? 100 : 0 }
}

function setOpen(state: LayoutPreferences, panel: Island, open: boolean): LayoutPreferences {
  if (panel === 'explorer') return state.explorerOpen === open ? state : { ...state, explorerOpen: open }
  return Boolean(state.chatsCollapsed) === !open ? state : { ...state, chatsCollapsed: open ? undefined : true }
}

/** Buttons and divider endpoints share the same visibility transitions. */
export function layoutReducer(state: LayoutPreferences, action: LayoutAction): LayoutPreferences {
  if (action.type === 'width') return state.sidebarWidth === action.value ? state : { ...state, sidebarWidth: action.value }
  if (action.type === 'toggle') {
    return setOpen(state, action.panel, action.panel === 'chats' ? Boolean(state.chatsCollapsed) : !state.explorerOpen)
  }
  if (action.source !== sidebarVisibility(state)) return state
  const { chats, explorer } = action.layout
  if (![chats, explorer].every((size) => Number.isFinite(size) && size >= 0) || chats + explorer <= 0) return state
  const chatsOpen = chats > 0.001
  const explorerOpen = explorer > 0.001
  let next = setOpen(setOpen(state, 'chats', chatsOpen), 'explorer', explorerOpen)
  if (chatsOpen && explorerOpen) {
    const height = 100 * explorer / (chats + explorer)
    if (Math.abs(state.explorerHeight - height) > 0.001) next = { ...next, explorerHeight: height }
  }
  return next
}
