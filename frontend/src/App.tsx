import { useEffect } from 'react'
import { ChatSidebar } from './features/chats/ChatSidebar'
import { Conversation } from './features/conversation/Conversation'
import { useWorkspace } from './store/workspace'

function App() {
  const loadChats = useWorkspace((state) => state.loadChats)
  const reset = useWorkspace((state) => state.reset)

  useEffect(() => {
    void loadChats()
    return reset
  }, [loadChats, reset])

  return (
    <div className="min-h-dvh md:grid md:h-dvh md:grid-cols-[17rem_minmax(0,1fr)] md:overflow-hidden">
      <ChatSidebar />
      <Conversation />
    </div>
  )
}

export default App
