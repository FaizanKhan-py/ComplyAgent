import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { api } from '../api/client'
import type { AssistantReply } from '../api/types'
import { AnswerContent } from '../components/AnswerContent'
import { Badge, Notice, PageHeader } from '../components/ui'
import { useResource } from '../hooks/useResource'
import { errorMessage } from '../lib/format'

interface Turn {
  id: number
  question: string
  state: 'loading' | 'done' | 'error'
  reply?: AssistantReply
  error?: string
}

const POLICY_EXAMPLES = [
  'What hours are we allowed to contact customers?',
  'When must an account be escalated to a human?',
  'How much can a hardship plan reduce the installment?',
]
const ADMIN_EXAMPLES = [
  'Which high-risk customers are overdue right now?',
  'Summarise customer 4 and recommend the next action.',
]

/** Plain names for the assistant's tools, shown under each answer. */
const TOOL_LABEL: Record<string, string> = {
  search_policies: 'Searched the policies',
  customer_overview: 'Looked up the customer',
  payment_history: 'Read the payment history',
  list_overdue_customers: 'Listed overdue customers',
  portfolio_summary: 'Read the portfolio summary',
  recommend_next_action: 'Asked the Decision Agent',
}

function Answer({ turn, onRetry }: { turn: Turn; onRetry: () => void }) {
  if (turn.state === 'loading') {
    return (
      <div className="bubble bubble-assistant" role="status">
        <span className="spinner spinner-sm" aria-hidden="true" /> Thinking and looking things up…
      </div>
    )
  }
  if (turn.state === 'error' || !turn.reply) {
    return (
      <div className="bubble bubble-assistant">
        <Notice tone="error">{turn.error ?? 'The request failed.'}</Notice>
        <button type="button" className="btn btn-sm" onClick={onRetry}>
          Retry
        </button>
      </div>
    )
  }
  const r = turn.reply
  return (
    <div className="bubble bubble-assistant">
      <AnswerContent response={r} idPrefix={`turn-${turn.id}`} />
      {r.steps.length ? (
        <div className="answer-meta">
          {r.steps.map((step, index) => (
            <span key={index} title={step.input}>
              <Badge tone="info">{TOOL_LABEL[step.tool] ?? step.tool}</Badge>
            </span>
          ))}
        </div>
      ) : null}
    </div>
  )
}

let turnId = 0

const noHealth = () => Promise.resolve(null)

/**
 * The AI Assistant: one chat for policy questions and, for administrators, customer questions.
 * A LangChain agent answers with read-only tools and cites the policy passages it read; the
 * server remembers the conversation. `showModelStatus` (administrators) also shows the model's
 * health and the customer examples. `compact` fits it into the pop-up chat panel: no page
 * header, a small toolbar instead.
 */
export function Playground({
  showModelStatus = true,
  compact = false,
}: {
  showModelStatus?: boolean
  compact?: boolean
}) {
  const questionId = useId()
  const config = useResource(api.config)
  const health = useResource(showModelStatus ? api.health : noHealth, showModelStatus ? 15000 : undefined)
  const llm = health.data?.components.llm
  const [turns, setTurns] = useState<Turn[]>([])
  const [question, setQuestion] = useState('')
  const [conversationId, setConversationId] = useState<string | null>(null)
  const abort = useRef<AbortController | null>(null)
  const endRef = useRef<HTMLDivElement>(null)
  const busy = turns.some((t) => t.state === 'loading')
  const examples = showModelStatus ? [...POLICY_EXAMPLES.slice(0, 2), ...ADMIN_EXAMPLES] : POLICY_EXAMPLES

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: 'end', behavior: 'smooth' })
  }, [turns])
  useEffect(() => () => abort.current?.abort(), [])

  async function ask(text: string, replaceId?: number) {
    const trimmed = text.trim()
    if (!trimmed || busy) return
    const id = replaceId ?? ++turnId
    setTurns((current) =>
      replaceId
        ? current.map((t) => (t.id === id ? { id, question: trimmed, state: 'loading' } : t))
        : [...current, { id, question: trimmed, state: 'loading' }],
    )
    setQuestion('')

    const controller = new AbortController()
    abort.current = controller
    const update = (patch: Partial<Turn>) =>
      setTurns((current) => current.map((t) => (t.id === id ? { ...t, ...patch } : t)))
    try {
      const reply = await api.assistantChat({ message: trimmed, conversation_id: conversationId }, controller.signal)
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
    setQuestion('')
    setConversationId(null)
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    void ask(question)
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      void ask(question)
    }
  }

  const startOver = (
    <button
      type="button"
      className={compact ? 'btn btn-sm' : 'btn'}
      onClick={newConversation}
      disabled={turns.length === 0}
    >
      New conversation
    </button>
  )

  return (
    <div className={compact ? 'playground playground-compact' : 'playground'}>
      {compact ? (
        <div className="playground-toolbar">
          <span className="muted small">Answers cite the policies they use.</span>
          {startOver}
        </div>
      ) : (
        <PageHeader
          title="AI Assistant"
          eyebrow="Policy desk"
          description={
            showModelStatus
              ? 'Ask about the policies, customers and the portfolio. Answers cite the policies they use and show the tools the assistant used.'
              : 'Ask policy questions. Answers come from the uploaded policies and show their sources.'
          }
          actions={startOver}
        />
      )}

      {config.data && config.data.llm_model === null ? (
        <Notice tone="info">
          No answer model is configured, so responses show the retrieved passages without a generated
          summary.{' '}
          {showModelStatus ? (
            <>
              Set the <code>AZURE_AI_*</code> settings (or <code>ANTHROPIC_API_KEY</code>) on the backend to
              enable generated answers.
            </>
          ) : (
            'An administrator can enable generated answers.'
          )}
        </Notice>
      ) : null}

      {config.data?.llm_model && llm?.status === 'error' ? (
        <Notice tone="warn">
          The answer model is not available right now, so responses will show the retrieved passages
          only. {llm.detail}
        </Notice>
      ) : null}

      <section className="card chat" aria-label="Conversation">
        {turns.length === 0 ? (
          <div className="state">
            <strong>Ask the AI Assistant</strong>
            <span>
              Answers are built only from the indexed policies{showModelStatus ? ' and the customer records' : ''},
              and always list their sources.
            </span>
            <div className="chips">
              {examples.map((example) => (
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
                <div className="bubble bubble-user">{turn.question}</div>
                <Answer turn={turn} onRetry={() => void ask(turn.question, turn.id)} />
              </li>
            ))}
          </ol>
        )}
        <div ref={endRef} />
      </section>

      <form className="card composer" onSubmit={onSubmit}>
        <label className="visually-hidden" htmlFor={questionId}>
          Question
        </label>
        <textarea
          id={questionId}
          rows={2}
          maxLength={2000}
          placeholder="Ask a question…  (Enter to send, Shift+Enter for a new line)"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={onKeyDown}
        />
        <div className="composer-row">
          <span className="muted small">Read-only: the assistant advises, a collections officer decides.</span>
          <button type="submit" className="btn btn-primary" disabled={busy || !question.trim()}>
            {busy ? <span className="spinner spinner-sm" aria-hidden="true" /> : null}
            Ask
          </button>
        </div>
      </form>
    </div>
  )
}
