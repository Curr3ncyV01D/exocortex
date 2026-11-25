import { lazy, Suspense, useEffect, useMemo, useReducer, useRef, useState, useSyncExternalStore } from 'react'
import { Group, Panel, Separator, type GroupImperativeHandle } from 'react-resizable-panels'
import { ChatSidebar } from '../chats/ChatSidebar'
import { NewChatButton } from '../chats/NewChatButton'
import { Conversation } from '../conversation/Conversation'
import { getLayoutPreferences, saveLayoutPreferences } from './preferences'
import { createResizeQueue } from './resizeQueue'
import { layoutReducer, sidebarLayout, sidebarVisibility, type LayoutAction } from './sidebarState'

const desktopQuery = '(min-width: 48rem)'
const ExplorerPanel = lazy(() => import('../explorer/ExplorerPanel').then((module) => ({ default: module.ExplorerPanel })))

function subscribeToViewport(onChange: () => void) {
  const media = window.matchMedia?.(desktopQuery)
  media?.addEventListener('change', onChange)
  return () => media?.removeEventListener('change', onChange)
}

function isDesktop() {
  return window.matchMedia?.(desktopQuery).matches ?? true
}

function MobileWorkspace() {
  const drawer = useRef<HTMLDialogElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const [open, setOpen] = useState(false)

  function closeChats() {
    if (!drawer.current?.open) return
    drawer.current?.close()
    setOpen(false)
    trigger.current?.focus()
  }

  return (
    <>
      <Conversation navigation={
        <button
          ref={trigger}
          type="button"
          className="button-secondary"
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-controls="chat-drawer"
          onClick={() => {
            drawer.current?.showModal()
            setOpen(true)
          }}
        >
          Чаты
        </button>
      } />
      <dialog
        ref={drawer}
        id="chat-drawer"
        aria-label="Чаты"
        className="workspace-island fixed top-3 right-auto bottom-auto left-3 m-0 h-[calc(100dvh-1.5rem)] max-h-none w-[min(22rem,calc(100vw-1.5rem))] max-w-none text-foreground backdrop:bg-black/65"
        onCancel={(event) => {
          event.preventDefault()
          closeChats()
        }}
      >
        <ChatSidebar onClose={closeChats} onChatOpened={closeChats} />
      </dialog>
    </>
  )
}

export function WorkspaceLayout() {
  const desktop = useSyncExternalStore(subscribeToViewport, isDesktop)
  const [preferences, dispatch] = useReducer(layoutReducer, undefined, getLayoutPreferences)
  const { sidebarWidth, explorerOpen, chatsCollapsed = false } = preferences
  const sidebar = useRef<GroupImperativeHandle>(null)
  const resizeQueue = useMemo(() => createResizeQueue<LayoutAction>(dispatch), [])
  useEffect(() => () => resizeQueue.dispose(), [resizeQueue])
  // Re-project visibility commands only; ordinary user resizing owns the live geometry.
  const initialSidebarLayout = useMemo(() => sidebarLayout(preferences), [chatsCollapsed, explorerOpen])
  useEffect(() => {
    if (desktop && (!chatsCollapsed || explorerOpen)) sidebar.current?.setLayout(initialSidebarLayout)
  }, [desktop, initialSidebarLayout, chatsCollapsed, explorerOpen])
  const columnLayout = useMemo(() => ({ sidebar: sidebarWidth, conversation: 100 - sidebarWidth }), [sidebarWidth])
  function togglePanel(panel: 'chats' | 'explorer') {
    resizeQueue.dispose()
    dispatch({ type: 'toggle', panel })
  }
  function toggleChats() { togglePanel('chats') }
  function toggleExplorer() { togglePanel('explorer') }
  function sectionToggle(name: string, open: boolean, action: () => void, controls: string) {
    return <button type="button" className="island-toggle" aria-expanded={open} aria-controls={controls} onClick={action}>
      <span aria-hidden="true">{open ? '⌄' : '›'}</span> {name}
    </button>
  }

  useEffect(() => {
    saveLayoutPreferences(preferences)
  }, [preferences])

  return (
    <div className="h-dvh overflow-hidden p-3">
      {desktop ? (
        <Group
          id="workspace-columns"
          defaultLayout={columnLayout}
          onLayoutChanged={(layout, { isUserInteraction }) => {
            if (isUserInteraction) {
              dispatch({ type: 'width', value: layout.sidebar })
            }
          }}
        >
          <Panel id="sidebar" minSize="12rem" maxSize="42%">
            <div className="sidebar-shell">
              <header className="workspace-island sidebar-navigation">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="font-semibold">ExoCortex</p><NewChatButton />
                </div>
                <div className="mt-2 flex min-h-7 flex-wrap gap-2">
                  {chatsCollapsed && sectionToggle('Чаты', false, toggleChats, 'chat-list')}
                  {!explorerOpen && sectionToggle('Проводник', false, toggleExplorer, 'explorer-region')}
                </div>
              </header>
              {(!chatsCollapsed || explorerOpen) && <Group id="workspace-sidebar" orientation="vertical"
                groupRef={sidebar} defaultLayout={initialSidebarLayout}
                onLayoutChanged={(layout, { isUserInteraction }) => {
                  if (isUserInteraction) resizeQueue.push({
                    type: 'resize', layout: { chats: layout.chats, explorer: layout.explorer },
                    source: sidebarVisibility(preferences),
                  })
                }}>
                <Panel id="chats" collapsible collapsedSize="0%" minSize="8rem">
                  {!chatsCollapsed && <div className="workspace-island h-full overflow-hidden flex flex-col">
                    <header className="px-3 py-2">{sectionToggle('Чаты', true, toggleChats, 'chat-list')}</header>
                    <div id="chat-list" className="min-h-0 flex-1"><ChatSidebar listOnly /></div>
                  </div>}
                </Panel>
                <Separator aria-label="Высота проводника" className="workspace-separator h-3" />
                <Panel id="explorer" collapsible collapsedSize="0%" minSize="18%">
                  {explorerOpen && <section id="explorer-region" aria-label="Панель проводника" className="workspace-island explorer-panel">
                    <header className="px-3 py-2">{sectionToggle('Проводник', true, toggleExplorer, 'explorer-region')}</header>
                    <Suspense fallback={<p role="status" className="p-3 text-sm text-muted">Загрузка проводника…</p>}>
                      <ExplorerPanel />
                    </Suspense>
                  </section>}
                </Panel>
              </Group>}
            </div>
          </Panel>
          <Separator aria-label="Ширина левой колонки" className="workspace-separator w-3" />
          <Panel id="conversation" minSize="27rem">
            <Conversation navigation={null} />
          </Panel>
        </Group>
      ) : <MobileWorkspace />}
    </div>
  )
}
