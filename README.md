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
| Case Analyst Agent | Customer page → Start conversation | Reads the payment history and writes the case file: summary, situation, urgency, patterns, care flags; every finding cites a fact |
| Decision Agent | Customer page → Start conversation | Reads the case file and the policies; recommends reminder / payment plan / call / escalation; every reason cites a fact or policy |
| Compliance Red Team + loop | Customer page → Compliance review | A second model attacks every draft (and the action behind it); the writer rewrites until nothing is left to fix, up to 4 rounds, or the draft is blocked |
| Communication Agent | Customer page → The customer replied? | Reads the whole email thread, works out what the customer wants, and drafts the reply, or hands the conversation to a person (dispute, complaint, stop contact) |
| Human approval | Customer page, Approvals | Every email waits for an officer: approve (edit is re-checked), reject with a reason, write again with feedback, then send. Payment-plan and hardship emails need a second approval by a different person who is a team lead (four eyes) |
| Contact rules | (every send) | Fixed rules read from the contact policy: only 08:00-21:00 customer time, not on public holidays, at most 3 contacts in 7 days, none for 48 hours after a call. A send that breaks one is refused, whatever the model wrote |
| Collections ledger | Customer page → Collections ledger | Payments (lower what the emails ask for), promises to pay (the Communication Agent records "I'll pay Friday" as a date; the Follow-up Agent checks it the day after; two broken promises go to a person) and every contact attempt |
| Email | Approvals → Check the mailbox | Microsoft 365 through Microsoft Graph: Send really sends, replies are matched to their thread and answered. Off by default; outside production every email goes to a test address |
| Do not contact | Customer page | A stop request (read by the agent, caught by a fixed phrase check, or recorded by an officer) blocks every email until an officer lifts it |
| Follow-up Agent | Follow-ups, customer page | After each email it plans, from the contact policy, what happens without a reply: wait N days, then a reminder (drafted and checked like any email), a call, or a hand-over |
| Counterfactual Simulator | Customer page → Counterfactual simulator | What-ifs on the risk model (pays in full, catches up, misses next month...), a fairness check (only sex or education changed), and your own change, optionally decided by the Decision Agent |
| Agent orchestration | (backend) `orchestration/graph.py` | One LangGraph `StateGraph` connects the agents; the Red Team loop is its conditional edges. `GET /api/admin/agents/graph` draws it |
| Audit trail | Audit Trail | Every agent step and officer action, append-only (the database refuses changes), filterable by customer and event |
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

## How the agents connect (LangGraph)

```
 Start conversation          Customer replied / follow-up due
        │                                │
  case_analyst ─► decision_agent ─►  writer ─(dispute, complaint, stop contact)─► hand to a person
                                         │
                                     guardrails ─(rule broken)─► writer        (max 4 rounds,
                                         │                                       then "blocked")
                                      red_team ─(blocker)─► writer
                                         │      (action challenged: decision_agent decides again)
                                         ▼
                      draft waits for an officer: approve / edit / reject / write again
                                         │ send
                                         ▼
                      Follow-up Agent plans the next step ─► due: reminder, call or hand-over
```

Every step above is written to the audit trail (`app/core/audit.py`).

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
| | `case_analyst.py` | The Case Analyst Agent: payment facts → a case file the Decision Agent reads |
| | `messages.py` | The email writer: decision + messaging policy → one draft, fixed guardrails |
| | `red_team.py` | The Compliance Red Team Agent: attacks a draft; findings must quote the email |
| | `compliance_loop.py` | Writer ⇄ guardrails ⇄ Red Team, round after round; can make the Decision Agent decide again |
| | `conversation.py` | One email thread per customer: runs the graph, stores every draft, approval workflow |
| | `follow_up.py` | The Follow-up Agent: plans the follow-up after a send, checks promises, carries it out when due |
| | `contact_rules.py` | Contact hours, frequency and quiet period, read from the contact policy; enforced on every send |
| | `agent_evaluation.py` | Scores the agents on `sample_policies/agent_eval.json` (intents, hand-overs, Red Team catches) |
| `customers/` | `ledger.py` | Payments, promises to pay and contact attempts |
| `integrations/` | `mail.py` | Email: Microsoft Graph (send, read replies) or record-only |
| `orchestration/` | `graph.py` | The LangGraph `StateGraph`: every agent as a node, the compliance loop as its edges |
| `core/` | `audit.py` | The append-only audit trail |
| `risk/` | `counterfactual.py` | The Counterfactual Simulator: what-ifs and the fairness check on the risk model |
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

**Team leads** give the second approval on payment-plan and hardship emails. Make an
administrator one with `.venv\Scripts\python -m app.manage set-supervisor --email lead@example.com`.

**Email** is off until you set it up. In Microsoft Entra ID, register an app with the
application permissions `Mail.Send` and `Mail.ReadWrite` (with admin consent) and a client
secret, then set `EMAIL_PROVIDER=graph`, `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`,
`GRAPH_CLIENT_SECRET` and `GRAPH_MAILBOX` in `backend/.env`. Outside production the backend
refuses to start without `EMAIL_REDIRECT_TO`: every email goes to that address instead of the
customer. Replies are read with *Check the mailbox* on the Approvals page,
`python -m app.manage poll-email`, or every `EMAIL_POLL_MINUTES`.

**Score the agents** against the real model with `.venv\Scripts\python -m app.manage evaluate-agents`.

**Follow-ups** run when you press *Run due follow-ups* on the Follow-ups page, or with
`.venv\Scripts\python -m app.manage run-follow-ups` (for a scheduled task). To have the
backend run them itself, set `FOLLOW_UP_INTERVAL_MINUTES=60` in `backend/.env`.

## Deploy to Azure

`deploy/azure/deploy.ps1` builds the image in Azure and deploys `deploy/azure/main.bicep`:

| Resource | What it runs |
|---|---|
| Container App `complyagent-web` | the API and the React app (one image, `Dockerfile`), 1-3 replicas over HTTPS |
| Container Apps job, every 15 min | `python -m app.manage run-follow-ups` |
| Container Apps job, every 5 min | `python -m app.manage poll-email` (only with `EMAIL_PROVIDER=graph`) |
| Azure Database for PostgreSQL | the database (`DATABASE_URL`), instead of the SQLite file |
| Azure Files share | the uploaded policy documents, shared by every replica |

```powershell
az login
./deploy/azure/deploy.ps1 -ResourceGroup complyagent-rg -AzureAiEndpoint https://<name>.openai.azure.com/ -AdminEmails you@example.com
```

It asks for the PostgreSQL password, the Azure AI key and an admin API token, and prints the
commands that load the data and the policies once. On Azure the schedulers run as jobs, not
inside the web app: a follow-up is claimed before it runs and a mailbox email is never received
twice, so the jobs and several web replicas can run side by side.

**PostgreSQL locally:** set `DATABASE_URL=postgresql://user:password@localhost:5432/complyagent`
in `backend/.env`. Without it the backend uses the SQLite file in `backend/storage/`. To run the
tests on PostgreSQL too, set `TEST_DATABASE_URL` (each test gets its own schema).

## Retrain the model

The notebook is the only place the model is trained. To change it, edit and run the notebook;
its last cell saves new files to `backend/models/risk_model/`. Then score the customers again.

The model does not use sex or education: a collections decision must depend on how the customer
pays, not on who they are. Leaving them out kept the test ROC-AUC at 0.767 (0.769 with them) and
raised recall from 0.623 to 0.634. Age is still an input; the simulator's fairness check shows how
much it moves each prediction.


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
