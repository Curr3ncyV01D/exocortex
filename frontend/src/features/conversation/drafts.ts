const draftKey = (branchId: string) => `exocortex.draft.${branchId}`
const draftCleared = 'exocortex:draft-cleared'

interface SentDraft {
  branchId: string
  text: string
}

export function readDraft(branchId: string): string {
  try {
    return localStorage.getItem(draftKey(branchId)) ?? ''
  } catch {
    return ''
  }
}

export function writeDraft(branchId: string, text: string): void {
  try {
    if (text) localStorage.setItem(draftKey(branchId), text)
    else localStorage.removeItem(draftKey(branchId))
  } catch {
    // The current editor remains usable when storage is unavailable.
  }
}

export function clearSentDraft(branchId: string, text: string): void {
  if (readDraft(branchId) === text) writeDraft(branchId, '')
  // The same branch may have remounted while its save request was pending.
  window.dispatchEvent(new CustomEvent<SentDraft>(draftCleared, { detail: { branchId, text } }))
}

export function onDraftCleared(branchId: string, clear: (sentText: string) => void): () => void {
  function listener(event: Event) {
    const { detail } = event as CustomEvent<SentDraft>
    if (detail.branchId === branchId) clear(detail.text)
  }
  window.addEventListener(draftCleared, listener)
  return () => window.removeEventListener(draftCleared, listener)
}
