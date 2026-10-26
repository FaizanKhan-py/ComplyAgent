import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { api } from '../api/client'
import type { ChatMessage, QueryResponse } from '../api/types'
import { AnswerContent } from '../components/AnswerContent'
import { Notice, PageHeader } from '../components/ui'
import { useResource } from '../hooks/useResource'
import { errorMessage } from '../lib/format'

interface Turn {
  id: number
  question: string
  state: 'loading' | 'done' | 'error'
  response?: QueryResponse
  error?: string
}

const EXAMPLES = [
  'What hours are we allowed to contact customers?',
  'When must an account be escalated to a human?',
  'How much can a hardship plan reduce the installment?',
]

/** Only real model answers are sent back as history; fixed fallback messages are not. */
function buildHistory(turns: Turn[]): ChatMessage[] {
  return turns
    .filter((t) => t.state === 'done' && t.response?.answer_mode === 'generated')
    .flatMap((t) => [
      { role: 'user' as const, content: t.question },
      { role: 'assistant' as const, content: t.response!.answer },
    ])
    .slice(-12)
}

function Answer({ turn, onRetry }: { turn: Turn; onRetry: () => void }) {
  if (turn.state === 'loading') {
    return (
      <div className="bubble bubble-assistant" role="status">
        <span className="spinner spinner-sm" aria-hidden="true" /> Searching the policy documents…
      </div>
    )
  }
  if (turn.state === 'error' || !turn.response) {
    return (
      <div className="bubble bubble-assistant">
        <Notice tone="error">{turn.error ?? 'The request failed.'}</Notice>
        <button type="button" className="btn btn-sm" onClick={onRetry}>
          Retry
        </button>
      </div>
    )
  }
  const r = turn.response
  return (
    <div className="bubble bubble-assistant">
      <AnswerContent response={r} idPrefix={`turn-${turn.id}`} />
      <p className="muted small">
        Searched {r.retrieval.chunks_searched} chunk{r.retrieval.chunks_searched === 1 ? '' : 's'} ·
        top {r.retrieval.top_k} · minimum score {r.retrieval.min_score}
      </p>
    </div>
  )
}

let turnId = 0

const noHealth = () => Promise.resolve(null)

/** `showModelStatus` is off for ordinary users, who may not read the service health. */
export function Playground({ showModelStatus = true }: { showModelStatus?: boolean }) {
  const config = useResource(api.config)
  const health = useResource(showModelStatus ? api.health : noHealth, showModelStatus ? 15000 : undefined)
  const llm = health.data?.components.llm
  const [turns, setTurns] = useState<Turn[]>([])
  const [question, setQuestion] = useState('')
  const [topK, setTopK] = useState('')
  const [minScore, setMinScore] = useState('')
  const abort = useRef<AbortController | null>(null)
  const endRef = useRef<HTMLDivElement>(null)
  const busy = turns.some((t) => t.state === 'loading')

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: 'end', behavior: 'smooth' })
  }, [turns])
  useEffect(() => () => abort.current?.abort(), [])

  async function ask(text: string, replaceId?: number) {
    const trimmed = text.trim()
    if (!trimmed || busy) return
    const id = replaceId ?? ++turnId
    const previous = turns.filter((t) => t.id !== id)
    setTurns([...previous, { id, question: trimmed, state: 'loading' }])
    setQuestion('')

    const controller = new AbortController()
    abort.current = controller
    const update = (patch: Partial<Turn>) =>
      setTurns((current) => current.map((t) => (t.id === id ? { ...t, ...patch } : t)))
    try {
      const response = await api.query(
        {
          question: trimmed,
          history: buildHistory(previous),
          ...(topK ? { top_k: Number(topK) } : {}),
          ...(minScore ? { min_score: Number(minScore) } : {}),
        },
        controller.signal,
      )
      update({ state: 'done', response })
    } catch (error) {
      if (controller.signal.aborted) return
      update({ state: 'error', error: errorMessage(error) })
    }
  }

  function newConversation() {
    abort.current?.abort()
    setTurns([])
    setQuestion('')
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

  return (
    <div className="playground">
      <PageHeader
        title="AI Assistant"
        eyebrow="Policy desk"
        description="Ask policy questions. Answers come from the uploaded policies and show their sources."
        actions={
          <button type="button" className="btn" onClick={newConversation} disabled={turns.length === 0}>
            New conversation
          </button>
        }
      />

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
            <strong>Ask a question about the uploaded policies</strong>
            <span>Answers are built only from indexed documents and always list their sources.</span>
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
                <div className="bubble bubble-user">{turn.question}</div>
                <Answer turn={turn} onRetry={() => void ask(turn.question, turn.id)} />
              </li>
            ))}
          </ol>
        )}
        <div ref={endRef} />
      </section>

      <form className="card composer" onSubmit={onSubmit}>
        <label className="visually-hidden" htmlFor="question">
          Question
        </label>
        <textarea
          id="question"
          rows={2}
          maxLength={2000}
          placeholder="Ask a policy question…  (Enter to send, Shift+Enter for a new line)"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={onKeyDown}
        />
        <div className="composer-row">
          <details className="settings">
            <summary>Retrieval settings</summary>
            <div className="settings-grid">
              <label className="field">
                <span>Chunks to retrieve (1–20)</span>
                <input
                  type="number"
                  min={1}
                  max={20}
                  step={1}
                  value={topK}
                  placeholder={config.data ? `Default ${config.data.default_top_k}` : 'Default'}
                  onChange={(event) => setTopK(event.target.value)}
                />
              </label>
              <label className="field">
                <span>Minimum score (0–1)</span>
                <input
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  value={minScore}
                  placeholder={config.data ? `Default ${config.data.default_min_score}` : 'Default'}
                  onChange={(event) => setMinScore(event.target.value)}
                />
              </label>
            </div>
          </details>
          <button type="submit" className="btn btn-primary" disabled={busy || !question.trim()}>
            {busy ? <span className="spinner spinner-sm" aria-hidden="true" /> : null}
            Ask
          </button>
        </div>
      </form>
    </div>
  )
}
