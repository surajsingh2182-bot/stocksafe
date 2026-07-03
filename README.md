# StockSafe

AI-powered SEBI fraud detection for retail investors. Search any Indian
penny stock (or paste a WhatsApp tip) and get a 0–100 risk score, plain
language red flags with SEBI source citations, a historical pattern stat,
and a position-sizing nudge — before you invest.

Built from `StockSafe_PRD_v2_Complete.docx` and `StockSafe_Design_Handoff.docx`.

## What's inside

| Component | Tool |
|---|---|
| Language | Python 3.11 |
| Backend | FastAPI on Render (free tier) |
| Frontend | Streamlit on HuggingFace Spaces (free) |
| Database | Supabase (PostgreSQL 15 + pgvector, free tier) |
| LLM | Gemini 1.5 Flash (free, 1500 req/day) |
| PDF parsing | PyMuPDF |
| Entity extraction | Noticee-table regex (primary) + spaCy NER (fallback) |
| Fuzzy matching | rapidfuzz |
| Scheduler | GitHub Actions cron (free) |

## Project structure

```
stocksafe/
├── schema.sql                  # run once in Supabase SQL Editor
├── requirements.txt             # api/ deps (Render)
├── requirements-ingestion.txt   # ingestion/ deps (local + GH Actions)
├── ingestion/
│   ├── scraper.py               # SEBI enforcement-orders scraper + pipeline
│   ├── pdf_parser.py            # order PDF -> structured fields
│   ├── entity_linker.py         # fuzzy company/director matching
│   └── seed_outcomes.py         # 20 seeded historical outcomes
├── api/
│   ├── main.py                  # 9 endpoints
│   ├── resolver.py              # fuzzy company search
│   ├── retrieval.py             # order lookups + pattern stats
│   ├── risk_scorer.py           # scoring weights/formula
│   └── gemini_client.py         # red flags + tip of the day
├── frontend/
│   └── app.py                   # all 8 screens + router
├── .github/workflows/scraper.yml
└── tests/                       # pytest — no DB needed
```

## Run it locally

**1. Prerequisite — Python 3.11.** This project's pinned dependencies
(spaCy, PyMuPDF) don't build on newer Pythons. Install 3.11 alongside
whatever you already have:

```powershell
winget install --id Python.Python.3.11 -e
```

A `.venv` built with 3.11 already exists in this repo if you're picking up
where the build left off. To recreate it:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m spacy download en_core_web_sm
```

**2. Create your `.env`.** Copy `.env.example` to `.env` (repo root) and
`frontend/.env.example` to `frontend/.env`, then fill in real values — see
"Get your credentials" below. Never commit the real `.env` files.

**3. Set up the database.** Create a free Supabase project, open the SQL
Editor, paste in the entire contents of `schema.sql`, and run it once.

**4. Run the ingestion pipeline** (downloads real SEBI order PDFs and
populates the DB — takes a few minutes, respects a 2s delay between
requests):

```powershell
.venv\Scripts\python ingestion\scraper.py --pages 10   # first run — historical backfill
.venv\Scripts\python ingestion\seed_outcomes.py         # 20 seed rows for pattern stats
```

**5. Run the API:**

```powershell
.venv\Scripts\python -m uvicorn api.main:app --reload --port 8000
```

**6. Run the frontend** (separate terminal):

```powershell
.venv\Scripts\python -m pip install -r frontend\requirements.txt
.venv\Scripts\python -m streamlit run frontend\app.py
```

**7. Run the tests** (no DB or API keys needed):

```powershell
.venv\Scripts\python -m pytest tests\ -v
```

## Get your credentials

- **Supabase**: [supabase.com](https://supabase.com) → New project (free
  tier) → Settings → API → copy the Project URL and `anon` key into `.env`.
- **Gemini API key**: [aistudio.google.com](https://aistudio.google.com) →
  Get API key (free tier, 1500 requests/day) → paste into `.env`.

## Deploy to a public URL

**Backend → Render**
1. [render.com](https://render.com) → New Web Service → connect this GitHub repo.
2. Build command: `pip install -r requirements.txt`
3. Start command: `uvicorn api.main:app --host 0.0.0.0 --port $PORT`
4. Instance type: Free. Add env vars: `SUPABASE_URL`, `SUPABASE_KEY`, `GEMINI_API_KEY`.
5. Deploy, then copy the `.onrender.com` URL.

**Frontend → HuggingFace Spaces**
1. [huggingface.co](https://huggingface.co) → New Space → Streamlit → free tier.
2. Upload `frontend/app.py` and `frontend/requirements.txt`.
3. Space secrets: `API_BASE_URL` = your Render URL from above.

**Daily scraper → GitHub Actions**
1. Push this repo to GitHub.
2. Repo → Settings → Secrets → Actions → add `SUPABASE_URL`, `SUPABASE_KEY`, `GEMINI_API_KEY`.
3. Actions tab → run the "Daily SEBI Scraper" workflow manually once to verify it works.

## Known limits (free tier)

- Render cold start ~15s after inactivity — the loading screen expects this.
- Gemini caps at 1500 unique-company searches/day; `search_cache` (24h TTL) prevents repeat calls from counting twice.
- HuggingFace Spaces sleeps after ~15 min idle.
- The 48-hour reminder is a database record only — no real push notification (documented as a v2+ feature).
