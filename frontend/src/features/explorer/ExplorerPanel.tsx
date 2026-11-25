import { createContext, memo, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { useStore } from 'zustand'
import { Handle, Position, ReactFlow, BaseEdge, useViewport, useKeyPress, type EdgeProps, type NodeProps, type ReactFlowInstance } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useWorkspace } from '../../store/workspace'
import type { BranchResponse, ChatGraphResponse } from '../../api/types'
import { buildGraph, type ExplorerNode } from './graph'
import { explorerStore } from './state'
import { editedTitle } from '../conversation/messageTime'
import { roundedPath, routeConnection } from './routing'
import { resolveNavigationBranches } from './navigation'
import { onChatChanged } from '../../store/chatEvents'
import { MessageDialog } from '../conversation/MessageDialog'

const roles = { user: 'Вы', model: 'Модель', system_merge: 'Слияние' }
const navigateContext = createContext<(id: string) => void>(() => {})
const inspectContext = createContext<(id: string | null) => void>(() => {})

const MessagePoint = memo(function MessagePoint({ id, data, selected }: NodeProps<ExplorerNode>) {
  const inspect = useContext(inspectContext)
  const navigate = useContext(navigateContext)
  return <>
    <Handle type="target" position={Position.Top} className="explorer-handle" style={{ top: 7, left: 12, transform: 'translate(-50%, -50%)' }} />
    <button type="button" className="explorer-point nodrag nopan" style={{ color: data.color }}
      aria-label={`${roles[data.shape]}: ${data.message.preview || 'Пустое сообщение'}`}
      aria-pressed={selected} data-role={data.shape} data-masked={!data.message.is_active}
      data-active={data.active} data-selected={selected}
      onKeyDown={(event) => { if (event.key === 'Enter' && !event.repeat) { event.preventDefault(); event.stopPropagation(); explorerStore.getState().select(id); navigate(id) } }}
      onFocus={() => inspect(id)} onBlur={() => inspect(null)}>
      <span className="explorer-point-shape" />
    </button>
    {data.labels.length > 0 && data.label && <BranchLabel names={data.labels} position={data.label} />}
    <Handle type="source" position={Position.Bottom} className="explorer-handle" style={{ top: 17, bottom: 'auto', left: 12, transform: 'translate(-50%, -50%)' }} />
  </>
})

function BranchLabel({ names, position }: { names: string[]; position: { x: number; y: number } }) {
  const { zoom } = useViewport()
  if (zoom < 0.4) return null
  return <span className="explorer-branch-label" style={{ left: position.x, top: position.y }} title={names.join(', ')}>{names.join(', ')}</span>
}

const nodeTypes = { message: MessagePoint }
const RoutedEdge = memo(function RoutedEdge({ id, sourceX, sourceY, targetX, targetY, data, style }: EdgeProps) {
  const points = routeConnection({ x: sourceX, y: sourceY }, { x: targetX, y: targetY }, data?.kind === 'merge' ? 'merge' : 'history')
  return <BaseEdge id={id} path={roundedPath(points)} style={style} />
})
const edgeTypes = { routed: RoutedEdge }

function GraphCanvas({ graph, activeBranchId, onNavigate }: { graph: ChatGraphResponse; activeBranchId: string | null; onNavigate: (id: string) => void }) {
  const canvas = useRef<HTMLDivElement>(null)
  const spacePressed = useKeyPress('Space')
  const selectedId = useStore(explorerStore, (state) => state.selectedId)
  const [hovered, setHovered] = useState<string | null>(null)
  const [flow, setFlow] = useState<ReactFlowInstance<ExplorerNode> | null>(null)
  const model = useMemo(() => buildGraph(graph, activeBranchId), [graph, activeBranchId])
  const minimumZoom = Math.min(0.15, 1 / (graph.nodes.length + 1))
  const nodes = useMemo(() => model.nodes.map((node) => ({ ...node, selected: node.id === selectedId })), [model, selectedId])
  const inspected = graph.nodes.find((node) => node.id === (hovered ?? selectedId))
  const inspectedNode = model.nodes.find((node) => node.id === inspected?.id)
  const screenPoint = inspectedNode && flow?.flowToScreenPosition({ x: inspectedNode.position.x + 12, y: inspectedNode.position.y + 12 })
  const bounds = canvas.current?.getBoundingClientRect()
  const previewAbove = Boolean(screenPoint && bounds && screenPoint.y - bounds.top >= bounds.height / 2)
  const merge = graph.merges.find((item) => item.message_id === inspected?.id)

  function clearSelection() {
    explorerStore.getState().select(null)
    setHovered(null)
  }

  return <div ref={canvas} className="explorer-canvas" role="group" aria-label="Миникарта истории" data-space={spacePressed}>
    <navigateContext.Provider value={onNavigate}><inspectContext.Provider value={setHovered}>
      <ReactFlow<ExplorerNode> nodes={nodes} edges={model.edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
        nodesDraggable={false} nodesConnectable={false} nodesFocusable={false} edgesFocusable={false}
        elementsSelectable={false} selectionOnDrag={false} deleteKeyCode={null}
        panOnDrag={[1]} panActivationKeyCode="Space" zoomOnScroll zoomOnDoubleClick={false}
        minZoom={minimumZoom} maxZoom={3} forceColorMode="dark"
        onNodeDoubleClick={(_event, node) => { explorerStore.getState().select(node.id); onNavigate(node.id) }}
        onNodeClick={(_event, node) => explorerStore.getState().select(node.id)}
        onNodeMouseEnter={(_event, node) => setHovered(node.id)} onNodeMouseLeave={() => setHovered(null)}
        onPaneClick={clearSelection}
        onMoveEnd={(_event, viewport) => explorerStore.getState().saveViewport(graph.chat_id, viewport)}
        onInit={(instance) => {
          setFlow(instance)
          const saved = explorerStore.getState().viewports[graph.chat_id]
          if (saved) { void instance.setViewport(saved); return }
          const branch = graph.branches.find((item) => item.id === activeBranchId) ?? graph.branches.find((item) => item.name === 'main')
          const head = nodes.find((node) => node.id === branch?.head_message_id) ?? nodes[0]
          if (head) void instance.setCenter(head.position.x + 12, head.position.y + 12, { zoom: 1.2 })
        }}
      />
    </inspectContext.Provider></navigateContext.Provider>
    <div className="explorer-camera-controls">
      <button type="button" aria-label="Увеличить граф" title="Увеличить" onClick={() => void flow?.zoomIn()}>+</button>
      <button type="button" aria-label="Уменьшить граф" title="Уменьшить" onClick={() => void flow?.zoomOut()}>−</button>
      <button type="button" aria-label="Показать весь граф" title="Показать весь граф" onClick={() => void flow?.fitView({ padding: '15%', minZoom: minimumZoom, maxZoom: 1.2 })}>
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" aria-hidden="true"><path d="M6 2H2v4m8-4h4v4M2 10v4h4m8-4v4h-4" /></svg>
      </button>
    </div>
    {graph.nodes.length === 0 && <p className="explorer-empty">В этом чате пока нет сообщений.</p>}
    {inspected && <aside className="explorer-preview" style={{ top: previewAbove ? 40 : undefined, bottom: previewAbove ? 'auto' : 6, maxHeight: previewAbove ? 'calc(50% - 50px)' : 'calc(50% - 16px)' }} aria-label="Превью сообщения">
      <div className="flex items-center justify-between gap-2 text-xs text-muted">
        <strong>{roles[inspected.role]}</strong>
        <time dateTime={inspected.created_at}>{new Date(inspected.created_at).toLocaleString()}</time>
      </div>
      {inspectedNode && inspectedNode.data.labels.length > 0 && <p className="text-xs text-muted">Ветки: {inspectedNode.data.labels.join(', ')}</p>}
      {merge && <p className="text-xs text-muted">Источник: {merge.source_branch_name}{merge.source_head_message_id ? '' : ' · точная вершина неизвестна'}</p>}
      {!inspected.is_active && <p className="text-xs text-muted">Вне контекста</p>}
      {inspected.is_edited && <p className="text-xs text-muted" title={editedTitle(inspected.updated_at)}>Изменено</p>}
      <p className="break-words text-sm">{inspected.preview || 'Пустое сообщение'}{inspected.preview_truncated ? '…' : ''}</p>
    </aside>}
  </div>
}

export function ExplorerPanel() {
  const chatId = useWorkspace((state) => state.activeChatId)
  const activeBranchId = useWorkspace((state) => state.activeBranchId)
  const resource = useStore(explorerStore, (state) => state.resource)
  const refreshing = useStore(explorerStore, (state) => state.refreshing)
  const refreshError = useStore(explorerStore, (state) => state.refreshError)
  const navigation = useWorkspace((state) => state.navigation)
  const [choice, setChoice] = useState<{ id: string; branches: BranchResponse[] } | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const navigate = useCallback((id: string) => {
    const current = explorerStore.getState().resource
    if (current.status !== 'ready') return
    useWorkspace.getState().cancelNavigation()
    setNotice(null)
    const branches = resolveNavigationBranches(current.data, id, useWorkspace.getState().activeBranchId)
    if (!branches.length) { setNotice('Сообщение не входит ни в одну доступную ветку.'); return }
    if (branches.length === 1) { setChoice(null); void useWorkspace.getState().navigateToMessage(branches[0].id, id) }
    else setChoice({ id, branches })
  }, [])
  const loadedChatId = useStore(explorerStore, (state) => state.chatId)
  useEffect(() => {
    let mounted = true
    let timer: ReturnType<typeof setTimeout> | undefined
    setChoice(null); setNotice(null)
    const unsubscribe = onChatChanged((changed) => {
      if (changed !== chatId) return
      clearTimeout(timer)
      timer = setTimeout(() => { if (mounted) void explorerStore.getState().refresh() }, 50)
    })
    // Coalesce StrictMode's setup/cleanup probe before starting a network request.
    queueMicrotask(() => { if (mounted && chatId) void explorerStore.getState().open(chatId) })
    return () => { mounted = false; unsubscribe(); clearTimeout(timer); explorerStore.getState().close() }
  }, [chatId])

  return <>
    <div className="absolute top-2 right-3 z-10">
      <button type="button" className="explorer-refresh" aria-busy={refreshing} aria-label="Обновить граф" title="Обновить граф" disabled={!chatId || resource.status === 'loading' || refreshing}
        onClick={() => void explorerStore.getState().refresh()}>↻</button>
    </div>
    {refreshError && <p role="alert" className="px-3 text-xs text-muted">{refreshError} <button type="button" onClick={() => void explorerStore.getState().refresh()}>Повторить обновление</button></p>}
    {refreshing && <span role="status" className="sr-only">Обновляем граф…</span>}
    {notice && <p role="status" className="px-3 text-xs text-muted">{notice}</p>}
    {choice && <MessageDialog title="Выберите ветку" pending={false} onCancel={() => { useWorkspace.getState().cancelNavigation(); setChoice(null) }}>
      <p className="text-sm text-muted">Сообщение доступно в нескольких ветках.</p>
      {navigation?.status === 'error' && <p role="alert">{navigation.message}</p>}
      <div className="flex flex-col gap-2">
        {choice.branches.map((branch) => <button type="button" key={branch.id} className="button-secondary" disabled={navigation?.status === 'loading'}
          onClick={async () => { if (await useWorkspace.getState().navigateToMessage(branch.id, choice.id)) setChoice(null) }}>
          {branch.name} · {{ active: 'Активная', archived: 'Архивная', merged: 'Слита' }[branch.status]}
        </button>)}
        <button type="button" className="button-secondary" onClick={() => { useWorkspace.getState().cancelNavigation(); setChoice(null) }}>Отмена</button>
      </div>
    </MessageDialog>}
    {!chatId ? <p className="px-3 text-sm text-muted">Выберите чат для просмотра графа.</p>
      : loadedChatId !== chatId || resource.status === 'idle' || resource.status === 'loading'
        ? <p role="status" className="px-3 text-sm text-muted">Загрузка графа…</p>
        : resource.status === 'error' ? <div role="alert" className="px-3 text-sm">
          <p>{resource.message}</p><button type="button" className="button-secondary mt-2" onClick={() => void explorerStore.getState().open(chatId)}>Повторить</button>
        </div> : resource.status === 'ready' ? <GraphCanvas key={chatId} graph={resource.data} activeBranchId={activeBranchId} onNavigate={navigate} /> : null}
  </>
}
