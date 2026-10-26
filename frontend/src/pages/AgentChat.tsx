import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { api } from '../api/client'
import type { AgentReply } from '../api/types'
import { Badge, Notice, PageHeader } from '../components/ui'
import { errorMessage } from '../lib/format'

const EXAMPLES = [
  'Which high-risk customers are overdue right now?',
  'Summarise customer 4 and recommend the next action.',
  'What does the policy say about contacting a customer at 60 days overdue?',
]

/** Plain names for the agent's tools, shown under each answer. */
const TOOL_LABEL: Record<string, string> = {
  search_policies: 'Searched the policies',
  customer_overview: 'Looked up the customer',
  payment_history: 'Read the payment history',
  list_overdue_customers: 'Listed overdue customers',
  portfolio_summary: 'Read the portfolio summary',
  recommend_next_action: 'Asked the Decision Agent',
}

interface Turn {
  id: number
  message: string
  state: 'loading' | 'done' | 'error'
  reply?: AgentReply
  error?: string
}

let turnId = 0

/**
 * The Collections Agent: a LangChain agent that answers by calling read-only tools (policies,
 * customers, the portfolio, the Decision Agent). The server remembers the conversation.
 */
export function AgentChat() {
  const [turns, setTurns] = useState<Turn[]>([])
  const [message, setMessage] = useState('')
  const [conversationId, setConversationId] = useState<string | null>(null)
  const abort = useRef<AbortController | null>(null)
  const endRef = useRef<HTMLDivElement>(null)
  const busy = turns.some((t) => t.state === 'loading')

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: 'end', behavior: 'smooth' })
  }, [turns])
  useEffect(() => () => abort.current?.abort(), [])

  async function ask(text: string) {
    const trimmed = text.trim()
    if (!trimmed || busy) return
    const id = ++turnId
    setTurns((current) => [...current, { id, message: trimmed, state: 'loading' }])
    setMessage('')
    const controller = new AbortController()
    abort.current = controller
    const update = (patch: Partial<Turn>) =>
      setTurns((current) => current.map((t) => (t.id === id ? { ...t, ...patch } : t)))
    try {
      const reply = await api.agentChat({ message: trimmed, conversation_id: conversationId }, controller.signal)
      setConversationId(reply.conversation_id)
      update({ state: 'done', reply })
    } catch (error) {
      if (controller.signal.aborted) return
      update({ state: 'error', error: errorMessage(error) })
    }
  }

  function newConversation() {
    abort.current?.abort()
    setTurns([])
    setMessage('')
    setConversationId(null)
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    void ask(message)
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      void ask(message)
    }
  }

  return (
    <div className="playground">
      <PageHeader
        title="Collections Agent"
        eyebrow="LangChain agent"
        description="Ask about customers, the portfolio and the policies. The agent looks things up with read-only tools and remembers the conversation; it never sends anything."
        actions={
          <button type="button" className="btn" onClick={newConversation} disabled={turns.length === 0}>
            New conversation
          </button>
        }
      />

      <section className="card chat" aria-label="Agent conversation">
        {turns.length === 0 ? (
          <div className="state">
            <strong>Ask the Collections Agent</strong>
            <span>It answers from the customer records and the indexed policies, and shows the tools it used.</span>
            <div className="chips">
              {EXAMPLES.map((example) => (
                <button key={example} type="button" className="chip" onClick={() => void ask(example)}>
                  {example}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <ol className="turns" aria-live="polite">
            {turns.map((turn) => (
              <li key={turn.id}>
                <div className="bubble bubble-user">{turn.message}</div>
                {turn.state === 'loading' ? (
                  <div className="bubble bubble-assistant" role="status">
                    <span className="spinner spinner-sm" aria-hidden="true" /> Thinking and looking things up…
                  </div>
                ) : turn.state === 'error' ? (
                  <div className="bubble bubble-assistant">
                    <Notice tone="error">{turn.error}</Notice>
                  </div>
                ) : (
                  <div className="bubble bubble-assistant">
                    <p className="agent-answer">{turn.reply?.answer || 'The agent gave no answer.'}</p>
                    <div className="answer-meta">
                      {turn.reply?.steps.length ? (
                        turn.reply.steps.map((step, index) => (
                          <span key={index} title={step.input}>
                            <Badge tone="info">{TOOL_LABEL[step.tool] ?? step.tool}</Badge>
                          </span>
                        ))
                      ) : (
                        <Badge tone="neutral">No tools needed</Badge>
                      )}
                      <span className="muted small">{turn.reply?.model}</span>
                    </div>
                  </div>
                )}
              </li>
            ))}
          </ol>
        )}
        <div ref={endRef} />
      </section>

      <form className="card composer" onSubmit={onSubmit}>
        <label className="visually-hidden" htmlFor="agent-message">
          Message
        </label>
        <textarea
          id="agent-message"
          rows={2}
          maxLength={2000}
          placeholder="Ask the agent…  (Enter to send, Shift+Enter for a new line)"
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          onKeyDown={onKeyDown}
        />
        <div className="composer-row">
          <span className="muted small">Read-only: the agent advises, a collections officer decides.</span>
          <button type="submit" className="btn btn-primary" disabled={busy || !message.trim()}>
            {busy ? <span className="spinner spinner-sm" aria-hidden="true" /> : null}
            Ask
          </button>
        </div>
      </form>
    </div>
  )
}
