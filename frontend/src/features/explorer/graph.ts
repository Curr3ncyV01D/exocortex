import type { Edge, Node } from '@xyflow/react'
import { placeLabels } from './routing'
import type { ChatGraphResponse, GraphNodeResponse } from '../../api/types'

const palette = ['#e1c58c', '#9cc8a2', '#d6a0ae', '#b9a5d8', '#8ac9c7', '#d5b19a']
const mainColor = '#92b7ea'

export function branchColor(id: string, name: string): string {
  if (name === 'main') return mainColor
  let hash = 0
  for (const char of id) hash = (Math.imul(hash, 31) + char.charCodeAt(0)) | 0
  return palette[(hash >>> 0) % palette.length]
}

export type ExplorerNode = Node<{
  message: GraphNodeResponse; shape: GraphNodeResponse['role']; color: string
  active: boolean; labels: string[]; label?: { x: number; y: number; side: 'left' | 'right' }
}, 'message'>

/** Parent links determine depth; merge provenance never changes the history layout. */
export function buildGraph(graph: ChatGraphResponse, activeBranchId: string | null): { nodes: ExplorerNode[]; edges: Edge[] } {
  const messages = new Map(graph.nodes.map((node) => [node.id, node]))
  const branches = [...graph.branches].sort((a, b) => Number(b.name === 'main') - Number(a.name === 'main') || a.id.localeCompare(b.id))
  const owners = new Map<string, string>()
  const colors = new Map(branches.map((branch) => [branch.id, branchColor(branch.id, branch.name)]))
  const lanes = new Map(branches.map((branch, index) => [branch.id, index === 0 ? 0 : (index % 2 ? 1 : -1) * Math.ceil(index / 2)]))
  const active = new Set<string>()
  const labels = new Map<string, string[]>()
  for (const branch of branches) {
    if (branch.head_message_id) labels.set(branch.head_message_id, [...(labels.get(branch.head_message_id) ?? []), branch.name])
    let id = branch.head_message_id
    const visited = new Set<string>()
    while (id && messages.has(id) && !visited.has(id)) {
      visited.add(id)
      if (!owners.has(id)) owners.set(id, branch.id)
      if (branch.id === activeBranchId) active.add(id)
      id = messages.get(id)!.parent_id
    }
  }
  const depth = new Map<string, number>()
  const ordered = [...graph.nodes].sort((a, b) => a.id.localeCompare(b.id))
  for (const message of ordered) {
    const path: string[] = []
    const seen = new Set<string>()
    let id: string | null = message.id
    while (id && messages.has(id) && !depth.has(id)) {
      if (seen.has(id)) throw new Error('Invalid graph cycle')
      seen.add(id)
      path.push(id)
      id = messages.get(id)!.parent_id
    }
    let level = id ? (depth.get(id) ?? -1) + 1 : 0
    for (const item of path.reverse()) depth.set(item, level++)
    if (!owners.has(message.id)) {
      const chain: string[] = []
      id = message.id
      while (id && !owners.has(id)) { chain.push(id); id = messages.get(id)?.parent_id ?? null }
      const owner = id ? owners.get(id)! : `unassigned:${chain[chain.length - 1]}`
      if (!lanes.has(owner)) lanes.set(owner, branches.length + lanes.size + 1)
      for (const item of chain) owners.set(item, owner)
    }
  }
  const nodes: ExplorerNode[] = ordered.map((message) => ({
    id: message.id, type: 'message', position: { x: (lanes.get(owners.get(message.id)!) ?? 0) * 90, y: depth.get(message.id)! * 48 },
    data: { message, shape: message.role, color: colors.get(owners.get(message.id)!) ?? '#a9afb7', active: active.has(message.id), labels: labels.get(message.id) ?? [] },
  }))
  const placements = placeLabels(nodes.map((node) => ({ id: node.id, ...node.position, names: node.data.labels })))
  for (const node of nodes) node.data.label = placements[node.id]
  const nodeColors = new Map(nodes.map((node) => [node.id, node.data.color]))
  const positions = new Map(nodes.map((node) => [node.id, node.position.x]))
  const edges: Edge[] = ordered.filter((node) => node.parent_id).map((node) => ({
    id: `history:${node.id}`, source: node.parent_id!, target: node.id, type: positions.get(node.parent_id!) === positions.get(node.id) ? 'straight' : 'routed', data: { kind: 'history' },
    style: { stroke: nodeColors.get(node.id), strokeWidth: active.has(node.id) ? 2.5 : 1.5, opacity: active.has(node.id) ? 1 : 0.5 },
  }))
  for (const merge of [...graph.merges].sort((a, b) => a.message_id.localeCompare(b.message_id))) {
    if (merge.source_head_message_id) edges.push({
      id: `merge:${merge.message_id}`, source: merge.source_head_message_id, target: merge.message_id,
      type: 'routed', data: { kind: 'merge' }, style: { stroke: nodeColors.get(merge.source_head_message_id), strokeWidth: 1.5, strokeDasharray: '4 4', opacity: 0.65 },
    })
  }
  return { nodes, edges }
}
