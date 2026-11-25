/** Coalesce observer notifications and apply layout state outside the observer callback. */
export function createResizeQueue<T>(
  apply: (value: T) => void,
  schedule: (callback: () => void) => number = (callback) => window.requestAnimationFrame(callback),
  cancel: (frame: number) => void = (frame) => window.cancelAnimationFrame(frame),
) {
  let frame: number | undefined
  let latest: T
  return {
    push(value: T) {
      latest = value
      if (frame === undefined) frame = schedule(() => {
        frame = undefined
        apply(latest)
      })
    },
    dispose() {
      if (frame !== undefined) cancel(frame)
      frame = undefined
    },
  }
}
