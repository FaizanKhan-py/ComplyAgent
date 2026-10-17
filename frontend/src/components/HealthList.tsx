import type { Health } from '../api/types'
import { StatusBadge } from './ui'

const LABEL: Record<string, string> = {
  database: 'Knowledge base store',
  storage: 'File storage',
  embeddings: 'Embedding model',
  llm: 'Answer model (LLM)',
}

export function HealthList({ health }: { health: Health }) {
  return (
    <ul className="health">
      {Object.entries(health.components).map(([name, component]) => (
        <li key={name}>
          <div>
            <strong>{LABEL[name] ?? name}</strong>
            <div className="muted small">{component.detail}</div>
          </div>
          <StatusBadge status={component.status} />
        </li>
      ))}
    </ul>
  )
}
