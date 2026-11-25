/** Notify open views only after a chat mutation has been confirmed. */
const listeners = new Set<(chatId: string) => void>()
export function onChatChanged(listener: (chatId: string) => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}
export function notifyChatChanged(chatId: string): void {
  for (const listener of listeners) listener(chatId)
}
