# ComplyAgent

ComplyAgent is an AI-powered collections decision-support system. This repository contains:

| Part | Location | What it does |
|---|---|---|
| Data pipeline | `notebooks/`, `sql/`, `src/` | Builds the training table and loads it into MySQL |
| **Admin API + RAG** | `backend/` | Ingests policy documents, indexes them, answers policy questions with sources |
| **Admin Panel** | `frontend/` | Dashboard, document management, knowledge-base view, RAG playground |
| Sample policies | `sample_policies/` | Three fictional policies for demos and tests |

The rest of this README covers the Admin Panel and the RAG system.

## Architecture

```
Browser ── React admin panel (Vite, TypeScript)
              │  /api/*  (Bearer admin token)
              ▼
        FastAPI backend ──► SQLite knowledge base   documents, chunks, embeddings, activity log
              │         ──► storage/uploads/        original files, named by document ID
              ├──► Embeddings: BAAI/bge-small-en-v1.5, run locally with fastembed (no API key)
              └──► Answers:    Claude (Anthropic API) or a local Ollama model, optional
```

| Concern | Choice | Why |
|---|---|---|
| Backend | FastAPI, Python 3.10+ | The team's code is Python |
| Knowledge base | One SQLite file; vectors stored as blobs, cosine search with NumPy | Zero setup, and exact search is instant at policy-corpus size (thousands of chunks). No separate vector database to run |
| Embeddings | `fastembed` running `BAAI/bge-small-en-v1.5` locally | Semantic search with no API key and no cost. The model (about 70 MB) is downloaded once |
| Answer model | Claude `claude-opus-5-5` through the `anthropic` SDK, or a local model through Ollama | Optional. With neither configured the system runs in retrieval-only mode |
| Frontend | React 19, TypeScript, Vite, React Router, plain CSS | No UI framework dependency |

The knowledge base is separate from the team's MySQL database, which is not required to run this part.

## Quick start

You need Python 3.10 or newer (tested on 3.12) and Node.js 20 or newer.

**1. Backend** (terminal 1)

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                 # then edit it, see "Configuration"
uvicorn app.main:app --reload --port 8000
```

The first start downloads the embedding model, so it needs internet access once.
Interactive API docs are at http://localhost:8000/docs.

**2. Frontend** (terminal 2)

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. The dev server forwards `/api` to `http://127.0.0.1:8000`
(override with `VITE_DEV_BACKEND=http://host:port npm run dev`).

## Configuration

All backend settings are environment variables, read from `backend/.env`. That file is
git-ignored; `backend/.env.example` documents every variable.

| Variable | Default | Meaning |
|---|---|---|
| `APP_ENV` | `development` | With `production` the server refuses to start unless `ADMIN_API_TOKEN` is set |
| `ADMIN_API_TOKEN` | empty | Bearer token required on every `/api/admin/*` request. Empty means open access, for local development only |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Browser origins allowed to call the API |
| `STORAGE_DIR` | `backend/storage` | SQLite file, uploaded files, embedding model cache |
| `MAX_UPLOAD_MB` | `20` | Upload size limit |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `500` / `80` | Chunk length and overlap, in characters |
| `EMBEDDING_PROVIDER` | `fastembed` | `fastembed` (semantic) or `hashing` (offline keyword fallback, lower quality) |
| `EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | Any text model fastembed supports |
| `RAG_TOP_K` | `5` | Chunks retrieved per question |
| `RAG_MIN_SCORE` | provider default (`0.6`) | Chunks scoring below this are discarded |
| `LLM_PROVIDER` | `auto` | `auto`: Claude if `ANTHROPIC_API_KEY` is set, else retrieval-only. `anthropic`: always Claude. `ollama`: a local Ollama model. `none`: never |
| `ANTHROPIC_API_KEY` | empty | Key for generated answers |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` | Answer model |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama server, used when `LLM_PROVIDER=ollama` |
| `OLLAMA_MODEL` | `llama3.2` | Ollama answer model. Pull it first with `ollama pull llama3.2` |
| `LLM_TIMEOUT_SECONDS` | `60` | Per-request timeout |

To generate answers locally with no API key, install [Ollama](https://ollama.com), run
`ollama pull llama3.2`, and set `LLM_PROVIDER=ollama`. A small local model is slower and less
precise with citations than Claude, so raise `LLM_TIMEOUT_SECONDS` on a machine without a GPU.

Generate an admin token with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

The frontend has one optional variable, `VITE_API_BASE_URL` (see `frontend/.env.example`), for
when the built frontend is hosted on a different origin from the API.

If you change the embedding model, chunks made with the old model stop being searched and the
Knowledge Base page reports them as stale. Use **Rebuild index** to re-embed everything.

## Supported files

| Type | Extension | Notes |
|---|---|---|
| PDF | `.pdf` | Text is extracted per page, so sources cite real page numbers. Scanned PDFs with no text layer fail with a clear message (no OCR). Password-protected PDFs are rejected |
| Plain text | `.txt` | UTF-8 or Windows-1252 |
| Markdown | `.md` | Indexed as text |

Anything else is rejected with HTTP 415. The server checks the extension, the size, and that
the bytes match the type (a `.pdf` must really be a PDF).

## From upload to answer

1. **Upload.** `POST /api/admin/documents` validates the file and stores it as
   `storage/uploads/<document-id>.<ext>`. The document ID is the first 16 hex characters of the
   file's SHA-256, so the same bytes always get the same ID and a second upload of the same file
   returns `409 duplicate_document`. The original filename is only ever used for display.
2. **Queue.** The API answers `202` with status `pending` and ingests in the background.
3. **Extract.** PDF pages or the text file are read and whitespace is normalised.
4. **Chunk.** Text is split on paragraph, line, sentence, then word boundaries into overlapping
   chunks. Chunks never cross a page, so each keeps an exact page number and a chunk index.
5. **Embed and store.** Chunks are embedded and written with their vectors in a single
   transaction that replaces any previous chunks of that document. Status becomes `indexed`.
6. **Failure.** If any step fails, the status becomes `failed`, the reason is stored on the
   document and shown in the panel, and the document keeps no chunks. **Retry** re-runs
   ingestion from the stored file. Ingestions cut off by a server restart are marked failed at
   the next start.
7. **Ask.** `POST /api/admin/rag/query` embeds the question, ranks chunks of indexed documents
   by cosine similarity, and keeps the top `top_k` that score at least `min_score`.
8. **Answer.** The response always contains the retrieved sources (filename, page number when
   one exists, chunk index, score, text) and one of three modes:

   | `answer_mode` | When | Answer |
   |---|---|---|
   | `generated` | Chunks were found and the answer model (Claude or Ollama) answered | The model's answer with `[n]` citations. `cited` marks the sources it used. `insufficient_evidence` is true if the model judged the passages do not answer the question |
   | `retrieval_only` | Chunks were found but no LLM is configured, or the LLM call failed (`warning` says why) | A fixed message; the passages themselves are the result |
   | `no_evidence` | Nothing scored above the threshold, or nothing is indexed | A fixed "not found" message. The LLM is not called |

Sources are only ever chunks that were actually retrieved. Citation numbers that do not match a
retrieved source are removed from the answer, and page numbers are omitted rather than guessed
for formats without pages. Document text is sent to Claude inside escaped `<source>` blocks,
and the system prompt tells it to treat that text as evidence, not as instructions.

## API

Every `/api/admin/*` route requires `Authorization: Bearer <ADMIN_API_TOKEN>` when a token is
configured. Errors always have the shape `{"error": {"code": "...", "message": "..."}}`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Public liveness: overall status only |
| GET | `/api/auth/status` | Public: whether a token is required |
| GET | `/api/admin/session` | Check a token |
| GET | `/api/admin/health` | Status of the store, file storage, embedding model and LLM |
| GET | `/api/admin/config` | Limits, models and retrieval defaults (no secrets) |
| GET | `/api/admin/documents` | List documents with status and metadata |
| POST | `/api/admin/documents` | Upload (multipart field `file`); returns 202 |
| GET | `/api/admin/documents/{id}` | One document |
| DELETE | `/api/admin/documents/{id}` | Delete the document, its file and its chunks |
| POST | `/api/admin/documents/{id}/reindex` | Re-run ingestion from the stored file |
| GET | `/api/admin/activity?limit=20` | Recent uploads, indexing results, deletions |
| GET | `/api/admin/kb/stats` | Document and chunk counts, knowledge-base status |
| POST | `/api/admin/kb/rebuild` | Reindex every document |
| POST | `/api/admin/rag/query` | Ask a question: `{question, history?, top_k?, min_score?, document_ids?}` |

## Demo script

1. Start both servers and open http://localhost:5173. If you set `ADMIN_API_TOKEN`, sign in with it.
2. **Dashboard**: everything is zero and the knowledge base is "Empty".
3. **Documents**: upload the three files in `sample_policies/`. Each moves from Queued to Indexed
   with its chunk count; the PDF shows 2 pages.
4. Upload `escalation_policy.pdf` again to show the duplicate rejection, and any `.docx` or
   `.exe` to show type validation.
5. **Knowledge Base**: 3 of 3 indexed, 14 searchable chunks, model and chunking settings, health.
6. **RAG Playground**, ask:
   - "When must an account be escalated to a human?" The top source is `escalation_policy.pdf`,
     page 2. Open it to read the passage.
   - "Can an automated agent approve a hardship plan?" The top source is the hardship policy.
   - "How do I bake sourdough bread?" The result is "No relevant evidence" with no sources.
7. **Documents**: delete the hardship policy, then ask the hardship question again. It is no
   longer cited.

With `ANTHROPIC_API_KEY` set or `LLM_PROVIDER=ollama`, step 6 also shows a written answer with clickable `[n]`
citations. Without it, the panel says so and shows the retrieved passages only.

## Tests and checks

```bash
# Backend (from backend/, with the virtualenv active)
pytest                   # 80 tests, no network: keyword embedder + scripted LLM
pytest -m integration    # 6 tests using the real embedding model
ruff check .

# Frontend (from frontend/)
npm test                 # 24 tests: API client contract and all pages, fetch mocked
npm run lint
npm run build            # type-check + production build into dist/
```

The default backend suite replaces only the embedding model and the LLM; the API, validation,
extraction, chunking, SQLite storage and retrieval code all run for real. The integration suite
runs retrieval with the real embedding model. No automated test calls the Claude API, because
that needs a key.

## Security notes

- Set `ADMIN_API_TOKEN` and `APP_ENV=production` for anything that is not your own laptop, and
  serve it over HTTPS. The token is a single shared admin credential: there are no user
  accounts or roles yet.
- The panel keeps the token in `sessionStorage`, so it is cleared when the tab closes.
- Uploaded files are stored under their document ID, never under a user-supplied name.
- Logs record document IDs, sizes and counts, not document text or questions.

## Known limitations

- No OCR, and no DOCX support.
- Ingestion runs in the API process. That is fine for policy documents; a very large batch
  would want a job queue.
- Search loads all vectors into memory for each query. Beyond a few hundred thousand chunks,
  move to a vector index.
- The default score threshold (0.6) was tuned on the sample policies with `bge-small`. Questions
  that are on-topic but unanswered by the documents can still retrieve passages; in `generated`
  mode Claude is instructed to say the evidence is insufficient. Tune `min_score` live in the
  playground's retrieval settings.
