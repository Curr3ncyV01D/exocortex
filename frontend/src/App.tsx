import { useEffect } from 'react'
import { WorkspaceLayout } from './features/layout/WorkspaceLayout'
import { useWorkspace } from './store/workspace'

function App() {
  const loadChats = useWorkspace((state) => state.loadChats)
  const reset = useWorkspace((state) => state.reset)

  useEffect(() => {
    void loadChats()
    return reset
  }, [loadChats, reset])

  return <WorkspaceLayout />
}

export default App
