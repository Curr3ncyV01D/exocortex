const draftKey = (branchId: string) => `exocortex.draft.${branchId}`
const draftCleared = 'exocortex:draft-cleared'
const submissionKey = (branchId: string) => `exocortex.submission.${branchId}`

interface PendingSubmission {
  key: string
  content: string
}

const inMemorySubmissions = new Map<string, PendingSubmission>()

function readSubmission(branchId: string): PendingSubmission | null | undefined {
  const fallback = inMemorySubmissions.get(branchId)
  if (fallback) return fallback
  try {
    return JSON.parse(localStorage.getItem(submissionKey(branchId)) ?? 'null') as PendingSubmission | null
  } catch {
    return undefined
  }
}

export function startSubmission(branchId: string, content: string): string {
  const previous = readSubmission(branchId)
  if (previous?.content === content && typeof previous.key === 'string') return previous.key

  const submission = { content, key: crypto.randomUUID() }
  try {
    localStorage.setItem(submissionKey(branchId), JSON.stringify(submission))
    inMemorySubmissions.delete(branchId)
  } catch {
    inMemorySubmissions.set(branchId, submission)
  }
  return submission.key
}

export function finishSubmission(branchId: string, key: string): void {
  if (readSubmission(branchId)?.key !== key) return
  inMemorySubmissions.delete(branchId)
  try {
    localStorage.removeItem(submissionKey(branchId))
  } catch {
    // The in-memory fallback is already cleared.
  }
}

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
