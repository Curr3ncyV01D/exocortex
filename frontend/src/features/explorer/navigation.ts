import type { BranchResponse, ChatGraphResponse } from '../../api/types'

/** Resolve ancestry on the compact graph; the server validates the chosen path again. */
export function resolveNavigationBranches(graph: ChatGraphResponse, messageId: string, currentId: string | null): BranchResponse[] {
  const parents = new Map(graph.nodes.map((node) => [node.id, node.parent_id]))
  const candidates = graph.branches.filter((branch) => {
    let cursor = branch.head_message_id
    const visited = new Set<string>()
    while (cursor && parents.has(cursor) && !visited.has(cursor)) {
      if (cursor === messageId) return true
      visited.add(cursor)
      cursor = parents.get(cursor) ?? null
    }
    return false
  }).sort((a, b) => Number(b.name === 'main') - Number(a.name === 'main') || a.name.localeCompare(b.name) || a.id.localeCompare(b.id))
  const current = candidates.find((branch) => branch.id === currentId)
  return current ? [current] : candidates
}
