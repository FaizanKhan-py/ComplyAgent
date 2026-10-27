import { useEffect, useRef, useState } from 'react'
import { Playground } from '../pages/Playground'

/**
 * The AI Assistant as a pop-up chat in the bottom-right corner of every page. The chat mounts on
 * the first open and then stays mounted, so closing the panel or moving to another page keeps the
 * conversation. `hidden` hides the button on the full-page chat, so two chats never show at once.
 */
export function ChatWidget({ isAdmin, hidden }: { isAdmin: boolean; hidden: boolean }) {
  const [open, setOpen] = useState(false)
  const [started, setStarted] = useState(false)
  const launcher = useRef<HTMLButtonElement>(null)
  const panel = useRef<HTMLElement>(null)

  // Escape closes the panel and hands focus back to the button.
  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false)
        launcher.current?.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open])

  function toggle() {
    if (open) {
      setOpen(false)
      return
    }
    setStarted(true)
    setOpen(true)
    // Once the panel is on screen, put the cursor in the question box.
    window.requestAnimationFrame(() => panel.current?.querySelector('textarea')?.focus())
  }

  return (
    <div className="chat-widget" hidden={hidden}>
      {started ? (
        <section
          ref={panel}
          id="chat-panel"
          className="chat-panel"
          role="dialog"
          aria-label="AI Assistant"
          hidden={!open}
        >
          <header className="chat-panel-head">
            <strong>AI Assistant</strong>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              aria-label="Close the AI Assistant"
              onClick={() => {
                setOpen(false)
                launcher.current?.focus()
              }}
            >
              ✕
            </button>
          </header>
          <div className="chat-panel-body">
            <Playground compact showModelStatus={isAdmin} />
          </div>
        </section>
      ) : null}
      <button
        ref={launcher}
        type="button"
        className="chat-launcher"
        aria-expanded={open}
        aria-controls={started ? 'chat-panel' : undefined}
        onClick={toggle}
      >
        <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
          <path
            d={open ? 'M6 6l12 12M18 6 6 18' : 'M20.5 11.5a8 8 0 0 1-11.7 7.1L3.5 20l1.4-5.3a8 8 0 1 1 15.6-3.2Z'}
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        <span>{open ? 'Close' : 'AI Assistant'}</span>
      </button>
    </div>
  )
}
