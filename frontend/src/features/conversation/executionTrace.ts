export function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

export function readExecutionTrace(metadata: Record<string, unknown> | null) {
  const trace = asRecord(metadata?.execution_trace)
  if (!trace) return null

  const rawSteps: unknown[] = Array.isArray(trace.steps) ? trace.steps : []
  const steps = rawSteps.flatMap((value) => {
    const step = asRecord(value)
    if (typeof step?.tool_called !== 'string') return []
    return [{
      tool_called: step.tool_called,
      arguments: step.arguments,
      output_summary: step.output_summary,
      execution_time_ms: step.execution_time_ms,
    }]
  })
  return { steps, total_duration_ms: trace.total_duration_ms }
}

export function formatDuration(milliseconds: unknown): string | null {
  if (typeof milliseconds !== 'number' || !Number.isFinite(milliseconds) || milliseconds < 0) return null
  if (milliseconds < 1000) return `${Math.round(milliseconds)} мс`
  return `${new Intl.NumberFormat('ru', { maximumFractionDigits: 1 }).format(milliseconds / 1000)} с`
}
