import type { QueryResponse, Source } from '../api/types'
import { Badge, Notice } from './ui'

function SourceCard({ source, idPrefix }: { source: Source; idPrefix: string }) {
  return (
    <li id={`${idPrefix}-source-${source.rank}`} className="source">
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
function AnswerText({ text, sources, idPrefix }: { text: string; sources: Source[]; idPrefix: string }) {
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
            href={`#${idPrefix}-source-${rank}`}
            aria-label={`Source ${rank}`}
            onClick={() => {
              const details = document.querySelector(`#${idPrefix}-source-${rank} details`)
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

/** One answer: how it was produced, the text with clickable [n] citations, and its sources. */
export function AnswerContent({ response: r, idPrefix }: { response: QueryResponse; idPrefix: string }) {
  return (
    <>
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
      <AnswerText text={r.answer} sources={r.sources} idPrefix={idPrefix} />
      {r.sources.length > 0 ? (
        <>
          <h3 className="sources-title">
            Retrieved sources ({r.sources.length}) <span className="muted small">select one to read the passage</span>
          </h3>
          <ol className="sources">
            {r.sources.map((source) => (
              <SourceCard key={source.rank} source={source} idPrefix={idPrefix} />
            ))}
          </ol>
        </>
      ) : null}
    </>
  )
}
