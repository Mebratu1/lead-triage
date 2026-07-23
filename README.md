# LeadTriage

LeadTriage is a FastAPI backend portfolio project for an AI-assisted lead-classification API. The current implementation is intentionally limited to the aligned HTTP contract and infrastructure needed before adding idempotency, persistence, and AI classification.

## Current Status

- FastAPI project scaffold complete
- Health endpoints complete
- Supabase configuration complete
- Database schema complete
- Lead request contract aligned
- AI classification not yet connected
- Lead persistence not yet connected
- Idempotency processing not yet connected

## Runtime

LeadTriage targets Python `>=3.12,<3.13`.

The repository includes `.python-version` with:

```text
3.12
```

The current workstation may temporarily run tests with Python 3.14.3 until Python 3.12 is installed. Do not claim full Python 3.12 verification until the suite passes under Python 3.12.

## API

### Health

```http
GET /health
GET /health/database
```

`GET /health/database` performs a read-only Supabase query against the `leads` table. Tests override the database dependency with a mock.

### Lead Inquiry Contract

```http
POST /api/leads
```

Request:

```json
{
  "source": "website",
  "message": "I need emergency plumbing service today. Please call me at 301-555-0144."
}
```

Rules:

- `source` is optional and defaults to `website`
- `source` length must be 2 to 50 characters
- `message` is required
- `message` length must be 10 to 5000 characters
- leading and trailing whitespace is trimmed
- unsupported extra fields are rejected

Temporary response:

```json
{
  "status": "accepted",
  "source": "website",
  "message": "I need emergency plumbing service today. Please call me at 301-555-0144.",
  "classification_status": "pending"
}
```

The endpoint returns `202 Accepted`. It validates the public contract only. It does not persist the lead, call OpenAI, generate an idempotency key, or create fake classification data yet.

## Database

The migration in `app/db/migrations/001_init_schema.sql` defines the future `leads` table shape:

- `id`
- `idempotency_key`
- `source`
- `raw_message`
- `customer_name`
- `email`
- `phone`
- `requested_service`
- `urgency`
- `lead_score`
- `ai_summary`
- `classification_status`
- `created_at`

The request field `message` maps later to `raw_message` when persistence is connected.

Do not run destructive migrations automatically. Apply the SQL manually in the Supabase SQL Editor when ready.

## Setup

Install Python 3.12 on Windows:

```powershell
winget install Python.Python.3.12
py -0p
py -3.12 --version
```

Create the virtual environment:

```powershell
cd "C:\Users\mebbr\OneDrive\Documents\lead-triage"
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
```

Copy environment variables:

```powershell
Copy-Item .env.example .env
```

Never commit `.env` or service-role keys.

## Development Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -ra -W default
```

Run locally:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Test the aligned lead contract:

```powershell
$body = @{
    source = "website"
    message = "I need emergency plumbing service today."
} | ConvertTo-Json

Invoke-RestMethod `
    -Method Post `
    -Uri http://127.0.0.1:8000/api/leads `
    -ContentType "application/json" `
    -Body $body
```

## Testing Notes

Current API tests are synchronous and use `TestClient`. Service tests are intentionally minimal because repository, OpenAI, idempotency, and persistence behavior belong to later milestones.

`pytest.ini` disables pytest's cache provider only for this local OneDrive-backed repository because cache creation previously hung. Moving the repository outside a synced OneDrive folder may allow normal pytest caching again.

## Later Milestones

Next implementation work should add, in order:

1. Typed classified lead output contracts
2. Deterministic idempotency key generation and lookup
3. OpenAI-compatible structured classification
4. Supabase persistence using `raw_message`
5. Vercel deployment configuration
