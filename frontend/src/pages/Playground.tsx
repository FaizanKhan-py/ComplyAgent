import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { api } from '../api/client'
import type { ChatMessage, QueryResponse, Source } from '../api/types'
import { Badge, Notice, PageHeader } from '../components/ui'
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

function SourceCard({ source, turnId }: { source: Source; turnId: number }) {
  return (
    <li id={`turn-${turnId}-source-${source.rank}`} className="source">
      <details>
        <summary>
          <span className="source-rank">[{source.rank}]</span>
          <span className="source-title">
            <span className="filename">{source.filename}</span>
            <span className="muted small">
              {source.page_number !== null ? `Page ${source.page_number} · ` : ''}
              Chunk {source.chunk_index + 1}
            </span>
          </span>
          {source.cited ? <Badge tone="ok">Cited</Badge> : null}
          <span className="score" title="Cosine similarity between the question and this chunk">
            <span className="score-bar" aria-hidden="true">
              <span style={{ width: `${Math.max(0, Math.min(1, source.score)) * 100}%` }} />
            </span>
            <span className="mono small">{source.score.toFixed(3)}</span>
          </span>
        </summary>
        <p className="source-text">{source.text}</p>
      </details>
    </li>
  )
}

/** Renders [n] markers as links to the matching retrieved source; other text is left as is. */
function AnswerText({ text, sources, turnId }: { text: string; sources: Source[]; turnId: number }) {
  const ranks = new Set(sources.map((s) => s.rank))
  const parts = text.split(/(\[\d+\])/g)
  return (
    <p className="answer-text">
      {parts.map((part, index) => {
        const match = /^\[(\d+)\]$/.exec(part)
        const rank = match ? Number(match[1]) : null
        if (rank === null || !ranks.has(rank)) return <span key={index}>{part}</span>
        return (
          <a
            key={index}
            className="cite"
            href={`#turn-${turnId}-source-${rank}`}
            aria-label={`Source ${rank}`}
            onClick={() => {
              const details = document.querySelector(`#turn-${turnId}-source-${rank} details`)
              if (details) (details as HTMLDetailsElement).open = true
            }}
          >
            {part}
          </a>
        )
      })}
    </p>
  )
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
      <div className="answer-meta">
        {r.answer_mode === 'generated' ? (
          <Badge tone={r.insufficient_evidence ? 'warn' : 'ok'}>
            {r.insufficient_evidence ? 'Insufficient evidence' : 'Generated answer'}
          </Badge>
        ) : r.answer_mode === 'retrieval_only' ? (
          <Badge tone="info">Retrieval only · no generated answer</Badge>
        ) : (
          <Badge tone="warn">No relevant evidence</Badge>
        )}
        {r.model ? <span className="muted small mono">{r.model}</span> : null}
      </div>
      {r.warning ? <Notice tone="warn">{r.warning}</Notice> : null}
      <AnswerText text={r.answer} sources={r.sources} turnId={turn.id} />
      {r.sources.length > 0 ? (
        <>
          <h3 className="sources-title">
            Retrieved sources ({r.sources.length}) <span className="muted small">select one to read the passage</span>
          </h3>
          <ol className="sources">
            {r.sources.map((source) => (
              <SourceCard key={source.rank} source={source} turnId={turn.id} />
            ))}
          </ol>
        </>
      ) : null}
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
        title="RAG Playground"
        description="Ask policy questions and inspect exactly which passages were retrieved."
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
              Set <code>ANTHROPIC_API_KEY</code> or <code>LLM_PROVIDER=ollama</code> on the backend to enable
              generated answers.
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
