import { useEffect, useLayoutEffect, useRef, useState } from 'react'

/** True when the browser can animate and the person has not asked for reduced motion. */
export function motionAllowed(): boolean {
  return typeof window.matchMedia === 'function' && !window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/**
 * Tracks where the `.active` child of a container sits, so a single indicator can slide between
 * items instead of each item drawing its own highlight.
 */
export function useActiveRect<T extends HTMLElement>(dependency: unknown) {
  const ref = useRef<T>(null)
  const [rect, setRect] = useState<{ left: number; top: number; width: number; height: number } | null>(null)

  useLayoutEffect(() => {
    const root = ref.current
    if (!root) return
    const update = () => {
      const active = root.querySelector<HTMLElement>('.active')
      setRect(
        active
          ? { left: active.offsetLeft, top: active.offsetTop, width: active.offsetWidth, height: active.offsetHeight }
          : null,
      )
    }
    update()
    window.addEventListener('resize', update)
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(update) : null
    observer?.observe(root)
    return () => {
      window.removeEventListener('resize', update)
      observer?.disconnect()
    }
  }, [dependency])

  return [ref, rect] as const
}

/**
 * Counts the leading number of a formatted value ("22.7%", "1,204", "NT$1.2M") up from zero,
 * keeping its prefix, suffix, separators and decimals. Without animation it shows the value as is.
 */
export function useCountUp(value: string, duration = 900): string {
  // The in-between figure, tagged with the value it is counting towards; stale frames are ignored.
  const [frame, setFrame] = useState<{ target: string; text: string } | null>(null)
  useEffect(() => {
    const match = /^([^\d-]*)(-?[\d,]*\.?\d+)(.*)$/.exec(value)
    if (!match || !motionAllowed()) return
    const [, prefix, digits, suffix] = match
    const target = Number(digits.replace(/,/g, ''))
    const decimals = digits.includes('.') ? digits.split('.')[1].length : 0
    const grouped = digits.includes(',')
    const format = (n: number) =>
      prefix +
      n.toLocaleString('en-US', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
        useGrouping: grouped,
      }) +
      suffix
    let request = 0
    const start = performance.now()
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration)
      const eased = 1 - Math.pow(1 - t, 4)
      setFrame(t < 1 ? { target: value, text: format(target * eased) } : null)
      if (t < 1) request = requestAnimationFrame(tick)
    }
    request = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(request)
  }, [value, duration])
  return frame && frame.target === value ? frame.text : value
}

/**
 * Primary buttons lean a few pixels towards the pointer. One listener for the whole document;
 * only on devices with a fine pointer and only when motion is allowed.
 */
export function installMagneticButtons(): void {
  if (!motionAllowed() || !window.matchMedia('(pointer: fine)').matches) return
  let current: HTMLElement | null = null
  const release = () => {
    if (!current) return
    current.style.removeProperty('--mx')
    current.style.removeProperty('--my')
    current = null
  }
  document.addEventListener('pointermove', (event) => {
    const target = (event.target as Element | null)?.closest?.<HTMLElement>('.btn-primary:not(:disabled), .chip')
    if (target !== current) release()
    if (!target) return
    current = target
    const box = target.getBoundingClientRect()
    const x = ((event.clientX - box.left) / box.width - 0.5) * 2
    const y = ((event.clientY - box.top) / box.height - 0.5) * 2
    target.style.setProperty('--mx', `${(x * 4).toFixed(2)}px`)
    target.style.setProperty('--my', `${(y * 3).toFixed(2)}px`)
  })
  document.addEventListener('pointerleave', release)
}
