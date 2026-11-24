import { lazy, Suspense, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { Group, Panel, Separator } from 'react-resizable-panels'
import { ChatSidebar } from '../chats/ChatSidebar'
import { Conversation } from '../conversation/Conversation'
import { getLayoutPreferences, saveLayoutPreferences } from './preferences'

const ExplorerPanel = lazy(() => import('../explorer/ExplorerPanel').then((module) => ({ default: module.ExplorerPanel })))

const desktopQuery = '(min-width: 48rem)'

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
  const [preferences, setPreferences] = useState(getLayoutPreferences)
  const { sidebarWidth, explorerHeight, explorerOpen } = preferences

  useEffect(() => {
    saveLayoutPreferences(preferences)
  }, [preferences])

  return (
    <div className="h-dvh overflow-hidden p-3">
      {desktop ? (
        <Group
          id="workspace-columns"
          defaultLayout={{ sidebar: sidebarWidth, conversation: 100 - sidebarWidth }}
          onLayoutChanged={(layout, { isUserInteraction }) => {
            if (isUserInteraction) {
              setPreferences((current) => ({ ...current, sidebarWidth: layout.sidebar }))
            }
          }}
        >
          <Panel id="sidebar" minSize="12rem" maxSize="42%">
            <Group
              id="workspace-sidebar"
              orientation="vertical"
              defaultLayout={explorerOpen ? { chats: 100 - explorerHeight, explorer: explorerHeight } : { chats: 100 }}
              onLayoutChanged={(layout, { isUserInteraction }) => {
                if (isUserInteraction && layout.explorer !== undefined) {
                  setPreferences((current) => ({ ...current, explorerHeight: layout.explorer }))
                }
              }}
            >
              <Panel id="chats"><ChatSidebar /></Panel>
              {explorerOpen && (
                <Separator aria-label="Высота проводника" className="workspace-separator h-3" />
              )}
              {explorerOpen && (
                <Panel id="explorer" minSize="18%" maxSize="50%">
                  <section id="explorer-region" aria-label="Панель проводника" className="workspace-island explorer-panel">
                    <h2 className="px-3 py-2 text-sm font-medium text-muted">Проводник</h2>
                    <Suspense fallback={<p role="status" className="p-3 text-sm text-muted">Загрузка проводника…</p>}>
                      <ExplorerPanel />
                    </Suspense>
                  </section>
                </Panel>
              )}
            </Group>
          </Panel>
          <Separator aria-label="Ширина левой колонки" className="workspace-separator w-3" />
          <Panel id="conversation" minSize="27rem">
            <Conversation navigation={
              <button
                type="button"
                className="button-secondary"
                aria-expanded={explorerOpen}
                aria-controls={explorerOpen ? 'explorer-region' : undefined}
                onClick={() => setPreferences((current) => ({ ...current, explorerOpen: !current.explorerOpen }))}
              >
                Проводник
              </button>
            } />
          </Panel>
        </Group>
      ) : <MobileWorkspace />}
    </div>
  )
}
