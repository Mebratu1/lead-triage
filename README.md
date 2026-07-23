# LeadTriage - AI-Powered Lead Classification API

Production-ready portfolio project demonstrating modern Python backend development.

## Overview

LeadTriage is an intelligent lead management system that:

- **Receives** incoming sales leads via REST API
- **Validates** all requests using Pydantic
- **Deduplicates** leads to prevent duplicate processing (7-day window)
- **Classifies** leads using OpenAI GPT-4 with intelligent scoring (0-100)
- **Stores** lead data in Supabase PostgreSQL
- **Exposes** REST APIs for ingestion and retrieval
- **Deploys** to Vercel as serverless functions

## Tech Stack

- **Framework**: FastAPI (Python 3.12)
- **Database**: Supabase (PostgreSQL)
- **LLM**: OpenAI GPT-4 Turbo
- **Validation**: Pydantic v2
- **Testing**: pytest + Starlette TestClient
- **Deployment**: Vercel

## Runtime Requirement

LeadTriage targets Python 3.12 only: `>=3.12,<3.13`.

The `.python-version` file pins local tooling to Python 3.12. The current Windows workstation is temporarily validating with Python 3.14.3 until Python 3.12 is installed.

## Architecture

### Folder Structure

```
lead-triage/
├── app/
│   ├── main.py              # FastAPI application
│   ├── config.py            # Configuration management
│   ├── db/
│   │   ├── client.py        # Supabase client
│   │   └── migrations/      # Database schemas
│   ├── models/
│   │   ├── lead.py          # Domain model
│   │   └── schemas.py       # Request/response models
│   ├── services/
│   │   ├── lead_service.py  # Lead orchestration
│   │   ├── classifier.py    # LLM classification
│   │   └── dedup.py         # Deduplication logic
│   └── routes/
│       ├── health.py        # Health check
│       └── leads.py         # Lead endpoints
├── tests/
│   ├── conftest.py          # Pytest fixtures
│   ├── test_leads.py        # API tests
│   ├── test_classifier.py   # Classifier tests
│   └── test_dedup.py        # Dedup tests
└── scripts/
    └── setup_db.py          # Database setup
```

### Data Flow

```
1. POST /leads/ingest
   ↓
2. Pydantic validation
   ↓
3. Duplicate check (email/phone)
   ↓
4. If duplicate: mark as duplicate, link to original
   ↓
5. If new: LLM classification (GPT-4)
   ↓
6. Store in Supabase + audit log
   ↓
7. Return classified lead
```

## Setup

### 1. Environment Variables

```bash
cp .env.example .env
```

Fill in your credentials:

```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-anon-key
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
OPENAI_API_KEY=your-openai-api-key
JWT_SECRET=<generate-with-python-c-import-secrets-print-secrets-token-urlsafe-32>
```

### 2. Install Dependencies

```bash
pip install -e ".[dev]"
```

### 3. Initialize Database

```bash
python scripts/setup_db.py
```

Then manually execute the SQL in Supabase SQL Editor, or use:

```bash
# For Supabase admin
psql postgresql://postgres:password@your-db.supabase.co:5432/postgres \
  -f app/db/migrations/001_init_schema.sql
```

## Running

### Development Server

```bash
uvicorn app.main:app --reload --port 8000
```

Visit: http://localhost:8000/docs (Swagger UI)

### Testing

```bash
# All tests
pytest

# With coverage
pytest --cov=app --cov-report=html

# Specific markers
pytest -m unit           # Unit tests only
pytest -m integration    # Integration tests
pytest -m "not slow"     # Skip slow tests
```

## API Endpoints

### Health Check

```bash
GET /health
```

Response:
```json
{
  "status": "ok",
  "environment": "development",
  "version": "0.1.0"
}
```

### Ingest Lead

```bash
POST /leads/ingest
Content-Type: application/json

{
  "email": "john@example.com",
  "first_name": "John",
  "last_name": "Doe",
  "phone": "+1-555-123-4567",
  "company": "Acme Corp",
  "job_title": "Sales Manager"
}
```

Response:
```json
{
  "id": "uuid-here",
  "email": "john@example.com",
  "first_name": "John",
  "last_name": "Doe",
  "lead_score": 85,
  "status": "qualified",
  "tags": ["sales_ready", "high_priority"],
  "is_duplicate": false,
  "classification_rationale": "...",
  "received_at": "2026-07-22T11:00:00Z",
  "classified_at": "2026-07-22T11:00:05Z",
  "created_at": "2026-07-22T11:00:00Z",
  "updated_at": "2026-07-22T11:00:00Z"
}
```

### Get Lead

```bash
GET /leads/{lead_id}
```

## Classification Criteria

Leads are scored 0-100 based on:

- **Fit** to business criteria
- **Engagement quality** (title, company size, industry)
- **Budget signals** (company size, type)
- **Decision-making authority**
- **Timeline urgency**

Statuses:
- `qualified`: 80-100 (ready for sales)
- `needs_nurture`: 60-79 (good potential)
- `low_value`: 0-59 (not qualified)

Tags:
- `sales_ready`: Immediate outreach
- `needs_nurture`: Follow-up sequence
- `high_priority`: VIP account
- `spam`: Invalid/suspicious
- `low_value`: Not worth effort

## Key Design Decisions

### 1. **Asynchronous Processing**

- All database and LLM calls are async (FastAPI + AsyncClient)
- Enables high throughput, low latency
- Production-ready for serverless deployment

### 2. **Deduplication Window**

- 7-day window (configurable)
- Prevents duplicate processing within recent history
- Maintains audit trail of duplicates

### 3. **LLM Prompting Strategy**

- Detailed system prompt with classification criteria
- Strict JSON response format for reliability
- Fallback to `processing_error` status if classification fails
- Configurable model (defaults to GPT-4 Turbo)

### 4. **Database Schema**

- Normalized tables: `leads`, `lead_tags`, `classification_history`, `duplicate_log`
- Audit trail via `classification_history` and `duplicate_log`
- Full-text search indexes on email, status, score
- Row-level security (RLS) for multi-tenant deployments
- Automatic `updated_at` timestamp via trigger

### 5. **Error Handling**

- Graceful degradation: leads stored even if LLM classification fails
- Detailed audit logging for troubleshooting
- Structured error responses with HTTP status codes

## Testing Strategy

### Unit Tests
- Pydantic model validation
- LLM response parsing
- Deduplication logic

### Integration Tests
- End-to-end lead ingestion
- Database operations
- LLM classification (mocked)

### Test Markers
```bash
pytest -m unit           # Fast, no external deps
pytest -m integration    # Requires Supabase/OpenAI
pytest -m slow           # Long-running tests
```

## Deployment

### Vercel

```bash
vercel
```

Configure environment variables in Vercel dashboard.

### Docker

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install -e .
COPY app/ ./app/
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```bash
docker build -t leadtriage .
docker run -p 8000:8000 --env-file .env leadtriage
```

## Interview Talking Points

1. **Async Architecture**: Why async/await, how it scales
2. **Deduplication**: Trade-offs between precision and recall
3. **LLM Integration**: Prompt engineering, error handling, cost optimization
4. **Database Design**: Normalization, indexing, audit trails
5. **Testing**: Unit vs integration, fixture patterns, markers
6. **Error Handling**: Graceful degradation, observability
7. **Deployment**: Serverless vs containerized, configuration management

## Project Milestones

- ✅ **Milestone 1**: Project Setup & Database Schema
- 📋 **Milestone 2**: Lead Ingestion Service (API + Validation)
- 📋 **Milestone 3**: Deduplication & Classification
- 📋 **Milestone 4**: Advanced Filtering & Analytics
- 📋 **Milestone 5**: Production Hardening & Deployment

## License

MIT
