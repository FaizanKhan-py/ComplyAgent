import { useRef, useState, type DragEvent } from 'react'
import { api } from '../api/client'
import type { AdminConfig, PolicyDocument } from '../api/types'
import { ConfirmDialog } from '../components/ConfirmDialog'
import {
  EmptyState,
  ErrorState,
  Loading,
  Notice,
  PageHeader,
  RefreshButton,
  StatusBadge,
} from '../components/ui'
import { useResource } from '../hooks/useResource'
import { errorMessage, formatBytes, formatDateTime } from '../lib/format'

interface UploadResult {
  key: number
  name: string
  state: 'uploading' | 'accepted' | 'rejected'
  message?: string
}

const FALLBACK_CONFIG = { supported_extensions: ['.pdf', '.txt', '.md'], max_upload_mb: 20 }

/** Mirrors the server's checks so obvious mistakes fail instantly; the server still decides. */
function validate(file: File, config: Pick<AdminConfig, 'supported_extensions' | 'max_upload_mb'>) {
  const ext = file.name.includes('.') ? `.${file.name.split('.').pop()!.toLowerCase()}` : ''
  if (!config.supported_extensions.includes(ext)) {
    return `Unsupported file type. Use ${config.supported_extensions.join(', ')}.`
  }
  if (file.size === 0) return 'The file is empty.'
  if (file.size > config.max_upload_mb * 1024 * 1024) {
    return `The file is larger than the ${config.max_upload_mb} MB limit.`
  }
  return null
}

const isBusy = (doc: PolicyDocument) => doc.status === 'pending' || doc.status === 'processing'
let uploadKey = 0

export function Documents() {
  const documents = useResource(api.listDocuments, (docs) => (docs.some(isBusy) ? 1500 : null))
  const config = useResource(api.config)
  const limits = config.data ?? FALLBACK_CONFIG

  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [uploads, setUploads] = useState<UploadResult[]>([])
  const [toDelete, setToDelete] = useState<PolicyDocument | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [actionId, setActionId] = useState<string | null>(null)
  const [notice, setNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)

  async function uploadFiles(files: File[]) {
    setNotice(null)
    const batch = files.map((file) => ({ file, key: ++uploadKey }))
    setUploads((current) => [
      ...batch.map(({ file, key }) => ({ key, name: file.name, state: 'uploading' as const })),
      ...current,
    ])
    const settle = (key: number, patch: Partial<UploadResult>) =>
      setUploads((current) => current.map((u) => (u.key === key ? { ...u, ...patch } : u)))

    await Promise.all(
      batch.map(async ({ file, key }) => {
        const problem = validate(file, limits)
        if (problem) return settle(key, { state: 'rejected', message: problem })
        try {
          await api.uploadDocument(file)
          settle(key, { state: 'accepted', message: 'Accepted. Indexing started.' })
        } catch (error) {
          settle(key, { state: 'rejected', message: errorMessage(error) })
        }
      }),
    )
    await documents.refresh()
  }

  function onDrop(event: DragEvent) {
    event.preventDefault()
    setDragging(false)
    const files = Array.from(event.dataTransfer.files)
    if (files.length) void uploadFiles(files)
  }

  async function reindex(doc: PolicyDocument) {
    setActionId(doc.id)
    setNotice(null)
    try {
      await api.reindexDocument(doc.id)
      setNotice({ tone: 'ok', text: `Reindexing “${doc.filename}”.` })
    } catch (error) {
      setNotice({ tone: 'error', text: `Could not reindex “${doc.filename}”: ${errorMessage(error)}` })
    } finally {
      setActionId(null)
      await documents.refresh()
    }
  }

  async function confirmDelete() {
    if (!toDelete) return
    setDeleting(true)
    try {
      const result = await api.deleteDocument(toDelete.id)
      setNotice({
        tone: 'ok',
        text: `Deleted “${toDelete.filename}” and removed ${result.chunks_removed} indexed chunk${result.chunks_removed === 1 ? '' : 's'}.`,
      })
    } catch (error) {
      setNotice({
        tone: 'error',
        text: `Could not delete “${toDelete.filename}”: ${errorMessage(error)}`,
      })
    } finally {
      setDeleting(false)
      setToDelete(null)
      await documents.refresh()
    }
  }

  const docs = documents.data

  return (
    <>
      <PageHeader
        title="Documents"
        description="Upload policy documents and follow them through indexing."
        actions={<RefreshButton busy={documents.refreshing} onClick={documents.refresh} />}
      />

      <section className="card" aria-labelledby="upload-title">
        <h2 id="upload-title">Upload</h2>
        <div
          className={`dropzone ${dragging ? 'dropzone-active' : ''}`}
          onDragOver={(event) => {
            event.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
        >
          <p>
            <strong>Drop files here</strong> or
          </p>
          <button type="button" className="btn btn-primary" onClick={() => inputRef.current?.click()}>
            Choose files
          </button>
          <input
            ref={inputRef}
            type="file"
            multiple
            className="visually-hidden"
            aria-label="Choose policy documents to upload"
            accept={limits.supported_extensions.join(',')}
            onChange={(event) => {
              const files = Array.from(event.target.files ?? [])
              event.target.value = ''
              if (files.length) void uploadFiles(files)
            }}
          />
          <p className="muted small">
            Supported: {limits.supported_extensions.join(', ')} · up to {limits.max_upload_mb} MB each
            · scanned PDFs without a text layer cannot be indexed
          </p>
        </div>

        {uploads.length > 0 ? (
          <ul className="uploads" aria-label="Upload results" aria-live="polite">
            {uploads.map((upload) => (
              <li key={upload.key} className={`upload upload-${upload.state}`}>
                <span className="filename">{upload.name}</span>
                <span>
                  {upload.state === 'uploading' ? (
                    <>
                      <span className="spinner spinner-sm" aria-hidden="true" /> Uploading…
                    </>
                  ) : (
                    upload.message
                  )}
                </span>
              </li>
            ))}
            <li className="uploads-clear">
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setUploads([])}>
                Clear list
              </button>
            </li>
          </ul>
        ) : null}
      </section>

      {notice ? (
        <Notice tone={notice.tone} onDismiss={() => setNotice(null)}>
          {notice.text}
        </Notice>
      ) : null}

      <section className="card" aria-labelledby="docs-title">
        <div className="card-head">
          <h2 id="docs-title">Uploaded documents{docs ? ` (${docs.length})` : ''}</h2>
        </div>
        {documents.loading ? (
          <Loading label="Loading documents…" />
        ) : documents.error && !docs ? (
          <ErrorState error={documents.error} onRetry={documents.refresh} />
        ) : docs && docs.length === 0 ? (
          <EmptyState title="No documents yet">
            Upload a PDF, TXT or Markdown policy above to build the knowledge base.
          </EmptyState>
        ) : docs ? (
          <>
            {documents.error ? (
              <Notice tone="warn">Showing the last known list. {documents.error.message}</Notice>
            ) : null}
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Name</th>
                    <th scope="col">Type</th>
                    <th scope="col">Size</th>
                    <th scope="col">Uploaded</th>
                    <th scope="col">Status</th>
                    <th scope="col">Pages</th>
                    <th scope="col">Chunks</th>
                    <th scope="col">
                      <span className="visually-hidden">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {docs.map((doc) => (
                    <tr key={doc.id}>
                      <td data-label="Name">
                        <div>
                          <span className="filename">{doc.filename}</span>
                          <div className="muted small mono">ID {doc.id}</div>
                        </div>
                      </td>
                      <td data-label="Type">{doc.file_type.toUpperCase()}</td>
                      <td data-label="Size">{formatBytes(doc.size_bytes)}</td>
                      <td data-label="Uploaded">{formatDateTime(doc.uploaded_at)}</td>
                      <td data-label="Status">
                        <div>
                          <StatusBadge status={doc.status} />
                          {doc.error ? <div className="error-text small">{doc.error}</div> : null}
                        </div>
                      </td>
                      <td data-label="Pages">{doc.page_count ?? '—'}</td>
                      <td data-label="Chunks">{doc.status === 'indexed' ? doc.chunk_count : '—'}</td>
                      <td className="row-actions">
                        <button
                          type="button"
                          className="btn btn-sm"
                          disabled={isBusy(doc) || actionId === doc.id}
                          onClick={() => void reindex(doc)}
                          aria-label={`Reindex ${doc.filename}`}
                        >
                          {doc.status === 'failed' ? 'Retry' : 'Reindex'}
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm btn-danger-outline"
                          disabled={isBusy(doc)}
                          onClick={() => setToDelete(doc)}
                          aria-label={`Delete ${doc.filename}`}
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : null}
      </section>

      <ConfirmDialog
        open={toDelete !== null}
        title="Delete this document?"
        confirmLabel="Delete document"
        danger
        busy={deleting}
        onConfirm={() => void confirmDelete()}
        onCancel={() => setToDelete(null)}
      >
        <p>
          <strong>{toDelete?.filename}</strong> will be removed along with its stored file and{' '}
          {toDelete?.chunk_count ?? 0} indexed chunk{toDelete?.chunk_count === 1 ? '' : 's'}. Answers
          will no longer cite it. This cannot be undone.
        </p>
      </ConfirmDialog>
    </>
  )
}
