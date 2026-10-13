# ComplyAgent

ComplyAgent is an AI-powered collections decision-support system. This repository contains:

| Part | Location | What it does |
|---|---|---|
| Data and model | `notebooks/Data_pipeline_and_Modeling.ipynb` | Explores the UCI credit card default data and trains the risk model the app uses |
| MySQL database | `database/` | The team's MySQL schema and loader for the same data. The app does not need it: it keeps its own copy in SQLite, with matching tables |
| Earlier data pipeline | `notebooks/complyagent_data_extraction.ipynb`, `sql/`, `src/` | The first, LendingClub-based pipeline. The app no longer uses it |
| **Admin API + RAG** | `backend/` | Ingests policy documents, indexes them, answers policy questions with sources |
| **Admin Panel** | `frontend/` | Sign-in and sign-up, and three sections: Risk Dashboard, Customer Records, AI Assistant |
| Sample policies | `sample_policies/` | Three fictional policies for demos and tests |

The rest of this README covers the Admin Panel and the RAG system.

## Architecture

```
Browser ── React admin panel (Vite, TypeScript)
              │  /api/*  (session cookie + CSRF token)
              ▼
        FastAPI backend ──► SQLite database         users, sessions, documents, chunks, embeddings, activity log, customers, payment history, predictions
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
| Accounts | Email and password; Argon2id hashes; server-side sessions in an HttpOnly cookie; roles `user` and `admin` | No separate identity service to run. See "Accounts and access" |
| Frontend | React 19, TypeScript, Vite, React Router, plain CSS | No UI framework dependency |

The knowledge base is separate from the team's MySQL database, which is not required to run this part.

## The admin panel

| Section | What it shows |
|---|---|
| **Risk Dashboard** | Portfolio totals, customers by predicted risk, the delinquency trend by month, default rate by education, and the model's test scores |
| **Customer Records** | Filterable list of customers with predicted risk and actual status; each record has account details, the prediction and six months of payment history |
| **AI Assistant** | Policy questions answered with sources. Administrators also get the **Documents** and **Knowledge Base** tabs here |

The Risk Dashboard and Customer Records use the
[UCI "Default of Credit Card Clients"](https://archive.ics.uci.edu/dataset/350/default) data
(30,000 customers, six months of payment history each) and the model from
`notebooks/Data_pipeline_and_Modeling.ipynb`. Set both up once:

```bash
# from backend/, with the virtualenv active
pip install -r requirements-ml.txt        # scikit-learn and xgboost, needed for training only
python -m app.manage import-customers     # downloads the data from UCI into the SQLite database
python -m app.manage train-model          # trains the model and saves a prediction for every customer
```

`import-customers --csv file.csv` loads a copy you downloaded yourself, and `--replace` deletes
what is loaded first. Until the data is loaded both sections show these commands.

**The model.** `train-model` builds the notebook's final model: XGBoost with the hyperparameters
its grid search chose, on the same seven features (credit limit, age, on-time payment rate,
average payment delay, average credit utilisation, education, sex), the same 70/15/15 stratified
split and the same class weighting. On the 4,500 test customers it scores ROC-AUC 0.770, recall
0.62, precision 0.44, accuracy 0.74; the notebook reports 0.769, 0.62, 0.45 and 0.75 (the small
gap is library versions). The API does not load the model: it reads the saved predictions, so
the server runs without scikit-learn or xgboost.

What to know about the figures:

- **Predicted and actual are shown side by side.** "Predicted risk" is the model's chance of
  default next month, in three levels (low under 40%, medium 40–69%, high 70% or more, as in the
  team's `v_customer_risk_report`). "Actual status" is what the data records.
- **Most customers were used to train the model**, so their predictions are not an independent
  test. Each customer record says whether that customer was in the training, validation or test
  group; the Risk Dashboard reports the scores on the test group only.
- **The model uses sex and age as inputs**, as the notebook does. Using them to make real
  lending or collections decisions is restricted or illegal in many places; treat this as a
  study model.
- The data is from Taiwan in 2005: amounts are New Taiwan dollars and customers have no names.

## Accounts and access

Everything except the sign-in and sign-up pages needs an account. There is no open or
"development" mode: without a valid session the API answers 401.

| Role | How you get it | Can do |
|---|---|---|
| `user` | Sign up on the web page | Use the AI Assistant: ask questions, see answers and sources |
| `admin` | Created or promoted from the server command line only | Everything: risk dashboard, customer records, upload/delete/reindex documents, knowledge base, health, assistant |

Sign-up always creates a `user`. The request has no role field, and the server ignores any
extra fields, so nobody can register as an administrator. Roles are checked by the API on every
request; the panel hiding a page is only a convenience.

Account administration is done on the server, by someone who can already run commands there:

```bash
# from backend/, with the virtualenv active
python -m app.manage create-admin --email you@example.com --name "Your Name"
python -m app.manage set-role --email someone@example.com --role admin    # or --role user
python -m app.manage reset-password --email someone@example.com
python -m app.manage list-users
```

`set-role` and `reset-password` also end that person's open sessions.

How it works:

- **Passwords** are hashed with Argon2id (`argon2-cffi`) and never stored or logged. They must
  be 8 to 128 characters. Emails are validated and lower-cased, so `Ada@Example.com` and
  `ada@example.com` are one account.
- **Sessions.** Signing in creates a random session token. The browser gets it in a cookie that
  is `HttpOnly` (scripts cannot read it), `SameSite=Lax`, and `Secure` in production. The
  database stores only a SHA-256 hash of the token. Sessions last `SESSION_TTL_HOURS` (12 by
  default). Signing out deletes the session on the server, so a copied cookie stops working.
- **Page reloads.** Nothing is kept in `localStorage` or `sessionStorage`. On load the panel
  asks `GET /api/auth/me`, which answers from the cookie.
- **CSRF.** Every state-changing request made with the cookie must carry the session's CSRF
  token in the `X-CSRF-Token` header (the panel gets it from sign-in and `/api/auth/me` and
  keeps it in memory). Requests whose `Origin` is not this site or in `CORS_ORIGINS` are refused.
- **Brute force.** After `LOGIN_MAX_ATTEMPTS` failed sign-ins for one email from one client
  within `LOGIN_WINDOW_MINUTES`, further attempts get HTTP 429 until the window passes. Sign-up
  is limited to `REGISTER_MAX_PER_HOUR` per client.
- **No account probing at sign-in.** A wrong password and an unknown email get the same message
  and take the same time. Sign-up does say when an email is already registered, because the
  form has to; that endpoint is rate limited.
- **Expired sessions.** Any 401 from the API returns the panel to the sign-in page with a
  "Your session ended" message.

Not included, and what each would need:

- **Email verification.** The project has no way to send email. It would need an SMTP or
  email-API setting, an `email_verified_at` column, a signed single-use token, and a
  `/api/auth/verify` route. Until then, set `ALLOW_REGISTRATION=false` if only invited people
  should have accounts.
- **Forgot password by email.** Same missing piece. For now an administrator runs
  `reset-password` and gives the person the new password; the sign-in page says so.
- **Managing users in the panel.** Roles are changed from the command line only.

## Configuration

All backend settings are environment variables, read from `backend/.env`. That file is
git-ignored; `backend/.env.example` documents every variable.

| Variable | Default | Meaning |
|---|---|---|
| `APP_ENV` | `development` | `production` forces the session cookie to be `Secure` (HTTPS only) |
| `ALLOW_REGISTRATION` | `true` | `false` closes public sign-up |
| `SESSION_TTL_HOURS` | `12` | How long a sign-in lasts |
| `COOKIE_SECURE` | empty | Send the cookie over HTTPS only. Empty: on in production, off in development |
| `LOGIN_MAX_ATTEMPTS` / `LOGIN_WINDOW_MINUTES` | `5` / `15` | Failed sign-ins per email and client before a temporary lock-out |
| `REGISTER_MAX_PER_HOUR` | `10` | Sign-ups per client per hour |
| `ADMIN_API_TOKEN` | empty | Optional credential for scripts: `Authorization: Bearer <token>` gets administrator access without a session. Empty turns it off. At least 16 characters |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Browser origins allowed to call the API and send the session cookie |
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
| `OLLAMA_MODEL` | `llama3.2` | Ollama chat model. Pull it first with `ollama pull llama3.2` |
| `OLLAMA_NUM_CTX` | empty | Context window in tokens. Empty uses Ollama's default (4096) |
| `LLM_TIMEOUT_SECONDS` | `60` | Per-request timeout |

No setting holds a password or session secret: accounts live in the database. If you use
`ADMIN_API_TOKEN`, generate it with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

The frontend has one optional variable, `VITE_API_BASE_URL` (see `frontend/.env.example`), for
when the built frontend is hosted on a different origin from the API.

## Local answers with Ollama

Ollama generates the written answers on your own machine: no API key, no cost, and no document
text leaves the machine. Embeddings do **not** use Ollama; they stay on `fastembed`
(`BAAI/bge-small-en-v1.5`, 384 dimensions) for both documents and questions, so switching the
answer model never requires re-indexing.

1. Install Ollama from https://ollama.com/download and start it. It listens on
   `http://127.0.0.1:11434`. Check with `ollama --version` and `ollama list`.
2. Pull a chat model (about 2 GB for the default):

   ```bash
   ollama pull llama3.2
   ```

3. In `backend/.env` set:

   ```
   LLM_PROVIDER=ollama
   OLLAMA_BASE_URL=http://127.0.0.1:11434
   OLLAMA_MODEL=llama3.2
   LLM_TIMEOUT_SECONDS=120
   ```

4. Restart the backend. The startup log prints `Answer model: llama3.2 (Ollama) is available`,
   and the same line appears under **Answer model (LLM)** on the Knowledge Base
   pages.

`OLLAMA_MODEL` must be a chat model. Embedding-only models such as `nomic-embed-text` are
rejected with a clear message. The first question after Ollama starts is slower while the model
loads; without a GPU, keep `LLM_TIMEOUT_SECONDS` at 120 or more.

If Ollama is stopped, the model is not pulled, or a request fails, nothing breaks: queries
return the retrieved passages with a warning that says why, and the health panel and the
assistant show the actual error. Typical messages:

| Message | Fix |
|---|---|
| `Ollama is not reachable at http://127.0.0.1:11434. Is it running?` | Start Ollama, or correct `OLLAMA_BASE_URL` |
| `Ollama is running but 'llama3.2' is not pulled. Run: ollama pull llama3.2` | Pull the model, or set `OLLAMA_MODEL` to one from `ollama list` |
| `'nomic-embed-text' is not a chat model` | Set `OLLAMA_MODEL` to a chat model |
| `Ollama timed out.` | Raise `LLM_TIMEOUT_SECONDS`, or use a smaller model |

With Ollama the model returns its answer as separate statements, each with the sources that
support it, and the backend adds the `[n]` markers. Small local models are less reliable than
Claude: they tend to copy sentences from the passages, and can occasionally mislabel an answer
as insufficient evidence. The retrieved sources shown under every answer are always the real
retrieved chunks.

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

Access is one of: **public**, **user** (any signed-in account), or **admin**. Without a valid
session the private routes answer 401; an ordinary user on an admin route gets 403. Scripts can
instead send `Authorization: Bearer <ADMIN_API_TOKEN>` if that token is configured. Errors
always have the shape `{"error": {"code": "...", "message": "..."}}`.

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/api/health` | public | Liveness: overall status only |
| GET | `/api/auth/status` | public | Whether sign-up is open |
| POST | `/api/auth/register` | public | Create an ordinary account: `{full_name, email, password}`. 409 if the email is taken |
| POST | `/api/auth/login` | public | `{email, password}`. Sets the session cookie; returns the user and the CSRF token |
| GET | `/api/auth/me` | user | The signed-in user and the CSRF token |
| POST | `/api/auth/logout` | user | Ends the session on the server and clears the cookie |
| GET | `/api/admin/session` | admin | Check an API token or an administrator session |
| GET | `/api/admin/health` | admin | Live status of the store, file storage, embedding model and LLM (for Ollama: reachable and model pulled) |
| GET | `/api/admin/config` | user | Limits, answer provider and model, retrieval defaults (no secrets) |
| GET | `/api/admin/documents` | admin | List documents with status and metadata |
| POST | `/api/admin/documents` | admin | Upload (multipart field `file`); returns 202 |
| GET | `/api/admin/documents/{id}` | admin | One document |
| DELETE | `/api/admin/documents/{id}` | admin | Delete the document, its file and its chunks |
| POST | `/api/admin/documents/{id}/reindex` | admin | Re-run ingestion from the stored file |
| GET | `/api/admin/activity?limit=20` | admin | Recent uploads, indexing results, deletions |
| GET | `/api/admin/kb/stats` | admin | Document and chunk counts, knowledge-base status |
| POST | `/api/admin/kb/rebuild` | admin | Reindex every document |
| GET | `/api/admin/portfolio/summary` | admin | Portfolio totals, the groups behind the Risk Dashboard charts, and the model's scores |
| GET | `/api/admin/customers` | admin | Customers, paged: `search` (customer number), `risk` (`low`, `medium`, `high`), `actual` (`defaulted`, `paid`), `sort`, `order`, `page`, `page_size` |
| GET | `/api/admin/customers/{id}` | admin | One customer with their prediction and payment history |
| POST | `/api/admin/rag/query` | user | Ask a question: `{question, history?, top_k?, min_score?, document_ids?}` |

## Demo script

1. Start both servers, create an administrator (see "Quick start"), open http://localhost:5173
   and sign in.
2. **Risk Dashboard** and **Customer Records**: the customer portfolio and risk predictions, once
   `import-customers` and `train-model` have run.
   Open **AI Assistant → Knowledge Base**: everything is zero and the knowledge base is "Empty".
3. **AI Assistant → Documents**: upload the three files in `sample_policies/`. Each moves from Queued to Indexed
   with its chunk count; the PDF shows 2 pages.
4. Upload `escalation_policy.pdf` again to show the duplicate rejection, and any `.docx` or
   `.exe` to show type validation.
5. **Knowledge Base**: 3 of 3 indexed, 14 searchable chunks, model and chunking settings, health.
6. **AI Assistant → Chat**, ask:
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
pytest                   # 138 tests, no network: keyword embedder + scripted LLM
pytest -m integration    # 6 tests using the real embedding model
ruff check .

# Frontend (from frontend/)
npm test                 # 34 tests: API client contract, sign-in/sign-up and all pages, fetch mocked
npm run lint
npm run build            # type-check + production build into dist/
```

The default backend suite replaces only the embedding model and the LLM; the API, validation,
extraction, chunking, SQLite storage and retrieval code all run for real. The integration suite
runs retrieval with the real embedding model. No automated test calls the Claude API, because
that needs a key.

## Security notes

- Set `APP_ENV=production` and serve over HTTPS for anything that is not your own laptop, so
  the session cookie is only ever sent encrypted.
- Sign-in, sessions, roles, CSRF and rate limiting are described in "Accounts and access".
- Leave `ADMIN_API_TOKEN` empty unless a script needs it. It is a shared administrator
  credential with no expiry.
- Host the panel and the API on the same site (the same origin, or sub-domains of one domain).
  The session cookie is `SameSite=Lax` and is not sent between unrelated sites.
- Uploaded files are stored under their document ID, never under a user-supplied name.
- Logs record document IDs, sizes and counts, not document text or questions.

## Known limitations

- No OCR, and no DOCX support.
- No email verification or self-service password reset, because nothing here sends email.
- Rate limits are counted in memory by client address. They reset when the server restarts,
  are not shared between several server processes, and behind a reverse proxy every visitor
  has the proxy's address, so the per-email limit is the one that matters there.
- Ingestion runs in the API process. That is fine for policy documents; a very large batch
  would want a job queue.
- Search loads all vectors into memory for each query. Beyond a few hundred thousand chunks,
  move to a vector index.
- The default score threshold (0.6) was tuned on the sample policies with `bge-small`. Questions
  that are on-topic but unanswered by the documents can still retrieve passages; in `generated`
  mode Claude is instructed to say the evidence is insufficient. Tune `min_score` live in the
  assistant's retrieval settings.
