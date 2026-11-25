export interface Point { x: number; y: number }

/** Keep long provenance paths in an exterior corridor rather than on a history lane. */
export function routeConnection(source: Point, target: Point, kind: 'history' | 'merge'): Point[] {
  if (kind === 'history' && source.x === target.x) return [source, target]
  const direction = target.y >= source.y ? 1 : -1
  if (kind === 'merge') {
    const corridor = source.x + (source.x <= target.x ? -28 : 28)
    const entry = target.y - direction * Math.min(18, Math.abs(target.y - source.y) / 3)
    return [source, { x: corridor, y: source.y }, { x: corridor, y: entry }, { x: target.x, y: entry }, target]
  }
  const departure = source.y + direction * Math.min(18, Math.abs(target.y - source.y) / 3)
  return [source, { x: source.x, y: departure }, { x: target.x, y: departure }, target]
}

/** Round only the corners, preserving the long straight corridor. */
export function roundedPath(points: Point[]): string {
  let path = `M${points[0].x},${points[0].y}`
  for (let i = 1; i < points.length - 1; i++) {
    const before = points[i - 1], corner = points[i], after = points[i + 1]
    const incoming = Math.hypot(corner.x - before.x, corner.y - before.y)
    const outgoing = Math.hypot(after.x - corner.x, after.y - corner.y)
    const radius = Math.min(10, incoming / 2, outgoing / 2)
    if (!radius) continue
    const start = { x: corner.x + (before.x - corner.x) * radius / incoming, y: corner.y + (before.y - corner.y) * radius / incoming }
    const end = { x: corner.x + (after.x - corner.x) * radius / outgoing, y: corner.y + (after.y - corner.y) * radius / outgoing }
    path += ` L${start.x},${start.y} Q${corner.x},${corner.y} ${end.x},${end.y}`
  }
  const last = points[points.length - 1]
  return path + ` L${last.x},${last.y}`
}

interface LabelNode extends Point { id: string; names: string[] }
interface LabelPlacement { x: number; y: number; side: 'left' | 'right' }

/** Reserve a full label rectangle, including against nodes without branch pointers. */
export function placeLabels(nodes: LabelNode[]): Record<string, LabelPlacement> {
  const result: Record<string, LabelPlacement> = {}
  const occupied: { x: number; y: number; width: number; height: number }[] = []
  const overlaps = (a: { x: number; y: number; width: number; height: number }, b: { x: number; y: number; width: number; height: number }) =>
    a.x < b.x + b.width + 3 && a.x + a.width + 3 > b.x && a.y < b.y + b.height + 3 && a.y + a.height + 3 > b.y
  for (const node of [...nodes].sort((a, b) => Number(b.names.includes('main')) - Number(a.names.includes('main')) || a.id.localeCompare(b.id))) {
    if (!node.names.length) continue
    const sides = node.x < 0 ? ['left', 'right'] as const : ['right', 'left'] as const
    search: for (let row = 0; row < 30; row++) {
      for (const side of sides) {
        const x = side === 'right' ? 28 : -114
        const y = row === 0 ? 3 : -20 * row
        const box = { x: node.x + x, y: node.y + y, width: 110, height: 16 }
        if (nodes.some((other) => other.id !== node.id && overlaps(box, { ...other, width: 24, height: 24 })) || occupied.some((other) => overlaps(box, other))) continue
        result[node.id] = { x, y, side }
        occupied.push(box)
        break search
      }
    }
  }
  return result
}
