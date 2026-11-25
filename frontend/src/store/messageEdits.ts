import { create } from 'zustand'

/** Retain only unfinished editor drafts when their message cards leave the bounded DOM. */
export const useMessageEdits = create<{
  drafts: Record<string, string>
  update: (id: string, content: string) => void
  finish: (id: string) => void
  reset: () => void
}>()((set) => ({
  drafts: {},
  update: (id, content) => set((state) => ({ drafts: { ...state.drafts, [id]: content } })),
  finish: (id) => set((state) => { const drafts = { ...state.drafts }; delete drafts[id]; return { drafts } }),
  reset: () => set({ drafts: {} }),
}))
