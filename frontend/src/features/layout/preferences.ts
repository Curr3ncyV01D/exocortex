export interface LayoutPreferences {
  sidebarWidth: number
  explorerHeight: number
  explorerOpen: boolean
  chatsCollapsed?: boolean
}

const storageKey = 'exocortex.workspace-layout'
const defaults: LayoutPreferences = { sidebarWidth: 18, explorerHeight: 45, explorerOpen: false }

export function getLayoutPreferences(): LayoutPreferences {
  try {
    const saved = localStorage.getItem(storageKey)
    if (!saved) return defaults
    const { sidebarWidth, explorerHeight, explorerOpen, chatsCollapsed } = JSON.parse(saved) as Partial<LayoutPreferences>
    return {
      sidebarWidth: typeof sidebarWidth === 'number' && sidebarWidth > 0 && sidebarWidth <= 42
        ? sidebarWidth : defaults.sidebarWidth,
      explorerHeight: typeof explorerHeight === 'number' && explorerHeight >= 18 && explorerHeight < 100
        ? explorerHeight : defaults.explorerHeight,
      explorerOpen: explorerOpen === true,
      ...(chatsCollapsed === true ? { chatsCollapsed: true } : {}),
    }
  } catch {
    return defaults
  }
}

export function saveLayoutPreferences(preferences: LayoutPreferences): void {
  try {
    localStorage.setItem(storageKey, JSON.stringify(preferences))
  } catch {
    // Resizing still works for the current page when browser storage is unavailable.
  }
}
