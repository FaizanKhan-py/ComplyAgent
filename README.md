# ComplyAgent

ComplyAgent helps a collections team handle overdue credit card accounts. It predicts which
customers are likely to default, answers questions about the company's collections policies,
recommends the next action for a customer, and drafts the collections email for a person to
review. The AI features are built with **LangChain**.

Data: the UCI [Default of Credit Card Clients](https://archive.ics.uci.edu/dataset/350/default)
dataset (30,000 customers, six months of payment history each), with made-up email addresses.

## What it does

| Feature | Where in the app | How it works |
|---|---|---|
| Risk dashboard | Dashboard | Portfolio totals, late-payment trend, risk groups, model scores |
| Customer records | Customers | Search, filter and open any customer: details, payment history, risk |
| Default prediction | Customer page, Predict | XGBoost model trained in the notebook, saved as files the backend loads |
| AI Assistant | AI Assistant | One chat. A LangChain agent searches the policies and cites them [n]; for admins it also looks up customers, the portfolio and the Decision Agent. Remembers the conversation |
| Decision Agent | Customer page → Generate message | Recommends reminder / payment plan / call / escalation; every reason cites a fact or policy |
| Email drafts | Customer page | Written in the policy's tone, then checked: no threats, no invented amounts or links |
| Policy documents | AI Assistant → Documents | Upload PDF, Word, text or Markdown; they are chunked, embedded and indexed |

## How the pieces connect

```
 notebooks/Data_pipeline_and_Modeling.ipynb
      │ trains XGBoost, saves
      ▼
 backend/models/risk_model/  (model.json, metadata.json, data_split.csv)
      │ loaded by app/risk/model_files.py
      ▼
 Browser ── React UI (frontend/) ──► FastAPI (backend/app/main.py) ──► SQLite (app/core/db.py)
                                         │
                                         ▼
                 backend/app/langchain/  (models · prompts · chains · memory · indexes · agents)
                                         │
                                         ▼
                              Azure OpenAI (or Claude)
```

## The LangChain layer: `backend/app/langchain/`

One file per LangChain component. Each one is used by the app, not kept as an example.

| File | Component | What it holds | Used by |
|---|---|---|---|
| `models.py` | Models | `AzureChatOpenAI` or `ChatAnthropic`, picked by `LLM_PROVIDER`; one error mapping for every provider | everything below |
| `prompts.py` | Prompts | Every system prompt, and the `ChatPromptTemplate`s (with a `MessagesPlaceholder` for memory) | chains, agent |
| `chains.py` | Chains | LCEL pipelines: `prompt \| model.with_structured_output(schema)` | customer policy guidance, Decision Agent, email writer |
| `memory.py` | Memory | Window memory (`trim_messages`) for the assistant; LangGraph `InMemorySaver` for the agent's conversations | `llm.py`, agent |
| `llm.py` | (assembly) | `LangChainGenerator`, built from models + prompts + chains + memory | RAG answers, Decision Agent, email writer |
| `indexes.py` | Indexes | `PolicyRetriever`, a `BaseRetriever` over the embedded policy chunks, returning `Document`s | agent's `search_policies` tool |
| `agents.py` | Agents | The AI Assistant: `create_agent` with six `@tool`s (users get `search_policies` only), numbered sources | `POST /api/assistant/chat` |

`langchain/llm.py` is the single entry point the rest of the backend calls
(`LangChainGenerator`).

## Every file, in one line

**backend/app/**, one folder per part of the system:

```
app/
  main.py              the HTTP API: every route, sign-in and admin checks, error format
  manage.py            command line: accounts, data import, scoring, documents, evaluation
  core/                what everything else builds on
  rag/                 the policy knowledge base
  langchain/           the LangChain components (table above)
  decisions/           what to do for one customer
  risk/                the default-risk model
  customers/           the customer data
```

| Folder | File | What it is |
|---|---|---|
| `core/` | `config.py` | All settings, read from `backend/.env` (template: `.env.example`) |
| | `db.py` | SQLite: documents and chunks, users and sessions, customers, predictions |
| | `auth.py` | Password hashing (Argon2), session tokens, sign-in rate limits |
| | `schemas.py` | The API's request and response shapes |
| `rag/` | `knowledge_base.py` | RAG pipeline: upload → extract → chunk → embed → store; search; answer with citations |
| | `extraction.py` | File checks and text extraction (PDF, Word, text, Markdown) |
| | `chunking.py` | Splits text into overlapping chunks that keep their page numbers |
| | `embeddings.py` | Local embeddings (fastembed), or a hashing fallback |
| | `evaluation.py` | Scores the RAG system against `sample_policies/eval_questions.json` |
| `langchain/` | see above | Models, prompts, chains, memory, indexes, agents, and `llm.py` |
| `decisions/` | `decision.py` | The Decision Agent: facts + policies → one checked recommendation |
| | `messages.py` | The email writer: decision + messaging policy → one checked draft |
| | `guidance.py` | Turns a customer's facts into policy questions for the customer page |
| `risk/` | `model_files.py` | Loads the notebook's model files (`backend/models/risk_model/`) |
| | `risk_model.py` | Scores the customers in the database with that model |
| | `predictor.py` | Scores an uploaded CSV/Excel file with that model |
| `customers/` | `portfolio.py` | Import the dataset, dashboard figures, list and record queries |
| | `tables.py` | Reads CSV, .xls and .xlsx tables |

**frontend/src/**: `App.tsx` (routes), `api/` (client and types), `pages/` (one file per
screen, e.g. `Playground.tsx` for the AI Assistant), `components/` (shared UI), `lib/` (formatting).

**Other folders:** `notebooks/` (exploration and model training), `backend/models/risk_model/`
(the trained model files), `sample_policies/` (example policies and evaluation questions),
`backend/tests/` and `frontend/src/**/*.test.tsx` (tests).

## Run it

You need Python 3.12 and Node.js 22.

```powershell
# Backend
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
copy .env.example .env          # fill in AZURE_AI_ENDPOINT, AZURE_AI_API_KEY, AZURE_AI_DEPLOYMENT

# First time only: data, scores, policies, an administrator
.venv\Scripts\python -m app.manage import-customers --csv ..\default_of_credit_card_clients.xls
.venv\Scripts\python -m app.manage score-customers
.venv\Scripts\python -m app.manage add-documents ..\sample_policies\customer_contact_policy.md ..\sample_policies\escalation_policy.pdf ..\sample_policies\hardship_assistance_policy.txt ..\sample_policies\messaging_policy.docx
.venv\Scripts\python -m app.manage create-admin --email you@example.com --name "Your Name"

.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000

# Frontend (second terminal)
cd frontend
npm install
npm run dev                      # http://localhost:5173
```

## Retrain the model

The notebook is the only place the model is trained. To change it, edit and run the notebook;
its last cell saves new files to `backend/models/risk_model/`. Then score the customers again:

```powershell
cd backend
.venv\Scripts\pip install -r ..\notebooks\requirements.txt
.venv\Scripts\python -m ipykernel install --sys-prefix --name complyagent
.venv\Scripts\python -m nbconvert --to notebook --execute --inplace --ExecutePreprocessor.kernel_name=complyagent ..\notebooks\Data_pipeline_and_Modeling.ipynb
.venv\Scripts\python -m app.manage score-customers
```

(Or open the notebook in VS Code or Jupyter and pick the `complyagent` kernel.)

## Tests

```powershell
cd backend;  .venv\Scripts\python -m pytest     # no network or API key needed
cd frontend; npm test
```

The LangChain tests run the real chains against a scripted Azure endpoint, and the real agent
loop with a scripted tool-calling model, so they need no key.
